from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import EDITOR_ROLE, VALID_COLLABORATOR_ROLES, require_project_edit, require_project_owner, require_project_view
from app.db.session import get_db
from app.models.panorama import Panorama
from app.models.panorama_comment import PanoramaComment
from app.models.project_collaborator import ProjectCollaborator
from app.models.user import User

router = APIRouter()


class CollaboratorCreate(BaseModel):
    email: str
    role: str = Field(default="viewer")

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        role = v.strip().lower()
        if role not in VALID_COLLABORATOR_ROLES:
            raise ValueError("Role must be viewer or editor")
        return role


class CollaboratorUpdate(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        role = v.strip().lower()
        if role not in VALID_COLLABORATOR_ROLES:
            raise ValueError("Role must be viewer or editor")
        return role


class CommentCreate(BaseModel):
    panorama_id: int
    body: str = Field(min_length=1, max_length=2000)
    yaw: float
    pitch: float = 0.0

    @field_validator("yaw")
    @classmethod
    def normalize_yaw(cls, v: float) -> float:
        return v % 360.0

    @field_validator("pitch")
    @classmethod
    def clamp_pitch(cls, v: float) -> float:
        return max(-90.0, min(90.0, v))


class CommentResolve(BaseModel):
    resolved: bool


def _user_label(user: Optional[User]) -> str:
    if not user:
        return "Deleted user"
    return user.full_name or user.email


def _comment_response(comment: PanoramaComment) -> dict:
    return {
        "id": comment.id,
        "project_id": comment.project_id,
        "panorama_id": comment.panorama_id,
        "body": comment.body,
        "yaw": float(comment.yaw),
        "pitch": float(comment.pitch),
        "resolved": bool(comment.resolved),
        "author": {
            "id": comment.author.id if comment.author else None,
            "name": _user_label(comment.author),
            "email": comment.author.email if comment.author else None,
        },
        "resolved_by": {
            "id": comment.resolved_by.id if comment.resolved_by else None,
            "name": _user_label(comment.resolved_by),
            "email": comment.resolved_by.email if comment.resolved_by else None,
        } if comment.resolved_by else None,
        "resolved_at": comment.resolved_at.isoformat() if comment.resolved_at else None,
        "created_at": comment.created_at.isoformat() if comment.created_at else None,
        "updated_at": comment.updated_at.isoformat() if comment.updated_at else None,
    }


def _collaborator_response(collab: ProjectCollaborator) -> dict:
    return {
        "id": collab.id,
        "project_id": collab.project_id,
        "role": collab.role,
        "created_at": collab.created_at.isoformat() if collab.created_at else None,
        "user": {
            "id": collab.user.id,
            "email": collab.user.email,
            "name": _user_label(collab.user),
        },
    }


@router.get("/projects/{project_id}/collaborators")
def list_collaborators(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = require_project_owner(db, project_id, current_user)
    rows = (
        db.query(ProjectCollaborator)
        .filter(ProjectCollaborator.project_id == project.id)
        .order_by(ProjectCollaborator.created_at.asc())
        .all()
    )
    return {
        "owner": {
            "id": project.owner.id,
            "email": project.owner.email,
            "name": _user_label(project.owner),
            "role": EDITOR_ROLE,
        },
        "collaborators": [_collaborator_response(r) for r in rows],
    }


@router.post("/projects/{project_id}/collaborators", status_code=status.HTTP_201_CREATED)
def add_collaborator(
    project_id: int,
    body: CollaboratorCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = require_project_owner(db, project_id, current_user)
    user = db.query(User).filter(User.email == body.email.strip().lower()).first()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == project.owner_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Owner already has access")

    collab = (
        db.query(ProjectCollaborator)
        .filter(ProjectCollaborator.project_id == project.id, ProjectCollaborator.user_id == user.id)
        .first()
    )
    if collab:
        collab.role = body.role
    else:
        collab = ProjectCollaborator(
            project_id=project.id,
            user_id=user.id,
            role=body.role,
            invited_by_id=current_user.id,
        )
        db.add(collab)
    db.commit()
    db.refresh(collab)
    return _collaborator_response(collab)


@router.patch("/projects/{project_id}/collaborators/{collaborator_id}")
def update_collaborator(
    project_id: int,
    collaborator_id: int,
    body: CollaboratorUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    project = require_project_owner(db, project_id, current_user)
    collab = (
        db.query(ProjectCollaborator)
        .filter(ProjectCollaborator.id == collaborator_id, ProjectCollaborator.project_id == project.id)
        .first()
    )
    if not collab:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collaborator not found")
    collab.role = body.role
    db.commit()
    db.refresh(collab)
    return _collaborator_response(collab)


@router.delete("/projects/{project_id}/collaborators/{collaborator_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_collaborator(
    project_id: int,
    collaborator_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    project = require_project_owner(db, project_id, current_user)
    collab = (
        db.query(ProjectCollaborator)
        .filter(ProjectCollaborator.id == collaborator_id, ProjectCollaborator.project_id == project.id)
        .first()
    )
    if not collab:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Collaborator not found")
    db.delete(collab)
    db.commit()


@router.get("/projects/{project_id}/comments")
def list_comments(
    project_id: int,
    since: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict]:
    require_project_view(db, project_id, current_user)
    q = db.query(PanoramaComment).filter(PanoramaComment.project_id == project_id)
    if since:
        q = q.filter(PanoramaComment.updated_at >= since)
    rows = q.order_by(PanoramaComment.created_at.asc()).all()
    return [_comment_response(r) for r in rows]


@router.post("/projects/{project_id}/comments", status_code=status.HTTP_201_CREATED)
def create_comment(
    project_id: int,
    body: CommentCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    require_project_view(db, project_id, current_user)
    pano = db.query(Panorama).filter(Panorama.id == body.panorama_id, Panorama.project_id == project_id).first()
    if not pano:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Panorama not found")
    comment = PanoramaComment(
        project_id=project_id,
        panorama_id=body.panorama_id,
        author_id=current_user.id,
        body=body.body.strip(),
        yaw=body.yaw,
        pitch=body.pitch,
    )
    db.add(comment)
    db.commit()
    db.refresh(comment)
    return _comment_response(comment)


@router.patch("/comments/{comment_id}/resolve")
def resolve_comment(
    comment_id: int,
    body: CommentResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict:
    comment = db.query(PanoramaComment).filter(PanoramaComment.id == comment_id).first()
    if not comment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comment not found")
    require_project_edit(db, comment.project_id, current_user)

    comment.resolved = body.resolved
    if body.resolved:
        comment.resolved_by_id = current_user.id
        comment.resolved_at = datetime.utcnow()
    else:
        comment.resolved_by_id = None
        comment.resolved_at = None
    db.commit()
    db.refresh(comment)
    return _comment_response(comment)
