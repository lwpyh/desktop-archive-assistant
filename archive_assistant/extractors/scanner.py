"""扫描目录，构建桌面文件 Asset 列表。"""
from __future__ import annotations

import os
from typing import Iterable, List, Set

from ..core import Asset
from ..utils import expand, logger

# 跳过隐藏/系统目录，以及本工具自己产出的归档目录（避免二次归档时重复扫描）
SKIP_DIRS = {".git", ".cache", "__pycache__", "_duplicates", ".archive_assistant",
             "_albums", "_archived"}

# 快捷方式扩展名：扫描到但标记 is_shortcut=True，后续绝不移动
SHORTCUT_EXTS = {"lnk", "app", "url", "webloc", "desktop"}

# 系统文件名：直接跳过不扫描
SYSTEM_FILENAMES = {"desktop.ini", "Thumbs.db", ".DS_Store", ".localized"}


def _iter_files(root: str, recursive: bool, max_depth: int) -> Iterable[str]:
    root = expand(root)
    base_depth = root.rstrip(os.sep).count(os.sep)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        # 安全：跳过隐藏与系统目录
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        depth = dirpath.count(os.sep) - base_depth
        if depth > max_depth:
            dirnames[:] = []
            continue
        for fn in filenames:
            if fn.startswith("."):
                continue
            if fn in SYSTEM_FILENAMES:
                continue
            yield os.path.join(dirpath, fn)
        if not recursive:
            dirnames[:] = []


def scan_desktop(root: str, recursive: bool = False, max_depth: int = 1) -> List[Asset]:
    assets: List[Asset] = []
    for fp in _iter_files(root, recursive, max_depth):
        try:
            a = Asset.from_path(fp, kind="file")
            # 标记快捷方式
            if a.ext in SHORTCUT_EXTS:
                a.is_shortcut = True
            assets.append(a)
        except OSError as e:
            logger.warning("skip %s: %s", fp, e)
    n_shortcuts = sum(1 for a in assets if a.is_shortcut)
    logger.info("desktop scan: %d files under %s (%d shortcuts, will be skipped)",
                len(assets), root, n_shortcuts)
    return assets


def list_existing_folders(root: str) -> List[str]:
    """列出目标目录下已有的文件夹名（用于 AI 语义匹配归入已有文件夹）。"""
    root = expand(root)
    folders: List[str] = []
    try:
        for name in os.listdir(root):
            full = os.path.join(root, name)
            if os.path.isdir(full) and not name.startswith(".") and name not in SKIP_DIRS:
                folders.append(name)
    except OSError as e:
        logger.warning("list_existing_folders failed: %s", e)
    return folders
