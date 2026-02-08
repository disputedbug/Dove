from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional


@dataclass
class Job:
    id: str
    status: str
    created_at: float
    updated_at: float
    error: Optional[str]
    input_dir: str
    output_dir: str
    zip_path: Optional[str]
    options_json: str


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    error TEXT,
                    input_dir TEXT NOT NULL,
                    output_dir TEXT NOT NULL,
                    zip_path TEXT,
                    options_json TEXT NOT NULL
                )
                """
            )
            conn.commit()

    def create(self, job_id: str, input_dir: Path, output_dir: Path, options: dict[str, Any]) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO jobs (id, status, created_at, updated_at, error, input_dir, output_dir, zip_path, options_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (job_id, "queued", now, now, None, str(input_dir), str(output_dir), None, json.dumps(options)),
            )
            conn.commit()

    def update_status(self, job_id: str, status: str, error: Optional[str] = None, zip_path: Optional[Path] = None) -> None:
        now = time.time()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE jobs SET status = ?, updated_at = ?, error = ?, zip_path = ? WHERE id = ?
                """,
                (status, now, error, str(zip_path) if zip_path else None, job_id),
            )
            conn.commit()

    def get(self, job_id: str) -> Optional[Job]:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, status, created_at, updated_at, error, input_dir, output_dir, zip_path, options_json
                FROM jobs WHERE id = ?
                """,
                (job_id,),
            ).fetchone()
        if not row:
            return None
        return Job(
            id=row[0],
            status=row[1],
            created_at=row[2],
            updated_at=row[3],
            error=row[4],
            input_dir=row[5],
            output_dir=row[6],
            zip_path=row[7],
            options_json=row[8],
        )
