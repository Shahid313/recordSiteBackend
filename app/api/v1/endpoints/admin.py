from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.api.deps import get_current_superuser
from app.db.session import get_db
from app.models.user import User
from app.models.project import Project
from app.models.video import Video
from app.models.panorama import Panorama
from app.core.security import get_password_hash
from app.core.config import settings
from app.schemas.panorama import PanoramaResponse


router = APIRouter()


class AdminUserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(default=None, max_length=100)
    password: Optional[str] = Field(default=None, min_length=6, max_length=72)
    is_active: Optional[bool] = None
    is_superuser: Optional[bool] = None


class AdminUserResponse(BaseModel):
    id: int
    email: str
    full_name: Optional[str]
    is_active: bool
    is_superuser: bool

    class Config:
        from_attributes = True


class PanoramaSetResponse(BaseModel):
    project_id: int
    project_name: str
    video_id: int
    video_filename: str
    panoramas_count: int
    sample_thumbnail_url: Optional[str] = None


@router.get("/users", response_model=list[AdminUserResponse])
def admin_list_users(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    return db.query(User).order_by(User.id.asc()).all()


@router.put("/users/{user_id}", response_model=AdminUserResponse)
def admin_update_user(
    user_id: int,
    payload: AdminUserUpdate,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    data = payload.model_dump(exclude_unset=True)
    if "password" in data and data["password"]:
        user.hashed_password = get_password_hash(data["password"])
        del data["password"]

    for k, v in data.items():
        setattr(user, k, v)

    db.commit()
    db.refresh(user)
    return user


@router.delete("/users/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_user(
    user_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_superuser),
):
    if admin.id == user_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Admin cannot delete self")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    db.delete(user)
    db.commit()
    return None


@router.get("/users/{user_id}/projects")
def admin_list_user_projects(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    projects = db.query(Project).filter(Project.owner_id == user_id).order_by(Project.updated_at.desc()).all()
    return projects


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def admin_delete_project(
    project_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    db.delete(project)
    db.commit()
    return None


@router.get("/users/{user_id}/panorama_sets", response_model=list[PanoramaSetResponse])
def admin_list_panorama_sets(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    # Group panoramas by (project, video)
    rows = (
        db.query(
            Project.id.label("project_id"),
            Project.name.label("project_name"),
            Video.id.label("video_id"),
            Video.filename.label("video_filename"),
            func.count(Panorama.id).label("panoramas_count"),
            func.min(Panorama.thumbnail_path).label("sample_thumb"),
        )
        .join(Video, Video.project_id == Project.id)
        .join(Panorama, Panorama.video_id == Video.id)
        .filter(Project.owner_id == user_id)
        .group_by(Project.id, Project.name, Video.id, Video.filename)
        .order_by(Project.id.asc(), Video.id.asc())
        .all()
    )

    base = ""
    out: list[PanoramaSetResponse] = []
    for r in rows:
        thumb_url = None
        if r.sample_thumb:
            thumb_url = base + f"{settings.API_V1_STR}/files/thumbnails/{r.sample_thumb}"
        out.append(
            PanoramaSetResponse(
                project_id=r.project_id,
                project_name=r.project_name,
                video_id=r.video_id,
                video_filename=r.video_filename,
                panoramas_count=int(r.panoramas_count),
                sample_thumbnail_url=thumb_url,
            )
        )
    return out


@router.get("/videos/{video_id}/panoramas", response_model=list[PanoramaResponse])
def admin_list_video_panoramas(
    video_id: int,
    request: Request,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_superuser),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    panoramas = (
        db.query(Panorama)
        .filter(Panorama.video_id == video_id)
        .order_by(Panorama.frame_number.asc())
        .all()
    )

    base = ""
    out: list[PanoramaResponse] = []
    for pano in panoramas:
        out.append(
            PanoramaResponse(
                id=pano.id,
                project_id=pano.project_id,
                video_id=pano.video_id,
                storage_path=pano.storage_path,
                thumbnail_path=pano.thumbnail_path,
                frame_number=pano.frame_number,
                timestamp=pano.timestamp,
                created_at=pano.created_at,
                file_url=base + f"{settings.API_V1_STR}/files/panoramas/{pano.storage_path}",
                thumbnail_url=base + f"{settings.API_V1_STR}/files/thumbnails/{pano.thumbnail_path}",
            )
        )
    return out


