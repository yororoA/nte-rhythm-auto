"""
游戏场景状态检测：识别 OTHER / SONG_SELECT / PLAYING / RESULTS 四种状态。

原理：
1. 模板匹配：用 cv2.matchTemplate 比对选歌/结算/演奏界面的特征模板图片，
    精确识别 SONG_SELECT / RESULTS / PLAYING。
2. Laplacian 方差检测：在四条轨道对应的画面底部各取一块正方形灰度图，
    计算 Laplacian 方差，作为 PLAYING 的兜底识别。

状态分类逻辑（优先级从高到低）：
  song_select 模板匹配成功            → SONG_SELECT
  results 模板匹配成功                 → RESULTS
  playing 模板匹配成功                 → PLAYING
  四鼓位 Laplacian 方差全部达标         → PLAYING
  以上都不满足                          → OTHER
"""

from __future__ import annotations

import logging
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from src.assets import list_scene_templates
from src.lanes import LaneLayout

logger = logging.getLogger(__name__)

STATE_OTHER = "other"
STATE_SONG_SELECT = "song_select"
STATE_PLAYING = "playing"
STATE_RESULTS = "results"


class SceneGate:
    """带滞后的场景状态分类器。"""

    def __init__(self, cfg: dict[str, Any]) -> None:
        p = cfg.get("presence") or {}
        self.enabled = bool(p.get("enabled", True))
        self.drum_y_frac = float(p.get("drum_center_y_frac", 0.88))
        self.patch_half_w_frac = float(p.get("patch_half_width_frac", 0.032))
        self.min_laplace_var = float(p.get("min_laplace_variance", 95.0))
        self.min_mean_gray = float(p.get("min_mean_gray", 18.0))
        self.max_mean_gray = float(p.get("max_mean_gray", 255.0))
        self.arm_after_good_frames = max(1, int(p.get("arm_after_good_frames", 1)))
        self.disarm_after_bad_frames = max(1, int(p.get("disarm_after_bad_frames", 5)))

        sc = cfg.get("scene") or {}
        self._song_select_thresh = float(sc.get("song_select_match_threshold", 0.75))
        self._results_thresh = float(sc.get("results_match_threshold", 0.75))
        self._playing_thresh = float(sc.get("playing_match_threshold", 0.75))
        self._state_confirm_frames = max(1, int(sc.get("state_confirm_frames", 2)))
        self._song_select_tpls = self._load_templates("song_select")
        self._results_tpls = self._load_templates("results")
        self._playing_tpls = self._load_templates("playing")

        self._state: str = STATE_OTHER
        self._target_state: str = STATE_OTHER
        self._state_streak: int = 0
        self._armed = False
        self._good_streak = 0
        self._bad_streak = 0

    @property
    def state(self) -> str:
        return self._state

    def step(
        self,
        frame_bgr: NDArray[np.uint8],
        layout: LaneLayout,
    ) -> tuple[str, dict[str, Any]]:
        if not self.enabled:
            return STATE_PLAYING, {
                "enabled": False,
                "state": STATE_PLAYING,
                "armed": True,
                "transitioned": False,
                "state_transitioned": False,
                "per_ok": [True, True, True, True],
                "lap_vars": [0.0, 0.0, 0.0, 0.0],
                "mean_grays": [0.0, 0.0, 0.0, 0.0],
                "song_select_tpl_val": 0.0,
                "results_tpl_val": 0.0,
                "playing_tpl_val": 0.0,
            }

        per_ok, lap_vars, mean_grays = self._measure_patches(frame_bgr, layout)
        ss_ok, ss_val, ss_name = self._match_templates(
            frame_bgr, self._song_select_tpls, self._song_select_thresh
        )
        rs_ok, rs_val, rs_name = self._match_templates(
            frame_bgr, self._results_tpls, self._results_thresh
        )
        pl_ok, pl_val, pl_name = self._match_templates(
            frame_bgr, self._playing_tpls, self._playing_thresh
        )

        drums_present = all(per_ok)
        if ss_ok:
            target = STATE_SONG_SELECT
        elif rs_ok:
            target = STATE_RESULTS
        elif pl_ok:
            target = STATE_PLAYING
        elif drums_present:
            target = STATE_PLAYING
        else:
            target = STATE_OTHER

        prev_state = self._state
        if target != self._target_state:
            self._target_state = target
            self._state_streak = 1
        else:
            self._state_streak += 1

        if target != self._state and self._state_streak >= self._state_confirm_frames:
            self._state = target
            self._state_streak = 0

        state_transitioned = prev_state != self._state
        scene_playing = self._state == STATE_PLAYING

        if scene_playing:
            self._good_streak += 1
            self._bad_streak = 0
        else:
            self._bad_streak += 1
            self._good_streak = 0

        prev_armed = self._armed
        if self._armed:
            if not scene_playing and self._bad_streak >= self.disarm_after_bad_frames:
                self._armed = False
        else:
            if scene_playing and self._good_streak >= self.arm_after_good_frames:
                self._armed = True

        armed_transitioned = prev_armed != self._armed

        info: dict[str, Any] = {
            "enabled": True,
            "state": self._state,
            "armed": self._armed,
            "transitioned": armed_transitioned,
            "state_transitioned": state_transitioned,
            "per_ok": per_ok,
            "lap_vars": lap_vars,
            "mean_grays": mean_grays,
            "good_streak": self._good_streak,
            "bad_streak": self._bad_streak,
            "song_select_tpl_ok": ss_ok,
            "song_select_tpl_val": ss_val,
            "song_select_tpl_name": ss_name,
            "results_tpl_ok": rs_ok,
            "results_tpl_val": rs_val,
            "results_tpl_name": rs_name,
            "playing_tpl_ok": pl_ok,
            "playing_tpl_val": pl_val,
            "playing_tpl_name": pl_name,
        }
        return self._state, info

    def _load_templates(self, kind: str) -> list[tuple[str, NDArray[np.uint8]]]:
        templates: list[tuple[str, NDArray[np.uint8]]] = []
        for name, tpl_path in list_scene_templates(kind):
            img = cv2.imread(str(tpl_path), cv2.IMREAD_COLOR)
            if img is None:
                img_bytes = np.fromfile(str(tpl_path), dtype=np.uint8)
                img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
            if img is None:
                logger.warning("无法读取场景模板图片：%s", tpl_path)
                continue
            th, tw = img.shape[:2]
            logger.info("已加载场景模板：%s/%s (%dx%d)", kind, tpl_path.name, tw, th)
            templates.append((name, img))
        if not templates:
            logger.debug("未找到场景模板：%s", kind)
        return templates

    def _match_templates(
        self,
        frame_bgr: NDArray[np.uint8],
        templates: list[tuple[str, NDArray[np.uint8]]],
        threshold: float,
    ) -> tuple[bool, float, str]:
        if not templates:
            return False, 0.0, ""
        fh, fw = frame_bgr.shape[:2]
        best_val = 0.0
        best_name = ""
        for name, template in templates:
            th, tw = template.shape[:2]
            if th > fh or tw > fw:
                continue
            result = cv2.matchTemplate(frame_bgr, template, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, _ = cv2.minMaxLoc(result)
            if max_val > best_val:
                best_val = float(max_val)
                best_name = name
        return best_val >= threshold, best_val, best_name

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