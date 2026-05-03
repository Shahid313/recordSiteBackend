from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class VideoUploadResponse(BaseModel):
    video_id: int
    status: str


class VideoStatusResponse(BaseModel):
    id: int
    project_id: int
    filename: str
    file_size: int
    storage_path: str

    duration: Optional[float] = None
    fps: Optional[float] = None
    resolution: Optional[str] = None

    status: str
    error_message: Optional[str] = None

    created_at: datetime
    processed_at: Optional[datetime] = None

    processed_frames: int
    total_frames: Optional[int] = None
    progress_percent: Optional[float] = None

    class Config:
        from_attributes = True


