import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.security import verify_password, get_password_hash
from app.db.session import get_db
from app.models.user import User
from app.models.user_profile import UserProfile
from app.schemas.user import UserResponse
from app.services.storage import storage


router = APIRouter()

MAX_AVATAR_BYTES = 5 * 1024 * 1024  # 5MB
ALLOWED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=72)
    new_password: str = Field(..., min_length=6, max_length=72)


@router.post("/me/avatar", response_model=UserResponse)
def upload_avatar(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

    ext = os.path.splitext(file.filename.lower())[1]
    if ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unsupported image type")

    # Read (small) file into memory to enforce size.
    content = file.file.read(MAX_AVATAR_BYTES + 1)
    file.file.close()
    if len(content) > MAX_AVATAR_BYTES:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Avatar too large (max 5MB)")

    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    avatar_rel = f"{current_user.id}/avatar_{ts}{ext}"

    # Write the upload to a real temp file, then hand it to the storage service.
    # (Don't ask storage for a path to write to — that doesn't work for object stores.)
    import tempfile
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(content)
        storage.upload_file("avatars", avatar_rel, tmp_path, content_type=file.content_type)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass

    profile = db.query(UserProfile).filter(UserProfile.user_id == current_user.id).first()
    if not profile:
        profile = UserProfile(user_id=current_user.id, avatar_path=avatar_rel)
        db.add(profile)
        db.commit()
    else:
        old = profile.avatar_path
        profile.avatar_path = avatar_rel
        db.commit()
        if old and old != avatar_rel:
            try:
                storage.delete_file("avatars", old)
            except Exception:
                pass

    # Return /auth/me-style payload; frontend refreshes via /auth/me anyway,
    # but this keeps response consistent.
    return {
        "id": current_user.id,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "is_active": current_user.is_active,
        "is_superuser": current_user.is_superuser,
        "avatar_url": None,  # computed by /auth/me; optional here
        "created_at": current_user.created_at,
    }


@router.post("/me/change-password")
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")

    current_user.hashed_password = get_password_hash(payload.new_password)
    db.commit()
    return {"message": "Password updated"}


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def delete_my_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Remove avatar files
    prefix = f"{current_user.id}/"
    try:
        for f in storage.list_files("avatars", prefix=prefix):
            try:
                storage.delete_file("avatars", f)
            except Exception:
                pass
    except Exception:
        pass

    db.delete(current_user)
    db.commit()
    return None


