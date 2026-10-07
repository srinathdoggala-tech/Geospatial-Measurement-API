"""SQLite persistence; metadata and results commit together in one transaction."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.schemas import FeatureResult, FileInfo


class Repository:
    def __init__(self, database: Path):
        self.database = database

    @contextmanager
    def connection(self):
        connection = sqlite3.connect(self.database, timeout=30)
        try:
            connection.execute("PRAGMA foreign_keys=ON")
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY, metadata TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS features (
                    file_id TEXT NOT NULL REFERENCES files(id),
                    feature_index INTEGER NOT NULL,
                    result TEXT NOT NULL,
                    PRIMARY KEY (file_id, feature_index)
                );
            """)

    def save(self, info: FileInfo, features: list[FeatureResult]):
        with self.connection() as connection:
            connection.execute(
                "INSERT INTO files VALUES (?, ?)", (str(info.id), info.model_dump_json())
            )
            connection.executemany(
                "INSERT INTO features VALUES (?, ?, ?)",
                [(str(info.id), feature.index, feature.model_dump_json()) for feature in features],
            )

    def get(self, file_id: str):
        with self.connection() as connection:
            row = connection.execute(
                "SELECT metadata FROM files WHERE id = ?", (file_id,)
            ).fetchone()
        return FileInfo.model_validate_json(row[0]) if row else None

    def measurements(self, file_id: str, limit: int, offset: int):
        with self.connection() as connection:
            rows = connection.execute(
                "SELECT result FROM features WHERE file_id = ? "
                "ORDER BY feature_index LIMIT ? OFFSET ?",
                (file_id, limit, offset),
            ).fetchall()
        return [FeatureResult.model_validate_json(row[0]) for row in rows]
