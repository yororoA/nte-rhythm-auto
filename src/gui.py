"""简易图形界面：启动/停止、配置路径、调试选项与状态显示。"""

from __future__ import annotations

import logging
import threading
import tkinter as tk
from argparse import Namespace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from src.config_loader import load_config
from src.main import _setup_logging, run_loop

logger = logging.getLogger(__name__)


class RhythmAutoGUI:
    def __init__(self, default_config: Path) -> None:
        self._default_config = default_config
        self._stop = threading.Event()
        self._worker: threading.Thread | None = None

        self.root = tk.Tk()
        self.root.title("nte-rhythm-auto")
        self.root.minsize(420, 320)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

        self.var_config = tk.StringVar(value=str(default_config.resolve()))
        self.var_debug = tk.BooleanVar(value=False)
        self.var_show = tk.BooleanVar(value=False)
        self.var_debug_interval = tk.StringVar(value="0.5")
        self.var_press_delay_ms = tk.StringVar(value="0")
        self.var_key_hold_ms = tk.StringVar(value="10")
        self.var_target_fps = tk.StringVar(value="90")
        self.var_input_mode = tk.StringVar(value="foreground")
        self.var_status = tk.StringVar(value="就绪。请先启动异环，再点「开始」。")

        self._build()

    def _build(self) -> None:
        pad = {"padx": 8, "pady": 4}
        try:
            self.root.call("ttk::style", "theme", "use", "clam")
        except tk.TclError:
            pass
        self.root.minsize(720, 520)
        frm = ttk.Frame(self.root, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        title = ttk.Label(frm, text="NTE Rhythm Auto", font=("Segoe UI", 16, "bold"))
        title.grid(row=0, column=0, columnspan=3, sticky=tk.W, **pad)
        note = (
            "推荐：游戏 1280x720（16:9）+ 30 FPS；截图走 WGC；默认前台按键，请保持游戏窗口在前台。"
        )
        ttk.Label(frm, text=note, wraplength=660, justify=tk.LEFT).grid(row=1, column=0, columnspan=3, sticky=tk.W, **pad)

        config_box = ttk.LabelFrame(frm, text="配置")
        config_box.grid(row=2, column=0, columnspan=3, sticky=tk.EW, **pad)
        ent = ttk.Entry(config_box, textvariable=self.var_config, width=60)
        ttk.Label(config_box, text="配置文件").grid(row=0, column=0, sticky=tk.W, **pad)
        ent.grid(row=0, column=1, sticky=tk.EW, **pad)
        ttk.Button(config_box, text="浏览…", command=self._browse_config).grid(row=0, column=2, **pad)
        config_box.columnconfigure(1, weight=1)

        timing_box = ttk.LabelFrame(frm, text="时序 / 设备差异")
        timing_box.grid(row=3, column=0, columnspan=3, sticky=tk.EW, **pad)
        ttk.Label(timing_box, text="按键延迟 ms").grid(row=0, column=0, sticky=tk.W, **pad)
        ttk.Entry(timing_box, textvariable=self.var_press_delay_ms, width=8).grid(row=0, column=1, sticky=tk.W, **pad)
        ttk.Label(timing_box, text="按键保持 ms").grid(row=0, column=2, sticky=tk.W, **pad)
        ttk.Entry(timing_box, textvariable=self.var_key_hold_ms, width=8).grid(row=0, column=3, sticky=tk.W, **pad)
        ttk.Label(timing_box, text="目标 FPS").grid(row=0, column=4, sticky=tk.W, **pad)
        ttk.Entry(timing_box, textvariable=self.var_target_fps, width=8).grid(row=0, column=5, sticky=tk.W, **pad)
        ttk.Label(timing_box, text="输入模式").grid(row=1, column=0, sticky=tk.W, **pad)
        mode = ttk.Combobox(timing_box, textvariable=self.var_input_mode, values=("foreground", "background"), width=12, state="readonly")
        mode.grid(row=1, column=1, sticky=tk.W, **pad)
        ttk.Label(
            timing_box,
            text="按早：增加延迟；按晚：减少延迟或下移判定线。后台模式需要管理员且不保证异环会吃。",
            wraplength=620,
            justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=6, sticky=tk.W, **pad)

        debug_box = ttk.LabelFrame(frm, text="调试")
        debug_box.grid(row=4, column=0, columnspan=3, sticky=tk.EW, **pad)
        ttk.Checkbutton(debug_box, text="定期保存叠加图到 debug_frames/", variable=self.var_debug).grid(
            row=0, column=0, columnspan=2, sticky=tk.W, **pad
        )
        ttk.Checkbutton(debug_box, text="显示 OpenCV 预览窗口", variable=self.var_show).grid(
            row=1, column=0, columnspan=2, sticky=tk.W, **pad
        )
        ttk.Label(debug_box, text="写盘间隔(秒)").grid(row=0, column=2, sticky=tk.W, **pad)
        ttk.Entry(debug_box, textvariable=self.var_debug_interval, width=6).grid(row=0, column=3, sticky=tk.W, **pad)

        btn_row = ttk.Frame(frm)
        btn_row.grid(row=5, column=0, columnspan=3, pady=12)
        self.btn_start = ttk.Button(btn_row, text="开始", command=self._start)
        self.btn_start.pack(side=tk.LEFT, padx=6)
        self.btn_stop = ttk.Button(btn_row, text="停止", command=self._stop_worker, state=tk.DISABLED)
        self.btn_stop.pack(side=tk.LEFT, padx=6)

        status_box = ttk.LabelFrame(frm, text="状态")
        status_box.grid(row=6, column=0, columnspan=3, sticky=tk.NSEW, **pad)
        ttk.Label(status_box, textvariable=self.var_status, wraplength=660, justify=tk.LEFT).pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        ttk.Label(frm, text="提示：不同设备主要调“按键延迟 ms / 按键保持 ms / 目标 FPS”。", foreground="#666").grid(
            row=7, column=0, columnspan=3, sticky=tk.W, **pad
        )

        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(6, weight=1)

    def _browse_config(self) -> None:
        p = filedialog.askopenfilename(
            title="选择 YAML 配置",
            filetypes=[("YAML", "*.yaml *.yml"), ("全部", "*.*")],
        )
        if p:
            self.var_config.set(p)

    def _parse_debug_interval(self) -> float:
        try:
            v = float(self.var_debug_interval.get().strip())
            return max(0.1, min(v, 10.0))
        except ValueError:
            return 0.5

    def _parse_ms(self, var: tk.StringVar, default_ms: float, min_ms: float, max_ms: float) -> float:
        try:
            v = float(var.get().strip())
        except ValueError:
            v = default_ms
        return max(min_ms, min(v, max_ms)) / 1000.0

    def _parse_int(self, var: tk.StringVar, default: int, min_v: int, max_v: int) -> int:
        try:
            v = int(float(var.get().strip()))
        except ValueError:
            v = default
        return max(min_v, min(v, max_v))

    def _apply_runtime_overrides(self, cfg: dict) -> None:
        keys_cfg = cfg.setdefault("keys", {})
        keys_cfg["mode"] = self.var_input_mode.get().strip() or "foreground"
        keys_cfg["press_delay_sec"] = self._parse_ms(self.var_press_delay_ms, 0, 0, 200)
        keys_cfg["key_hold_sec"] = self._parse_ms(self.var_key_hold_ms, 10, 1, 100)
        run_cfg = cfg.setdefault("run", {})
        run_cfg["target_fps"] = self._parse_int(self.var_target_fps, 90, 30, 240)

    def _start(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            messagebox.showinfo("提示", "已在运行中。")
            return

        cfg_path = Path(self.var_config.get().strip())
        if not cfg_path.is_file():
            messagebox.showerror("错误", f"找不到配置文件:\n{cfg_path}")
            return

        try:
            cfg = load_config(cfg_path)
            self._apply_runtime_overrides(cfg)
        except Exception as e:
            messagebox.showerror("错误", f"读取配置失败:\n{e}")
            return

        self._stop.clear()
        args = Namespace(
            debug=bool(self.var_debug.get()),
            show=bool(self.var_show.get()),
            debug_interval=self._parse_debug_interval(),
        )

        self.btn_start.configure(state=tk.DISABLED)
        self.btn_stop.configure(state=tk.NORMAL)
        self.var_status.set("正在连接游戏窗口…")

        def worker() -> None:
            try:
                code = run_loop(
                    args,
                    cfg,
                    stop_event=self._stop,
                    status_callback=self._schedule_status,
                    wait_for_window=True,
                )
                if code == 2 and not self._stop.is_set():
                    self._schedule_status({"error": "未找到游戏窗口（HTGame.exe）"})
            except Exception as e:
                logger.exception("运行线程异常")
                self._schedule_status({"error": str(e)})
            finally:
                self.root.after(0, self._on_worker_done)

        self._worker = threading.Thread(target=worker, name="nte-rhythm-worker", daemon=True)
        self._worker.start()

    def _schedule_status(self, data: dict) -> None:
        def apply() -> None:
            self._apply_status(data)

        try:
            self.root.after(0, apply)
        except tk.TclError:
            pass

    def _apply_status(self, data: dict) -> None:
        if data.get("waiting"):
            self.var_status.set("等待游戏窗口…请启动异环（HTGame.exe）。")
            return
        if err := data.get("error"):
            self.var_status.set(f"错误: {err}")
            return
        fps = data.get("ema_fps", 0.0)
        pr = data.get("presses", (0, 0, 0, 0))
        w, h = data.get("size", (0, 0))
        title = data.get("title") or ""
        tr = data.get("triggers")
        tr_s = f" 本帧触发: {list(tr)}" if isinstance(tr, (list, tuple)) else ""
        px = data.get("pixels")
        px_s = ""
        if isinstance(px, (list, tuple)) and len(px) >= 4:
            px_s = f" | 像素 D/F/J/K: {px[0]}/{px[1]}/{px[2]}/{px[3]}"
        armed = data.get("scene_armed")
        scene_s = ""
        if armed is not None:
            po = data.get("scene_per_ok")
            lap = data.get("scene_lap")
            if isinstance(po, (list, tuple)) and isinstance(lap, (list, tuple)) and len(po) >= 4:
                scene_s = (
                    f"\n门控: {'已解锁可按键' if armed else '锁定中(不按键)'} | "
                    f"四鼓位={list(po)} | Lap≈{list(lap)}"
                )
            else:
                scene_s = f"\n门控: {'已解锁可按键' if armed else '锁定中(不按键)'}"
        self.var_status.set(
            f"运行中 | ~{fps:.0f} FPS | 画面 {w}x{h} | D/F/J/K 累计: {pr[0]}/{pr[1]}/{pr[2]}/{pr[3]}"
            f"\n窗口: {title}{tr_s}{px_s}{scene_s}"
        )

    def _stop_worker(self) -> None:
        self._stop.set()
        self.var_status.set("正在停止…")

    def _on_worker_done(self) -> None:
        self.btn_start.configure(state=tk.NORMAL)
        self.btn_stop.configure(state=tk.DISABLED)
        if self._stop.is_set():
            self.var_status.set("已停止。")
        self._worker = None

    def _on_close(self) -> None:
        self._stop.set()
        if self._worker is not None and self._worker.is_alive():
            self._worker.join(timeout=2.5)
        self.root.destroy()

    def run(self) -> int:
        self.root.mainloop()
        return 0


def run_gui(default_config: Path) -> int:
    _setup_logging(debug=False)
    app = RhythmAutoGUI(default_config)
    return app.run()
