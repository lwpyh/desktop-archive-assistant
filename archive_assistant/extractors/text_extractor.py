"""文本类抽取：PDF、DOCX、纯文本，及文件名解析。"""
from __future__ import annotations

import os
from typing import Optional

from ..utils import logger

_TEXT_LIMIT = 4000  # 抽到 4k 字符够用


def _read_pdf(path: str) -> str:
    try:
        import pypdf  # lazy
    except ImportError:
        return ""
    try:
        reader = pypdf.PdfReader(path)
        out = []
        for page in reader.pages[:8]:        # 仅前 8 页
            out.append(page.extract_text() or "")
            if sum(len(x) for x in out) > _TEXT_LIMIT:
                break
        return ("\n".join(out))[:_TEXT_LIMIT]
    except Exception as e:                    # noqa: BLE001
        logger.debug("pdf read failed %s: %s", path, e)
        return ""


def _read_docx(path: str) -> str:
    try:
        import docx  # python-docx
    except ImportError:
        return ""
    try:
        d = docx.Document(path)
        text = "\n".join(p.text for p in d.paragraphs if p.text)
        return text[:_TEXT_LIMIT]
    except Exception as e:                    # noqa: BLE001
        logger.debug("docx read failed %s: %s", path, e)
        return ""


def _read_plain(path: str) -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(_TEXT_LIMIT)
    except OSError:
        return ""


def extract_body_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    if ext == "pdf":
        return _read_pdf(path)
    if ext == "docx":
        return _read_docx(path)
    if ext in {"txt", "md", "rst", "csv", "json", "log", "py", "js", "ts", "html"}:
        return _read_plain(path)
    return ""


def filename_signal(path: str) -> str:
    """文件名/路径里能用的语义信号。"""
    base = os.path.splitext(os.path.basename(path))[0]
    parent = os.path.basename(os.path.dirname(path))
    return f"{parent} {base}".replace("_", " ").replace("-", " ").strip()
