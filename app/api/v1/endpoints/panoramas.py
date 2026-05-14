import math
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.api.permissions import require_project_view
from app.core.config import settings
from app.db.session import get_db
from app.models.connection import Connection
from app.models.panorama import Panorama
from app.models.project import Project
from app.models.user import User
from app.models.video import Video, VideoStatus
from app.models.floorplan import Floorplan


router = APIRouter()


def _hotspot(from_p: Panorama, to_p: Panorama) -> dict[str, float]:
    """
    Calculate hotspot yaw/pitch from from_p to to_p.
    Yaw is adjusted by from_p.orientation (yaw degrees).
    """
    fx = float(from_p.position_x) if from_p.position_x is not None else float(from_p.frame_number) * 5.0
    fy = float(from_p.position_y) if from_p.position_y is not None else 0.0
    tx = float(to_p.position_x) if to_p.position_x is not None else float(to_p.frame_number) * 5.0
    ty = float(to_p.position_y) if to_p.position_y is not None else 0.0

    dx = tx - fx
    dy = ty - fy
    angle = math.degrees(math.atan2(dy, dx)) % 360.0
    from_yaw = float(from_p.orientation) if from_p.orientation is not None else 0.0
    relative = (angle - from_yaw) % 360.0
    return {"yaw": relative, "pitch": 0.0}


@router.get("/panoramas/{pano_id}")
def get_panorama_details(
    pano_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    pano = db.query(Panorama).filter(Panorama.id == pano_id).first()
    if not pano:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Panorama not found")

    require_project_view(db, pano.project_id, current_user)

    base = ""
    image_url = base + f"{settings.API_V1_STR}/files/panoramas/{pano.storage_path}"

    # Pull connections touching this pano
    conns = (
        db.query(Connection)
        .filter((Connection.from_pano_id == pano.id) | (Connection.to_pano_id == pano.id))
        .all()
    )

    connected = []
    for c in conns:
        other_id = c.to_pano_id if c.from_pano_id == pano.id else c.from_pano_id
        other = db.query(Panorama).filter(Panorama.id == other_id).first()
        if not other:
            continue
        if other.video_id != pano.video_id:
            continue

        fx = float(pano.position_x) if pano.position_x is not None else float(pano.frame_number) * 5.0
        fy = float(pano.position_y) if pano.position_y is not None else 0.0
        tx = float(other.position_x) if other.position_x is not None else float(other.frame_number) * 5.0
        ty = float(other.position_y) if other.position_y is not None else 0.0
        dist = math.hypot(tx - fx, ty - fy)

        hs = _hotspot(pano, other)
        connected.append(
            {
                "id": other.id,
                "direction": hs["yaw"],  # alias
                "distance": dist,
                "confidence": float(c.confidence),
                "manual": bool(c.manual_override),
                "yaw": hs["yaw"],
                "pitch": hs["pitch"],
            }
        )

    # Filter and rank: confidence > 0.5 and closest first (limit 5)
    connected = [x for x in connected if x["confidence"] >= 0.5]
    connected.sort(key=lambda x: (x["distance"], -x["confidence"]))
    connected = connected[:5]

    return {
        "id": pano.id,
        "image_url": image_url,
        "position": {
            "x": float(pano.position_x) if pano.position_x is not None else float(pano.frame_number) * 5.0,
            "y": float(pano.position_y) if pano.position_y is not None else 0.0,
        },
        "orientation": float(pano.orientation) if pano.orientation is not None else 0.0,
        "frame_number": pano.frame_number,
        "timestamp": float(pano.timestamp),
        "connected_panoramas": connected,
        "floor_id": pano.floor_id,
        "is_transition": bool(pano.is_transition),
        "transition_floor_id": pano.transition_floor_id,
        "transition_pano_id": pano.transition_pano_id,
    }


@router.get("/projects/{project_id}/tour-data")
def get_tour_data(
    project_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    project, role = require_project_view(db, project_id, current_user)

    video = (
        db.query(Video)
        .filter(Video.project_id == project.id, Video.status == VideoStatus.COMPLETED.value)
        .order_by(Video.created_at.desc())
        .first()
    )
    if not video:
        return {"panoramas": [], "connections": [], "metadata": {"total": 0, "start_pano_id": None}}

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
    pano_payload = []
    for p in panos:
        pano_payload.append(
            {
                "id": p.id,
                "image_url": base + f"{settings.API_V1_STR}/files/panoramas/{p.storage_path}",
                "thumbnail_url": base + f"{settings.API_V1_STR}/files/thumbnails/{p.thumbnail_path}",
                "position": {
                    "x": float(p.position_x) if p.position_x is not None else float(p.frame_number) * 5.0,
                    "y": float(p.position_y) if p.position_y is not None else 0.0,
                },
                "orientation": float(p.orientation) if p.orientation is not None else 0.0,
                "frame_number": p.frame_number,
                "timestamp": float(p.timestamp),
                "floor_id": p.floor_id,
                "is_transition": bool(p.is_transition),
                "transition_floor_id": p.transition_floor_id,
                "transition_pano_id": p.transition_pano_id,
            }
        )

    conn_payload = []
    for c in conns:
        conn_payload.append(
            {
                "from": c.from_pano_id,
                "to": c.to_pano_id,
                "confidence": float(c.confidence),
                "manual": bool(c.manual_override),
            }
        )

    start_pano_id = panos[0].id if panos else None

    # Include floorplans for multi-level support
    fps = (
        db.query(Floorplan)
        .filter(Floorplan.project_id == project_id)
        .order_by(Floorplan.floor_order.asc())
        .all()
    )
    floorplan_payload = [
        {
            "id": f.id,
            "name": f.name,
            "floor_order": f.floor_order,
            "image_url": f"{settings.API_V1_STR}/files/floorplans/{f.image_path}",
            "transform_matrix": {
                "scale": f.transform_scale,
                "rotation": f.transform_rotation,
                "offset_x": f.transform_offset_x,
                "offset_y": f.transform_offset_y,
            },
        }
        for f in fps
    ]

    return {
        "panoramas": pano_payload,
        "connections": conn_payload,
        "metadata": {"total": len(panos), "start_pano_id": start_pano_id, "video_id": video.id, "access_role": role},
        "floorplans": floorplan_payload,
    }


