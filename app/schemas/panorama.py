from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class PanoramaResponse(BaseModel):
    id: int
    project_id: int
    video_id: int
    storage_path: str
    thumbnail_path: str
    frame_number: int
    timestamp: float
    created_at: datetime

    file_url: Optional[str] = None
    thumbnail_url: Optional[str] = None

    class Config:
        from_attributes = True


