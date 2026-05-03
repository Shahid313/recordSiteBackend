import io
from dataclasses import dataclass
from typing import Generator, Optional, Tuple

import cv2
from PIL import Image

from app.core.config import settings


@dataclass
class VideoMetadata:
    duration: Optional[float]
    fps: Optional[float]
    resolution: Optional[str]


@dataclass
class ExtractedFrame:
    frame_number: int
    timestamp: float
    jpeg_bytes: bytes
    thumbnail_jpeg_bytes: bytes


class VideoProcessor:
    def get_metadata(self, video_path: str) -> VideoMetadata:
        cap = cv2.VideoCapture(video_path)
        try:
            if not cap.isOpened():
                raise ValueError("Unable to open video")

            fps = cap.get(cv2.CAP_PROP_FPS) or None
            frame_count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or None
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)

            duration = None
            if fps and frame_count and fps > 0:
                duration = float(frame_count) / float(fps)

            resolution = None
            if width > 0 and height > 0:
                resolution = f"{width}x{height}"

            return VideoMetadata(duration=duration, fps=fps, resolution=resolution)
        finally:
            cap.release()

    def extract_frames(
        self,
        video_path: str,
        interval_seconds: int = settings.FRAME_EXTRACTION_INTERVAL_SECONDS,
        jpeg_quality: int = 90,
        thumbnail_max_size: Tuple[int, int] = (320, 320),
    ) -> Generator[ExtractedFrame, None, None]:
        cap = cv2.VideoCapture(video_path)
        try:
            if not cap.isOpened():
                raise ValueError("Unable to open video")

            metadata = self.get_metadata(video_path)
            duration = metadata.duration
            if duration is None:
                # Fallback: attempt to approximate from CAP_PROP_POS_MSEC while reading sequentially
                duration = 0.0

            last_second = int(duration)
            frame_number = 0

            for sec in range(0, max(1, last_second + 1), max(1, interval_seconds)):
                cap.set(cv2.CAP_PROP_POS_MSEC, sec * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue

                # BGR -> RGB
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                img = Image.fromarray(rgb)

                jpeg_bytes = self._encode_jpeg(img, quality=jpeg_quality)
                thumb = img.copy()
                thumb.thumbnail(thumbnail_max_size)
                thumb_bytes = self._encode_jpeg(thumb, quality=jpeg_quality)

                yield ExtractedFrame(
                    frame_number=frame_number,
                    timestamp=float(sec),
                    jpeg_bytes=jpeg_bytes,
                    thumbnail_jpeg_bytes=thumb_bytes,
                )
                frame_number += 1
        finally:
            cap.release()

    def _encode_jpeg(self, img: Image.Image, quality: int = 90) -> bytes:
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        return buf.getvalue()


video_processor = VideoProcessor()


