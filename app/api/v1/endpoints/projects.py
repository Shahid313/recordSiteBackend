from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import get_project_role, require_project_edit, require_project_view
from app.db.session import get_db
from app.models.project_collaborator import ProjectCollaborator
from app.models.user import User
from app.models.project import Project
from app.models.video import Video
from app.schemas.project import ProjectCreate, ProjectResponse, ProjectUpdate
from app.schemas.video import VideoStatusResponse
from app.services.storage_cleanup import build_project_cleanup_plan, cleanup_storage_plan


router = APIRouter()


@router.post("/", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    project_in: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = Project(
        name=project_in.name,
        description=project_in.description,
        owner_id=current_user.id,
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


@router.get("/", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    projects = (
        db.query(Project)
        .outerjoin(ProjectCollaborator, ProjectCollaborator.project_id == Project.id)
        .filter((Project.owner_id == current_user.id) | (ProjectCollaborator.user_id == current_user.id))
        .distinct()
        .order_by(Project.updated_at.desc())
        .all()
    )
    for project in projects:
        project.access_role = get_project_role(db, project, current_user)
    return projects


@router.get("/{project_id}", response_model=ProjectResponse)
def get_project(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project, role = require_project_view(db, project_id, current_user)
    project.access_role = role
    return project


@router.put("/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: int,
    project_in: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = require_project_edit(db, project_id, current_user)

    data = project_in.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(project, k, v)
    db.commit()
    db.refresh(project)
    return project


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        return None
    if project.owner_id != current_user.id and not getattr(current_user, "is_superuser", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project owner access required")

    cleanup_plan = build_project_cleanup_plan(db, project.id)
    db.delete(project)
    db.commit()
    background_tasks.add_task(cleanup_storage_plan, cleanup_plan)
    return None


@router.get("/{project_id}/videos", response_model=list[VideoStatusResponse])
def list_project_videos(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project, _role = require_project_view(db, project_id, current_user)

    videos = (
        db.query(Video)
        .filter(Video.project_id == project.id)
        .order_by(Video.created_at.desc())
        .all()
    )

    # For list views, reuse VideoStatusResponse but compute progress server-side.
    responses: list[VideoStatusResponse] = []
    from app.models.panorama import Panorama  # local import to avoid circulars

    for video in videos:
        processed_frames = db.query(Panorama).filter(Panorama.video_id == video.id).count()
        total_frames = int(video.duration) if video.duration else None
        progress_percent = None
        if total_frames and total_frames > 0:
            progress_percent = min(100.0, (processed_frames / total_frames) * 100.0)
        responses.append(
            VideoStatusResponse(
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
        )
    return responses


