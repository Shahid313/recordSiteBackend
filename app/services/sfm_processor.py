import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
from sqlalchemy.orm import Session

from app.models.panorama import Panorama
from app.models.connection import Connection
from app.services.storage import storage

logger = logging.getLogger(__name__)


@dataclass
class SfMResult:
    updated_panoramas: int
    created_connections: int
    used_fallback: bool


class SfMProcessor:
    """
    Lightweight SfM-style chaining:
    - Detect features (prefer SIFT, fallback ORB)
    - Match between i and i+1..i+3
    - Estimate homography; derive dx, dy and yaw
    - Accumulate positions; normalize step sizes

    If matching fails overall, falls back to linear layout:
      x = frame_number * 5, y = 0, yaw = 0
    """

    def process_panoramas(self, db: Session, video_id: int) -> SfMResult:
        panos: List[Panorama] = (
            db.query(Panorama)
            .filter(Panorama.video_id == video_id)
            .order_by(Panorama.frame_number.asc())
            .all()
        )

        if not panos:
            return SfMResult(updated_panoramas=0, created_connections=0, used_fallback=True)

        detector, matcher, is_binary = self._build_feature_stack()

        # Precompute keypoints/descriptors
        feats: List[Tuple[List[cv2.KeyPoint], Optional[np.ndarray]]] = []
        for p in panos:
            path = storage.get_file_path("panoramas", p.storage_path)
            img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
            if img is None:
                feats.append(([], None))
                continue
            kps, des = detector.detectAndCompute(img, None)
            feats.append((kps or [], des))

        # Initialize first pano at origin
        panos[0].position_x = 0.0
        panos[0].position_y = 0.0
        panos[0].orientation = 0.0

        created_connections = 0
        had_any_good = False

        # Clear any existing auto connections for this video (keep manual overrides)
        pano_ids = [p.id for p in panos]
        db.query(Connection).filter(
            Connection.from_pano_id.in_(pano_ids),
            Connection.manual_override.is_(False),
        ).delete(synchronize_session=False)
        db.query(Connection).filter(
            Connection.to_pano_id.in_(pano_ids),
            Connection.manual_override.is_(False),
        ).delete(synchronize_session=False)
        db.commit()

        # Accumulate positions along sequence using best of i+1..i+3
        step_lengths: List[float] = []
        for i in range(len(panos) - 1):
            best = None  # (j, dx, dy, yaw, confidence)
            for hop in (1, 2, 3):
                j = i + hop
                if j >= len(panos):
                    continue

                kps1, des1 = feats[i]
                kps2, des2 = feats[j]
                if des1 is None or des2 is None or len(kps1) < 10 or len(kps2) < 10:
                    continue

                good, inlier_ratio = self._match_and_homography(matcher, kps1, des1, kps2, des2, is_binary=is_binary)
                if good is None:
                    continue
                H, good_count, inlier_ratio = good

                if good_count < 20:
                    continue

                dx, dy, yaw = self._pose_from_homography(H)
                # Confidence: combine inlier ratio and match count
                confidence = float(min(1.0, (good_count / 80.0) * (inlier_ratio or 0.0)))
                if best is None or confidence > best[4]:
                    best = (j, dx, dy, yaw, confidence)

            if best is None:
                continue

            had_any_good = True
            j, dx, dy, yaw, conf = best

            # Accumulate: if we hopped > 1, we still connect i -> j, but positions are along i->(i+1) primarily.
            # We'll apply movement to i+1 only if i+1 wasn't computed yet; otherwise ignore.
            target_idx = i + 1
            base_x = float(panos[i].position_x or 0.0)
            base_y = float(panos[i].position_y or 0.0)

            step = math.hypot(dx, dy)
            if step > 0:
                step_lengths.append(step)

            if target_idx < len(panos) and panos[target_idx].position_x is None:
                panos[target_idx].position_x = base_x + dx
                panos[target_idx].position_y = base_y + dy
                panos[target_idx].orientation = yaw

            db.add(Connection(from_pano_id=panos[i].id, to_pano_id=panos[j].id, confidence=conf, manual_override=False))
            created_connections += 1

            # Also connect sequentially if hop > 1 (gives denser path)
            if j != i + 1:
                db.add(Connection(from_pano_id=panos[i].id, to_pano_id=panos[i + 1].id, confidence=max(0.4, conf * 0.8), manual_override=False))
                created_connections += 1

            if (i % 50) == 0:
                db.commit()

        # Normalize distances to arbitrary units (median step => 5 units)
        if had_any_good and step_lengths:
            median = float(np.median(np.array(step_lengths, dtype=np.float32)))
            scale = 1.0
            if median > 1e-6:
                scale = 5.0 / median

            last_known = (0.0, 0.0)
            for p in panos:
                if p.position_x is None or p.position_y is None:
                    # Fill gaps linearly from last known
                    p.position_x = last_known[0] + 5.0
                    p.position_y = last_known[1]
                    p.orientation = p.orientation or 0.0
                else:
                    p.position_x = float(p.position_x) * scale
                    p.position_y = float(p.position_y) * scale
                last_known = (float(p.position_x), float(p.position_y))

            db.commit()
            return SfMResult(updated_panoramas=len(panos), created_connections=created_connections, used_fallback=False)

        # Fallback: linear layout
        for p in panos:
            p.position_x = float(p.frame_number) * 5.0
            p.position_y = 0.0
            p.orientation = 0.0

        # Connect sequentially with low confidence to still show a usable graph
        for i in range(len(panos) - 1):
            db.add(Connection(from_pano_id=panos[i].id, to_pano_id=panos[i + 1].id, confidence=0.35, manual_override=False))
            created_connections += 1

        db.commit()
        return SfMResult(updated_panoramas=len(panos), created_connections=created_connections, used_fallback=True)

    def _build_feature_stack(self):
        # Prefer SIFT if available (opencv-contrib)
        try:
            detector = cv2.SIFT_create()
            matcher = cv2.BFMatcher(cv2.NORM_L2, crossCheck=False)
            return detector, matcher, False
        except Exception:
            detector = cv2.ORB_create(nfeatures=2000)
            matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
            return detector, matcher, True

    def _match_and_homography(self, matcher, kps1, des1, kps2, des2, is_binary: bool):
        try:
            matches = matcher.knnMatch(des1, des2, k=2)
        except Exception:
            return None, None

        good = []
        for m, n in matches:
            if m.distance < 0.7 * n.distance:
                good.append(m)

        if len(good) < 20:
            return None, None

        pts1 = np.float32([kps1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        pts2 = np.float32([kps2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

        H, mask = cv2.findHomography(pts1, pts2, cv2.RANSAC, 5.0)
        if H is None or mask is None:
            return None, None

        inliers = int(mask.sum())
        inlier_ratio = float(inliers / max(1, len(good)))
        return (H, len(good), inlier_ratio), inlier_ratio

    def _pose_from_homography(self, H: np.ndarray) -> Tuple[float, float, float]:
        # Translation components (pixel space)
        dx = float(H[0, 2])
        dy = float(H[1, 2])

        # Approx yaw from rotation-ish part of H
        yaw = math.degrees(math.atan2(float(H[1, 0]), float(H[0, 0])))

        # Clamp wildly large translations (bad homographies)
        if abs(dx) > 2000:
            dx = math.copysign(2000.0, dx)
        if abs(dy) > 2000:
            dy = math.copysign(2000.0, dy)

        # Reduce pixel-space deltas to smaller steps; normalization later handles scale.
        dx *= 0.01
        dy *= 0.01
        return dx, dy, yaw


sfm_processor = SfMProcessor()


