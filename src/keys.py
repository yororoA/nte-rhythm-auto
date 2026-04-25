"""按键输出：前台 pynput / 后台 Win32 PostMessage 或 SendMessage（消息进目标 HWND，不占全局键盘焦点）。"""

from __future__ import annotations

import logging
import time
from typing import Any

import win32api
import win32con
import win32gui

logger = logging.getLogger(__name__)

# 字母键虚拟键码（与 ASCII 大写一致）
_VK = {"d": 0x44, "f": 0x46, "j": 0x4A, "k": 0x4B}


def _lparam_keydown(vk: int) -> int:
    scan = win32api.MapVirtualKey(vk, 0) & 0xFF
    return 1 | (scan << 16)


def _lparam_keyup(vk: int) -> int:
    scan = win32api.MapVirtualKey(vk, 0) & 0xFF
    return 1 | (scan << 16) | (1 << 30) | (1 << 31)


class KeySender:
    def __init__(self, cfg: dict[str, Any], hwnd: int | None) -> None:
        keys_cfg = cfg.get("keys") or {}
        self._lanes = [str(x).lower() for x in (keys_cfg.get("lanes") or ["d", "f", "j", "k"])]
        if len(self._lanes) != 4:
            raise ValueError("keys.lanes 必须为 4 个键名")
        self._mode = str(keys_cfg.get("mode", "foreground")).lower()
        self._hold = float(keys_cfg.get("key_hold_sec", 0.02))
        self._delay = max(0.0, float(keys_cfg.get("press_delay_sec", 0.0)))
        self._hwnd = hwnd
        self._win32_dispatch = str(keys_cfg.get("win32_dispatch", "post")).lower()
        self._fake_activate = bool(keys_cfg.get("fake_activate", True))

        if self._mode == "foreground":
            from pynput.keyboard import Controller

            self._kb = Controller()
        else:
            self._kb = None

    def maybe_fake_activate(self) -> None:
        """部分 UE 窗口对后台键更敏感，可先发 WM_ACTIVATE（仍不一定等同前台焦点）。"""
        if self._mode != "background" or not self._hwnd or not self._fake_activate:
            return
        try:
            win32gui.SendMessage(self._hwnd, win32con.WM_ACTIVATE, win32con.WA_ACTIVE, 0)
        except Exception as e:
            logger.debug("fake_activate: %s", e)

    def lane_key_name(self, lane_index: int) -> str:
        return self._lanes[lane_index]

    def press_lane(self, lane_index: int) -> None:
        key = self._lanes[lane_index]
        vk = _VK.get(key)
        if vk is None:
            logger.warning("不支持的键位: %s", key)
            return
        if self._delay > 0:
            time.sleep(self._delay)

        if self._mode == "background":
            if not self._hwnd:
                logger.error("后台模式需要有效 hwnd")
                return
            try:
                l_down = _lparam_keydown(vk)
                l_up = _lparam_keyup(vk)
                if self._win32_dispatch == "send":
                    win32gui.SendMessage(self._hwnd, win32con.WM_KEYDOWN, vk, l_down)
                    time.sleep(self._hold)
                    win32gui.SendMessage(self._hwnd, win32con.WM_KEYUP, vk, l_up)
                    tag = "SendMessage"
                else:
                    win32gui.PostMessage(self._hwnd, win32con.WM_KEYDOWN, vk, l_down)
                    time.sleep(self._hold)
                    win32gui.PostMessage(self._hwnd, win32con.WM_KEYUP, vk, l_up)
                    tag = "PostMessage"
                logger.debug(
                    "%s 已发送 lane=%d key=%s vk=0x%02X hold=%.3fs",
                    tag,
                    lane_index,
                    key,
                    vk,
                    self._hold,
                )
            except Exception as e:
                logger.error("后台键消息失败: %s", e)
            return

        # 前台 pynput
        try:
            k = key
            self._kb.press(k)
            time.sleep(self._hold)
            self._kb.release(k)
            logger.debug(
                "pynput 已发送 lane=%d key=%s vk=0x%02X hold=%.3fs",
                lane_index,
                key,
                vk,
                self._hold,
            )
        except Exception as e:
            logger.error("pynput 按键失败: %s", e)
