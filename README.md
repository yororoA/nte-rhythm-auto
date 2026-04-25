# nte-rhythm-auto

异环（Neverness to Everness）**超强音自动点击项目**：通过**窗口截图 + OpenCV HSV 检测**判定线附近的音符像素，并在触发时模拟按下 **D / F / J / K**（可在配置中修改）。

> **风险说明**：第三方自动化可能违反游戏用户协议，存在封号等后果。本项目仅供学习计算机视觉与自动化接口，请自行承担使用风险。

## 推荐运行设置

- Windows 10/11
- 游戏进程名通常为 `HTGame.exe`，窗口类名 `UnrealWindow`（与 [ok-nte](https://github.com/BnanZ0/ok-nte) 一致）
- 推荐游戏设置：**1280×720（16:9）+ 30 FPS**
- 分辨率可以换，但请保持 **16:9**；检测参数已尽量按比例换算，非 16:9 需要重新校准轨道
- 当前默认使用 **WGC（Windows Graphics Capture）** 截图，不会被其它 app 遮挡
- 当前默认使用 **前台按键**，运行时请让游戏窗口保持前台焦点
- 若尝试后台 HWND 按键，请用**管理员权限**运行终端；异环/UE 可能仍不吃普通 `WM_KEYDOWN` 消息

## 使用者下载


1. 打开 [GitHub Release 下载页](https://github.com/Gloaming02/nte-rhythm-auto/releases/latest)。
2. 下载 `nte-rhythm-auto-win64.zip`。
3. 解压 zip。
4. 右键 `nte-rhythm-auto.exe`，选择 **以管理员身份运行**。
5. 启动异环，进入超强音玩法，并保持游戏在前台。
6. 默认值没问题就不要调整，直接点击工具里的「开始」。

解压后的 `configs/default.yaml` 会被优先读取；需要调参时改这个文件即可，不需要改 exe。

## 开发者安装

```powershell
cd E:\nte\nte-rhythm-auto
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 截图方式（`capture.method`）

- **`wgc`（默认）**：Windows Graphics Capture，按异环窗口抓帧，不吃其它 app 遮挡。
- **`win32`**：GDI 窗口截图；异环/UE 场景已验证会吃遮挡或失败，仅保留作对比。
- **`mss`**：按屏幕坐标截矩形，桌面上谁盖在最上面就截到谁，仅适合调试。

默认配置 `target_width: 0` / `target_height: 0`，表示使用游戏窗口实际客户区尺寸；轨道坐标与组件检测参数按比例换算，适配 720p/1080p 等 16:9 分辨率。

### Windows 10 兼容性

本工具依赖 Windows Graphics Capture。Windows 10 版本过旧可能不支持 WGC，或不支持某些捕获边框/鼠标/副窗口开关。当前版本不会主动切换这些可选开关，以兼容更多 Win10；如果仍出现“此平台上的图形捕获 API 不支持……”一类错误，请先更新 Windows 10 到较新版本。

### 与 ok-nte / Noki 的「后台」差异

- **ok-nte** 基于 [ok-script](https://github.com/ok-oldking/ok-script)：截图可走 **WGC / DXGI** 等无焦点管线；交互在配置里可选 **Pynput** 或 **PostMessage**，由框架统一处理。
- **Noki-NTE-Auto** 同样是 **OpenCV + 后台键鼠（PostMessage 等）** 思路。

本仓库是**精简脚本**：截图默认走 **WGC**；按键默认走 **`keys.mode: foreground`**（`pynput` 前台按键）。异环/UE 对 `PostMessage` / `SendMessage` 后台键消息不稳定，当前推荐保持游戏前台运行。

**鼠标**：当前节奏逻辑**不模拟鼠标**；若以后做钓鱼等再单独加。

## 配置

编辑 [`configs/default.yaml`](configs/default.yaml)：

- `lanes.center_x_frac`：四条轨道中心的水平比例（需与画面上的鼓位对齐）
- `lanes.judge_line_y_frac`：判定线纵向位置
- `hsv_ranges`：每条轨道音符环的大致 HSV 范围，可按 `test-image` 输出图微调
- `keys.mode`：默认 **`foreground`**（pynput 前台输入）；`background` 为 HWND 消息模式，异环/UE 不一定响应。
- `keys.press_delay_sec` / `keys.key_hold_sec`：不同设备可调的按键延迟与保持时间；GUI 中以秒显示。

## 开发者用法

**图形界面**：

```powershell
python -m src gui
```

也可带配置路径：`python -m src gui --config E:\path\to\my.yaml`

**命令行实时运行**（需游戏已启动；默认前台按键，请保持游戏窗口可接收键盘焦点）：

```powershell
python -m src run
python -m src --debug run
python -m src --debug --show run
```

**截取一帧**（检查窗口与分辨率）：

```powershell
python -m src grab-once -o debug_frames\once.png
```

**离线调试**（对截图跑检测并输出叠加图）：

```powershell
python -m src test-image path\to\screenshot.png --out-vis debug_frames\out.png
```

## 调参建议

1. 推荐先用 **1280×720 + 30 FPS**；程序自身建议 `run.target_fps: 90`。
2. 先用 `grab-once` 保存一帧，再用 `test-image` 看四条斜框/判定框是否对准鼓与判定线。
3. 不同电脑可先在 GUI 调 **按键延迟 s / 按键保持 s / 截图间隔 s**：
   - 音符普遍按早：增加 `按键延迟 s`
   - 音符普遍按晚：减少延迟，或下移对应轨道判定线
   - 偶发漏：减少 `截图间隔 s`，例如 0.011 -> 0.008
   - 按键不稳：把 `按键保持 s` 从 0.010 调到 0.015/0.020
4. D/J/K 连续同轨音符使用 `component_mode_lanes` 组件识别；若连续音符漏，优先调 `component_same_note_y_frac` 和 `component_history_sec`。
5. F 轨黄色容易吃到底部静态鼓面，默认不使用组件识别，并通过 `judge_line_y_frac_by_lane` 单独上移判定线。
6. 光效较强时，可适当提高 `s_min` / `v_min` 或增大 `morph_kernel` 去噪。

## 开发者打包 Release

维护者可用 PyInstaller 打包 GUI exe：

```powershell
.\.venv\Scripts\Activate.ps1
pip install pyinstaller
pyinstaller --noconfirm --clean --onefile --windowed `
  --name nte-rhythm-auto `
  --add-data "configs\default.yaml;configs" `
  run_gui.py
```

产物在 `dist\nte-rhythm-auto.exe`。发布到 GitHub Release 时建议打包 zip，包含：

- `nte-rhythm-auto.exe`
- `configs/default.yaml`
- `README.md`

## 日志说明

- **INFO（默认）**：仅在真正触发时打印一行，包含轨道号、`hsv_ranges` 里的配置名、判定带匹配像素数与阈值、将要按的键、输入模式（前台/后台）。
- **DEBUG**：`python -m src run --debug` 时启用；包含 `pynput`/`PostMessage` 发送细节。可在 `configs/default.yaml` 里设置 `detection.log_pixels_interval_sec`（例如 `0.5`）周期性打印四条轨当前像素；`detection.log_cooldown_debug: true` 可在像素够但冷却未结束时打节流后的 DEBUG。

## 节奏界面门控（防止未进页乱按）

默认开启 `presence.enabled: true`：在画面底部与四条轨道对齐的四个区域检测「鼓位」纹理（Laplacian 方差）。**四块同时达标并连续若干帧**后才**允许**根据音符识别去按键；离开节奏界面后会自动重新锁定，避免在主界面因背景色误触 F 等。

若已进入节奏页仍长期显示「锁定」，可适当**降低** `min_laplace_variance`，或微调 `drum_center_y_frac` / `lanes.center_x_frac` 与鼓圈对齐。不需要模板截图；若要改成「与参考图对比」，可再单独加模板匹配功能。

### 进入节奏页后一直按 F？

第二轨多为黄/金色，**判定带若盖住鼓面或光晕**，会一直满足 F 的 HSV。默认已做缓解：`F` 不启用组件识别，且 `judge_line_y_frac_by_lane` 中 F 轨单独上移。若仍误触：继续上移 F 的判定线或收紧 F 的 HSV；若真音符漏按，再小幅下移 F 判定线。

## 项目结构

```
configs/default.yaml   # 默认参数
src/
  main.py              # CLI 与主循环
  window.py            # 查找 HTGame.exe + UnrealWindow
  capture.py           # WGC / win32 / mss 截图
  lanes.py             # 比例坐标 -> 像素 ROI
  detector.py          # HSV 掩膜与冷却触发
  presence.py          # 四鼓在位门控（未进节奏页不按键）
  keys.py              # 前台/后台按键
```

## 参考

- [Noki-NTE-Auto](https://github.com/nokiruy/Noki-NTE-Auto)：OpenCV、ADB、Win32 后台键鼠思路
- [ok-nte](https://github.com/BnanZ0/ok-nte)：`HTGame.exe` / `UnrealWindow` 与截图相关经验