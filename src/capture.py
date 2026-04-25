"""游戏画面截取：Win32 从窗口 DC 复制客户区；mss 仅在显式配置时截屏幕矩形。"""

from __future__ import annotations

import ctypes
import logging
import threading
from typing import TYPE_CHECKING, Any

import cv2
import numpy as np
import win32con
import win32gui
import win32ui

if TYPE_CHECKING:
    from numpy.typing import NDArray

logger = logging.getLogger(__name__)

_MIN_BRIGHT = 4.0  # 均值低于此视为无效/黑屏，继续尝试下一种截法
_WGC_GRABBER: "_WgcWindowGrabber | None" = None
_WGC_LOCK = threading.Lock()


def grab_bgr(left: int, top: int, width: int, height: int) -> "NDArray[np.uint8]":
    """
    使用 mss 截取 **屏幕** 矩形 [left, top, left+width, top+height)。
    若有其它窗口叠在游戏上，会截到叠层内容。
    """
    try:
        import mss
    except ImportError as e:
        raise RuntimeError(
            "未安装 mss（mss 截图依赖）。请 pip install mss，"
            "或将 configs/default.yaml 的 capture.method 改回 wgc。"
        ) from e

    if width <= 0 or height <= 0:
        raise ValueError(f"无效尺寸: {width}x{height}")

    with mss.mss() as sct:
        region = {"left": int(left), "top": int(top), "width": int(width), "height": int(height)}
        shot = sct.grab(region)
    arr = np.asarray(shot, dtype=np.uint8)
    return arr[:, :, :3].copy()


def _resize_to_configured_size(
    frame: "NDArray[np.uint8]",
    cap_cfg: dict[str, Any],
) -> "NDArray[np.uint8]":
    target_width = int(cap_cfg.get("target_width", 0) or 0)
    target_height = int(cap_cfg.get("target_height", 0) or 0)
    if target_width <= 0 or target_height <= 0:
        return frame
    h, w = frame.shape[:2]
    if w == target_width and h == target_height:
        return frame
    return cv2.resize(frame, (target_width, target_height), interpolation=cv2.INTER_LINEAR)


def _bitmap_to_bgr(save_bitmap: Any, height: int, width: int) -> "NDArray[np.uint8]":
    bmpstr = save_bitmap.GetBitmapBits(True)
    img = np.frombuffer(bmpstr, dtype=np.uint8)
    img.shape = (height, width, 4)
    return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)


def _crop_wgc_client(hwnd: int, frame: "NDArray[np.uint8]") -> "NDArray[np.uint8]":
    """WGC 按窗口抓到的是整窗可见框，这里裁成客户区。"""
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    csx, csy = win32gui.ClientToScreen(hwnd, (0, 0))
    _, _, cr, cb = win32gui.GetClientRect(hwnd)
    cw, ch = int(cr), int(cb)
    fh, fw = frame.shape[:2]
    full_w = int(wr - wl)
    full_h = int(wb - wt)

    # DWM 的不可见 resize 边框通常被 GetWindowRect 算入，但 WGC 帧里没有。
    hidden_x = max(0, (full_w - fw) // 2)
    hidden_y = max(0, full_h - fh - hidden_x)
    x0 = max(0, int(csx - wl - hidden_x))
    y0 = max(0, int(csy - wt - hidden_y))
    x1 = min(fw, x0 + cw)
    y1 = min(fh, y0 + ch)
    if x1 <= x0 or y1 <= y0:
        raise RuntimeError("WGC 客户区裁剪区域无效")
    crop = frame[y0:y1, x0:x1].copy()
    if crop.shape[0] != ch or crop.shape[1] != cw:
        crop = cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_LINEAR)
    return crop


class _WgcWindowGrabber:
    def __init__(self, hwnd: int) -> None:
        try:
            from windows_capture import WindowsCapture
        except ImportError as e:
            raise RuntimeError(
                "未安装 windows-capture（WGC 截图依赖），无法启动 WGC 模式。"
                "请 pip install windows-capture，或将 configs/default.yaml 的 capture.method 改为 win32。"
            ) from e

        self.hwnd = int(hwnd)
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._closed = False
        self._last_frame: "NDArray[np.uint8] | None" = None
        self._capture = WindowsCapture(
            cursor_capture=None,
            draw_border=None,
            secondary_window=None,
            window_hwnd=self.hwnd,
        )

        @self._capture.event
        def on_frame_arrived(frame: Any, capture_control: Any) -> None:
            bgr = frame.convert_to_bgr().frame_buffer.copy()
            bgr = _crop_wgc_client(self.hwnd, bgr)
            with self._lock:
                self._last_frame = bgr
            self._ready.set()

        @self._capture.event
        def on_closed() -> None:
            self._closed = True
            self._ready.set()

        self._control = self._capture.start_free_threaded()

    def grab(self, timeout_sec: float) -> "NDArray[np.uint8]":
        if not self._ready.wait(timeout_sec):
            raise RuntimeError("WGC 等待首帧超时")
        if self._closed:
            raise RuntimeError("WGC 捕获会话已关闭")
        with self._lock:
            if self._last_frame is None:
                raise RuntimeError("WGC 尚无可用帧")
            return self._last_frame.copy()

    def stop(self) -> None:
        try:
            self._control.stop()
        except Exception:
            pass


def grab_bgr_wgc_client(hwnd: int, timeout_sec: float = 1.0) -> "NDArray[np.uint8]":
    global _WGC_GRABBER
    with _WGC_LOCK:
        if _WGC_GRABBER is None or _WGC_GRABBER.hwnd != int(hwnd):
            if _WGC_GRABBER is not None:
                try:
                    _WGC_GRABBER.stop()
                except Exception:
                    pass
            _WGC_GRABBER = _WgcWindowGrabber(hwnd)
        grabber = _WGC_GRABBER
    return grabber.grab(timeout_sec)


def stop_wgc_grabber() -> None:
    """主循环退出时调用，避免 WGC 后台线程残留。"""
    global _WGC_GRABBER
    with _WGC_LOCK:
        if _WGC_GRABBER is not None:
            try:
                _WGC_GRABBER.stop()
            except Exception:
                pass
            _WGC_GRABBER = None


def _bitblt_client_copy(
    hwnd: int,
    width: int,
    height: int,
    *,
    use_getdc: bool,
) -> "NDArray[np.uint8] | None":
    """
    use_getdc=True: GetDC(hwnd)，源坐标从客户区 (0,0) 开始（多数顶层游戏窗口应走这条）。
    use_getdc=False: GetWindowDC + 从整窗 DC 中跳过边框偏移到客户区。
    """
    if use_getdc:
        hdc_raw = win32gui.GetDC(hwnd)
        src_x, src_y = 0, 0
    else:
        hdc_raw = win32gui.GetWindowDC(hwnd)
        wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
        csx, csy = win32gui.ClientToScreen(hwnd, (0, 0))
        src_x, src_y = int(csx - wl), int(csy - wt)

    if not hdc_raw:
        return None

    src_dc = win32ui.CreateDCFromHandle(hdc_raw)
    mem_dc = src_dc.CreateCompatibleDC()
    bmp = win32ui.CreateBitmap()
    bmp.CreateCompatibleBitmap(src_dc, width, height)
    old = mem_dc.SelectObject(bmp)
    try:
        mem_dc.BitBlt((0, 0), (width, height), src_dc, (src_x, src_y), win32con.SRCCOPY)
        out = _bitmap_to_bgr(bmp, height, width)
    finally:
        mem_dc.SelectObject(old)
        win32gui.DeleteObject(bmp.GetHandle())
        mem_dc.DeleteDC()
        src_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hdc_raw)

    return out


def _printwindow_client_bitmap(hwnd: int, width: int, height: int) -> "NDArray[np.uint8] | None":
    """PrintWindow 到与客户区等大的位图。"""
    hdc_screen = win32gui.GetDC(0)
    if not hdc_screen:
        return None
    try:
        screen_dc = win32ui.CreateDCFromHandle(hdc_screen)
        mem_dc = screen_dc.CreateCompatibleDC()
        bmp = win32ui.CreateBitmap()
        bmp.CreateCompatibleBitmap(screen_dc, width, height)
        old = mem_dc.SelectObject(bmp)
        try:
            PW = 0x00000001 | 0x00000002
            ok = bool(ctypes.windll.user32.PrintWindow(hwnd, int(mem_dc.GetHandleOutput()), PW))
            if not ok:
                return None
            return _bitmap_to_bgr(bmp, height, width)
        finally:
            mem_dc.SelectObject(old)
            win32gui.DeleteObject(bmp.GetHandle())
            mem_dc.DeleteDC()
            screen_dc.DeleteDC()
    finally:
        win32gui.ReleaseDC(0, hdc_screen)


def grab_bgr_win32_client(hwnd: int) -> "NDArray[np.uint8]":
    """
    从窗口 DC 复制客户区到内存位图；多种顺序尝试，尽量避免回退 mss 叠窗。
    """
    left, top, right, bottom = win32gui.GetClientRect(hwnd)
    width = int(right - left)
    height = int(bottom - top)
    if width <= 0 or height <= 0:
        raise ValueError("客户区尺寸无效")

    # 1) GetDC：客户区坐标系，源 (0,0) —— UE / D3D 常能成功
    try:
        bgr = _bitblt_client_copy(hwnd, width, height, use_getdc=True)
        if bgr is not None and float(bgr.mean()) >= _MIN_BRIGHT:
            logger.debug("win32 截图: GetDC+BitBlt 成功")
            return bgr
        if bgr is not None:
            logger.warning("GetDC+BitBlt 偏暗 (mean=%.2f)，尝试 GetWindowDC…", float(bgr.mean()))
    except Exception as e:
        logger.debug("GetDC+BitBlt 异常: %s", e)

    # 2) GetWindowDC + 边框偏移
    try:
        bgr = _bitblt_client_copy(hwnd, width, height, use_getdc=False)
        if bgr is not None and float(bgr.mean()) >= _MIN_BRIGHT:
            logger.debug("win32 截图: GetWindowDC+BitBlt 成功")
            return bgr
        if bgr is not None:
            logger.warning("GetWindowDC+BitBlt 偏暗 (mean=%.2f)，尝试 PrintWindow…", float(bgr.mean()))
    except Exception as e:
        logger.debug("GetWindowDC+BitBlt 异常: %s", e)

    # 3) PrintWindow 到屏幕兼容 DC（部分文档推荐与屏幕 DC 兼容的位图）
    try:
        bgr = _printwindow_client_bitmap(hwnd, width, height)
        if bgr is not None and float(bgr.mean()) >= _MIN_BRIGHT:
            logger.debug("win32 截图: PrintWindow(客户区标志) 成功")
            return bgr
    except Exception as e:
        logger.debug("PrintWindow(客户区位图) 异常: %s", e)

    logger.warning("客户区截屏仍失败或偏黑，尝试整窗 PrintWindow 后裁剪…")
    return _print_window_full_crop_client(hwnd, width, height)


def _print_window_full_crop_client(hwnd: int, cw: int, ch: int) -> "NDArray[np.uint8]":
    """整窗 PW_RENDERFULLCONTENT 后按客户区相对位置裁剪。"""
    wl, wt, wr, wb = win32gui.GetWindowRect(hwnd)
    fw = int(wr - wl)
    fh = int(wb - wt)
    if fw <= 0 or fh <= 0:
        raise RuntimeError("GetWindowRect 无效")

    pt = win32gui.ClientToScreen(hwnd, (0, 0))
    xoff = int(pt[0] - wl)
    yoff = int(pt[1] - wt)

    hwnd_dc = win32gui.GetWindowDC(hwnd)
    mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
    save_dc = mfc_dc.CreateCompatibleDC()
    save_bitmap = win32ui.CreateBitmap()
    save_bitmap.CreateCompatibleBitmap(mfc_dc, fw, fh)
    save_dc.SelectObject(save_bitmap)
    hdc_out = int(save_dc.GetHandleOutput())
    PW_RENDERFULLCONTENT = 0x00000002
    pw_ok = bool(ctypes.windll.user32.PrintWindow(hwnd, hdc_out, PW_RENDERFULLCONTENT))
    if not pw_ok:
        win32gui.DeleteObject(save_bitmap.GetHandle())
        save_dc.DeleteDC()
        mfc_dc.DeleteDC()
        win32gui.ReleaseDC(hwnd, hwnd_dc)
        raise RuntimeError("PrintWindow 整窗失败")

    full = _bitmap_to_bgr(save_bitmap, fh, fw)
    win32gui.DeleteObject(save_bitmap.GetHandle())
    save_dc.DeleteDC()
    mfc_dc.DeleteDC()
    win32gui.ReleaseDC(hwnd, hwnd_dc)

    y1 = min(fh, yoff + ch)
    x1 = min(fw, xoff + cw)
    if yoff >= fh or xoff >= fw or y1 <= yoff or x1 <= xoff:
        raise RuntimeError("裁剪区域与整窗位图不匹配")
    crop = full[yoff:y1, xoff:x1].copy()
    if crop.shape[0] != ch or crop.shape[1] != cw:
        crop = cv2.resize(crop, (cw, ch), interpolation=cv2.INTER_LINEAR)
    if float(crop.mean()) < _MIN_BRIGHT:
        raise RuntimeError("PrintWindow 裁剪结果仍近黑")
    logger.debug("win32 截图: 整窗 PrintWindow+裁剪 成功")
    return crop


def grab_game_client_bgr(
    hwnd: int,
    cap_cfg: dict[str, Any],
    *,
    client_rect: tuple[int, int, int, int],
    window_rect: tuple[int, int, int, int],
    use_client: bool,
) -> "NDArray[np.uint8]":
    """
    按配置选择 win32 / mss，返回 BGR 图。
    `client_rect` / `window_rect` 为屏幕坐标矩形 (sx,sy,ex,ey)，仅显式 method=mss 时使用。
    """
    method = str(cap_cfg.get("method", "win32")).lower()
    if method == "mss":
        sx, sy, ex, ey = client_rect if use_client else window_rect
        return _resize_to_configured_size(grab_bgr(sx, sy, ex - sx, ey - sy), cap_cfg)

    if method == "wgc":
        return _resize_to_configured_size(grab_bgr_wgc_client(hwnd), cap_cfg)

    if not use_client:
        logger.warning("capture.method=win32 时暂只支持客户区；已按客户区截取。")

    try:
        frame = grab_bgr_win32_client(hwnd)
        if frame.size > 0 and float(frame.mean()) < _MIN_BRIGHT:
            raise RuntimeError("win32 截图为近黑屏；为避免截到其它 app，已禁止回退 mss，请尝试切到无边框窗口化或改用 WGC")
        return _resize_to_configured_size(frame, cap_cfg)
    except Exception as e:
        raise RuntimeError(f"win32 窗口截图失败；为避免截到其它 app，已禁止回退 mss: {e}") from e
