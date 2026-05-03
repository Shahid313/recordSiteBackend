from fastapi import APIRouter, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.user import User
from app.models.project import Project
from app.models.video import Video, VideoStatus
from app.models.panorama import Panorama
from app.models.connection import Connection


router = APIRouter()


@router.get("/projects/{project_id}/constellation")
def get_project_constellation(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or project.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")

    # Default to the most recent non-failed video
    video = (
        db.query(Video)
        .filter(Video.project_id == project.id, Video.status != VideoStatus.FAILED.value)
        .order_by(Video.created_at.desc())
        .first()
    )
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


