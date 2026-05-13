import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


VALID_CATEGORIES = {"videos", "panoramas", "thumbnails", "exports", "avatars", "floorplans"}


def _normalize_key(category: str, filename: str) -> str:
    if category not in VALID_CATEGORIES:
        raise ValueError("Invalid storage category")
    if filename.startswith("/") or ".." in filename.split("/"):
        raise ValueError("Invalid filename/path")
    return f"{category}/{filename}"


class LocalStorageService:
    """
    Local filesystem storage under backend/storage/{category}/...
    Used for development. Not safe across multiple Railway containers.
    """

    # Kept for backward compatibility with any external imports.
    VALID_CATEGORIES = VALID_CATEGORIES

    def __init__(self) -> None:
        backend_root = Path(__file__).resolve().parents[2]  # .../backend
        configured = Path(settings.STORAGE_ROOT)
        self.root = (configured if configured.is_absolute() else backend_root / configured).resolve()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for cat in VALID_CATEGORIES:
            (self.root / cat).mkdir(parents=True, exist_ok=True)

    def _safe_path(self, category: str, filename: str) -> Path:
        _normalize_key(category, filename)  # validation only
        base = (self.root / category).resolve()
        target = (base / filename).resolve()
        if os.path.commonpath([str(base), str(target)]) != str(base):
            raise ValueError("Invalid filename/path")
        return target

    def upload_file(self, category: str, filename: str, file_path: str, content_type: Optional[str] = None) -> None:
        try:
            dst = self._safe_path(category, filename)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file_path, dst)
        except Exception as e:
            logger.exception("Failed upload to %s/%s: %s", category, filename, e)
            raise

    def get_file_path(self, category: str, filename: str) -> str:
        return str(self._safe_path(category, filename))

    def delete_file(self, category: str, filename: str) -> None:
        try:
            p = self._safe_path(category, filename)
            if p.exists():
                p.unlink()
        except Exception as e:
            logger.exception("Failed delete %s/%s: %s", category, filename, e)
            raise

    def list_files(self, category: str, prefix: str = "") -> list[str]:
        base = (self.root / category).resolve()
        if prefix:
            start = self._safe_path(category, prefix)
            if not start.exists():
                return []
            roots = [start]
        else:
            roots = [base]

        out: list[str] = []
        for r in roots:
            if r.is_file():
                out.append(str(r.relative_to(base)).replace("\\", "/"))
                continue
            for p in r.rglob("*"):
                if p.is_file():
                    out.append(str(p.relative_to(base)).replace("\\", "/"))
        out.sort()
        return out

    def get_public_url(self, category: str, filename: str) -> Optional[str]:
        return None


class R2StorageService:
    """
    Cloudflare R2 (S3-compatible) storage. Used in production.

    Object keys are prefixed with the category, e.g. "videos/1/2/myclip.mp4".
    `get_file_path()` downloads the object to a temp file and returns its path
    so existing code that uses cv2.VideoCapture / cv2.imread / FileResponse
    continues to work unchanged. Temp files live under a per-process temp dir
    that the OS will reclaim.
    """

    def __init__(self) -> None:
        import boto3
        from botocore.config import Config

        missing = [
            name for name, val in [
                ("R2_ACCESS_KEY_ID", settings.R2_ACCESS_KEY_ID),
                ("R2_SECRET_ACCESS_KEY", settings.R2_SECRET_ACCESS_KEY),
                ("R2_BUCKET_NAME", settings.R2_BUCKET_NAME),
                ("R2_ENDPOINT_URL", settings.R2_ENDPOINT_URL),
            ] if not val
        ]
        if missing:
            raise RuntimeError(
                "STORAGE_BACKEND=r2 requires these env vars: " + ", ".join(missing)
            )

        self.bucket = settings.R2_BUCKET_NAME
        self.public_base = settings.R2_PUBLIC_URL.rstrip("/") if settings.R2_PUBLIC_URL else ""
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name="auto",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )
        self._tmp_root = Path(tempfile.gettempdir()) / "constellation_r2"
        self._tmp_root.mkdir(parents=True, exist_ok=True)

    def upload_file(self, category: str, filename: str, file_path: str, content_type: Optional[str] = None) -> None:
        key = _normalize_key(category, filename)
        extra = {"ContentType": content_type} if content_type else {}
        try:
            self._client.upload_file(file_path, self.bucket, key, ExtraArgs=extra)
        except Exception as e:
            logger.exception("R2 upload failed for %s: %s", key, e)
            raise

    def get_file_path(self, category: str, filename: str) -> str:
        """
        Download the object to a temp file and return its local path.
        """
        key = _normalize_key(category, filename)
        local = self._tmp_root / category / filename
        local.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._client.download_file(self.bucket, key, str(local))
        except Exception as e:
            logger.exception("R2 download failed for %s: %s", key, e)
            raise
        return str(local)

    def delete_file(self, category: str, filename: str) -> None:
        key = _normalize_key(category, filename)
        try:
            self._client.delete_object(Bucket=self.bucket, Key=key)
        except Exception as e:
            logger.exception("R2 delete failed for %s: %s", key, e)
            raise

    def list_files(self, category: str, prefix: str = "") -> list[str]:
        if category not in VALID_CATEGORIES:
            raise ValueError("Invalid storage category")
        full_prefix = f"{category}/{prefix}" if prefix else f"{category}/"
        out: list[str] = []
        paginator = self._client.get_paginator("list_objects_v2")
        try:
            for page in paginator.paginate(Bucket=self.bucket, Prefix=full_prefix):
                for obj in page.get("Contents", []) or []:
                    key = obj["Key"]
                    if key.startswith(f"{category}/"):
                        out.append(key[len(category) + 1 :])
        except Exception as e:
            logger.exception("R2 list failed for %s: %s", full_prefix, e)
            raise
        out.sort()
        return out

    def get_public_url(self, category: str, filename: str) -> Optional[str]:
        if not self.public_base:
            return None
        key = _normalize_key(category, filename)
        return f"{self.public_base}/{key}"


def _build_storage():
    backend = settings.STORAGE_BACKEND
    if backend == "r2":
        return R2StorageService()
    if backend == "local":
        return LocalStorageService()
    raise RuntimeError(f"Unknown STORAGE_BACKEND: {backend!r} (expected 'local' or 'r2')")


storage = _build_storage()


