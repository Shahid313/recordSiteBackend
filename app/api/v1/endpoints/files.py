import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, RedirectResponse

from app.api.deps import get_current_user
from app.models.user import User
from app.services.storage import storage


router = APIRouter()


@router.get("/{category}/{filename:path}")
def get_file(
    category: str,
    filename: str,
    current_user: User = Depends(get_current_user),
):
    """
    Serve a stored file. Auth required.
    - On R2 backend with a configured public URL, redirect the browser there.
    - Otherwise stream the file from local disk (or downloaded temp copy).
    """
    _ = current_user

    # Prefer a direct public URL when available (R2 backend).
    try:
        public_url = storage.get_public_url(category, filename)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path")
    if public_url:
        return RedirectResponse(url=public_url, status_code=302)

    try:
        abs_path = storage.get_file_path(category, filename)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path")

    if not os.path.exists(abs_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    media_type, _enc = mimetypes.guess_type(abs_path)
    return FileResponse(path=abs_path, media_type=media_type or "application/octet-stream")


