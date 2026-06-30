"""CLIP 图像/文本 embedding（HF transformers），失败时返回 None 让上层降级。

设计：
- 全局单例，避免重复加载。
- 优先尝试本地路径；其次 HF id；都失败返回 None。
- 不抛异常给上层，上层调用 ``available()`` 判断。
"""
from __future__ import annotations

import os
from typing import List, Optional

import numpy as np

from ..utils import logger

# 优先级：openai/clip-vit-base-patch32 (~600MB) 是最常见兜底
_DEFAULT_CLIP_IDS = [
    "openai/clip-vit-base-patch32",
]

_state = {"model": None, "processor": None, "device": None, "tried": False, "model_id": None}


def _try_load(model_dir_or_id: Optional[str] = None) -> bool:
    if _state["model"] is not None:
        return True
    if _state["tried"] and _state["model"] is None:
        return False
    _state["tried"] = True

    try:
        import torch
        from transformers import CLIPModel, CLIPProcessor
    except Exception as e:                    # noqa: BLE001
        logger.info("CLIP unavailable (transformers/torch not importable): %s", e)
        return False

    candidates: List[str] = []
    if model_dir_or_id:
        candidates.append(model_dir_or_id)
    candidates.extend(_DEFAULT_CLIP_IDS)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    for cid in candidates:
        try:
            logger.info("loading CLIP from %s on %s", cid, device)
            proc = CLIPProcessor.from_pretrained(cid)
            model = CLIPModel.from_pretrained(cid).to(device)
            model.eval()
            _state.update(model=model, processor=proc, device=device, model_id=cid)
            return True
        except Exception as e:                # noqa: BLE001
            logger.warning("CLIP load failed for %s: %s", cid, e)
    return False


def available(model_dir_or_id: Optional[str] = None) -> bool:
    return _try_load(model_dir_or_id)


def _to_numpy(t) -> np.ndarray:
    return t.detach().cpu().numpy().astype("float32")


def encode_images(paths: List[str], batch_size: int = 32) -> Optional[np.ndarray]:
    """返回 (N, D) L2-normalized；失败返回 None。"""
    if not _try_load():
        return None
    import torch
    from PIL import Image

    feats = []
    proc, model, device = _state["processor"], _state["model"], _state["device"]

    for i in range(0, len(paths), batch_size):
        batch_paths = paths[i:i + batch_size]
        imgs = []
        keep_idx = []
        for j, p in enumerate(batch_paths):
            try:
                im = Image.open(p).convert("RGB")
                imgs.append(im)
                keep_idx.append(j)
            except Exception as e:            # noqa: BLE001
                logger.debug("clip skip %s: %s", p, e)
        if not imgs:
            # 全失败 → 给零向量占位
            feats.append(np.zeros((len(batch_paths), 512), dtype="float32"))
            continue

        inputs = proc(images=imgs, return_tensors="pt").to(device)
        with torch.no_grad():
            f = model.get_image_features(**inputs)
        # transformers 5.x 可能返回 ModelOutput 对象而非 tensor
        if hasattr(f, "image_embeds"):
            f = f.image_embeds
        elif hasattr(f, "pooler_output"):
            f = f.pooler_output
        elif hasattr(f, "last_hidden_state"):
            f = f.last_hidden_state.mean(dim=1)
        f = f / f.norm(dim=-1, keepdim=True).clamp(min=1e-8)
        f_np = _to_numpy(f)

        # 把 keep_idx 之外的位置补零
        out = np.zeros((len(batch_paths), f_np.shape[1]), dtype="float32")
        for k, ki in enumerate(keep_idx):
            out[ki] = f_np[k]
        feats.append(out)

    return np.concatenate(feats, axis=0) if feats else None


def encode_texts(texts: List[str]) -> Optional[np.ndarray]:
    if not _try_load():
        return None
    import torch
    proc, model, device = _state["processor"], _state["model"], _state["device"]
    inputs = proc(text=texts, return_tensors="pt", padding=True, truncation=True).to(device)
    with torch.no_grad():
        f = model.get_text_features(**inputs)
    if hasattr(f, "text_embeds"):
        f = f.text_embeds
    elif hasattr(f, "pooler_output"):
        f = f.pooler_output
    elif hasattr(f, "last_hidden_state"):
        f = f.last_hidden_state.mean(dim=1)
    f = f / f.norm(dim=-1, keepdim=True).clamp(min=1e-8)
    return _to_numpy(f)
