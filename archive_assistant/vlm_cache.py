"""VLM 结果缓存 —— 同一张图/视频的 caption/ocr/标题结果按内容指纹复用。

加速思路：批量整理常对同一批文件反复跑（增量整理、改参数重跑）。用
`路径 + mtime + size` 作为指纹键，命中即跳过 VLM 前向，零成本拿回历史结果。

- 指纹用 mtime+size 而非内容哈希：避免每次都全量读盘算 hash，足够区分"文件变了"。
- 持久化为单个 JSON，进程内读一次、退出/显式 flush 时写回。
- 失败一律静默降级（缓存只是优化，不能影响主流程）。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from .utils import expand, ensure_dir, logger

_DEFAULT_PATH = "~/.archive_assistant/cache/vlm_cache.json"


def _fingerprint(file_path: str) -> str:
    """用 mtime+size 生成轻量指纹；取不到时退化为路径本身。"""
    try:
        st = os.stat(file_path)
        return f"{int(st.st_mtime)}:{st.st_size}"
    except OSError:
        return "na"


class VLMCache:
    """轻量 KV 缓存。键 = f"{op}|{abspath}|{fingerprint}"，值 = 任意可 JSON 序列化结果。"""

    def __init__(self, path: str = _DEFAULT_PATH, enabled: bool = True):
        self.enabled = enabled
        self.path = expand(path)
        self._data: Dict[str, Any] = {}
        self._dirty = False
        if self.enabled:
            self._load()

    def _load(self) -> None:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    obj = json.load(f)
                if isinstance(obj, dict):
                    self._data = obj
        except Exception as e:                     # noqa: BLE001
            logger.debug("[vlm_cache] load failed: %s", e)
            self._data = {}

    @staticmethod
    def _key(op: str, file_path: str) -> str:
        ap = os.path.abspath(file_path)
        return f"{op}|{ap}|{_fingerprint(ap)}"

    def get(self, op: str, file_path: str) -> Optional[Any]:
        if not self.enabled:
            return None
        return self._data.get(self._key(op, file_path))

    def set(self, op: str, file_path: str, value: Any) -> None:
        if not self.enabled:
            return
        self._data[self._key(op, file_path)] = value
        self._dirty = True

    def flush(self) -> None:
        if not self.enabled or not self._dirty:
            return
        try:
            ensure_dir(os.path.dirname(self.path) or ".")
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False)
            os.replace(tmp, self.path)
            self._dirty = False
        except Exception as e:                     # noqa: BLE001
            logger.debug("[vlm_cache] flush failed: %s", e)
