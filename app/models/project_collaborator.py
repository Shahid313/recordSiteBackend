from datetime import datetime

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import relationship

from app.db.base import Base


class ProjectCollaborator(Base):
    __tablename__ = "project_collaborators"
    __table_args__ = (UniqueConstraint("project_id", "user_id", name="uq_project_collaborator_user"),)

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    role = Column(String(32), nullable=False, default="viewer")
    invited_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="collaborators")
    user = relationship("User", foreign_keys=[user_id], back_populates="project_collaborations")
    invited_by = relationship("User", foreign_keys=[invited_by_id])
