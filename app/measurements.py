"""Planar measurements in a local WGS84 UTM CRS, with conservative extent limits."""

import math

from pyproj import CRS, Transformer
from pyproj.exceptions import ProjError
from shapely import get_coordinates
from shapely.errors import GEOSException
from shapely.geometry import shape
from shapely.ops import transform
from shapely.validation import explain_validity

from app.readers import Feature
from app.schemas import FeatureResult


def measure(feature: Feature, index: int, source_crs: CRS) -> FeatureResult:
    result = FeatureResult(
        index=index,
        feature_id=feature.feature_id,
        geometry_type=feature.geometry_type,
        geometry=feature.geometry,
        crs=source_crs.to_string(),
        properties=feature.properties,
        measurement_status="SKIPPED",
        warning=feature.warning,
        source_geometry_xml=feature.source_geometry_xml,
    )
    if feature.warning:
        return result
    if feature.geometry is None:
        result.warning = "Feature has no geometry"
        return result
    try:
        geometry = shape(feature.geometry)
        if geometry.is_empty:
            result.warning = "Empty geometry"
            return result
        if not all(math.isfinite(v) for point in get_coordinates(geometry) for v in point):
            result.warning = "Geometry contains non-finite coordinates"
            return result
        if not geometry.is_valid:
            result.warning = f"Invalid geometry: {explain_validity(geometry)}"
            return result
        to_wgs84 = Transformer.from_crs(
            source_crs, "EPSG:4326", always_xy=True, allow_ballpark=False
        )
        geographic = transform(
            lambda x, y, z=None: to_wgs84.transform(x, y, errcheck=True), geometry
        )
        minx, miny, maxx, maxy = geographic.bounds
        if not (-180 <= minx <= maxx <= 180 and -90 <= miny <= maxy <= 90):
            result.warning = "Coordinates are outside valid WGS84 longitude/latitude bounds"
            return result
        if geometry.geom_type in {"Point", "MultiPoint"}:
            result.measurement_status = "NOT_REQUIRED"
            return result
        if geometry.geom_type not in {"Polygon", "MultiPolygon", "LineString", "MultiLineString"}:
            result.warning = f"Measurement is not supported for {geometry.geom_type}"
            return result
        # Avoid silently producing misleading results across the date line or at continental scales.
        if maxx - minx > 6 or maxy - miny > 8 or miny < -80 or maxy > 84:
            result.warning = (
                "Outside local UTM measurement scope: maximum extent is 6 degrees "
                "longitude by 8 degrees latitude, within 80S to 84N; "
                "antimeridian-spanning features require a dedicated strategy"
            )
            return result
        lon, lat = (minx + maxx) / 2, (miny + maxy) / 2
        zone = min(60, max(1, math.floor((lon + 180) / 6) + 1))
        projected_crs = CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)
        projector = Transformer.from_crs("EPSG:4326", projected_crs, always_xy=True)
        projected = transform(
            lambda x, y, z=None: projector.transform(x, y, errcheck=True), geographic
        )
        if not projected.is_valid:
            result.warning = "Geometry became invalid after projection"
            return result
        value = projected.area if "Polygon" in geometry.geom_type else projected.length
        if not math.isfinite(value):
            result.warning = "Projection did not produce a finite measurement"
            return result
        result.measurement_status = "MEASURED"
        result.measurement_crs = projected_crs.to_string()
        if "Polygon" in geometry.geom_type:
            result.area_m2 = value
        else:
            result.length_m = value
    except (ValueError, TypeError, GEOSException, ProjError) as exc:
        result.warning = f"Geometry could not be measured: {exc}"
    return result
