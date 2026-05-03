import math
import os
import shutil
import tempfile
import uuid
from typing import Any, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.db.session import get_db
from app.models.floorplan import Floorplan
from app.models.panorama import Panorama
from app.models.project import Project
from app.models.user import User
from app.services.storage import storage

router = APIRouter()

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg"}
MAX_FLOORPLAN_SIZE = 20 * 1024 * 1024  # 20 MB


def _own_project(db: Session, project_id: int, user: User) -> Project:
    project = db.query(Project).filter(Project.id == project_id).first()
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found")
    return project


def _own_floorplan(db: Session, floorplan_id: int, user: User) -> Floorplan:
    fp = db.query(Floorplan).filter(Floorplan.id == floorplan_id).first()
    if not fp:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Floorplan not found")
    project = db.query(Project).filter(Project.id == fp.project_id).first()
    if not project or project.owner_id != user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Floorplan not found")
    return fp


def _generate_thumbnail(source_path: str, dest_path: str, max_size: int = 300) -> None:
    """Generate a simple thumbnail by copying the image.
    If Pillow is available, resize; otherwise fallback to copy."""
    try:
        from PIL import Image
        img = Image.open(source_path)
        img.thumbnail((max_size, max_size), Image.LANCZOS)
        img.save(dest_path, quality=85)
    except ImportError:
        shutil.copyfile(source_path, dest_path)


def _fp_response(fp: Floorplan) -> dict:
    return {
        "id": fp.id,
        "project_id": fp.project_id,
        "name": fp.name,
        "floor_order": fp.floor_order,
        "image_url": f"{settings.API_V1_STR}/files/floorplans/{fp.image_path}",
        "thumbnail_url": f"{settings.API_V1_STR}/files/floorplans/{fp.thumbnail_path}" if fp.thumbnail_path else None,
        "transform_matrix": {
            "scale": fp.transform_scale,
            "rotation": fp.transform_rotation,
            "offset_x": fp.transform_offset_x,
            "offset_y": fp.transform_offset_y,
        },
        "created_at": fp.created_at.isoformat() if fp.created_at else None,
    }


# ──────────────────────────── Upload floorplan ────────────────────────────

@router.post("/projects/{project_id}/floorplans")
async def upload_floorplan(
    project_id: int,
    name: str = Form(...),
    floor_order: int = Form(0),
    image: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    _own_project(db, project_id, current_user)

    ext = os.path.splitext(image.filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported file type. Allowed: {', '.join(ALLOWED_EXTENSIONS)}",
        )

    # Read into temp file and validate size
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
    try:
        size = 0
        while chunk := await image.read(1024 * 256):
            size += len(chunk)
            if size > MAX_FLOORPLAN_SIZE:
                tmp.close()
                os.unlink(tmp.name)
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="File too large (max 20 MB)")
            tmp.write(chunk)
        tmp.close()

        unique = uuid.uuid4().hex[:12]
        store_name = f"{project_id}/{unique}{ext}"
        thumb_name = f"{project_id}/{unique}_thumb{ext}"

        storage.upload_file("floorplans", store_name, tmp.name)

        # Generate thumbnail
        thumb_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=ext)
        thumb_tmp.close()
        _generate_thumbnail(tmp.name, thumb_tmp.name)
        storage.upload_file("floorplans", thumb_name, thumb_tmp.name)
        os.unlink(thumb_tmp.name)
    finally:
        if os.path.exists(tmp.name):
            os.unlink(tmp.name)

    fp = Floorplan(
        project_id=project_id,
        name=name,
        image_path=store_name,
        thumbnail_path=thumb_name,
        floor_order=floor_order,
    )
    db.add(fp)
    db.commit()
    db.refresh(fp)
    return _fp_response(fp)


# ──────────────────────────── List floorplans ─────────────────────────────

@router.get("/projects/{project_id}/floorplans")
def list_floorplans(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _own_project(db, project_id, current_user)
    fps = (
        db.query(Floorplan)
        .filter(Floorplan.project_id == project_id)
        .order_by(Floorplan.floor_order.asc())
        .all()
    )
    return [_fp_response(f) for f in fps]


# ──────────────────────────── Update floorplan ────────────────────────────

@router.patch("/floorplans/{floorplan_id}")
def update_floorplan(
    floorplan_id: int,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    fp = _own_floorplan(db, floorplan_id, current_user)

    if "name" in body:
        fp.name = str(body["name"])[:255]
    if "floor_order" in body:
        fp.floor_order = int(body["floor_order"])
    if "transform_matrix" in body:
        tm = body["transform_matrix"]
        if "scale" in tm:
            fp.transform_scale = float(tm["scale"])
        if "rotation" in tm:
            fp.transform_rotation = float(tm["rotation"])
        if "offset_x" in tm:
            fp.transform_offset_x = float(tm["offset_x"])
        if "offset_y" in tm:
            fp.transform_offset_y = float(tm["offset_y"])

    db.commit()
    db.refresh(fp)
    return _fp_response(fp)


# ──────────────────────────── Delete floorplan ────────────────────────────

@router.delete("/floorplans/{floorplan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_floorplan(
    floorplan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    fp = _own_floorplan(db, floorplan_id, current_user)

    # Unassign panoramas
    db.query(Panorama).filter(Panorama.floor_id == fp.id).update(
        {Panorama.floor_id: None, Panorama.is_transition: False, Panorama.transition_floor_id: None, Panorama.transition_pano_id: None},
        synchronize_session=False,
    )

    # Delete stored files
    try:
        storage.delete_file("floorplans", fp.image_path)
    except Exception:
        pass
    try:
        if fp.thumbnail_path:
            storage.delete_file("floorplans", fp.thumbnail_path)
    except Exception:
        pass

    db.delete(fp)
    db.commit()


# ──────────────────────── Assign panoramas to floor ───────────────────────

@router.post("/floorplans/{floorplan_id}/assign-panoramas")
def assign_panoramas(
    floorplan_id: int,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    fp = _own_floorplan(db, floorplan_id, current_user)
    pano_ids: list[int] = body.get("panorama_ids", [])
    if not isinstance(pano_ids, list):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="panorama_ids must be a list")

    # Validate ownership
    panos = db.query(Panorama).filter(Panorama.id.in_(pano_ids), Panorama.project_id == fp.project_id).all()
    found_ids = {p.id for p in panos}
    for pid in pano_ids:
        if pid not in found_ids:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Panorama {pid} not found in project")

    for p in panos:
        p.floor_id = fp.id
    db.commit()

    return {"assigned": len(panos), "floorplan_id": fp.id}


# ──────────────── Set transition point on a panorama ─────────────────────

@router.patch("/panoramas/{pano_id}/transition")
def set_transition(
    pano_id: int,
    body: dict[str, Any],
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    pano = db.query(Panorama).filter(Panorama.id == pano_id).first()
    if not pano:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Panorama not found")
    proj = db.query(Project).filter(Project.id == pano.project_id).first()
    if not proj or proj.owner_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Panorama not found")

    pano.is_transition = bool(body.get("is_transition", False))
    if pano.is_transition:
        tfid = body.get("transition_floor_id")
        tpid = body.get("transition_pano_id")
        if tfid is not None:
            fp = db.query(Floorplan).filter(Floorplan.id == int(tfid), Floorplan.project_id == proj.id).first()
            if not fp:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target floor not found")
            pano.transition_floor_id = fp.id
        if tpid is not None:
            tp = db.query(Panorama).filter(Panorama.id == int(tpid), Panorama.project_id == proj.id).first()
            if not tp:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Target panorama not found")
            pano.transition_pano_id = tp.id
    else:
        pano.transition_floor_id = None
        pano.transition_pano_id = None

    db.commit()
    db.refresh(pano)
    return {
        "id": pano.id,
        "is_transition": pano.is_transition,
        "transition_floor_id": pano.transition_floor_id,
        "transition_pano_id": pano.transition_pano_id,
    }


# ──────────── Get panorama floor assignments for a project ────────────────

@router.get("/projects/{project_id}/floor-assignments")
def get_floor_assignments(
    project_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[dict[str, Any]]:
    _own_project(db, project_id, current_user)
    panos = db.query(Panorama).filter(Panorama.project_id == project_id).all()
    return [
        {
            "id": p.id,
            "frame_number": p.frame_number,
            "floor_id": p.floor_id,
            "is_transition": p.is_transition,
            "transition_floor_id": p.transition_floor_id,
            "transition_pano_id": p.transition_pano_id,
        }
        for p in panos
    ]
