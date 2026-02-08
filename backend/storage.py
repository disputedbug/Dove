from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import BinaryIO


class StorageError(RuntimeError):
    pass


class StorageBackend:
    def save_upload(self, file_obj: BinaryIO, dest_path: Path) -> Path:
        raise NotImplementedError

    def open(self, path: Path, mode: str = "rb"):
        raise NotImplementedError

    def exists(self, path: Path) -> bool:
        raise NotImplementedError

    def mkdir(self, path: Path) -> None:
        raise NotImplementedError


class LocalStorage(StorageBackend):
    def save_upload(self, file_obj: BinaryIO, dest_path: Path) -> Path:
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        with open(dest_path, "wb") as f:
            shutil.copyfileobj(file_obj, f)
        return dest_path

    def open(self, path: Path, mode: str = "rb"):
        return open(path, mode)

    def exists(self, path: Path) -> bool:
        return path.exists()

    def mkdir(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)


class S3Storage(StorageBackend):
    def __init__(self, bucket: str):
        raise StorageError(
            "S3 backend not configured yet. Set STORAGE_BACKEND=local or implement S3Storage."
        )

    def save_upload(self, file_obj: BinaryIO, dest_path: Path) -> Path:
        raise StorageError("S3 backend not implemented")

    def open(self, path: Path, mode: str = "rb"):
        raise StorageError("S3 backend not implemented")

    def exists(self, path: Path) -> bool:
        raise StorageError("S3 backend not implemented")

    def mkdir(self, path: Path) -> None:
        raise StorageError("S3 backend not implemented")


def get_storage_backend() -> StorageBackend:
    backend = os.environ.get("STORAGE_BACKEND", "local").lower()
    if backend == "local":
        return LocalStorage()
    if backend == "s3":
        bucket = os.environ.get("S3_BUCKET")
        if not bucket:
            raise StorageError("S3_BUCKET is required when STORAGE_BACKEND=s3")
        return S3Storage(bucket=bucket)
    raise StorageError(f"Unsupported STORAGE_BACKEND: {backend}")
