import logging
import os
import json
import tempfile
from datetime import datetime
from typing import Dict, List

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models.video import Video, VideoStatus
from app.models.panorama import Panorama
from app.models.connection import Connection
from app.services.storage import storage
from app.services.video_processor import video_processor
from app.services.sfm_processor import sfm_processor
from app.services.imu_extractor import imu_extractor
from app.services.imu_processor import imu_processor
from app.services.sensor_fusion import sensor_fusion
from app.core.config import settings
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


def _build_sfm_positions(db: Session, video_id: int, used_fallback: bool) -> List[Dict]:
    panos: List[Panorama] = (
        db.query(Panorama)
        .filter(Panorama.video_id == video_id)
        .order_by(Panorama.frame_number.asc())
        .all()
    )
    pano_ids = [p.id for p in panos]
    confidence_by_pano: Dict[int, float] = {}
    if pano_ids:
        conns = (
            db.query(Connection)
            .filter(Connection.from_pano_id.in_(pano_ids), Connection.to_pano_id.in_(pano_ids))
            .all()
        )
        for conn in conns:
            confidence_by_pano[conn.from_pano_id] = max(confidence_by_pano.get(conn.from_pano_id, 0.0), float(conn.confidence))
            confidence_by_pano[conn.to_pano_id] = max(confidence_by_pano.get(conn.to_pano_id, 0.0), float(conn.confidence))

    default_confidence = 0.35 if used_fallback else 0.75
    return [
        {
            "id": p.id,
            "frame_number": p.frame_number,
            "timestamp": float(p.timestamp),
            "x": float(p.position_x or 0.0),
            "y": float(p.position_y or 0.0),
            "orientation": float(p.orientation or 0.0),
            "confidence": confidence_by_pano.get(p.id, default_confidence),
        }
        for p in panos
    ]


def _apply_fused_positions(db: Session, video_id: int, fused_positions: List[Dict]) -> None:
    panos: List[Panorama] = (
        db.query(Panorama)
        .filter(Panorama.video_id == video_id)
        .order_by(Panorama.frame_number.asc())
        .all()
    )
    for idx, pano in enumerate(panos):
        if idx >= len(fused_positions):
            break
        pos = fused_positions[idx]
        pano.position_x = float(pos["x"])
        pano.position_y = float(pos["y"])
        pano.orientation = float(pos.get("orientation", pano.orientation or 0.0))
    db.commit()


def _write_positioning_comparison(
    project_id: int,
    video_id: int,
    sfm_positions: List[Dict],
    imu_positions: List[Dict],
    fused_positions: List[Dict],
) -> None:
    payload = {
        "video_id": video_id,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "sfm_only": sfm_positions,
        "imu_only": imu_positions,
        "fused": fused_positions,
    }
    object_key = f"{project_id}/{video_id}/positioning_comparison.json"
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as tmp:
        tmp_path = tmp.name
        json.dump(payload, tmp)
    try:
        storage.upload_file("exports", object_key, tmp_path, content_type="application/json")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


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

        # Phase 2: hybrid IMU + SfM positioning
        video.status = VideoStatus.EXTRACTING_IMU.value
        db.commit()
        self.update_state(state="PROGRESS", meta={"stage": "extracting_imu", "progress": 55})

        imu_data = imu_extractor.extract_imu_data(local_video_path)
        fps = float(video.fps or 30.0)
        imu_positions_full = imu_processor.calculate_positions_from_imu(imu_data, frame_rate=fps) if imu_data else []

        video.status = VideoStatus.PROCESSING_SFM.value
        db.commit()
        self.update_state(state="PROGRESS", meta={"stage": "processing_sfm", "progress": 65})

        # Clear existing auto connections for this video; processor also does this, but keep here for safety.
        pano_ids = [row[0] for row in db.query(Panorama.id).filter(Panorama.video_id == video.id).all()]
        if pano_ids:
            db.query(Connection).filter(Connection.from_pano_id.in_(pano_ids), Connection.manual_override.is_(False)).delete(synchronize_session=False)
            db.query(Connection).filter(Connection.to_pano_id.in_(pano_ids), Connection.manual_override.is_(False)).delete(synchronize_session=False)
            db.commit()

        sfm_result = sfm_processor.process_panoramas(db, video.id)
        sfm_positions = _build_sfm_positions(db, video.id, used_fallback=sfm_result.used_fallback)
        panorama_timestamps = [float(pos["timestamp"]) for pos in sfm_positions]
        imu_positions = imu_processor.positions_for_timestamps(imu_positions_full, panorama_timestamps) if imu_positions_full else []

        video.status = VideoStatus.FUSING_SENSORS.value
        db.commit()
        self.update_state(state="PROGRESS", meta={"stage": "fusing_sensors", "progress": 75})

        if imu_positions:
            fused_positions = sensor_fusion.fuse_sfm_and_imu(sfm_positions, imu_positions)
            _apply_fused_positions(db, video.id, fused_positions)
            logger.info(
                "Hybrid positioning complete for video %s: sfm=%s imu=%s fused=%s",
                video.id,
                len(sfm_positions),
                len(imu_positions),
                len(fused_positions),
            )
        else:
            logger.warning("No IMU data for video %s; using SfM-only positions", video.id)
            fused_positions = [
                {
                    "timestamp": pos["timestamp"],
                    "x": pos["x"],
                    "y": pos["y"],
                    "orientation": pos["orientation"],
                    "source": "sfm",
                    "sfm_confidence": pos.get("confidence", 0.0),
                }
                for pos in sfm_positions
            ]

        _write_positioning_comparison(video.project_id, video.id, sfm_positions, imu_positions, fused_positions)

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


