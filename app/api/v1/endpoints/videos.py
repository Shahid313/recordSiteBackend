import os
import tempfile
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile, status, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import require_project_edit, require_project_view
from app.core.config import settings
from app.db.session import SessionLocal
from app.db.session import get_db
from app.models.user import User
from app.models.video import Video, VideoStatus
from app.models.panorama import Panorama
from app.schemas.video import VideoUploadResponse, VideoStatusResponse
from app.schemas.panorama import PanoramaResponse
from app.services.storage import storage
from app.services.storage_cleanup import build_video_cleanup_plan, cleanup_storage_plan
from app.workers.video_tasks import process_video_task


router = APIRouter()

ALLOWED_EXTENSIONS = {".mp4", ".mov", ".avi"}


def _validate_extension(filename: str) -> None:
    ext = os.path.splitext(filename.lower())[1]
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported video format. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )


def _enqueue_video_processing(video_id: int) -> None:
    db = SessionLocal()
    try:
        process_video_task.delay(video_id)
    except Exception as exc:
        video = db.query(Video).filter(Video.id == video_id).first()
        if video:
            video.status = VideoStatus.FAILED.value
            video.error_message = f"Failed to enqueue processing task: {exc}"
            db.commit()
    finally:
        db.close()


@router.post("/upload/{project_id}", response_model=VideoUploadResponse, status_code=status.HTTP_201_CREATED)
def upload_video(
    project_id: int,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = require_project_edit(db, project_id, current_user)

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing filename")

    _validate_extension(file.filename)

    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp_path = tmp.name
        size = 0
        try:
            while True:
                chunk = file.file.read(1024 * 1024)  # 1MB
                if not chunk:
                    break
                size += len(chunk)
                if size > settings.MAX_VIDEO_SIZE_BYTES:
                    raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Video exceeds 2GB limit")
                tmp.write(chunk)
        finally:
            file.file.close()

    try:
        video = Video(
            project_id=project.id,
            filename=file.filename,
            storage_path="",
            file_size=size,
            status=VideoStatus.UPLOADED.value,
        )
        db.add(video)
        db.commit()
        db.refresh(video)

        object_key = f"{project.id}/{video.id}/{file.filename}"
        try:
            storage.upload_file(
                "videos",
                object_key,
                tmp_path,
                content_type=file.content_type or "application/octet-stream",
            )
            video.storage_path = object_key
            db.commit()
        except Exception as e:
            video.status = VideoStatus.FAILED.value
            video.error_message = str(e)
            db.commit()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to store video") from e

        background_tasks.add_task(_enqueue_video_processing, video.id)

        return VideoUploadResponse(video_id=video.id, status=video.status)
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


@router.get("/{video_id}", response_model=VideoStatusResponse)
def get_video_status(
    video_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    require_project_view(db, video.project_id, current_user)

    processed_frames = db.query(Panorama).filter(Panorama.video_id == video.id).count()
    total_frames: Optional[int] = int(video.duration) if video.duration else None
    progress_percent: Optional[float] = None

    if video.status == VideoStatus.EXTRACTING_FRAMES.value:
        if total_frames and total_frames > 0:
            progress_percent = min(50.0, (processed_frames / total_frames) * 50.0)
        else:
            progress_percent = None
    elif video.status == VideoStatus.PROCESSING_SFM.value:
        positioned = db.query(Panorama).filter(Panorama.video_id == video.id, Panorama.position_x.isnot(None)).count()
        if total_frames and total_frames > 0:
            progress_percent = 50.0 + min(40.0, (positioned / total_frames) * 40.0)
        else:
            progress_percent = 70.0 if positioned > 0 else 55.0
    elif video.status == VideoStatus.COMPLETED.value:
        progress_percent = 100.0
    elif video.status == VideoStatus.FAILED.value:
        progress_percent = None
    else:
        # UPLOADED or unknown
        progress_percent = 0.0

    return VideoStatusResponse(
        id=video.id,
        project_id=video.project_id,
        filename=video.filename,
        file_size=video.file_size,
        storage_path=video.storage_path,
        duration=video.duration,
        fps=video.fps,
        resolution=video.resolution,
        status=video.status,
        error_message=video.error_message,
        created_at=video.created_at,
        processed_at=video.processed_at,
        processed_frames=processed_frames,
        total_frames=total_frames,
        progress_percent=progress_percent,
    )


@router.get("/{video_id}/processing-status")
def get_processing_status(
    video_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Lightweight status endpoint for polling UI.
    """
    s = get_video_status(video_id=video_id, db=db, current_user=current_user)
    return {
        "video_id": s.id,
        "stage": s.status,
        "progress_percent": s.progress_percent,
        "processed_frames": s.processed_frames,
        "total_frames": s.total_frames,
        "error_message": s.error_message,
    }


@router.get("/{video_id}/panoramas", response_model=list[PanoramaResponse])
def list_video_panoramas(
    video_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Video not found")

    require_project_view(db, video.project_id, current_user)

    panoramas = (
        db.query(Panorama)
        .filter(Panorama.video_id == video.id)
        .order_by(Panorama.frame_number.asc())
        .all()
    )

    responses: list[PanoramaResponse] = []
    for pano in panoramas:
        file_url = f"{settings.API_V1_STR}/files/panoramas/{pano.storage_path}"
        thumb_url = f"{settings.API_V1_STR}/files/thumbnails/{pano.thumbnail_path}"
        responses.append(
            PanoramaResponse(
                id=pano.id,
                project_id=pano.project_id,
                video_id=pano.video_id,
                storage_path=pano.storage_path,
                thumbnail_path=pano.thumbnail_path,
                frame_number=pano.frame_number,
                timestamp=pano.timestamp,
                created_at=pano.created_at,
                file_url=file_url,
                thumbnail_url=thumb_url,
            )
        )
    return responses


@router.delete("/{video_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_video(
    video_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    video = db.query(Video).filter(Video.id == video_id).first()
    if not video:
        return None

    require_project_edit(db, video.project_id, current_user)

    cleanup_plan = build_video_cleanup_plan(db, video)
    db.delete(video)
    db.commit()
    background_tasks.add_task(cleanup_storage_plan, cleanup_plan)
    return None


