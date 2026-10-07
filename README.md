# Geospatial File Measurement API

Public repository: [project-code1cr/geospatial-measurement-api](https://github.com/project-code1cr/geospatial-measurement-api).

A FastAPI backend that accepts a zipped Shapefile or KML, preserves feature geometry and attributes, and calculates polygon area and line length in a suitable projected coordinate system. SQLite keeps processed results available across restarts.

## Setup

Use Python 3.14 for the pinned, tested environment. The project declares Python 3.12+ compatibility, but other Python versions are not yet tested. No separately installed GDAL is required; Shapely and pyproj use their published binary wheels.

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.lock
.\.venv\Scripts\python.exe -m pip install --no-deps -e .
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

### macOS / Linux

```bash
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-dev.lock
python -m pip install --no-deps -e .
python -m uvicorn app.main:app --reload
```

Open [interactive Swagger documentation](http://127.0.0.1:8000/docs), [ReDoc](http://127.0.0.1:8000/redoc), or [OpenAPI JSON](http://127.0.0.1:8000/openapi.json). Upload `examples/survey.kml` in Swagger to try the complete workflow.

The default database is `data/geospatial.sqlite3`, relative to the working directory. Override it with `GEOSPATIAL_DB`. For example, in PowerShell:

```powershell
$env:GEOSPATIAL_DB = "C:/geospatial-data/geospatial.sqlite3"
```

Use `requirements.lock` instead of `requirements-dev.lock` when only runtime dependencies are needed. The lock files pin the versions used during local verification; `pyproject.toml` describes allowed upgrade ranges. After upgrades, regenerate the pins and rerun the tests.

### Docker (optional)

```bash
docker build -t geospatial-api .
docker volume create geospatial-data
docker run --rm -p 8000:8000 -v geospatial-data:/data geospatial-api
```

The container runs as a non-root user. Its named volume retains the database. The Docker and GitHub Actions definitions are provided; they require verification in their respective environments.

## API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/files/` | Upload and synchronously process one file |
| GET | `/api/files/{id}/` | Retrieve persisted file information |
| GET | `/api/files/{id}/measurements/` | Retrieve paginated features and measurements |
| GET | `/health/` | Check application and database access |

### Upload

Send `multipart/form-data` with a field named `file`. On Windows, use `curl.exe` in these examples.

```bash
curl -X POST http://127.0.0.1:8000/api/files/ \
  -F "file=@examples/survey.kml"

curl -X POST http://127.0.0.1:8000/api/files/ \
  -F "file=@survey.zip"
```

Accepted formats:

- `.kml`: KML 2.2 or an unnamespaced KML document. Coordinates are WGS84 longitude, latitude, and optional altitude. Placemark names, descriptions, IDs, and ExtendedData are retained. Nested Documents and Folders are traversed. NetworkLinks are never fetched.
- `.zip`: exactly one dataset, with matching `.shp`, `.shx`, `.dbf`, and `.prj`. A `.cpg` file is optional; UTF-8 is the default attribute encoding. Files may be in a subdirectory and extensions are case-insensitive. Multiple datasets in one archive are rejected rather than selecting one arbitrarily.

Example response: **201 Created**. IDs and timestamps below are illustrative.

```json
{
  "id": "0985cdeb-1845-4b2d-a6ca-786d90110cae",
  "filename": "survey.kml",
  "feature_count": 3,
  "crs": "EPSG:4326",
  "status": "COMPLETED",
  "created_at": "2026-10-07T00:00:00Z",
  "measured_count": 2,
  "skipped_count": 0
}
```

`COMPLETED` means parsing and processing finished. Individual features may be `SKIPPED`; inspect `skipped_count` and their warnings. Points are `NOT_REQUIRED` and count toward neither measured nor skipped features. Reuploading the same file creates a new independent record. Failed uploads produce no database records; this synchronous service does not expose pending jobs or FAILED records.

### File information

```bash
curl http://127.0.0.1:8000/api/files/0985cdeb-1845-4b2d-a6ca-786d90110cae/
```

Returns the same metadata schema as the upload response.

### Measurements

```bash
curl "http://127.0.0.1:8000/api/files/0985cdeb-1845-4b2d-a6ca-786d90110cae/measurements/?limit=100&offset=0"
```

`limit` defaults to 100 and accepts 1–1000; `offset` defaults to 0 and must be nonnegative. Results are ordered by their zero-based feature index. This shortened example illustrates a page containing the road; the numeric length is rounded here for readability:

```json
{
  "file_id": "0985cdeb-1845-4b2d-a6ca-786d90110cae",
  "total": 3,
  "offset": 1,
  "limit": 1,
  "features": [
    {
      "index": 1,
      "feature_id": "road-1",
      "geometry_type": "LineString",
      "geometry": {"type": "LineString", "coordinates": [[75, 18], [75.01, 18]]},
      "crs": "EPSG:4326",
      "properties": {"name": "Access road"},
      "measurement_status": "MEASURED",
      "area_m2": null,
      "length_m": 1058.6,
      "measurement_crs": "EPSG:32643",
      "warning": null,
      "source_geometry_xml": null
    }
  ]
}
```

Geometry uses a GeoJSON-like structure **in the declared source CRS**; projected-coordinate output is not RFC 7946 GeoJSON. Feature IDs come from KML IDs or Shapefile record IDs, with a KML index fallback. Source IDs need not be unique: `index` uniquely identifies a feature within the uploaded file.

| Geometry | Result |
| --- | --- |
| Polygon / MultiPolygon | `area_m2`, including subtraction of holes |
| LineString / MultiLineString | `length_m`, summed across parts |
| Point / MultiPoint | `NOT_REQUIRED`, measurement fields are null |
| GeometryCollection, unsupported KML types, null/empty/invalid geometry | `SKIPPED` with a warning |

Homogeneous KML MultiGeometry containers become multi-geometries. Mixed containers become GeometryCollections and are skipped. Unsupported/malformed KML geometries retain their source XML when available; `geometry` is null if it could not be represented. Invalid geometries are never silently repaired. Planar measurement ignores elevation; this is not terrain surface area or 3D path length. KML altitude values are preserved in geometry; PyShp's GeoJSON representation does not retain Shapefile Z/M values.

### Errors and limits

Client errors have a `detail` field. FastAPI parameter-validation errors use its standard structured `detail` array.

```json
{"detail": "Shapefile requires matching .shp, .shx, .dbf, and .prj files"}
```

| Code | Meaning |
| --- | --- |
| 400 | Empty upload or invalid filename |
| 404 | Unknown file UUID |
| 413 | Upload, request, decompression, feature, or coordinate limit exceeded |
| 415 | Unsupported extension |
| 422 | Invalid content, missing CRS/sidecars, unsafe archive/XML, or invalid API parameters |
| 500 | Unexpected server failure; details logged server-side |

Defaults in `app/config.py`: 10 MiB file; 11 MiB entire request including multipart overhead; 50 MiB total declared uncompressed ZIP data; 100 archive entries; 100:1 maximum per-entry compression ratio; 10,000 features; 200,000 coordinates per file. The conservative compression limit may reject unusually repetitive legitimate archives.

The entire body is bounded before multipart parsing, including chunked requests. Small requests remain in memory; larger requests spill to temporary disk. ZIP paths, duplicate names, encryption, symlinks, and decompression limits are checked. ZIP members are never extracted to disk. XML DTDs and entities are forbidden. MIME type is not trusted: readers validate content. Temporary request files are closed, and raw uploads are not retained.

## Architecture

```text
app/
  main.py          Routes, request-body bounds, lifecycle, error handling
  config.py        Resource limits and database configuration
  readers.py       Safe ZIP/Shapefile and KML parsing into common features
  measurements.py  Geometry validation, CRS selection and projected measurements
  schemas.py       Typed, documented response models
  storage.py       Transactional SQLite persistence and pagination
  errors.py        Expected input error type
tests/             End-to-end API, archive security and numerical correctness tests
examples/          Ready-to-upload KML and Shapefile samples
```

### File-processing flow

1. Bound the HTTP body and validate the filename, extension, and file size.
2. Parse XML or validate the archive and read its dataset and source CRS.
3. Extract IDs, geometry types, geometry, and attributes with feature/coordinate limits.
4. Validate and measure each feature independently. Record reasons for skipped features.
5. Commit file metadata and all feature results together in a SQLite transaction.
6. Return the file UUID and metadata; subsequent requests read stored results without recomputing.

Synchronous FastAPI routes execute blocking parsing and geometry processing in its worker thread pool. There are no long SQLite transactions during parsing. SQLite uses WAL mode, a busy timeout, parameterized SQL, and a fresh connection per operation. This implementation targets bounded uploads and a single-host deployment.

### Measurement calculation and CRS handling

The API never measures longitude/latitude degrees or assumes a source projected CRS uses metres.

1. Read the Shapefile's `.prj`; never guess a missing CRS. KML uses EPSG:4326.
2. Transform a valid geometry to WGS84 using pyproj and explicit longitude/X-first ordering (`always_xy=True`). Disallow ballpark datum transformations. If a required transformation is unavailable, skip the feature with a warning.
3. Check geographic bounds and the supported extent. The current local-survey policy supports features spanning at most 6 degrees longitude and 8 degrees latitude, entirely within 80°S to 84°N. Polar, continental-scale, and antimeridian-spanning features are explicitly skipped.
4. Select the UTM zone from the feature's geographic bounding-box midpoint and its hemisphere: EPSG:326xx north or EPSG:327xx south.
5. Transform to that metre-based CRS. Use Shapely's planar `.area` or `.length`; include the selected `measurement_crs` in the response.

Each feature can have a different measurement CRS. File and feature `crs` describe source coordinates, not measurement coordinates. UTM introduces projection distortion, especially near zone boundaries. These are local planar measurements, not a survey-grade accuracy guarantee. The independent geodesic-reference tests check representative small features, not every possible location. Segment vertices are transformed directly without geodesic densification.

References: [FastAPI file uploads](https://fastapi.tiangolo.com/tutorial/request-files/), [pyproj Transformer and axis order](https://pyproj4.github.io/pyproj/stable/api/transformer.html), [PyShp](https://pypi.org/project/pyshp/).

## Design decisions and alternatives

- **FastAPI:** typed request/response validation and automatic OpenAPI with little boilerplate. Django REST Framework would be a good choice if an admin interface or a larger Django domain model were required.
- **PyShp plus a focused KML reader:** avoids requiring a system GDAL installation or driver-dependent KML availability. The tradeoff is a deliberately documented KML subset. Fiona/GeoPandas would offer more formats and broader driver support.
- **Shapely and pyproj:** separate geometry operations from coordinate transformations. A fixed Web Mercator projection was rejected because its scale distortion makes measurements misleading.
- **Per-feature UTM:** straightforward, inspectable metre-based measurements for local surveys. Global support would need antimeridian handling and projection selection suited to large/polar features, or geodesic measurement as an explicitly separate mode.
- **SQLite:** persistent, transactional, easy to run, and appropriate for this bounded submission. PostgreSQL/PostGIS would support richer spatial queries and multi-instance deployments.
- **Synchronous processing:** a successful POST immediately makes results available. A task queue with explicit job states is more suitable for large files, retries, cancellation, or heavy concurrent workloads.
- **Explicit skips:** invalid or unsupported feature geometry does not fail otherwise readable files. Unsafe or structurally unreadable files fail before persistence. Raw uploaded archives are discarded to reduce retention and cleanup requirements.

## Tests

```bash
python -m pytest -q
python -m ruff check .
python -m ruff format --check .
```

Tests exercise real multipart uploads, generated Shapefiles, geographic and projected CRS inputs, a feet-based CRS, polygon holes, multi-geometries, southern-hemisphere projection, invalid geometry, unsupported types, pagination, persistence after restart, concurrent uploads, upload limits, malformed archives, ZIP traversal/decompression defenses, and XML entity rejection. Numerical tests compare known metre-based geometry dimensions and independent ellipsoidal geodesic references.

## Learning

The key engineering lessons illustrated by this implementation are:

- A geometry and its CRS must travel together. A numerically plausible area can still be wrong when calculated in degrees or feet.
- Coordinate-axis order and the units of the destination projection are part of the API's correctness contract.
- File parsing, geometry validity, and measurement support are distinct concerns; preserving per-feature failure reasons makes partial processing useful.
- A Shapefile is a dataset of related files, so archive validation and sidecar consistency matter as much as the `.shp` reader.
- Persistence, bounded requests, reproducible dependencies, and numerical tests are necessary alongside successful parsing.

## Future scope

- Add polar and antimeridian-aware projection strategies, geodesic measurement mode, densification, and documented accuracy targets.
- Add asynchronous jobs, cancellation, retries, per-user quotas, and stronger CPU/memory isolation for untrusted parsing.
- Add authentication, ownership checks, rate limits, retention/deletion policies, audit logging, and observability before exposing private datasets in a public service.
- Move to PostgreSQL/PostGIS and object storage when multiple instances or raw-file retention are needed.
- Expand KML/GX support, preserve Shapefile Z/M coordinates, and support more formats and multiple layers with explicit selection.
- Add database migrations, dependency/security scanning, larger real-world fixtures, and Linux/container verification.

This submission has no authentication and is intended for local evaluation. Before internet deployment, configure a reverse proxy with request/time limits and the application-level controls above. No deployment or hosted API is implied by publishing its source repository.

## GitHub submission

Public source repository: [https://github.com/project-code1cr/geospatial-measurement-api](https://github.com/project-code1cr/geospatial-measurement-api).

Clone it, then follow the setup instructions above:

```bash
git clone https://github.com/project-code1cr/geospatial-measurement-api.git
cd geospatial-measurement-api
```
