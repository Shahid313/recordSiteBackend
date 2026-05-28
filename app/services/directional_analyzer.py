import logging
import math
from typing import Dict, List, Sequence

import cv2
import numpy as np
from scipy.signal import savgol_filter

from app.services.storage import storage

logger = logging.getLogger(__name__)


class DirectionalAnalyzer:
    """Automatically detect and smooth walking direction between sequential panoramas."""

    def analyze_sequential_directions(self, panoramas: Sequence) -> List[Dict]:
        """
        Analyze movement direction between each sequential panorama pair.

        Returns one direction record per pair:
        {
            "direction": yaw degrees,
            "confidence": 0..1,
            "flow_direction": yaw degrees,
            "feature_direction": optional yaw degrees,
            "flow_magnitude": pixels,
            "feature_matches": int
        }
        """
        directions: List[Dict] = []

        for i in range(len(panoramas) - 1):
            current = self._read_panorama_gray(panoramas[i])
            next_frame = self._read_panorama_gray(panoramas[i + 1])
            if current is None or next_frame is None:
                directions.append(self._fallback_direction(directions))
                continue

            current, next_frame = self._normalize_pair_size(current, next_frame)
            flow_direction, flow_magnitude, flow_confidence = self._direction_from_optical_flow(current, next_frame)
            feature_direction, feature_confidence, feature_matches = self._direction_from_features(current, next_frame)

            if feature_direction is not None:
                direction = self._weighted_angle_mean(
                    [
                        (flow_direction, max(0.1, flow_confidence)),
                        (feature_direction, max(0.1, feature_confidence)),
                    ]
                )
                confidence = min(1.0, (flow_confidence * 0.55) + (feature_confidence * 0.45))
            else:
                direction = flow_direction
                confidence = flow_confidence

            directions.append(
                {
                    "from_id": getattr(panoramas[i], "id", None),
                    "to_id": getattr(panoramas[i + 1], "id", None),
                    "direction": direction,
                    "confidence": confidence,
                    "flow_direction": flow_direction,
                    "feature_direction": feature_direction,
                    "flow_magnitude": flow_magnitude,
                    "feature_matches": feature_matches,
                }
            )

        return directions

    def refine_positions_from_directions(
        self,
        panoramas: Sequence,
        directions: Sequence,
        step_size: float | None = None,
        initial_position: Dict | None = None,
    ) -> List[Dict]:
        """Recalculate a sequentially consistent path from detected directions."""
        if not panoramas:
            return []

        step = step_size if step_size and step_size > 0 else self._estimate_step_size(panoramas)
        first = initial_position or {}
        positions: List[Dict] = [
            {
                "timestamp": float(getattr(panoramas[0], "timestamp", first.get("timestamp", 0.0))),
                "x": float(first.get("x", 0.0)),
                "y": float(first.get("y", 0.0)),
                "orientation": float(first.get("orientation", 0.0)),
                "source": "directional",
                "confidence": 1.0,
            }
        ]

        for i, raw_direction in enumerate(directions):
            direction = self._direction_value(raw_direction)
            confidence = self._direction_confidence(raw_direction)
            angle_rad = math.radians(direction)
            next_x = positions[-1]["x"] + step * math.cos(angle_rad)
            next_y = positions[-1]["y"] + step * math.sin(angle_rad)
            pano = panoramas[i + 1] if i + 1 < len(panoramas) else None
            positions.append(
                {
                    "timestamp": float(getattr(pano, "timestamp", i + 1)),
                    "x": next_x,
                    "y": next_y,
                    "orientation": direction,
                    "source": "directional",
                    "confidence": confidence,
                }
            )

        while len(positions) < len(panoramas):
            last = positions[-1]
            positions.append(
                {
                    "timestamp": float(getattr(panoramas[len(positions)], "timestamp", len(positions))),
                    "x": last["x"] + step,
                    "y": last["y"],
                    "orientation": last["orientation"],
                    "source": "directional_fallback",
                    "confidence": 0.25,
                }
            )

        return positions[: len(panoramas)]

    def smooth_positions(self, positions: List[Dict], window_length: int = 5, polyorder: int = 2) -> List[Dict]:
        """Apply Savitzky-Golay smoothing to path coordinates when enough points exist."""
        if len(positions) < 3:
            return positions

        window = min(window_length, len(positions))
        if window % 2 == 0:
            window -= 1
        if window <= polyorder:
            return positions

        x_coords = np.array([float(p["x"]) for p in positions], dtype=float)
        y_coords = np.array([float(p["y"]) for p in positions], dtype=float)
        x_smooth = savgol_filter(x_coords, window_length=window, polyorder=polyorder)
        y_smooth = savgol_filter(y_coords, window_length=window, polyorder=polyorder)

        smoothed: List[Dict] = []
        for idx, pos in enumerate(positions):
            item = dict(pos)
            item["x"] = float(x_smooth[idx])
            item["y"] = float(y_smooth[idx])
            smoothed.append(item)
        return smoothed

    def blend_positioning_methods(
        self,
        imu_sfm_positions: Sequence[Dict],
        directional_positions: Sequence[Dict],
        confidence_threshold: float = 0.8,
    ) -> List[Dict]:
        """
        Combine IMU+SfM with directional analysis.

        High-confidence fused points are retained; weaker fused points are
        replaced by sequentially consistent directional estimates.
        """
        blended: List[Dict] = []
        max_len = max(len(imu_sfm_positions), len(directional_positions))

        for i in range(max_len):
            fused = imu_sfm_positions[i] if i < len(imu_sfm_positions) else None
            directional = directional_positions[i] if i < len(directional_positions) else None
            fused_confidence = self._fused_confidence(fused)

            if fused and fused_confidence >= confidence_threshold:
                item = dict(fused)
                item["source"] = item.get("source", "hybrid")
                item["confidence"] = fused_confidence
                blended.append(item)
            elif directional:
                item = dict(directional)
                item["source"] = "directional_refined"
                item["hybrid_confidence"] = fused_confidence
                blended.append(item)
            elif fused:
                item = dict(fused)
                item["confidence"] = fused_confidence
                blended.append(item)

        return blended

    def _read_panorama_gray(self, panorama) -> np.ndarray | None:
        try:
            path = storage.get_file_path("panoramas", panorama.storage_path)
        except Exception as exc:
            logger.warning("Unable to fetch panorama %s for directional analysis: %s", getattr(panorama, "id", None), exc)
            return None

        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            logger.warning("Unable to read panorama image for directional analysis: %s", path)
            return None
        return self._resize_for_flow(img)

    def _resize_for_flow(self, img: np.ndarray, max_width: int = 960) -> np.ndarray:
        height, width = img.shape[:2]
        if width <= max_width:
            return img
        scale = max_width / float(width)
        return cv2.resize(img, (max_width, max(1, int(height * scale))), interpolation=cv2.INTER_AREA)

    def _normalize_pair_size(self, current: np.ndarray, next_frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        if current.shape == next_frame.shape:
            return current, next_frame
        height = min(current.shape[0], next_frame.shape[0])
        width = min(current.shape[1], next_frame.shape[1])
        return (
            cv2.resize(current, (width, height), interpolation=cv2.INTER_AREA),
            cv2.resize(next_frame, (width, height), interpolation=cv2.INTER_AREA),
        )

    def _direction_from_optical_flow(self, current: np.ndarray, next_frame: np.ndarray) -> tuple[float, float, float]:
        flow = cv2.calcOpticalFlowFarneback(current, next_frame, None, 0.5, 3, 15, 3, 5, 1.2, 0)
        flow_x = flow[:, :, 0]
        flow_y = flow[:, :, 1]
        magnitude = np.sqrt((flow_x * flow_x) + (flow_y * flow_y))

        strong = magnitude >= np.percentile(magnitude, 60)
        if np.any(strong):
            avg_flow_x = float(np.median(flow_x[strong]))
            avg_flow_y = float(np.median(flow_y[strong]))
            median_magnitude = float(np.median(magnitude[strong]))
        else:
            avg_flow_x = float(np.median(flow_x))
            avg_flow_y = float(np.median(flow_y))
            median_magnitude = float(np.median(magnitude))

        direction = math.degrees(math.atan2(avg_flow_y, avg_flow_x)) % 360.0
        confidence = min(1.0, median_magnitude / 12.0)
        return direction, median_magnitude, confidence

    def _direction_from_features(self, current: np.ndarray, next_frame: np.ndarray) -> tuple[float | None, float, int]:
        detector = cv2.ORB_create(nfeatures=1500)
        kps1, des1 = detector.detectAndCompute(current, None)
        kps2, des2 = detector.detectAndCompute(next_frame, None)
        if des1 is None or des2 is None or not kps1 or not kps2:
            return None, 0.0, 0

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)
        try:
            matches = matcher.knnMatch(des1, des2, k=2)
        except cv2.error:
            return None, 0.0, 0

        good = []
        for pair in matches:
            if len(pair) != 2:
                continue
            m, n = pair
            if m.distance < 0.75 * n.distance:
                good.append(m)

        if len(good) < 8:
            return None, min(0.35, len(good) / 20.0), len(good)

        dx = np.array([kps2[m.trainIdx].pt[0] - kps1[m.queryIdx].pt[0] for m in good], dtype=float)
        dy = np.array([kps2[m.trainIdx].pt[1] - kps1[m.queryIdx].pt[1] for m in good], dtype=float)
        direction = math.degrees(math.atan2(float(np.median(dy)), float(np.median(dx)))) % 360.0
        confidence = min(1.0, len(good) / 80.0)
        return direction, confidence, len(good)

    def _estimate_step_size(self, panoramas: Sequence) -> float:
        steps: List[float] = []
        for i in range(len(panoramas) - 1):
            x0 = getattr(panoramas[i], "position_x", None)
            y0 = getattr(panoramas[i], "position_y", None)
            x1 = getattr(panoramas[i + 1], "position_x", None)
            y1 = getattr(panoramas[i + 1], "position_y", None)
            if None in (x0, y0, x1, y1):
                continue
            step = math.hypot(float(x1) - float(x0), float(y1) - float(y0))
            if 0.5 <= step <= 25.0:
                steps.append(step)
        return float(np.median(np.array(steps, dtype=float))) if steps else 5.0

    def _direction_value(self, raw_direction) -> float:
        if isinstance(raw_direction, dict):
            return float(raw_direction.get("direction", 0.0)) % 360.0
        return float(raw_direction) % 360.0

    def _direction_confidence(self, raw_direction) -> float:
        if isinstance(raw_direction, dict):
            return float(raw_direction.get("confidence", 0.5))
        return 0.5

    def _fused_confidence(self, position: Dict | None) -> float:
        if not position:
            return 0.0
        if "confidence" in position:
            return float(position.get("confidence") or 0.0)
        return float(position.get("sfm_confidence") or 0.0)

    def _fallback_direction(self, directions: List[Dict]) -> Dict:
        direction = directions[-1]["direction"] if directions else 0.0
        return {
            "direction": direction,
            "confidence": 0.2,
            "flow_direction": direction,
            "feature_direction": None,
            "flow_magnitude": 0.0,
            "feature_matches": 0,
        }

    def _weighted_angle_mean(self, angles: Sequence[tuple[float, float]]) -> float:
        x = sum(math.cos(math.radians(angle)) * weight for angle, weight in angles)
        y = sum(math.sin(math.radians(angle)) * weight for angle, weight in angles)
        if abs(x) < 1e-9 and abs(y) < 1e-9:
            return 0.0
        return math.degrees(math.atan2(y, x)) % 360.0


directional_analyzer = DirectionalAnalyzer()
