"""Bounded input readers. ZIP members are read in memory, never extracted."""

import io
import json
import math
import stat
import struct
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any
from xml.etree.ElementTree import ParseError, tostring

import shapefile
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException
from pyproj import CRS
from pyproj.exceptions import CRSError

from app.config import Settings
from app.errors import ProcessingError

KML_NS = "http://www.opengis.net/kml/2.2"
GEOMETRY_NAMES = {"Point", "LineString", "Polygon", "MultiGeometry", "Model", "Track", "MultiTrack"}


@dataclass
class Feature:
    feature_id: str
    geometry: dict[str, Any] | None
    geometry_type: str
    properties: dict[str, Any]
    warning: str | None = None
    source_geometry_xml: str | None = None


def local_name(element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def child(element, name):
    return next((item for item in element if local_name(item) == name), None)


def child_text(element, name):
    found = child(element, name)
    return found.text if found is not None else None


def coordinates(element):
    text = child_text(element, "coordinates") or ""
    result = []
    for token in text.split():
        values = [float(value) for value in token.split(",")]
        if len(values) not in (2, 3) or not all(math.isfinite(v) for v in values):
            raise ValueError(
                "Coordinates must contain finite longitude, latitude, and optional altitude"
            )
        if not -180 <= values[0] <= 180 or not -90 <= values[1] <= 90:
            raise ValueError("KML longitude or latitude is outside its valid range")
        result.append(values)
    if not result:
        raise ValueError("Geometry has no coordinates")
    if len({len(point) for point in result}) != 1:
        raise ValueError("Coordinate dimensions must be consistent")
    return result


def kml_geometry(element):
    kind = local_name(element)
    if kind == "Point":
        coords = coordinates(element)
        if len(coords) != 1:
            raise ValueError("Point requires exactly one coordinate")
        return {"type": kind, "coordinates": coords[0]}
    if kind == "LineString":
        coords = coordinates(element)
        if len(coords) < 2:
            raise ValueError("LineString requires at least two coordinates")
        return {"type": kind, "coordinates": coords}
    if kind == "Polygon":
        outer = child(element, "outerBoundaryIs")
        if outer is None:
            raise ValueError("Polygon requires an outer boundary")
        boundaries = [outer] + [e for e in element if local_name(e) == "innerBoundaryIs"]
        rings = []
        for boundary in boundaries:
            ring = child(boundary, "LinearRing")
            if ring is None:
                raise ValueError("Polygon boundary requires a LinearRing")
            coords = coordinates(ring)
            if len(coords) < 4 or coords[0] != coords[-1]:
                raise ValueError("Polygon rings must be closed with at least four coordinates")
            rings.append(coords)
        return {"type": kind, "coordinates": rings}
    if kind == "MultiGeometry":
        geometries = [kml_geometry(e) for e in element if local_name(e) in GEOMETRY_NAMES]
        if not geometries:
            raise ValueError("MultiGeometry contains no supported geometry")
        kinds = {g["type"] for g in geometries}
        if len(kinds) == 1 and next(iter(kinds)) in {"Point", "LineString", "Polygon"}:
            return {
                "type": "Multi" + geometries[0]["type"],
                "coordinates": [g["coordinates"] for g in geometries],
            }
        return {"type": "GeometryCollection", "geometries": geometries}
    raise ValueError(f"Unsupported KML geometry: {kind}")


def read_kml(data: bytes, settings: Settings):
    try:
        root = SafeET.fromstring(data, forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise ProcessingError("Invalid or unsafe KML XML") from exc
    if root.tag not in {"kml", f"{{{KML_NS}}}kml"}:
        raise ProcessingError("Expected a KML document with a kml root element")
    pending = [(root, 0)]
    while pending:
        element, depth = pending.pop()
        if depth > 128:
            raise ProcessingError("KML nesting depth exceeds 128 levels", 413)
        pending.extend((element, depth + 1) for element in element)
    features = []
    coordinate_count = 0
    # Count before creating coordinate arrays, including unsupported geometry coordinates.
    for item in root.iter():
        if local_name(item) in {"coordinates", "coord"}:
            coordinate_count += len((item.text or "").split())
            if coordinate_count > settings.max_coordinates:
                raise ProcessingError("Too many coordinates in the file", 413)
    for placemark in root.iter():
        if local_name(placemark) != "Placemark":
            continue
        if len(features) >= settings.max_features:
            raise ProcessingError("Too many features in the file", 413)
        properties = {
            name: child_text(placemark, name)
            for name in ("name", "description")
            if child_text(placemark, name) is not None
        }
        extended = child(placemark, "ExtendedData")
        if extended is not None:
            properties["extended_data"] = {
                e.attrib["name"]: child_text(e, "value") if local_name(e) == "Data" else e.text
                for e in extended.iter()
                if local_name(e) in {"Data", "SimpleData"} and "name" in e.attrib
            }
        elements = [e for e in placemark if local_name(e) in GEOMETRY_NAMES]
        geometry, warning, source_xml = None, None, None
        kind = local_name(elements[0]) if elements else "Null"
        try:
            if len(elements) != 1:
                raise ValueError("Placemark must have one geometry or a MultiGeometry container")
            geometry = kml_geometry(elements[0])
            kind = geometry["type"]
        except (ValueError, TypeError, RecursionError) as exc:
            warning = str(exc)
            source_xml = "".join(tostring(e, encoding="unicode") for e in elements)
        features.append(
            Feature(
                placemark.attrib.get("id", str(len(features))),
                geometry,
                kind,
                properties,
                warning,
                source_xml,
            )
        )
    return CRS.from_epsg(4326), features


def read_shapefile(data: bytes, settings: Settings):
    try:
        return _read_shapefile(data, settings)
    except ProcessingError:
        raise
    except (
        zipfile.BadZipFile,
        RuntimeError,
        NotImplementedError,
        OSError,
        ValueError,
        UnicodeError,
        shapefile.ShapefileException,
        EOFError,
        IndexError,
        struct.error,
        OverflowError,
    ) as exc:
        raise ProcessingError("Invalid Shapefile archive or unreadable attribute encoding") from exc


def _read_shapefile(data: bytes, settings: Settings):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > settings.max_archive_entries:
            raise ProcessingError("Too many ZIP entries", 413)
        total = 0
        files = {}
        for member in members:
            # ZipInfo normalizes Windows separators and truncates NULs in filename.
            # Inspect the original spelling as well as the normalized path.
            path = PurePosixPath(member.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in member.orig_filename
                or "\u0000" in member.orig_filename
                or ":" in member.filename
                or stat.S_ISLNK(member.external_attr >> 16)
            ):
                raise ProcessingError("Unsafe path or symbolic link in ZIP archive")
            if member.flag_bits & 1:
                raise ProcessingError("Encrypted ZIP archives are not supported")
            total += member.file_size
            if (
                total > settings.max_uncompressed_bytes
                or member.file_size > max(member.compress_size, 1) * settings.max_compression_ratio
            ):
                raise ProcessingError("ZIP decompression limit exceeded", 413)
            if member.is_dir():
                continue
            key = member.filename.lower()
            if key in files:
                raise ProcessingError("Duplicate ZIP member names")
            files[key] = member
        shapes = [name for name in files if name.endswith(".shp")]
        if len(shapes) != 1:
            raise ProcessingError("ZIP must contain exactly one Shapefile dataset")
        base = shapes[0][:-4]
        if any(base + ext not in files for ext in (".shx", ".dbf", ".prj")):
            raise ProcessingError("Shapefile requires matching .shp, .shx, .dbf, and .prj files")

        def read(ext):
            return archive.read(files[base + ext])

        try:
            crs = CRS.from_wkt(read(".prj").decode("utf-8-sig"))
        except (CRSError, UnicodeError) as exc:
            raise ProcessingError("Invalid CRS in .prj file") from exc
        if not (crs.is_geographic or crs.is_projected):
            raise ProcessingError("Only geographic or projected source CRSs are supported")
        encoding = "utf-8"
        if base + ".cpg" in files:
            encoding = read(".cpg").decode("ascii").strip()
            if encoding == "65001":
                encoding = "utf-8"
            elif encoding.isdigit():
                encoding = "cp" + encoding
        try:
            "".encode(encoding)
        except LookupError as exc:
            raise ProcessingError("Unsupported encoding in .cpg file") from exc
        with shapefile.Reader(
            shp=io.BytesIO(read(".shp")),
            shx=io.BytesIO(read(".shx")),
            dbf=io.BytesIO(read(".dbf")),
            encoding=encoding,
        ) as reader:
            if reader.numRecords > settings.max_features:
                raise ProcessingError("Too many features in the file", 413)
            features = []
            coordinate_count = 0
            for index, record in enumerate(reader.iterShapeRecords()):
                if index >= settings.max_features:
                    raise ProcessingError("Too many features in the file", 413)
                coordinate_count += len(record.shape.points)
                if coordinate_count > settings.max_coordinates:
                    raise ProcessingError("Too many coordinates in the file", 413)
                geometry, warning = None, None
                kind = record.shape.shapeTypeName
                try:
                    if record.shape.shapeType != shapefile.NULL:
                        geometry = record.shape.__geo_interface__
                        # Reject non-finite values before they can reach JSON persistence.
                        json.dumps(geometry, allow_nan=False)
                        kind = geometry["type"]
                except (ValueError, TypeError, shapefile.ShapefileException) as exc:
                    geometry, warning = None, f"Unreadable or unsupported geometry: {exc}"
                properties = json.loads(
                    json.dumps(record.record.as_dict(), default=str, allow_nan=False)
                )
                features.append(
                    Feature(str(record.record.oid), geometry, kind, properties, warning)
                )
        return crs, features


def read_file(data: bytes, extension: str, settings: Settings):
    if not data:
        raise ProcessingError("Uploaded file is empty", 400)
    crs, features = (
        read_kml(data, settings) if extension == ".kml" else read_shapefile(data, settings)
    )
    if not features:
        raise ProcessingError("The file contains no features")
    return crs, features
