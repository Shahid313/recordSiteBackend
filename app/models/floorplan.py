from datetime import datetime

from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class Floorplan(Base):
    __tablename__ = "floorplans"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    image_path = Column(String(1024), nullable=False)
    thumbnail_path = Column(String(1024), nullable=True)
    floor_order = Column(Integer, nullable=False, default=0)

    # transform_matrix stored as individual columns for type safety
    transform_scale = Column(Float, nullable=False, default=1.0)
    transform_rotation = Column(Float, nullable=False, default=0.0)
    transform_offset_x = Column(Float, nullable=False, default=0.0)
    transform_offset_y = Column(Float, nullable=False, default=0.0)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="floorplans")
    panoramas = relationship("Panorama", back_populates="floorplan", foreign_keys="Panorama.floor_id")
