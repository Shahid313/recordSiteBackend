from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship

from app.db.base import Base


class EditHistory(Base):
    __tablename__ = "edit_history"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    action = Column(String(64), nullable=False)  # move_node, add_connection, delete_connection, toggle_manual
    payload = Column(Text, nullable=False)  # JSON-encoded previous state for undo
    undone = Column(Boolean, default=False, nullable=False)  # False = active, True = undone (available for redo)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project")
    user = relationship("User")
