from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel


class FileInfo(BaseModel):
    id: UUID
    filename: str
    feature_count: int
    crs: str
    status: Literal["COMPLETED"] = "COMPLETED"
    created_at: datetime
    measured_count: int
    skipped_count: int


class FeatureResult(BaseModel):
    index: int
    feature_id: str
    geometry_type: str
    geometry: dict[str, Any] | None
    crs: str
    properties: dict[str, Any]
    measurement_status: Literal["MEASURED", "NOT_REQUIRED", "SKIPPED"]
    area_m2: float | None = None
    length_m: float | None = None
    measurement_crs: str | None = None
    warning: str | None = None
    source_geometry_xml: str | None = None


class MeasurementsPage(BaseModel):
    file_id: UUID
    total: int
    offset: int
    limit: int
    features: list[FeatureResult]
