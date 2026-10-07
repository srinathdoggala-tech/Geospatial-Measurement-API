from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database: Path = Path("data/geospatial.sqlite3")
    max_upload_bytes: int = 10 * 1024 * 1024
    max_request_bytes: int = 11 * 1024 * 1024
    max_uncompressed_bytes: int = 50 * 1024 * 1024
    max_archive_entries: int = 100
    max_compression_ratio: int = 100
    max_features: int = 10_000
    max_coordinates: int = 200_000
