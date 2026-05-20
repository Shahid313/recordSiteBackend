from datetime import datetime
from enum import Enum
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, BigInteger, Float
from sqlalchemy.orm import relationship
from app.db.base import Base


class VideoStatus(str, Enum):
    UPLOADED = "UPLOADED"
    EXTRACTING_FRAMES = "EXTRACTING_FRAMES"
    EXTRACTING_IMU = "EXTRACTING_IMU"
    PROCESSING_SFM = "PROCESSING_SFM"
    FUSING_SENSORS = "FUSING_SENSORS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class Video(Base):
    __tablename__ = "videos"

    id = Column(Integer, primary_key=True, index=True)
    project_id = Column(Integer, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True)

    filename = Column(String(512), nullable=False)
    storage_path = Column(String(1024), nullable=False)
    file_size = Column(BigInteger, nullable=False)

    duration = Column(Float, nullable=True)  # seconds
    fps = Column(Float, nullable=True)
    resolution = Column(String(64), nullable=True)  # e.g. "3840x1920"

    status = Column(String(32), default=VideoStatus.UPLOADED.value, nullable=False, index=True)
    error_message = Column(String(2048), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="videos")
    panoramas = relationship("Panorama", back_populates="video", cascade="all, delete-orphan")


