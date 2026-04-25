"""加载 YAML 配置。"""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import yaml


def default_config_path() -> Path:
    """优先读 exe 同目录配置；开发环境读仓库配置；打包后兜底读内置配置。"""
    if getattr(sys, "frozen", False):
        external = Path(sys.executable).resolve().parent / "configs" / "default.yaml"
        if external.is_file():
            return external
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "configs" / "default.yaml"  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent / "configs" / "default.yaml"


def load_config(path: str | Path) -> dict[str, Any]:
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"配置文件不存在: {p}")
    with p.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("配置文件根节点必须是映射表")
    return data
