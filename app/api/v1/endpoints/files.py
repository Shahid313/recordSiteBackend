import mimetypes
import os

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse

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
    Serve a stored file from local filesystem.
    Auth required. (Ownership checks can be added later if desired.)
    """
    _ = current_user  # satisfy dependency; access check handled by get_current_user
    try:
        abs_path = storage.get_file_path(category, filename)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid file path")

    if not os.path.exists(abs_path):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    media_type, _enc = mimetypes.guess_type(abs_path)
    return FileResponse(path=abs_path, media_type=media_type or "application/octet-stream")


