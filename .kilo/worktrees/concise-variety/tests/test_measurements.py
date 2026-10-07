import pytest
from pyproj import CRS, Geod
from shapely.geometry import LineString, MultiPolygon, Polygon, mapping

from app.measurements import measure
from app.readers import Feature


def calculate(geometry, crs=4326):
    return measure(Feature("1", mapping(geometry), geometry.geom_type, {}), 0, CRS.from_epsg(crs))


def test_area_with_hole_and_multipolygon():
    polygon = Polygon(
        [(500000, 2000000), (500100, 2000000), (500100, 2000100), (500000, 2000100)],
        [[(500010, 2000010), (500010, 2000030), (500030, 2000030), (500030, 2000010)]],
    )
    assert calculate(polygon, 32643).area_m2 == pytest.approx(9600, abs=0.001)
    other = Polygon([(500200, 2000000), (500300, 2000000), (500300, 2000100), (500200, 2000100)])
    assert calculate(MultiPolygon([polygon, other]), 32643).area_m2 == pytest.approx(
        19600, abs=0.001
    )


def test_geographic_distance_against_independent_geodesic_reference():
    line = LineString([(75, 18), (75.01, 18)])
    geodesic = Geod(ellps="WGS84").geometry_length(line)
    assert calculate(line).length_m == pytest.approx(geodesic, rel=0.001)


def test_geographic_area_against_independent_geodesic_reference():
    polygon = Polygon([(75, 18), (75.001, 18), (75.001, 18.001), (75, 18.001)])
    area, _ = Geod(ellps="WGS84").geometry_area_perimeter(polygon)
    assert calculate(polygon).area_m2 == pytest.approx(abs(area), rel=0.001)


def test_southern_hemisphere():
    result = calculate(LineString([(151, -33), (151.01, -33)]))
    assert result.measurement_crs == "EPSG:32756"
    assert result.length_m > 900


def test_non_metric_source_crs_is_converted():
    # EPSG:2263 uses US survey feet. A 100-foot line is roughly 30.48 metres.
    result = calculate(LineString([(1000000, 200000), (1000100, 200000)]), 2263)
    assert result.length_m == pytest.approx(30.48, rel=0.002)


def test_invalid_polygon_is_not_silently_repaired():
    result = calculate(Polygon([(75, 18), (75.01, 18.01), (75.01, 18), (75, 18.01)]))
    assert result.measurement_status == "SKIPPED"
    assert "Invalid geometry" in result.warning


def test_polar_feature_is_explicitly_skipped():
    result = calculate(LineString([(0, 85), (0.01, 85)]))
    assert result.measurement_status == "SKIPPED"
    assert "scope" in result.warning
