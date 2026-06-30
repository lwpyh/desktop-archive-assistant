"""无 CLIP 时的 fallback 视觉特征：颜色直方图 + 长宽比。
不准但比 imagehash 强 —— 至少能把"风景"(蓝绿主调)与"室内"(暖色)分开。
"""
from __future__ import annotations

from typing import List, Optional

import numpy as np

from ..utils import logger


def color_hist_features(paths: List[str], bins: int = 6) -> Optional[np.ndarray]:
    try:
        from PIL import Image
    except ImportError:
        return None
    feats = []
    for p in paths:
        try:
            with Image.open(p) as im:
                im = im.convert("RGB").resize((64, 64))
                arr = np.asarray(im, dtype=np.float32) / 255.0
                # 3D 直方图（粗）
                hist, _ = np.histogramdd(
                    arr.reshape(-1, 3),
                    bins=(bins, bins, bins),
                    range=((0, 1), (0, 1), (0, 1)),
                )
                v = hist.flatten()
                # 加长宽比
                w, h = im.size
                ar = float(w) / float(h) if h else 1.0
                v = np.concatenate([v, [ar, np.log1p(w * h)]])
                n = np.linalg.norm(v) or 1.0
                feats.append(v / n)
        except Exception as e:                # noqa: BLE001
            logger.debug("hist skip %s: %s", p, e)
            feats.append(None)
    # 失败位置用 0 向量补齐
    dim = next((len(v) for v in feats if v is not None), bins ** 3 + 2)
    out = np.zeros((len(feats), dim), dtype=np.float32)
    for i, v in enumerate(feats):
        if v is not None:
            out[i] = v
    return out
