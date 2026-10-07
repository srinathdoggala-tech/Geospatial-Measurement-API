import io
import zipfile

import pytest
import shapefile
from fastapi.testclient import TestClient
from pyproj import CRS

from app.config import Settings
from app.main import create_app


@pytest.fixture
def settings(tmp_path):
    return Settings(database=tmp_path / "test.sqlite3")


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings)) as instance:
        yield instance


def kml(geometry, properties="", feature_id="first"):
    return (
        f'<kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
        f'<Placemark id="{feature_id}">{properties}{geometry}</Placemark>'
        "</Document></kml>"
    ).encode()


def shape_zip(kind="polygon", crs="EPSG:32643", missing=None, extra=None):
    streams = {ext: io.BytesIO() for ext in ("shp", "shx", "dbf")}
    with shapefile.Writer(**streams) as writer:
        writer.field("name", "C")
        if kind == "polygon":
            writer.poly(
                [
                    [
                        (500000, 2000000),
                        (500000, 2000100),
                        (500100, 2000100),
                        (500100, 2000000),
                        (500000, 2000000),
                    ]
                ]
            )
        elif kind == "line":
            writer.line([[(75, 18), (75.01, 18)]])
        elif kind == "point":
            writer.point(75, 18)
        else:
            writer.null()
        writer.record("survey")
    parts = {f"survey.{ext}": stream.getvalue() for ext, stream in streams.items()}
    parts["survey.prj"] = CRS.from_user_input(crs).to_wkt().encode()
    parts["survey.cpg"] = b"UTF-8"
    if missing:
        parts.pop("survey." + missing)
    parts.update(extra or {})
    return make_zip(parts)


def make_zip(parts, compression=zipfile.ZIP_STORED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=compression) as archive:
        for name, data in parts.items():
            archive.writestr(name, data)
    return buffer.getvalue()
