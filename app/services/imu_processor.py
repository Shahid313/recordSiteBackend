import math
from typing import Dict, List


class IMUProcessor:
    """Convert IMU sensor readings into 2D position estimates."""

    def __init__(self) -> None:
        self.gravity = 9.81

    def calculate_positions_from_imu(self, imu_data: List[Dict], frame_rate: float = 1.0) -> List[Dict]:
        """
        Convert IMU readings to 2D positions using yaw and double integration.

        Drift is damped with velocity decay; final fusion uses SfM updates to
        correct this inertial estimate.
        """
        if not imu_data:
            return []

        positions: List[Dict] = []
        x, y = 0.0, 0.0
        vx, vy = 0.0, 0.0
        orientation = float(imu_data[0].get("orientation", 0.0))
        fallback_dt = 1.0 / frame_rate if frame_rate and frame_rate > 0 else 1.0
        last_ts = float(imu_data[0].get("timestamp", 0.0))

        for i, imu in enumerate(imu_data):
            ts = float(imu.get("timestamp", i * fallback_dt))
            dt = ts - last_ts if i > 0 else fallback_dt
            if dt <= 0 or dt > 5.0:
                dt = fallback_dt

            gyro_z = float(imu.get("gyro_z", 0.0))
            if "orientation" in imu:
                orientation = float(imu["orientation"]) % 360.0
            else:
                orientation = (orientation + gyro_z * dt) % 360.0

            accel_x = float(imu.get("accel_x", 0.0))
            accel_y = float(imu.get("accel_y", 0.0))
            accel_y_corrected = accel_y + self.gravity

            rad = math.radians(orientation)
            ax_world = accel_x * math.cos(rad) - accel_y_corrected * math.sin(rad)
            ay_world = accel_x * math.sin(rad) + accel_y_corrected * math.cos(rad)

            vx = (vx + ax_world * dt) * 0.98
            vy = (vy + ay_world * dt) * 0.98
            x += vx * dt
            y += vy * dt

            positions.append(
                {
                    "timestamp": ts,
                    "x": x,
                    "y": y,
                    "orientation": orientation,
                    "velocity": math.sqrt(vx**2 + vy**2),
                }
            )
            last_ts = ts

        return positions

    def positions_for_timestamps(self, imu_positions: List[Dict], timestamps: List[float]) -> List[Dict]:
        """Sample/interpolate IMU positions at panorama timestamps."""
        if not imu_positions or not timestamps:
            return []

        sampled: List[Dict] = []
        idx = 0
        for ts in timestamps:
            while idx + 1 < len(imu_positions) and float(imu_positions[idx + 1]["timestamp"]) <= ts:
                idx += 1

            current = imu_positions[idx]
            if idx + 1 >= len(imu_positions):
                sampled.append(dict(current, timestamp=float(ts)))
                continue

            nxt = imu_positions[idx + 1]
            t0 = float(current["timestamp"])
            t1 = float(nxt["timestamp"])
            ratio = 0.0 if t1 <= t0 else (float(ts) - t0) / (t1 - t0)
            ratio = max(0.0, min(1.0, ratio))

            sampled.append(
                {
                    "timestamp": float(ts),
                    "x": self._lerp(float(current["x"]), float(nxt["x"]), ratio),
                    "y": self._lerp(float(current["y"]), float(nxt["y"]), ratio),
                    "orientation": self._lerp_angle(
                        float(current.get("orientation", 0.0)),
                        float(nxt.get("orientation", 0.0)),
                        ratio,
                    ),
                    "velocity": self._lerp(float(current.get("velocity", 0.0)), float(nxt.get("velocity", 0.0)), ratio),
                }
            )
        return sampled

    def _lerp(self, a: float, b: float, ratio: float) -> float:
        return a + (b - a) * ratio

    def _lerp_angle(self, a: float, b: float, ratio: float) -> float:
        delta = (b - a + 180.0) % 360.0 - 180.0
        return (a + delta * ratio) % 360.0


imu_processor = IMUProcessor()
