"""通用工具：配置加载、日志、安全文件名清洗、长任务进度。"""
from __future__ import annotations

import os
import re
import logging
from pathlib import Path
from typing import Any, Dict
import yaml

# 统一长任务进度展示（详见 utils/progress.py）。
from .progress import ProgressTracker, track, fmt_dur, set_enabled  # noqa: E402,F401

logger = logging.getLogger("archive_assistant")
if not logger.handlers:
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
    logger.addHandler(h)
    logger.setLevel(logging.INFO)


_DEFAULT_CFG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"


def load_config(path: str | os.PathLike | None = None) -> Dict[str, Any]:
    p = Path(path) if path else _DEFAULT_CFG_PATH
    with open(p, "r", encoding="utf-8") as f:
        # 安全：必须用 safe_load
        return yaml.safe_load(f)


_SAFE_NAME = re.compile(r"[^\w\u4e00-\u9fff\-. ]+", re.UNICODE)


def safe_folder_name(name: str, max_len: int = 60) -> str:
    """把 LLM 给的标签清洗成安全的文件夹名。"""
    if not name:
        return "Misc"
    name = name.strip().replace("/", "_").replace("\\", "_")
    name = _SAFE_NAME.sub("", name)
    name = re.sub(r"\s+", " ", name).strip(" .-_")
    return (name or "Misc")[:max_len]


def expand(path: str) -> str:
    return os.path.abspath(os.path.expanduser(os.path.expandvars(path)))


def ensure_dir(path: str) -> str:
    p = expand(path)
    os.makedirs(p, exist_ok=True)
    return p


# ---------- 跨平台助手 ----------

def platform_name() -> str:
    """返回标准化平台名：'Windows' | 'Darwin'(macOS) | 'Linux'。"""
    import platform
    return platform.system()


def default_desktop_dir() -> str:
    """跨平台检测当前用户桌面目录。

    Windows: %USERPROFILE%\\Desktop
    macOS:   ~/Desktop
    Linux:   ~/Desktop（XDG 未标准化桌面路径，多数发行版用 ~/Desktop；
             若不存在则回退到 ~）
    """
    home = os.path.expanduser("~")
    candidates = []
    if platform_name() == "Windows":
        candidates = [
            os.path.join(os.environ.get("USERPROFILE", home), "Desktop"),
            os.path.join(home, "Desktop"),
        ]
    else:
        candidates = [os.path.join(home, "Desktop"), home]
    for c in candidates:
        if c and os.path.isdir(c):
            return c
    return home
