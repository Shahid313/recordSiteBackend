from typing import Dict, List, Optional

import numpy as np

try:
    from filterpy.kalman import KalmanFilter
except ImportError:  # Allows the app to boot before requirements are installed locally.
    KalmanFilter = None


class _SimpleKalmanFilter:
    def __init__(self, dim_x: int, dim_z: int) -> None:
        self.x = np.zeros(dim_x)
        self.F = np.eye(dim_x)
        self.H = np.zeros((dim_z, dim_x))
        self.R = np.eye(dim_z)
        self.Q = np.eye(dim_x)
        self.P = np.eye(dim_x)

    def predict(self) -> None:
        self.x = self.F @ self.x
        self.P = self.F @ self.P @ self.F.T + self.Q

    def update(self, z: np.ndarray) -> None:
        y = z - (self.H @ self.x)
        s = self.H @ self.P @ self.H.T + self.R
        k = self.P @ self.H.T @ np.linalg.inv(s)
        self.x = self.x + k @ y
        identity = np.eye(self.P.shape[0])
        self.P = (identity - k @ self.H) @ self.P


class SensorFusion:
    """Fuse SfM and IMU positions with a Kalman filter."""

    def fuse_sfm_and_imu(self, sfm_positions: List[Dict], imu_positions: List[Dict]) -> List[Dict]:
        """
        Fuse SfM and IMU positions.

        SfM measurements correct drift when confidence is usable; IMU keeps the
        path moving through low-texture or poorly lit sections where SfM is weak.
        """
        if not imu_positions and not sfm_positions:
            return []
        if not imu_positions:
            return [self._position_with_source(pos, "sfm") for pos in sfm_positions]
        if not sfm_positions:
            return [self._position_with_source(pos, "imu") for pos in imu_positions]

        kf = self._build_filter()
        first = sfm_positions[0] if sfm_positions[0].get("confidence", 0.0) > 0.5 else imu_positions[0]
        kf.x = np.array([float(first.get("x", 0.0)), float(first.get("y", 0.0)), 0.0, 0.0])

        fused: List[Dict] = []
        max_len = max(len(sfm_positions), len(imu_positions))
        last_timestamp: Optional[float] = None

        for i in range(max_len):
            imu_pos = imu_positions[i] if i < len(imu_positions) else imu_positions[-1]
            sfm_pos = sfm_positions[i] if i < len(sfm_positions) else None
            timestamp = float(imu_pos.get("timestamp", sfm_pos.get("timestamp", i) if sfm_pos else i))

            dt = 1.0 if last_timestamp is None else max(0.001, min(5.0, timestamp - last_timestamp))
            self._set_dt(kf, dt)

            if i > 0 and i < len(imu_positions):
                prev_imu = imu_positions[i - 1]
                kf.x[2] = (float(imu_pos.get("x", 0.0)) - float(prev_imu.get("x", 0.0))) / dt
                kf.x[3] = (float(imu_pos.get("y", 0.0)) - float(prev_imu.get("y", 0.0))) / dt

            kf.predict()

            sfm_confidence = float(sfm_pos.get("confidence", 0.0)) if sfm_pos else 0.0
            if sfm_pos and sfm_confidence > 0.5:
                kf.R = np.eye(2) * max(0.5, 8.0 * (1.0 - min(0.95, sfm_confidence)))
                measurement = np.array([float(sfm_pos["x"]), float(sfm_pos["y"])])
                source = "sfm"
                orientation = float(sfm_pos.get("orientation", imu_pos.get("orientation", 0.0)))
            else:
                kf.R = np.eye(2) * 20.0
                measurement = np.array([float(imu_pos.get("x", 0.0)), float(imu_pos.get("y", 0.0))])
                source = "imu"
                orientation = float(imu_pos.get("orientation", 0.0))

            kf.update(measurement)
            fused.append(
                {
                    "timestamp": timestamp,
                    "x": float(kf.x[0]),
                    "y": float(kf.x[1]),
                    "orientation": orientation,
                    "source": source,
                    "sfm_confidence": sfm_confidence,
                }
            )
            last_timestamp = timestamp

        return fused

    def _build_filter(self):
        filter_cls = KalmanFilter or _SimpleKalmanFilter
        kf = filter_cls(dim_x=4, dim_z=2)
        kf.x = np.array([0.0, 0.0, 0.0, 0.0])
        self._set_dt(kf, 1.0)
        kf.H = np.array([[1, 0, 0, 0], [0, 1, 0, 0]])
        kf.R = np.eye(2) * 5.0
        kf.Q = np.eye(4) * 0.1
        kf.P = np.eye(4) * 10.0
        return kf

    def _set_dt(self, kf, dt: float) -> None:
        kf.F = np.array([[1, 0, dt, 0], [0, 1, 0, dt], [0, 0, 1, 0], [0, 0, 0, 1]])

    def _position_with_source(self, pos: Dict, source: str) -> Dict:
        return {
            "timestamp": float(pos.get("timestamp", 0.0)),
            "x": float(pos.get("x", 0.0)),
            "y": float(pos.get("y", 0.0)),
            "orientation": float(pos.get("orientation", 0.0)),
            "source": source,
            "sfm_confidence": float(pos.get("confidence", 0.0)),
        }


sensor_fusion = SensorFusion()
