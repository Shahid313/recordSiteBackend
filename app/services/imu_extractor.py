import json
import logging
import math
import subprocess
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class IMUExtractor:
    """Extract IMU (gyroscope, accelerometer) metadata from 360 video files."""

    def extract_imu_data(self, video_path: str) -> List[Dict[str, float]]:
        """
        Extract IMU data from video metadata.

        Returns readings shaped as:
        {
            "timestamp": seconds,
            "gyro_x": deg/s,
            "gyro_y": deg/s,
            "gyro_z": deg/s,
            "accel_x": m/s^2,
            "accel_y": m/s^2,
            "accel_z": m/s^2,
            "orientation": optional yaw degrees
        }
        """
        metadata = self._read_ffprobe_metadata(video_path)
        imu_data: List[Dict[str, float]] = []

        if metadata:
            tags = metadata.get("format", {}).get("tags", {})
            if isinstance(tags, dict):
                imu_data = self._extract_from_format_tags(tags)

            if not imu_data:
                for stream in metadata.get("streams", []) or []:
                    side_data = stream.get("side_data_list")
                    if side_data:
                        imu_data = self._extract_from_side_data(side_data)
                        if imu_data:
                            break

        if not imu_data:
            imu_data = self._extract_with_exiftool(video_path)

        if not imu_data:
            logger.warning("No IMU metadata found in %s; estimating motion from optical flow", video_path)
            imu_data = self._estimate_from_motion(video_path)

        return self._with_orientation(imu_data)

    def _read_ffprobe_metadata(self, video_path: str) -> Dict[str, Any]:
        cmd = [
            "ffprobe",
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            video_path,
        ]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            logger.warning("ffprobe is not installed; skipping ffprobe IMU extraction")
            return {}

        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("ffprobe metadata read failed for %s: %s", video_path, result.stderr.strip())
            return {}

        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError:
            logger.warning("ffprobe returned invalid JSON for %s", video_path)
            return {}

    def _extract_from_format_tags(self, tags: Dict[str, Any]) -> List[Dict[str, float]]:
        """Extract IMU readings from common format-level metadata tags."""
        candidates = [
            "camera-motion",
            "CameraMotion",
            "gyro",
            "Gyroscope",
            "accelerometer",
            "Accelerometer",
        ]
        for key in candidates:
            raw = tags.get(key)
            if not raw:
                continue
            parsed = self._parse_jsonish_motion(raw)
            if parsed:
                return parsed
        return []

    def _extract_from_side_data(self, side_data: List[Dict[str, Any]]) -> List[Dict[str, float]]:
        """Extract IMU from stream side data when cameras expose it there."""
        for item in side_data:
            parsed = self._parse_jsonish_motion(item)
            if parsed:
                return parsed
        return []

    def _extract_with_exiftool(self, video_path: str) -> List[Dict[str, float]]:
        """Fallback: use exiftool's embedded stream extraction when available."""
        cmd = ["exiftool", "-ee", "-G3", "-api", "LargeFileSupport=1", "-json", video_path]
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=False)
        except FileNotFoundError:
            logger.warning("exiftool is not installed; skipping exiftool IMU extraction")
            return []

        if result.returncode != 0 or not result.stdout.strip():
            logger.warning("exiftool IMU extraction failed for %s: %s", video_path, result.stderr.strip())
            return []

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return []

        if not data:
            return []
        return self._parse_exiftool_data(data[0])

    def _parse_exiftool_data(self, data: Dict[str, Any]) -> List[Dict[str, float]]:
        """Parse several common exiftool key patterns for gyro/accelerometer data."""
        motion_keys = [
            key
            for key in data
            if any(token in key.lower() for token in ("gyro", "accel", "cameraorientation", "cameramotion"))
        ]
        if not motion_keys:
            return []

        parsed = self._parse_jsonish_motion({key: data[key] for key in motion_keys})
        if parsed:
            return parsed

        # Some exiftool outputs are flat arrays by field. Pair samples by index.
        gyro = self._extract_vector_series(data, ("gyro", "gyroscope"))
        accel = self._extract_vector_series(data, ("accel", "accelerometer"))
        count = max(len(gyro), len(accel))
        readings: List[Dict[str, float]] = []
        for i in range(count):
            g = gyro[i] if i < len(gyro) else (0.0, 0.0, 0.0)
            a = accel[i] if i < len(accel) else (0.0, 0.0, 0.0)
            readings.append(
                {
                    "timestamp": float(i),
                    "gyro_x": g[0],
                    "gyro_y": g[1],
                    "gyro_z": g[2],
                    "accel_x": a[0],
                    "accel_y": a[1],
                    "accel_z": a[2],
                }
            )
        return readings

    def _parse_jsonish_motion(self, raw: Any) -> List[Dict[str, float]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                return []

        if isinstance(raw, dict):
            for key in ("samples", "data", "motion", "CameraMotion", "camera-motion"):
                if key in raw:
                    parsed = self._parse_jsonish_motion(raw[key])
                    if parsed:
                        return parsed

            if any(k.lower().startswith(("gyro", "accel")) for k in raw.keys()):
                reading = self._reading_from_mapping(raw, 0.0)
                return [reading] if reading else []

            return []

        if isinstance(raw, list):
            readings: List[Dict[str, float]] = []
            for idx, item in enumerate(raw):
                if isinstance(item, dict):
                    reading = self._reading_from_mapping(item, float(idx))
                    if reading:
                        readings.append(reading)
                elif isinstance(item, (list, tuple)) and len(item) >= 7:
                    readings.append(
                        {
                            "timestamp": self._float_or_default(item[0], float(idx)),
                            "gyro_x": self._float_or_default(item[1]),
                            "gyro_y": self._float_or_default(item[2]),
                            "gyro_z": self._float_or_default(item[3]),
                            "accel_x": self._float_or_default(item[4]),
                            "accel_y": self._float_or_default(item[5]),
                            "accel_z": self._float_or_default(item[6]),
                        }
                    )
            return readings

        return []

    def _reading_from_mapping(self, item: Dict[str, Any], default_timestamp: float) -> Optional[Dict[str, float]]:
        gyro = item.get("gyro") or item.get("gyroscope") or item.get("Gyroscope") or [0.0, 0.0, 0.0]
        accel = item.get("accel") or item.get("accelerometer") or item.get("Accelerometer") or [0.0, 0.0, 0.0]

        if isinstance(gyro, str):
            gyro = self._parse_numeric_triplet(gyro)
        if isinstance(accel, str):
            accel = self._parse_numeric_triplet(accel)

        if not isinstance(gyro, (list, tuple)):
            gyro = [item.get("gyro_x", item.get("GyroX", 0.0)), item.get("gyro_y", item.get("GyroY", 0.0)), item.get("gyro_z", item.get("GyroZ", 0.0))]
        if not isinstance(accel, (list, tuple)):
            accel = [item.get("accel_x", item.get("AccelX", 0.0)), item.get("accel_y", item.get("AccelY", 0.0)), item.get("accel_z", item.get("AccelZ", 0.0))]

        return {
            "timestamp": self._float_or_default(
                item.get("timestamp", item.get("time", item.get("Time", default_timestamp))),
                default_timestamp,
            ),
            "gyro_x": self._float_or_default(gyro[0] if len(gyro) > 0 else 0.0),
            "gyro_y": self._float_or_default(gyro[1] if len(gyro) > 1 else 0.0),
            "gyro_z": self._float_or_default(gyro[2] if len(gyro) > 2 else 0.0),
            "accel_x": self._float_or_default(accel[0] if len(accel) > 0 else 0.0),
            "accel_y": self._float_or_default(accel[1] if len(accel) > 1 else 0.0),
            "accel_z": self._float_or_default(accel[2] if len(accel) > 2 else 0.0),
        }

    def _extract_vector_series(self, data: Dict[str, Any], tokens: tuple[str, ...]) -> List[tuple[float, float, float]]:
        series: List[tuple[float, float, float]] = []
        for key, value in data.items():
            lower = key.lower()
            if not any(token in lower for token in tokens):
                continue
            if isinstance(value, str):
                triplet = self._parse_numeric_triplet(value)
                if triplet:
                    series.append(tuple(triplet))
            elif isinstance(value, (list, tuple)):
                if value and isinstance(value[0], (list, tuple)):
                    for row in value:
                        if len(row) >= 3:
                            series.append(
                                (
                                    self._float_or_default(row[0]),
                                    self._float_or_default(row[1]),
                                    self._float_or_default(row[2]),
                                )
                            )
                elif len(value) >= 3:
                    series.append(
                        (
                            self._float_or_default(value[0]),
                            self._float_or_default(value[1]),
                            self._float_or_default(value[2]),
                        )
                    )
        return series

    def _estimate_from_motion(self, video_path: str) -> List[Dict[str, float]]:
        """
        Estimate rough IMU-like readings from optical flow when metadata is absent.

        This is less accurate than embedded sensor metadata, but it preserves a
        graceful fallback for uploaded videos without motion tracks.
        """
        import cv2

        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        stride = max(1, int(fps))
        max_samples = 600
        if total_frames > 0:
            stride = max(stride, math.ceil(total_frames / max_samples))

        imu_estimates: List[Dict[str, float]] = []
        prev_gray = None
        frame_idx = 0

        try:
            while True:
                ret, frame = cap.read()
                if not ret or frame is None:
                    break

                if frame_idx % stride != 0:
                    frame_idx += 1
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                if prev_gray is not None:
                    flow = cv2.calcOpticalFlowFarneback(
                        prev_gray,
                        gray,
                        None,
                        pyr_scale=0.5,
                        levels=3,
                        winsize=15,
                        iterations=3,
                        poly_n=5,
                        poly_sigma=1.2,
                        flags=0,
                    )
                    avg_flow_x = float(flow[:, :, 0].mean())
                    avg_flow_y = float(flow[:, :, 1].mean())
                    gyro_estimate = math.degrees(math.atan2(avg_flow_y, avg_flow_x))
                    imu_estimates.append(
                        {
                            "timestamp": frame_idx / fps,
                            "gyro_x": 0.0,
                            "gyro_y": 0.0,
                            "gyro_z": gyro_estimate,
                            "accel_x": avg_flow_x * 10.0,
                            "accel_y": avg_flow_y * 10.0,
                            "accel_z": 0.0,
                            "estimated": 1.0,
                        }
                    )
                prev_gray = gray
                frame_idx += 1
        finally:
            cap.release()

        return imu_estimates

    def _with_orientation(self, readings: List[Dict[str, float]]) -> List[Dict[str, float]]:
        if not readings:
            return []

        out: List[Dict[str, float]] = []
        yaw = 0.0
        last_ts = readings[0].get("timestamp", 0.0)
        for reading in readings:
            ts = float(reading.get("timestamp", last_ts))
            dt = max(0.0, ts - last_ts)
            yaw = (yaw + float(reading.get("gyro_z", 0.0)) * dt) % 360.0
            enriched = dict(reading)
            enriched.setdefault("orientation", yaw)
            out.append(enriched)
            last_ts = ts
        return out

    def _parse_numeric_triplet(self, value: str) -> List[float]:
        cleaned = value.replace(",", " ").replace(";", " ")
        nums = []
        for token in cleaned.split():
            try:
                nums.append(float(token))
            except ValueError:
                continue
            if len(nums) == 3:
                break
        return nums

    def _float_or_default(self, value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default


imu_extractor = IMUExtractor()
