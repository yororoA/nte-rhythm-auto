"""查找异环 PC 客户端窗口（HTGame.exe + UnrealWindow）。"""

from __future__ import annotations

import logging
import ctypes
from dataclasses import dataclass

import psutil
import win32gui
import win32process

logger = logging.getLogger(__name__)


def enable_dpi_awareness() -> None:
    """避免 Windows 缩放把 1920x1080 虚拟成 1536x864。"""
    try:
        if ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4)):
            return
    except Exception:
        pass
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


enable_dpi_awareness()


@dataclass
class WindowInfo:
    hwnd: int
    pid: int
    title: str
    class_name: str


def _pids_for_exe(exe_name: str) -> list[int]:
    name_lower = exe_name.lower()
    pids: list[int] = []
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            n = proc.info.get("name")
            if n and str(n).lower() == name_lower:
                pids.append(int(proc.info["pid"]))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return pids


def find_unreal_game_window(
    exe_name: str = "HTGame.exe",
    class_name: str = "UnrealWindow",
) -> WindowInfo | None:
    """
    在目标 exe 的进程下查找类名为 UnrealWindow 的可见顶层窗口。
    若有多个，取客户区面积最大的一个。
    """
    pids = set(_pids_for_exe(exe_name))
    if not pids:
        logger.warning("未找到进程: %s", exe_name)
        return None

    candidates: list[tuple[int, int, WindowInfo]] = []

    def _enum(hwnd: int, _: object) -> None:
        if not win32gui.IsWindowVisible(hwnd):
            return
        try:
            _, pid = win32process.GetWindowThreadProcessId(hwnd)
        except Exception:
            return
        if pid not in pids:
            return
        try:
            cn = win32gui.GetClassName(hwnd)
        except Exception:
            return
        if cn != class_name:
            return
        try:
            title = win32gui.GetWindowText(hwnd) or ""
        except Exception:
            title = ""
        # 过滤极小窗口（托盘等）
        try:
            left, top, right, bottom = win32gui.GetClientRect(hwnd)
            w, h = right - left, bottom - top
            area = w * h
        except Exception:
            return
        if w < 320 or h < 240:
            return
        info = WindowInfo(hwnd=hwnd, pid=pid, title=title, class_name=cn)
        candidates.append((area, hwnd, info))

    try:
        win32gui.EnumWindows(_enum, None)
    except Exception as e:
        logger.error("EnumWindows 失败: %s", e)
        return None

    if not candidates:
        logger.warning("找到进程但未找到类名为 %s 的可见窗口", class_name)
        return None

    candidates.sort(key=lambda x: -x[0])
    _, _, best = candidates[0]
    logger.info(
        "选中窗口 hwnd=%s title=%r class=%s pid=%s",
        best.hwnd,
        best.title,
        best.class_name,
        best.pid,
    )
    return best


def client_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    """返回客户区在屏幕坐标下的 (left, top, right, bottom)。"""
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    sx, sy = win32gui.ClientToScreen(hwnd, (left, top))
    ex, ey = win32gui.ClientToScreen(hwnd, (right, bottom))
    return sx, sy, ex, ey


def window_rect_screen(hwnd: int) -> tuple[int, int, int, int]:
    """整窗外接矩形（含边框）。"""
    return win32gui.GetWindowRect(hwnd)
