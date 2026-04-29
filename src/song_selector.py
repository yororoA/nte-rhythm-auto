"""自动选歌：在选歌界面通过模板匹配找到目标歌曲并点击选中，再点击"开始演奏"。"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from numpy.typing import NDArray

from src.assets import asset_path, list_song_templates
from src.lanes import LaneLayout
from src.mouse import MouseClicker

logger = logging.getLogger(__name__)

_SEL_IDLE = "idle"
_SEL_SEARCHING = "searching"
_SEL_SCROLLING = "scrolling"
_SEL_CLICKING_SONG = "clicking_song"
_SEL_CLICKING_START = "clicking_start"
_SEL_WAITING = "waiting"
_SEL_DONE = "done"
_SEL_FAILED = "failed"


class SongSelector:
    def __init__(self, cfg: dict[str, Any]) -> None:
        sc = cfg.get("song_select") or {}
        self.enabled = bool(sc.get("enabled", False))
        self._song_name = str(sc.get("song_name", ""))
        self._scroll_area_x_frac = float(sc.get("scroll_area_x_frac", 0.35))
        self._scroll_area_y_frac = float(sc.get("scroll_area_y_frac", 0.50))
        self._scroll_delta = int(sc.get("scroll_delta", -3))
        self._max_scroll_attempts = max(1, int(sc.get("max_scroll_attempts", 30)))
        self._match_threshold = float(sc.get("match_threshold", 0.75))
        self._click_delay = float(sc.get("click_delay_sec", 0.5))
        self._start_delay = float(sc.get("start_delay_sec", 0.8))

        scene_cfg = cfg.get("scene") or {}
        self._start_btn_x_frac = float(scene_cfg.get("start_button_x_frac", 0.855))
        self._start_btn_y_frac = float(scene_cfg.get("start_button_y_frac", 0.855))

        self._template: NDArray[np.uint8] | None = None
        self._state: str = _SEL_IDLE
        self._scroll_attempts: int = 0
        self._last_action_time: float = 0.0
        self._match_loc: tuple[int, int] | None = None

        if self.enabled and self._song_name:
            self._load_template_by_name(self._song_name)
        elif self.enabled and not self._song_name:
            logger.warning("自动选歌已启用但未指定歌曲名称")
            self.enabled = False

    def _load_template_by_name(self, name: str) -> bool:
        templates = list_song_templates()
        for stem, path in templates:
            if stem == name:
                return self._load_template_file(path)
        logger.warning("未找到歌曲模板: %s (可用: %s)", name, [s for s, _ in templates])
        self.enabled = False
        return False

    def _load_template_file(self, p: Path) -> bool:
        if not p.is_file():
            logger.warning("歌曲模板文件不存在: %s", p)
            self.enabled = False
            return False
        img_bytes = np.fromfile(str(p), dtype=np.uint8)
        img = cv2.imdecode(img_bytes, cv2.IMREAD_COLOR)
        if img is None:
            logger.warning("无法读取歌曲模板图片： %s", p)
            self.enabled = False
            return False
        self._template = img
        th, tw = img.shape[:2]
        logger.info("已加载歌曲模板： %s (%dx%d)", p.name, tw, th)
        return True

    def select_song(self, name: str) -> bool:
        self._song_name = name
        ok = self._load_template_by_name(name)
        if ok:
            self.enabled = True
            self.reset()
        return ok

    def reset(self) -> None:
        self._state = _SEL_IDLE
        self._scroll_attempts = 0
        self._last_action_time = 0.0
        self._match_loc = None

    @property
    def state(self) -> str:
        return self._state

    @property
    def song_name(self) -> str:
        return self._song_name

    def step(
        self,
        frame_bgr: NDArray[np.uint8],
        layout: LaneLayout,
        mouse: MouseClicker,
        *,
        client_origin: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        if not self.enabled or self._template is None:
            return {"state": self._state, "action": "disabled"}

        now = time.perf_counter()
        h, w = frame_bgr.shape[:2]

        if self._state == _SEL_IDLE:
            self._state = _SEL_SEARCHING
            self._scroll_attempts = 0

        if self._state == _SEL_SEARCHING:
            match = self._find_template(frame_bgr)
            if match is not None:
                self._match_loc = match
                self._state = _SEL_CLICKING_SONG
                logger.info(
                    "歌曲模板匹配成功: 位置=(%d,%d), 将点击选中",
                    match[0], match[1],
                )
            elif self._scroll_attempts < self._max_scroll_attempts:
                self._state = _SEL_SCROLLING
            else:
                self._state = _SEL_FAILED
                logger.warning(
                    "已滚动 %d 次仍未找到目标歌曲，选歌失败",
                    self._scroll_attempts,
                )
            return {"state": self._state, "scroll_attempts": self._scroll_attempts}

        if self._state == _SEL_SCROLLING:
            if now - self._last_action_time < self._click_delay:
                return {"state": self._state, "action": "waiting"}
            sx = int(self._scroll_area_x_frac * w)
            sy = int(self._scroll_area_y_frac * h)
            mouse.scroll(sx, sy, self._scroll_delta, client_origin=client_origin)
            self._scroll_attempts += 1
            self._last_action_time = now
            self._state = _SEL_SEARCHING
            logger.debug("滚动搜索: 第 %d 次", self._scroll_attempts)
            return {"state": self._state, "action": "scroll", "scroll_attempts": self._scroll_attempts}

        if self._state == _SEL_CLICKING_SONG:
            if now - self._last_action_time < self._click_delay:
                return {"state": self._state, "action": "waiting"}
            if self._match_loc is not None:
                mx, my = self._match_loc
                mouse.click(mx, my, client_origin=client_origin)
                self._last_action_time = now
                self._state = _SEL_CLICKING_START
                logger.info("已点击目标歌曲位置 (%d,%d)，等待后点击开始演奏", mx, my)
            else:
                self._state = _SEL_SEARCHING
            return {"state": self._state, "action": "click_song"}

        if self._state == _SEL_CLICKING_START:
            if now - self._last_action_time < self._start_delay:
                return {"state": self._state, "action": "waiting"}
            bx = int(self._start_btn_x_frac * w)
            by = int(self._start_btn_y_frac * h)
            mouse.click(bx, by, client_origin=client_origin)
            self._last_action_time = now
            self._state = _SEL_WAITING
            logger.info("已点击「开始演奏」按钮 (%d,%d)", bx, by)
            return {"state": self._state, "action": "click_start"}

        if self._state == _SEL_WAITING:
            return {"state": self._state, "action": "waiting"}

        if self._state == _SEL_DONE:
            return {"state": self._state, "action": "done"}

        if self._state == _SEL_FAILED:
            return {"state": self._state, "action": "failed"}

        return {"state": self._state, "action": "unknown"}

    def _find_template(
        self, frame_bgr: NDArray[np.uint8]
    ) -> tuple[int, int] | None:
        if self._template is None:
            return None
        th, tw = self._template.shape[:2]
        fh, fw = frame_bgr.shape[:2]
        if th > fh or tw > fw:
            return None
        result = cv2.matchTemplate(frame_bgr, self._template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val >= self._match_threshold:
            cx = max_loc[0] + tw // 2
            cy = max_loc[1] + th // 2
            return cx, cy
        return None
