"""图像相关抽取：EXIF 时间、perceptual hash、可选 OCR。"""
from __future__ import annotations

import os
import time
from typing import Optional

from ..utils import logger


def extract_exif_time(path: str) -> Optional[float]:
    """读取 EXIF 拍摄时间，失败返回 None。"""
    try:
        from PIL import Image, ExifTags  # lazy
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            exif = im._getexif()  # noqa: SLF001
            if not exif:
                return None
            tag_map = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            for key in ("DateTimeOriginal", "DateTime", "DateTimeDigitized"):
                v = tag_map.get(key)
                if v:
                    # "2026:06:21 14:32:01"
                    try:
                        return time.mktime(time.strptime(v, "%Y:%m:%d %H:%M:%S"))
                    except ValueError:
                        continue
    except Exception as e:                    # noqa: BLE001
        logger.debug("exif read failed %s: %s", path, e)
    return None


def perceptual_hash(path: str) -> Optional[str]:
    try:
        from PIL import Image
        import imagehash
    except ImportError:
        return None
    try:
        with Image.open(path) as im:
            return str(imagehash.phash(im))
    except Exception as e:                    # noqa: BLE001
        logger.debug("phash failed %s: %s", path, e)
        return None


def ocr_image(path: str, lang: str = "chi_sim+eng", max_chars: int = 800) -> str:
    """轻量 OCR：仅对疑似截图/含字图调用。失败静默返回 ''。"""
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return ""
    try:
        with Image.open(path) as im:
            text = pytesseract.image_to_string(im, lang=lang)
        return (text or "").strip()[:max_chars]
    except Exception as e:                    # noqa: BLE001
        logger.debug("ocr failed %s: %s", path, e)
        return ""


def is_likely_screenshot(path: str) -> bool:
    name = os.path.basename(path).lower()
    keywords = ("screenshot", "screen shot", "屏幕快照", "截图", "snipaste", "scr_")
    return any(k in name for k in keywords)
