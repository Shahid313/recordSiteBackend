from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.models.project import Project
from app.models.project_collaborator import ProjectCollaborator
from app.models.user import User

VIEWER_ROLE = "viewer"
EDITOR_ROLE = "editor"
VALID_COLLABORATOR_ROLES = {VIEWER_ROLE, EDITOR_ROLE}


def get_project_role(db: Session, project: Project, user: User) -> str | None:
    if getattr(user, "is_superuser", False) or project.owner_id == user.id:
        return EDITOR_ROLE

    collab = (
        db.query(ProjectCollaborator)
        .filter(ProjectCollaborator.project_id == project.id, ProjectCollaborator.user_id == user.id)
        .first()
    )
    if not collab:
        return None
    return collab.role if collab.role in VALID_COLLABORATOR_ROLES else VIEWER_ROLE


def require_project_view(db: Session, project_id: int, user: User) -> tuple[Project, str]:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    role = get_project_role(db, project, user)
    if not role:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project, role


def require_project_edit(db: Session, project_id: int, user: User) -> Project:
    project, role = require_project_view(db, project_id, user)
    if role != EDITOR_ROLE:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Editor access required")
    return project


def require_project_owner(db: Session, project_id: int, user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    if project.owner_id != user.id and not getattr(user, "is_superuser", False):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Project owner access required")
    return project
