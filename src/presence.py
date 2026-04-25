"""
节奏界面门控：未进入四鼓节奏页时抑制一切按键，避免主界面 HSV 误检乱按。

原理：在四条轨道对应的画面底部各取一块正方形灰度图，计算 Laplacian 方差（边缘/纹理强度）。
四块同时超过阈值，并连续多帧保持，才认为「鼓位 UI 在场」，允许后续音符触发按键。
"""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from src.lanes import LaneLayout

logger = logging.getLogger(__name__)


class SceneGate:
    """带滞后的「是否允许按键」门控。"""

    def __init__(self, cfg: dict[str, Any]) -> None:
        p = cfg.get("presence") or {}
        self.enabled = bool(p.get("enabled", True))
        self.drum_y_frac = float(p.get("drum_center_y_frac", 0.88))
        self.patch_half_w_frac = float(p.get("patch_half_width_frac", 0.032))
        self.min_laplace_var = float(p.get("min_laplace_variance", 95.0))
        self.min_mean_gray = float(p.get("min_mean_gray", 18.0))
        self.max_mean_gray = float(p.get("max_mean_gray", 255.0))
        self.arm_after_good_frames = max(1, int(p.get("arm_after_good_frames", 5)))
        self.disarm_after_bad_frames = max(1, int(p.get("disarm_after_bad_frames", 5)))

        self._armed = False
        self._good_streak = 0
        self._bad_streak = 0

    def step(
        self,
        frame_bgr: NDArray[np.uint8],
        layout: LaneLayout,
    ) -> tuple[bool, dict[str, Any]]:
        """
        返回 (是否允许按键, 调试信息)。
        未启用门控时始终 (True, {...})。
        """
        if not self.enabled:
            return True, {
                "enabled": False,
                "armed": True,
                "transitioned": False,
                "per_ok": [True, True, True, True],
                "lap_vars": [0.0, 0.0, 0.0, 0.0],
                "mean_grays": [0.0, 0.0, 0.0, 0.0],
            }

        per_ok, lap_vars, mean_grays = self._measure_patches(frame_bgr, layout)
        all_ok = all(per_ok)
        prev_armed = self._armed

        if all_ok:
            self._good_streak += 1
            self._bad_streak = 0
        else:
            self._bad_streak += 1
            self._good_streak = 0

        if self._armed:
            if self._bad_streak >= self.disarm_after_bad_frames:
                self._armed = False
        else:
            if self._good_streak >= self.arm_after_good_frames:
                self._armed = True

        transitioned = prev_armed != self._armed
        info: dict[str, Any] = {
            "enabled": True,
            "armed": self._armed,
            "transitioned": transitioned,
            "all_ok_this_frame": all_ok,
            "per_ok": per_ok,
            "lap_vars": lap_vars,
            "mean_grays": mean_grays,
            "good_streak": self._good_streak,
            "bad_streak": self._bad_streak,
        }
        return self._armed, info

    def _measure_patches(
        self,
        frame_bgr: NDArray[np.uint8],
        layout: LaneLayout,
    ) -> tuple[list[bool], list[float], list[float]]:
        h, w = layout.frame_h, layout.frame_w
        cy = int(self.drum_y_frac * h)
        half = max(4, int(self.patch_half_w_frac * w))
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

        per_ok: list[bool] = []
        lap_vars: list[float] = []
        mean_grays: list[float] = []

        for i in range(4):
            cx = layout.center_x[i]
            x0 = max(0, cx - half)
            x1 = min(w, cx + half)
            y0 = max(0, cy - half)
            y1 = min(h, cy + half)
            if x1 <= x0 or y1 <= y0:
                lap_vars.append(0.0)
                mean_grays.append(0.0)
                per_ok.append(False)
                continue

            roi = gray[y0:y1, x0:x1]
            lap = cv2.Laplacian(roi, cv2.CV_64F)
            var = float(lap.var())
            mg = float(np.mean(roi))
            lap_vars.append(var)
            mean_grays.append(mg)
            ok = (
                var >= self.min_laplace_var
                and self.min_mean_gray <= mg <= self.max_mean_gray
            )
            per_ok.append(ok)

        return per_ok, lap_vars, mean_grays
