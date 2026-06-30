"""文本 embedding：用 sentence-transformers，CPU 可跑；不可用时退化为词袋。"""
from __future__ import annotations

from typing import List, Optional
import math
import re

from ..utils import logger

_model = None
_model_failed = False


def _try_load(model_id: str):
    global _model, _model_failed
    if _model is not None or _model_failed:
        return
    try:
        from sentence_transformers import SentenceTransformer
        _model = SentenceTransformer(model_id)
        logger.info("loaded text embed model: %s", model_id)
    except Exception as e:                    # noqa: BLE001
        logger.warning("text embed unavailable (%s); fallback to bow", e)
        _model_failed = True


_TOKEN = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _bow_vec(text: str, dim: int = 256) -> List[float]:
    """退化方案：哈希 BoW，保证流程跑通。"""
    v = [0.0] * dim
    for tok in _TOKEN.findall(text.lower()):
        v[hash(tok) % dim] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed_texts(texts: List[str], model_id: str = "BAAI/bge-small-zh-v1.5") -> List[List[float]]:
    _try_load(model_id)
    if _model is not None:
        try:
            arr = _model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
            return [list(map(float, x)) for x in arr]
        except Exception as e:                # noqa: BLE001
            logger.warning("encode failed (%s); fallback to bow", e)
    return [_bow_vec(t) for t in texts]


def cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    s = sum(x * y for x, y in zip(a, b))
    # 已 L2 normalize 时 s 即 cosine
    return float(s)
