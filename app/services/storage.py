import logging
import os
import shutil
from pathlib import Path
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)


class StorageService:
    """
    Local filesystem storage under:
      backend/storage/{videos,panoramas,thumbnails,exports}/

    NOTE: object names may include nested directories (e.g. "1/2/frame_000001.jpg").
    We guard against path traversal by resolving and enforcing the base directory prefix.
    """

    VALID_CATEGORIES = {"videos", "panoramas", "thumbnails", "exports", "avatars", "floorplans"}

    def __init__(self) -> None:
        # Make storage location independent of the current working directory.
        # Default `STORAGE_ROOT=storage` is resolved relative to the backend folder.
        backend_root = Path(__file__).resolve().parents[2]  # .../backend
        configured = Path(settings.STORAGE_ROOT)
        self.root = (configured if configured.is_absolute() else backend_root / configured).resolve()
        self._ensure_dirs()

    def _ensure_dirs(self) -> None:
        for cat in self.VALID_CATEGORIES:
            (self.root / cat).mkdir(parents=True, exist_ok=True)

    def _safe_path(self, category: str, filename: str) -> Path:
        if category not in self.VALID_CATEGORIES:
            raise ValueError("Invalid storage category")
        base = (self.root / category).resolve()
        target = (base / filename).resolve()
        # Ensure `target` is within `base`
        if os.path.commonpath([str(base), str(target)]) != str(base):
            raise ValueError("Invalid filename/path")
        return target

    def upload_file(self, category: str, filename: str, file_path: str, content_type: Optional[str] = None) -> None:
        """
        Save a local file into storage/category/filename.
        `content_type` is accepted for signature compatibility but ignored on filesystem storage.
        """
        try:
            dst = self._safe_path(category, filename)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file_path, dst)
        except Exception as e:
            logger.exception("Failed upload to %s/%s: %s", category, filename, e)
            raise

    def get_file_path(self, category: str, filename: str) -> str:
        """
        Return absolute path to a stored file.
        """
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
        """
        List stored files (relative paths) under category, optionally filtered by prefix.
        """
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


storage = StorageService()


