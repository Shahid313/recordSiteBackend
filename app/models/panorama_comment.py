from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class PanoramaComment(Base):
    __tablename__ = "panorama_comments"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    panorama_id = Column(Integer, ForeignKey("panoramas.id", ondelete="CASCADE"), nullable=False, index=True)
    author_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    body = Column(Text, nullable=False)
    yaw = Column(Float, nullable=False)
    pitch = Column(Float, nullable=False, default=0.0)
    resolved = Column(Boolean, default=False, nullable=False)
    resolved_by_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False, index=True)

    project = relationship("Project", back_populates="comments")
    panorama = relationship("Panorama", back_populates="comments")
    author = relationship("User", foreign_keys=[author_id], back_populates="panorama_comments")
    resolved_by = relationship("User", foreign_keys=[resolved_by_id])
