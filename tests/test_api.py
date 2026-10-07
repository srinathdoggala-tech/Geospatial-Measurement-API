from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4
from zipfile import ZIP_DEFLATED

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.conftest import kml, make_zip, shape_zip

POINT = "<Point><coordinates>75,18,10</coordinates></Point>"
LINE = "<LineString><coordinates>75,18 75.01,18</coordinates></LineString>"
POLYGON = (
    "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
    "75,18 75.001,18 75.001,18.001 75,18.001 75,18"
    "</coordinates></LinearRing></outerBoundaryIs></Polygon>"
)


def upload(client, data, filename="test.kml", expected=201):
    response = client.post("/api/files/", files={"file": (filename, data)})
    assert response.status_code == expected, response.text
    return response.json()


def results(client, file_id, query=""):
    response = client.get(f"/api/files/{file_id}/measurements/{query}")
    assert response.status_code == 200
    return response.json()


def test_kml_upload_metadata_geometry_attributes_and_measurements(client):
    document = kml(
        POLYGON,
        '<name>Plot</name><ExtendedData><Data name="owner">'
        "<value>Ada</value></Data></ExtendedData>",
        "plot-1",
    )
    info = upload(client, document)
    assert info["status"] == "COMPLETED"
    assert info["feature_count"] == info["measured_count"] == 1
    assert info["crs"] == "EPSG:4326"
    assert client.get(f"/api/files/{info['id']}/").json() == info
    feature = results(client, info["id"])["features"][0]
    assert feature["feature_id"] == "plot-1"
    assert feature["index"] == 0
    assert feature["geometry_type"] == "Polygon"
    assert feature["geometry"]["coordinates"][0][0] == [75, 18]
    assert feature["properties"]["extended_data"]["owner"] == "Ada"
    assert 11500 < feature["area_m2"] < 12000
    assert feature["length_m"] is None
    assert feature["measurement_crs"] == "EPSG:32643"


def test_point_no_measurement_and_altitude_preserved(client):
    info = upload(client, kml(POINT))
    feature = results(client, info["id"])["features"][0]
    assert feature["measurement_status"] == "NOT_REQUIRED"
    assert feature["geometry"]["coordinates"] == [75, 18, 10]
    assert feature["area_m2"] is feature["length_m"] is None


def test_geographic_line_metres(client):
    info = upload(client, kml(LINE))
    feature = results(client, info["id"])["features"][0]
    assert 1050 < feature["length_m"] < 1070


@pytest.mark.parametrize(
    "kind,crs",
    [
        ("polygon", "EPSG:32643"),
        ("line", "EPSG:4326"),
        ("point", "EPSG:4326"),
        ("null", "EPSG:4326"),
    ],
)
def test_shapefile(client, kind, crs):
    info = upload(client, shape_zip(kind, crs), "survey.zip")
    feature = results(client, info["id"])["features"][0]
    assert info["crs"] == crs
    assert feature["properties"]["name"] == "survey"
    if kind == "polygon":
        assert feature["area_m2"] == pytest.approx(10000, rel=1e-6)
    elif kind == "line":
        assert 1050 < feature["length_m"] < 1070
    elif kind == "null":
        assert feature["measurement_status"] == "SKIPPED"
    else:
        assert feature["measurement_status"] == "NOT_REQUIRED"


@pytest.mark.parametrize(
    "geometry",
    [
        "<Model/>",
        f"<MultiGeometry>{POINT}{LINE}</MultiGeometry>",
        "<LineString><coordinates>0,0 20,0</coordinates></LineString>",
        "<LineString><coordinates>179,0 -179,0</coordinates></LineString>",
        "<Point><coordinates>nan,1</coordinates></Point>",
        "<Point><coordinates>200,18</coordinates></Point>",
        "<Point><coordinates>75,18 76,18</coordinates></Point>",
        "<Polygon/>",
        "",
    ],
)
def test_unsupported_or_invalid_geometry_is_skipped(client, geometry):
    info = upload(client, kml(geometry))
    feature = results(client, info["id"])["features"][0]
    assert feature["measurement_status"] == "SKIPPED"
    assert feature["warning"]
    assert info["skipped_count"] == 1


def test_homogeneous_multigeometry(client):
    info = upload(client, kml(f"<MultiGeometry>{LINE}{LINE}</MultiGeometry>"))
    feature = results(client, info["id"])["features"][0]
    assert feature["geometry_type"] == "MultiLineString"
    assert 2100 < feature["length_m"] < 2140


@pytest.mark.parametrize(
    "data,name,status",
    [
        (b"", "test.kml", 400),
        (b"hello", "test.txt", 415),
        (b"<kml>", "test.kml", 422),
        (b"<hello/>", "test.kml", 422),
        (b"<kml/>", "test.kml", 422),
        (b"not a zip", "test.zip", 422),
        (
            b'<!DOCTYPE kml [<!ENTITY x SYSTEM "file:///etc/passwd">]><kml>&x;</kml>',
            "test.kml",
            422,
        ),
    ],
)
def test_invalid_files(client, data, name, status):
    assert "detail" in upload(client, data, name, status)


@pytest.mark.parametrize("extension", ["prj", "shx", "dbf"])
def test_missing_shapefile_sidecar(client, extension):
    upload(client, shape_zip(missing=extension), "survey.zip", 422)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "folder\\escape"])
def test_unsafe_zip_paths(client, name):
    data = shape_zip(extra={name: b"bad"})
    # Windows' ZIP writer normalizes separators; restore the hostile member spelling.
    if "\\" in name:
        data = data.replace(name.replace("\\", "/").encode(), name.encode())
    upload(client, data, "survey.zip", 422)


def test_zip_bomb(client):
    data = make_zip({"bomb.txt": b"a" * 200000}, compression=ZIP_DEFLATED)
    upload(client, data, "bomb.zip", 413)


def test_multiple_shapefiles_and_bad_crs(client):
    upload(client, shape_zip(extra={"other.shp": b""}), "test.zip", 422)
    upload(client, shape_zip(extra={"survey.prj": b"invalid"}), "test.zip", 422)


def test_pagination_and_duplicate_source_ids(client):
    body = (
        kml(POINT)
        .decode()
        .replace("</Document>", f'<Placemark id="first">{LINE}</Placemark></Document>')
    )
    info = upload(client, body.encode())
    page = results(client, info["id"], "?limit=1&offset=1")
    assert page["total"] == 2
    assert len(page["features"]) == 1
    assert page["features"][0]["index"] == 1
    assert results(client, info["id"], "?offset=50")["features"] == []
    assert client.get(f"/api/files/{info['id']}/measurements/?limit=1001").status_code == 422


def test_not_found_and_invalid_uuid(client):
    assert client.get(f"/api/files/{uuid4()}/").status_code == 404
    assert client.get(f"/api/files/{uuid4()}/measurements/").status_code == 404
    assert client.get("/api/files/not-a-uuid/").status_code == 422
    assert client.post("/api/files/").status_code == 422


def test_persistence_after_restart(settings):
    with TestClient(create_app(settings)) as first:
        info = upload(first, kml(LINE))
    with TestClient(create_app(settings)) as second:
        assert second.get(f"/api/files/{info['id']}/").status_code == 200
        assert results(second, info["id"])["features"][0]["length_m"] > 1000


@pytest.mark.parametrize(
    "overrides,data",
    [
        ({"max_upload_bytes": 10}, kml(POINT)),
        ({"max_request_bytes": 10}, kml(POINT)),
        ({"max_features": 0}, kml(POINT)),
        ({"max_coordinates": 1}, kml(LINE)),
    ],
)
def test_resource_limits(settings, overrides, data):
    with TestClient(create_app(replace(settings, **overrides))) as instance:
        upload(instance, data, expected=413)


def test_chunked_request_limit(settings):
    with TestClient(create_app(replace(settings, max_request_bytes=10))) as instance:
        response = instance.post("/api/files/", content=iter([b"123456", b"123456"]))
        assert response.status_code == 413


def test_concurrent_uploads(client):
    with ThreadPoolExecutor(max_workers=4) as executor:
        infos = list(executor.map(lambda _: upload(client, kml(POINT)), range(8)))
    assert len({info["id"] for info in infos}) == 8


def test_health_and_openapi(client):
    assert client.get("/health/").json() == {"status": "ok"}
    assert "/api/files/" in client.get("/openapi.json").json()["paths"]
