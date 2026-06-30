"""桌面文件特征灌注（VLM-first）：抽正文 + 文件名；图片型文件用 VLM caption/OCR。

注意：text_extractor 的 extract_body_text / filename_signal 在 clustering.desktop
中直接 import，此处不再 re-export。

大批量提速（关键）：
- 图片识别（caption/OCR）是整个流程最耗时的一环，单张需走一次 VLM 推理。
- ollama 后端服务端可并行处理多请求，故图片识别用线程池并发（并发度取
  vlm.ollama.concurrency），把墙钟从「N × 单张」压到「N / 并发 × 单张」。
- transformers 本地单卡 generate 非线程安全，自动退化为串行，保证稳定。
- 任务开始前先打印「图片数量 + 预估耗时」，过程中按档位打印实时进度，
  避免大目录（数百上千张）时用户面对长时间无输出而误以为卡死/超时。
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

from ..core import Asset
from ..utils import logger
from ..utils.progress import ProgressTracker
from .text_extractor import extract_body_text
from .image_extractor import (
    ocr_image as legacy_ocr, is_likely_screenshot,
)

_IMG_EXTS = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}

# 单张图片 VLM 识别的经验耗时（秒），仅用于「任务前」粗略预估给用户一个量级；
# 真实进度会在处理过程中按实测速率动态刷新剩余时间。
_EST_SEC_PER_IMG = 2.5


def _enrich_image(a: Asset, use_vlm: bool, vlm) -> None:
    """对单张图片做内容识别，结果写入 a.ocr_text。线程安全（各写各的 asset）。"""
    try:
        if is_likely_screenshot(a.path):
            # 截图：文字是最强信号 → OCR
            a.ocr_text = vlm.ocr(a.path) if use_vlm else legacy_ocr(a.path)
        elif use_vlm:
            # 普通图片：一句话描述作为摘要
            a.ocr_text = vlm.caption(a.path)
    except Exception as e:  # noqa: BLE001
        logger.warning("caption/ocr failed on %s: %s", a.path, e)


def _resolve_workers(use_vlm: bool, vlm) -> int:
    """决定图片识别并发度。

    仅 ollama 后端可安全并发（服务端可并行）；transformers 单卡 generate 非线程
    安全、RuleBased/无 VLM 走本地 OCR，均退化为串行。
    """
    if not use_vlm:
        return 1
    impl = getattr(vlm, "_impl", None)
    if type(impl).__name__ != "OllamaVLM":
        return 1
    return max(1, int(getattr(impl, "_concurrency", 1)))


def enrich_files(assets: List[Asset], vlm=None, show_progress: bool = True) -> None:
    """桌面文件：抽正文/文件名信号；图片用 VLM caption（截图用 OCR）生成摘要。

    结果写入 a.body_text / a.ocr_text，供 clustering 构造文件摘要。

    show_progress: True 时在图片识别前打印「数量 + 预估耗时」，过程中打印实时进度。
    """
    use_vlm = vlm is not None and not getattr(vlm, "is_fallback", True)

    # 1) 正文/文件名信号：图片直接返回空，开销极低，串行即可。
    for a in assets:
        a.body_text = extract_body_text(a.path)

    imgs = [a for a in assets if a.ext in _IMG_EXTS]
    n_img = len(imgs)
    if n_img == 0:
        logger.info("enriched %d file assets (images=0, vlm=%s)", len(assets), use_vlm)
        return

    workers = _resolve_workers(use_vlm, vlm)

    # 2) 统一进度：并发时每项墙钟≈单张耗时/并发度，作为预估单项耗时传入。
    #    进度器「大活儿提前告知 + 按档实时刷新剩余」，秒级小目录则自动静默。
    mode = f"并发 {workers} 路" if workers > 1 else "串行"
    tracker = ProgressTracker(
        n_img,
        label=f"图片识别（{mode}）",
        est_per_item=_EST_SEC_PER_IMG / max(1, workers),
        enabled=show_progress,
    )

    # 3) 执行：ollama 后端并发，其余串行。
    t0 = time.time()
    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_enrich_image, a, use_vlm, vlm) for a in imgs]
            for _ in as_completed(futs):
                tracker.advance()
    else:
        for a in imgs:
            _enrich_image(a, use_vlm, vlm)
            tracker.advance()
    tracker.finish()

    el = time.time() - t0
    logger.info(
        "enriched %d file assets (images=%d, vlm=%s, workers=%d, %.1fs)",
        len(assets), n_img, use_vlm, workers, el,
    )
