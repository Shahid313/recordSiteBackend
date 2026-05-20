import json

from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import require_project_view
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.models.project import Project
from app.models.video import Video, VideoStatus
from app.models.panorama import Panorama
from app.models.connection import Connection
from app.services.storage import storage


router = APIRouter()


def _latest_positionable_video(db: Session, project_id: int) -> Video | None:
    return (
        db.query(Video)
        .filter(Video.project_id == project_id, Video.status != VideoStatus.FAILED.value)
        .order_by(Video.created_at.desc())
        .first()
    )


@router.get("/projects/{project_id}/constellation")
def get_project_constellation(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project, _role = require_project_view(db, project_id, current_user)

    # Default to the most recent non-failed video
    video = _latest_positionable_video(db, project.id)
    if not video:
        return {"nodes": [], "connections": []}

    panos = (
        db.query(Panorama)
        .filter(Panorama.video_id == video.id)
        .order_by(Panorama.frame_number.asc())
        .all()
    )

    pano_ids = [p.id for p in panos]
    conns = []
    if pano_ids:
        conns = (
            db.query(Connection)
            .filter(Connection.from_pano_id.in_(pano_ids), Connection.to_pano_id.in_(pano_ids))
            .all()
        )

    base = ""

    nodes = []
    for p in panos:
        thumb_url = base + f"{settings.API_V1_STR}/files/thumbnails/{p.thumbnail_path}"
        # Always return a usable position even if SfM hasn't populated DB fields yet.
        x = float(p.position_x) if p.position_x is not None else float(p.frame_number) * 5.0
        y = float(p.position_y) if p.position_y is not None else 0.0
        yaw = float(p.orientation) if p.orientation is not None else 0.0
        nodes.append(
            {
                "id": p.id,
                "position": {"x": x, "y": y},
                "orientation": yaw,
                "thumbnail_url": thumb_url,
                "frame_number": p.frame_number,
                "timestamp": float(p.timestamp),
            }
        )

    edges = []
    for c in conns:
        edges.append(
            {
                "from": c.from_pano_id,
                "to": c.to_pano_id,
                "confidence": float(c.confidence),
                "manual": bool(c.manual_override),
            }
        )

    # If no connections exist yet (SfM still running), return a simple sequential chain
    # so the UI always renders a graph.
    if not edges and len(panos) > 1:
        for i in range(len(panos) - 1):
            edges.append({"from": panos[i].id, "to": panos[i + 1].id, "confidence": 0.35, "manual": False})

    return {"video_id": video.id, "video_status": video.status, "nodes": nodes, "connections": edges}


@router.get("/projects/{project_id}/constellation/comparison")
def compare_positioning_methods(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    Return SfM-only, IMU-only, and fused paths when a comparison artifact exists.

    Processing writes this artifact to the exports storage category, so it works
    with both local files and Cloudflare R2 without a database migration.
    """
    project, _role = require_project_view(db, project_id, current_user)
    video = _latest_positionable_video(db, project.id)
    if not video:
        return {"video_id": None, "sfm_only": [], "imu_only": [], "fused": []}

    object_key = f"{project.id}/{video.id}/positioning_comparison.json"
    try:
        local_path = storage.get_file_path("exports", object_key)
        with open(local_path, "r", encoding="utf-8") as f:
            payload = json.load(f)
        payload.setdefault("video_id", video.id)
        return payload
    except Exception:
        panos = (
            db.query(Panorama)
            .filter(Panorama.video_id == video.id)
            .order_by(Panorama.frame_number.asc())
            .all()
        )
        fused = [
            {
                "id": p.id,
                "frame_number": p.frame_number,
                "timestamp": float(p.timestamp),
                "x": float(p.position_x) if p.position_x is not None else float(p.frame_number) * 5.0,
                "y": float(p.position_y) if p.position_y is not None else 0.0,
                "orientation": float(p.orientation) if p.orientation is not None else 0.0,
                "source": "current_db",
            }
            for p in panos
        ]
        return {
            "video_id": video.id,
            "warning": "No positioning comparison artifact found; returning current database path as fused.",
            "sfm_only": [],
            "imu_only": [],
            "fused": fused,
        }


