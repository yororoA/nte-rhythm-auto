"""CLI 入口：截图、检测、按键。"""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.capture import grab_game_client_bgr
from src.config_loader import default_config_path, load_config
from src.detector import RhythmDetector
from src.keys import KeySender
from src.lanes import build_lane_layout, lane_full_roi_slice, lane_roi_quad
from src.presence import SceneGate
from src.window import client_rect_screen, find_unreal_game_window, window_rect_screen

logger = logging.getLogger(__name__)


def _setup_logging(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _draw_overlay(
    frame: np.ndarray,
    layout,
    masks: list,
    triggers: list[bool],
) -> np.ndarray:
    vis = frame.copy()
    h, w = vis.shape[:2]
    for i in range(4):
        x0, x1, y0, y1 = lane_full_roi_slice(layout, i)
        color = (0, 255, 0) if triggers[i] else (80, 80, 255)
        quad = np.array(lane_roi_quad(layout, i, y0, y1), dtype=np.int32)
        cv2.polylines(vis, [quad], isClosed=True, color=color, thickness=2)
        # 判定带
        jy0, jy1 = layout.judge_y0_by_lane[i], layout.judge_y1_by_lane[i]
        judge_quad = np.array(lane_roi_quad(layout, i, jy0, jy1), dtype=np.int32)
        cv2.polylines(vis, [judge_quad], isClosed=True, color=(255, 200, 0), thickness=1)
        if i < len(masks) and masks[i].size > 1:
            m = masks[i]
            mh, mw = m.shape[:2]
            if mh > 0 and mw > 0:
                small = cv2.resize(m, (min(80, mw * 4), min(120, mh * 4)), interpolation=cv2.INTER_NEAREST)
                small_bgr = cv2.cvtColor(small, cv2.COLOR_GRAY2BGR)
                sx = min(w - 85, 10 + i * 90)
                sy = 10
                sh, sw = small_bgr.shape[:2]
                if sy + sh < h and sx + sw < w:
                    vis[sy : sy + sh, sx : sx + sw] = cv2.addWeighted(
                        vis[sy : sy + sh, sx : sx + sw], 0.65, small_bgr, 0.35, 0
                    )
    return vis


def run_loop(
    args: argparse.Namespace,
    cfg: dict,
    *,
    stop_event: threading.Event | None = None,
    status_callback: Callable[[dict[str, Any]], None] | None = None,
    wait_for_window: bool = False,
) -> int:
    win_cfg = cfg.get("window") or {}
    exe = str(win_cfg.get("exe_name", "HTGame.exe"))
    cls = str(win_cfg.get("class_name", "UnrealWindow"))
    cap_cfg = cfg.get("capture") or {}
    use_client = bool(cap_cfg.get("use_client_area", True))
    cap_method = str(cap_cfg.get("method", "win32")).lower()
    if cap_method == "mss":
        logger.info("截图方式: mss（按屏幕矩形，若有窗口叠在游戏上会截到叠层）")
    elif cap_method == "wgc":
        logger.info("截图方式: wgc（Windows Graphics Capture，按游戏窗口抓帧，不吃其它 app 遮挡）")
    else:
        logger.info("截图方式: win32（GDI 窗口截图；异环/UE 场景可能吃遮挡）")

    info = None
    while info is None:
        if stop_event is not None and stop_event.is_set():
            return 1
        info = find_unreal_game_window(exe_name=exe, class_name=cls)
        if not info:
            if wait_for_window:
                if status_callback:
                    status_callback({"waiting": True})
                logger.warning("未找到游戏窗口，0.5s 后重试…")
                time.sleep(0.5)
                continue
            logger.error("未找到游戏窗口，请确认异环已启动。")
            return 2

    hwnd = info.hwnd
    keys_cfg = cfg.get("keys") or {}
    mode = str(keys_cfg.get("mode", "foreground")).lower()
    sender = KeySender(cfg, hwnd if mode == "background" else None)
    if mode == "background":
        sender.maybe_fake_activate()
        dispatch = str(keys_cfg.get("win32_dispatch", "post")).lower()
        logger.info("按键模式: background（%s WM_KEYDOWN/UP 发往游戏 HWND，不占用全局键盘）", dispatch)
    else:
        logger.info("按键模式: foreground（pynput，需游戏能接收前台键盘）")

    detector = RhythmDetector(cfg)
    scene_gate = SceneGate(cfg)
    run_cfg = cfg.get("run") or {}
    target_fps = max(1, int(run_cfg.get("target_fps", 60)))
    frame_dt = 1.0 / target_fps

    det_cfg = cfg.get("detection") or {}
    px_log_iv = float(det_cfg.get("log_pixels_interval_sec", 0))
    last_px_log = -1e9

    hsv_ranges = list(cfg.get("hsv_ranges") or [])

    debug_dir = Path("debug_frames")
    if args.debug:
        debug_dir.mkdir(parents=True, exist_ok=True)

    save_idx = 0
    next_save = time.perf_counter()

    press_counts = [0, 0, 0, 0]
    ema_fps = 0.0
    last_status_t = time.perf_counter()
    suppress_hint_logged = False

    logger.info("按 Ctrl+C 停止。窗口: %s", info.title or "(无标题)")
    if (cfg.get("presence") or {}).get("enabled", True):
        logger.info(
            "已启用「四鼓在场」门控：进入节奏界面并稳定 %d 帧后才按键；离开界面约 %d 帧后自动停止。",
            scene_gate.arm_after_good_frames,
            scene_gate.disarm_after_bad_frames,
        )

    try:
        while True:
            if stop_event is not None and stop_event.is_set():
                break
            t0 = time.perf_counter()
            cr = client_rect_screen(hwnd)
            wr = window_rect_screen(hwnd)
            sx, sy, ex, ey = cr
            w, h = ex - sx, ey - sy
            if w <= 0 or h <= 0:
                logger.warning("窗口尺寸异常，等待…")
                time.sleep(0.5)
                continue

            try:
                frame = grab_game_client_bgr(
                    hwnd,
                    cap_cfg,
                    client_rect=cr,
                    window_rect=wr,
                    use_client=use_client,
                )
            except RuntimeError as e:
                logger.warning("%s；本帧跳过，不使用屏幕截图回退", e)
                time.sleep(0.2)
                continue
            fh, fw = frame.shape[:2]
            layout = build_lane_layout(cfg, fw, fh)
            armed, gate_info = scene_gate.step(frame, layout)
            if gate_info.get("transitioned"):
                logger.info(
                    "节奏界面门控: %s | 四轨 Laplace 方差≈%s | 本帧四鼓位通过=%s",
                    "已解锁（允许按键）" if gate_info.get("armed") else "已锁定（禁止按键）",
                    [f"{v:.0f}" for v in gate_info.get("lap_vars", [])],
                    gate_info.get("per_ok"),
                )

            triggers, masks, pixels = detector.analyze(frame, layout)

            log_now = time.perf_counter()
            if px_log_iv > 0 and (log_now - last_px_log) >= px_log_iv:
                last_px_log = log_now
                logger.debug(
                    "各轨判定带 HSV 匹配像素 D=%d F=%d J=%d K=%d | 各轨阈值 D/F/J/K=%d/%d/%d/%d",
                    pixels[0],
                    pixels[1],
                    pixels[2],
                    pixels[3],
                    detector.min_pixels_for_lane(0),
                    detector.min_pixels_for_lane(1),
                    detector.min_pixels_for_lane(2),
                    detector.min_pixels_for_lane(3),
                )

            if any(triggers) and not armed:
                if not suppress_hint_logged:
                    logger.info(
                        "已抑制按键：节奏界面门控未解锁（未稳定检测到四鼓）。"
                        "进入四键节奏页后会自动开始；若已进入仍不按键，可调 configs/default.yaml -> presence。"
                    )
                    suppress_hint_logged = True
            elif armed:
                suppress_hint_logged = False

            for i, t in enumerate(triggers):
                if t and armed:
                    meta = hsv_ranges[i] if i < len(hsv_ranges) else {}
                    range_name = meta.get("name", "?")
                    key_name = sender.lane_key_name(i)
                    logger.info(
                        "识别触发 -> 按键 %s | 轨道=%d | HSV配置名=%s | 判定带像素=%d (阈值=%d) | 输入模式=%s",
                        key_name.upper(),
                        i + 1,
                        range_name,
                        pixels[i],
                        detector.min_pixels_for_lane(i),
                        mode,
                    )
                    sender.press_lane(i)
                    press_counts[i] += 1

            frame_elapsed = time.perf_counter() - t0
            inst_fps = 1.0 / max(frame_elapsed, 1e-6)
            ema_fps = 0.9 * ema_fps + 0.1 * inst_fps if ema_fps > 0 else inst_fps

            now = time.perf_counter()
            if status_callback is not None and (now - last_status_t) >= 0.2:
                last_status_t = now
                status_callback(
                    {
                        "ema_fps": ema_fps,
                        "presses": tuple(press_counts),
                        "size": (fw, fh),
                        "title": info.title,
                        "triggers": tuple(triggers),
                        "pixels": tuple(pixels),
                        "scene_armed": armed,
                        "scene_per_ok": tuple(gate_info.get("per_ok", ())),
                        "scene_lap": tuple(
                            round(float(x), 0) for x in gate_info.get("lap_vars", ())
                        ),
                    }
                )

            if args.debug:
                vis = _draw_overlay(frame, layout, masks, triggers)
                if args.show:
                    cv2.imshow("nte-rhythm-auto", vis)
                    cv2.waitKey(1)
                if time.perf_counter() >= next_save:
                    path = debug_dir / f"frame_{save_idx:05d}.png"
                    cv2.imwrite(str(path), vis)
                    save_idx += 1
                    next_save = time.perf_counter() + float(args.debug_interval)

            elapsed = time.perf_counter() - t0
            sleep_t = frame_dt - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)
    except KeyboardInterrupt:
        logger.info("已停止。")
    finally:
        if args.show:
            try:
                cv2.destroyAllWindows()
            except Exception:
                pass
    return 0


def cmd_grab_once(args: argparse.Namespace, cfg: dict) -> int:
    win_cfg = cfg.get("window") or {}
    info = find_unreal_game_window(
        exe_name=str(win_cfg.get("exe_name", "HTGame.exe")),
        class_name=str(win_cfg.get("class_name", "UnrealWindow")),
    )
    if not info:
        return 2
    cap_cfg = cfg.get("capture") or {}
    use_client = bool(cap_cfg.get("use_client_area", True))
    cr = client_rect_screen(info.hwnd)
    wr = window_rect_screen(info.hwnd)
    try:
        frame = grab_game_client_bgr(
            info.hwnd,
            cap_cfg,
            client_rect=cr,
            window_rect=wr,
            use_client=use_client,
        )
    except RuntimeError as e:
        logger.error("%s", e)
        return 3
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out), frame)
    logger.info("已保存: %s (%sx%s)", out, frame.shape[1], frame.shape[0])
    return 0


def cmd_test_image(args: argparse.Namespace, cfg: dict) -> int:
    path = Path(args.image)
    if not path.is_file():
        logger.error("文件不存在: %s", path)
        return 2
    frame = cv2.imread(str(path))
    if frame is None:
        logger.error("无法读取图像: %s", path)
        return 2
    h, w = frame.shape[:2]
    layout = build_lane_layout(cfg, w, h)
    detector = RhythmDetector(cfg)
    triggers, masks, pixels = detector.analyze(frame, layout)
    vis = _draw_overlay(frame, layout, masks, triggers)
    out = Path(args.out_vis) if args.out_vis else path.with_name(path.stem + "_vis.png")
    cv2.imwrite(str(out), vis)
    hsv_ranges = list(cfg.get("hsv_ranges") or [])
    keys_lanes = list((cfg.get("keys") or {}).get("lanes") or ["d", "f", "j", "k"])
    for i, fired in enumerate(triggers):
        if fired:
            rn = (hsv_ranges[i] if i < len(hsv_ranges) else {}).get("name", "?")
            kn = keys_lanes[i] if i < len(keys_lanes) else "?"
            logger.info(
                "test-image 触发 | 轨道=%d | HSV配置名=%s | 判定带像素=%d (阈值=%d) | 将对应按键=%s",
                i + 1,
                rn,
                pixels[i],
                detector.min_pixels_for_lane(i),
                kn,
            )
    logger.info("test-image 结果 triggers=%s pixels=%s -> 可视化: %s", triggers, pixels, out)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="nte-rhythm-auto 异环四键节奏辅助原型",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
子命令示例:
  python -m src gui
  python -m src run --debug
  python -m src grab-once -o debug_frames/once.png
  python -m src test-image screenshot.png --out-vis out.png
未写子命令时默认执行 run（可与 --debug 等全局参数混用）。
""".strip(),
    )
    p.add_argument(
        "--config",
        default=str(default_config_path()),
        help="YAML 配置文件路径",
    )
    p.add_argument("--debug", action="store_true", help="写入 debug_frames 叠加图")
    p.add_argument("--show", action="store_true", help="显示 OpenCV 窗口（调试用）")
    p.add_argument("--debug-interval", type=float, default=0.5, help="debug 写盘最小间隔秒")
    sub = p.add_subparsers(dest="command", metavar="command")
    sub.required = False

    run_p = sub.add_parser("run", help="实时运行（默认）")
    run_p.set_defaults(handler="run")

    g = sub.add_parser("grab-once", help="截取一帧游戏窗口")
    g.add_argument("-o", "--output", default="debug_frames/grab_once.png")
    g.set_defaults(handler="grab")

    t = sub.add_parser("test-image", help="离线测试单张截图")
    t.add_argument("image", help="输入图片路径")
    t.add_argument("--out-vis", default="", help="输出可视化路径")
    t.set_defaults(handler="test_image")

    gui_p = sub.add_parser("gui", help="打开图形界面（开始/停止、配置路径）")
    gui_p.set_defaults(handler="gui")

    return p


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv[1:])
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    _setup_logging(bool(getattr(args, "debug", False)))
    cfg_path = Path(args.config)
    cfg = load_config(cfg_path)

    handler = getattr(args, "handler", None)
    if handler is None:
        handler = "run"
    if handler == "grab":
        return cmd_grab_once(args, cfg)
    if handler == "test_image":
        return cmd_test_image(args, cfg)
    if handler == "gui":
        from src.gui import run_gui

        return run_gui(Path(args.config))
    return run_loop(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
