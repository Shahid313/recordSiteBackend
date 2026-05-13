import logging
import os
import tempfile
from datetime import datetime

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.video import Video, VideoStatus
from app.models.panorama import Panorama
from app.models.connection import Connection
from app.services.storage import storage
from app.services.video_processor import video_processor
from app.services.sfm_processor import sfm_processor
from app.core.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="process_video_task", bind=True)
def process_video_task(self, video_id: int) -> None:
    """
    Pipeline:
    - Download uploaded video from MinIO
    - Extract metadata, update Video
    - Extract frames (1 fps default), upload frames + thumbnails
    - Create Panorama rows
    - Mark Video COMPLETED / FAILED
    """
    db: Session = SessionLocal()
    try:
        video = db.query(Video).filter(Video.id == video_id).first()
        if not video:
            logger.error("Video not found: %s", video_id)
            return

        video.status = VideoStatus.EXTRACTING_FRAMES.value
        video.error_message = None
        db.commit()

        with tempfile.TemporaryDirectory() as tmpdir:
            logger.info("Downloading video from storage: %s / %s", "videos", video.storage_path)
            local_video_path = storage.get_file_path("videos", video.storage_path)
            logger.info("Video downloaded to: %s (exists=%s)", local_video_path, os.path.exists(local_video_path))

            # Metadata
            meta = video_processor.get_metadata(local_video_path)
            video.duration = meta.duration
            video.fps = meta.fps
            video.resolution = meta.resolution
            db.commit()

            total_frames = int(meta.duration) if meta.duration else None

            batch = []
            batch_size = 25

            for frame in video_processor.extract_frames(local_video_path):
                frame_key = f"{video.project_id}/{video.id}/frame_{frame.frame_number:06d}.jpg"
                thumb_key = f"{video.project_id}/{video.id}/thumb_{frame.frame_number:06d}.jpg"

                frame_path = os.path.join(tmpdir, f"frame_{frame.frame_number:06d}.jpg")
                thumb_path = os.path.join(tmpdir, f"thumb_{frame.frame_number:06d}.jpg")
                with open(frame_path, "wb") as f:
                    f.write(frame.jpeg_bytes)
                with open(thumb_path, "wb") as f:
                    f.write(frame.thumbnail_jpeg_bytes)

                storage.upload_file(
                    "panoramas",
                    frame_key,
                    frame_path,
                    content_type="image/jpeg",
                )
                storage.upload_file(
                    "thumbnails",
                    thumb_key,
                    thumb_path,
                    content_type="image/jpeg",
                )

                pano = Panorama(
                    project_id=video.project_id,
                    video_id=video.id,
                    storage_path=frame_key,
                    thumbnail_path=thumb_key,
                    frame_number=frame.frame_number,
                    timestamp=frame.timestamp,
                )
                batch.append(pano)

                if len(batch) >= batch_size:
                    db.add_all(batch)
                    db.commit()
                    batch = []

            if batch:
                db.add_all(batch)
                db.commit()

        # Phase 2: SfM / spatial linking
        video.status = VideoStatus.PROCESSING_SFM.value
        db.commit()

        # Clear existing auto connections for this video; processor also does this, but keep here for safety.
        pano_ids = [row[0] for row in db.query(Panorama.id).filter(Panorama.video_id == video.id).all()]
        if pano_ids:
            db.query(Connection).filter(Connection.from_pano_id.in_(pano_ids), Connection.manual_override.is_(False)).delete(synchronize_session=False)
            db.query(Connection).filter(Connection.to_pano_id.in_(pano_ids), Connection.manual_override.is_(False)).delete(synchronize_session=False)
            db.commit()

        sfm_processor.process_panoramas(db, video.id)

        video.status = VideoStatus.COMPLETED.value
        video.processed_at = datetime.utcnow()
        db.commit()

        logger.info(
            "Video processed: id=%s status=%s duration=%s total_frames=%s",
            video.id,
            video.status,
            video.duration,
            total_frames,
        )
    except Exception as e:
        logger.exception("Video processing failed for %s: %s", video_id, e)
        try:
            video = db.query(Video).filter(Video.id == video_id).first()
            if video:
                video.status = VideoStatus.FAILED.value
                video.error_message = str(e)
                video.processed_at = datetime.utcnow()
                db.commit()
        except Exception:
            db.rollback()
        raise
    finally:
        db.close()


