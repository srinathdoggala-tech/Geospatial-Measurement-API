import logging
import os
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from tempfile import SpooledTemporaryFile
from typing import Annotated
from uuid import UUID, uuid4

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse

from app.config import Settings
from app.errors import ProcessingError
from app.measurements import measure
from app.readers import read_file
from app.schemas import FileInfo, MeasurementsPage
from app.storage import Repository

logger = logging.getLogger(__name__)


class RequestSizeLimit:
    """Bound the entire multipart body before the form parser, including chunked uploads."""

    def __init__(self, app, limit: int):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] not in {"POST", "PUT", "PATCH"}:
            return await self.app(scope, receive, send)
        with SpooledTemporaryFile(max_size=1024 * 1024) as body:
            size = 0
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > self.limit:
                    response = JSONResponse(
                        {"detail": "Request body is too large"}, status_code=413
                    )
                    return await response(scope, receive, send)
                body.write(chunk)
                if not message.get("more_body", False):
                    break
            body.seek(0)
            delivered = False

            async def replay():
                nonlocal delivered
                if delivered:
                    return await receive()
                chunk = body.read(64 * 1024)
                delivered = body.tell() == size
                return {"type": "http.request", "body": chunk, "more_body": not delivered}

            await self.app(scope, replay, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings(
        database=Path(os.getenv("GEOSPATIAL_DB", "data/geospatial.sqlite3"))
    )
    repository = Repository(settings.database)

    @asynccontextmanager
    async def lifespan(application):
        repository.initialize()
        yield

    application = FastAPI(
        title="Geospatial File Measurement API", version="1.0.0", lifespan=lifespan
    )
    application.add_middleware(RequestSizeLimit, limit=settings.max_request_bytes)

    @application.exception_handler(ProcessingError)
    async def processing_error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status_code)

    @application.exception_handler(Exception)
    async def internal_error(request, exc):
        logger.error("Unexpected API failure", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse({"detail": "An internal error occurred"}, status_code=500)

    @application.get("/health/", tags=["Operations"])
    def health():
        with repository.connection() as connection:
            connection.execute("SELECT 1 FROM files LIMIT 1")
        return {"status": "ok"}

    @application.post("/api/files/", status_code=201, response_model=FileInfo, tags=["Files"])
    def upload(
        file: Annotated[UploadFile, File(description="KML or ZIP containing one Shapefile")],
    ):
        filename = (file.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 255 or any(ord(c) < 32 for c in filename):
            raise ProcessingError("Invalid filename", 400)
        extension = Path(filename).suffix.lower()
        if extension not in {".kml", ".zip"}:
            raise ProcessingError("Only .kml and .zip files are accepted", 415)
        # A sync route keeps CPU-intensive geometry work off the event loop.
        data = file.file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise ProcessingError("Uploaded file exceeds the size limit", 413)
        source_crs, features = read_file(data, extension, settings)
        results = [measure(feature, index, source_crs) for index, feature in enumerate(features)]
        info = FileInfo(
            id=uuid4(),
            filename=filename,
            feature_count=len(results),
            crs=source_crs.to_string(),
            created_at=datetime.now(UTC),
            measured_count=sum(r.measurement_status == "MEASURED" for r in results),
            skipped_count=sum(r.measurement_status == "SKIPPED" for r in results),
        )
        repository.save(info, results)
        return info

    def lookup(file_id: UUID):
        info = repository.get(str(file_id))
        if info is None:
            raise HTTPException(status_code=404, detail="File not found")
        return info

    @application.get("/api/files/{file_id}/", response_model=FileInfo, tags=["Files"])
    def file_info(file_id: UUID):
        return lookup(file_id)

    @application.get(
        "/api/files/{file_id}/measurements/", response_model=MeasurementsPage, tags=["Files"]
    )
    def measurements(
        file_id: UUID,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        info = lookup(file_id)
        return MeasurementsPage(
            file_id=file_id,
            total=info.feature_count,
            limit=limit,
            offset=offset,
            features=repository.measurements(str(file_id), limit, offset),
        )

    return application


app = create_app()
