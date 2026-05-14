from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, Float, Boolean
from sqlalchemy.orm import relationship
from app.db.base import Base


class Panorama(Base):
    __tablename__ = "panoramas"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)
    video_id = Column(Integer, ForeignKey("videos.id", ondelete="CASCADE"), nullable=False, index=True)

    storage_path = Column(String(1024), nullable=False)
    thumbnail_path = Column(String(1024), nullable=False)

    frame_number = Column(Integer, nullable=False, index=True)
    timestamp = Column(Float, nullable=False)  # seconds

    # Phase 2 (SfM / spatial layout)
    position_x = Column(Float, nullable=True)
    position_y = Column(Float, nullable=True)
    orientation = Column(Float, nullable=True)  # yaw degrees

    # Floor assignment & transition
    floor_id = Column(Integer, ForeignKey("floorplans.id", ondelete="SET NULL"), nullable=True)
    is_transition = Column(Boolean, default=False, nullable=False)
    transition_floor_id = Column(Integer, ForeignKey("floorplans.id", ondelete="SET NULL"), nullable=True)
    transition_pano_id = Column(Integer, ForeignKey("panoramas.id", ondelete="SET NULL"), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    project = relationship("Project", back_populates="panoramas")
    video = relationship("Video", back_populates="panoramas")
    floorplan = relationship("Floorplan", back_populates="panoramas", foreign_keys=[floor_id])
    comments = relationship("PanoramaComment", back_populates="panorama", cascade="all, delete-orphan")


