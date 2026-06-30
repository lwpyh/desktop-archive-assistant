"""
Qwen3.5-4B 多模态适配 —— 桌面文件整理专用。

提供 4 类能力：
1. review_cluster(cluster, mode, categories)           # cluster 级语义判断 + 命名
2. ocr(path)                                           # OCR / 截图理解（替代 pytesseract）
3. caption(path)                                       # 给一张图一句话描述（cluster 命名兜底）
4. organize_files(items, max_themes, chunk_size)       # 桌面文件按主题整批归类

设计：
- 真模型加载失败 → 自动回退到 RuleBasedVLM（流程不挂）
- 所有调用都走同一个 model 实例，懒加载
- 输出统一 JSON，使用 _parse_json 容错解析
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from ..core import Cluster
from ..utils import logger, safe_folder_name


# ============================================================
# 提速辅助：喂给 VLM 前把图缩到 max edge，显著降低 visual token
# ============================================================

def _open_image_scaled(path: str, max_edge: int = 512):
    """打开图片并把最长边缩到 max_edge（等比）。

    visual token 数随分辨率平方增长，把长边压到 512 能在几乎不损内容理解的
    前提下大幅减少每次前向的 token，单张延迟立竿见影。max_edge<=0 时不缩放。
    """
    from PIL import Image
    im = Image.open(path).convert("RGB")
    if max_edge and max_edge > 0 and max(im.size) > max_edge:
        im.thumbnail((max_edge, max_edge), Image.LANCZOS)
    return im


# ============================================================
# 输出 schema
# ============================================================

@dataclass
class ClusterDecision:
    cluster_name: str
    cluster_type: str
    confidence: float
    needs_split: bool = False
    needs_merge: bool = False
    discard_candidates: List[str] = None
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_name": self.cluster_name,
            "cluster_type": self.cluster_type,
            "confidence": self.confidence,
            "needs_split": self.needs_split,
            "needs_merge": self.needs_merge,
            "discard_candidates": self.discard_candidates or [],
            "rationale": self.rationale,
        }


# ============================================================
# Prompt 模板
# ============================================================

_DESKTOP_SYS = (
    "你是文件归档助手。看图与文本片段，仅输出一个 JSON 对象，禁止任何思考过程或解释。\n"
    "字段：\n"
    "- cluster_name: 6~16 字中英文，作为文件夹名（不含 / \\ : 等非法字符）\n"
    "- cluster_type: project | event | misc 之一\n"
    "- confidence: 0~1 浮点\n"
    "- needs_split: bool\n"
    "- discard_candidates: 可丢弃的 asset_id 数组\n"
    "示例输出：{\"cluster_name\":\"CVPR Rebuttal\",\"cluster_type\":\"project\",\"confidence\":0.9,"
    "\"needs_split\":false,\"discard_candidates\":[]}"
)

_OCR_SYS = (
    "OCR 任务：识别图中所有文字，按从上到下、从左到右顺序输出为纯文本。\n"
    "若图中没有任何文字，仅输出空字符串，不要任何解释、不要描述图片内容。"
)

_CAPTION_SYS = (
    "Look at the image carefully. Identify the SINGLE most prominent subject or scene "
    "(e.g. a child eating, a dog on grass, a license plate, a cityscape at night, "
    "an iPhone screenshot of a map). "
    "Answer in 3 to 7 English words describing the actual content. "
    "Output the phrase only, no explanation, no markdown."
)

_ORGANIZE_SYS = (
    "你是文件归档助手。给你一批文件（每条含 id、文件名、内容摘要），"
    "请把它们按主题归类到尽可能少的文件夹。规则：\n"
    "- 【合并优先】文件夹数量尽量少，内容/用途有任何关联的文件必须归到同一文件夹；"
    "只有主题完全无关时才拆分；目标：{max_themes} 个以内的文件夹覆盖所有文件。\n"
    "- 同一文件夹内的文件必须用完全相同的文件夹名（字符串精确匹配）。\n"
    "- 文件夹名 2~14 字中英文，能概括主题（如 '财务文档'、'学术论文'、'工作文档'、'设计素材'）。\n"
    "- 每个文件必须分到且只分到一个文件夹，覆盖所有给定 id。\n"
    "- 实在无主题的零散文件统一归到 '杂项'。\n"
    "只输出一个 JSON 对象，禁止任何解释或思考过程。\n"
    "格式：{{\"assignments\":{{\"<id>\":\"<文件夹名>\"}}}}\n"
    "示例：{{\"assignments\":{{\"a1b2c3\":\"财务文档\",\"d4e5f6\":\"财务文档\",\"g7h8i9\":\"学术论文\"}}}}"
)


# ============================================================
# JSON 解析
# ============================================================

_JSON_RE = re.compile(r"\{[\s\S]*\}")


def _parse_json(raw: str, default: Dict[str, Any]) -> Dict[str, Any]:
    if not raw:
        return default
    m = _JSON_RE.search(raw)
    if not m:
        return default
    try:
        obj = json.loads(m.group(0))
        if isinstance(obj, dict):
            return obj
    except json.JSONDecodeError:
        pass
    return default


def _parse_decision(raw: str) -> ClusterDecision:
    obj = _parse_json(raw, {})
    if not obj:
        return ClusterDecision(cluster_name="Misc", cluster_type="misc", confidence=0.1,
                               rationale=f"unparseable: {(raw or '')[:120]}")
    return ClusterDecision(
        cluster_name=safe_folder_name(str(obj.get("cluster_name", "Misc"))),
        cluster_type=str(obj.get("cluster_type", "misc")),
        confidence=float(obj.get("confidence", 0.5)),
        needs_split=bool(obj.get("needs_split", False)),
        needs_merge=bool(obj.get("needs_merge", False)),
        discard_candidates=list(obj.get("discard_candidates", []) or []),
        rationale=str(obj.get("rationale", "")),
    )


# ============================================================
# 真模型：Qwen3.5-4B
# ============================================================

class QwenVLM:
    """Qwen/Qwen3.5-4B 推理封装。延迟加载，失败抛 RuntimeError。"""

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg["vlm"]
        self._model = None
        self._processor = None
        self._device = None

    def _load(self):
        if self._model is not None:
            return
        try:
            import torch
            from transformers import AutoProcessor, AutoModelForImageTextToText
        except ImportError as e:
            raise RuntimeError(f"transformers/torch not available: {e}")

        local = os.path.expanduser(self.cfg.get("local_dir", "") or "")
        # 把相对路径基于 skill 根目录解析
        if local and not os.path.isabs(local):
            here = os.path.dirname(os.path.abspath(__file__))
            skill_root = os.path.abspath(os.path.join(here, "..", ".."))
            local = os.path.join(skill_root, local)

        # 模型就绪检测 + 自动下载
        model_ready = local and os.path.isdir(local) and os.path.exists(
            os.path.join(local, "config.json")
        )
        if not model_ready:
            # 尝试自动下载（跨平台）
            try:
                from ..setup_model import ensure_model
                logger.info("VLM 模型未就绪，尝试自动下载...")
                result = ensure_model(auto_download=True)
                if result.get("ready"):
                    local = result["target_dir"]
                    model_ready = True
                    logger.info("VLM 模型自动下载完成: %s", local)
                else:
                    msg = result.get("message", "")
                    logger.warning("VLM 模型自动下载未完成: %s", msg)
            except Exception as e:
                logger.warning("VLM 自动下载失败 (%s)", e)

        model_path = local if model_ready else self.cfg["model_id"]

        device_pref = self.cfg.get("device", "auto")
        if device_pref == "auto":
            if torch.cuda.is_available():
                self._device = "cuda"
            elif getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
                self._device = "mps"  # Apple Silicon
            else:
                self._device = "cpu"
        else:
            self._device = device_pref
        dtype = getattr(torch, self.cfg.get("dtype", "bfloat16"), torch.bfloat16)

        # 可选构造参数：4bit 量化（省显存、提吞吐）/ flash-attention（省时延）
        extra_kwargs: Dict[str, Any] = {}
        quant = str(self.cfg.get("quantization", "") or "").lower()  # ""|"4bit"|"8bit"
        if quant in ("4bit", "8bit") and self._device == "cuda":
            try:
                from transformers import BitsAndBytesConfig
                if quant == "4bit":
                    extra_kwargs["quantization_config"] = BitsAndBytesConfig(
                        load_in_4bit=True,
                        bnb_4bit_compute_dtype=dtype,
                        bnb_4bit_quant_type="nf4",
                        bnb_4bit_use_double_quant=True,
                    )
                else:
                    extra_kwargs["quantization_config"] = BitsAndBytesConfig(load_in_8bit=True)
                logger.info("VLM quantization enabled: %s", quant)
            except Exception as e:                 # noqa: BLE001
                logger.warning("量化配置不可用(%s)，按全精度加载", e)
        attn_impl = str(self.cfg.get("attn_implementation", "") or "")
        if attn_impl:
            extra_kwargs["attn_implementation"] = attn_impl  # 如 "flash_attention_2"

        logger.info("loading Qwen3.5-4B from %s (device=%s, dtype=%s%s)",
                    model_path, self._device, dtype,
                    f", quant={quant}" if quant else "")
        self._processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
        self._model = AutoModelForImageTextToText.from_pretrained(
            model_path,
            torch_dtype=dtype,
            device_map="auto" if self._device == "cuda" else self._device,
            trust_remote_code=True,
            **extra_kwargs,
        )
        self._model.eval()
        logger.info("Qwen3.5-4B loaded.")

    def _generate(self, system: str, user_text: str, image_paths: List[str],
                  max_new_tokens: Optional[int] = None,
                  max_images: Optional[int] = None) -> str:
        self._load()
        import torch  # noqa: F401

        max_imgs = max_images if (max_images and max_images > 0) \
            else self.cfg.get("max_reps_per_cluster", 6)
        content: List[Dict[str, Any]] = []
        for p in image_paths[:max_imgs]:
            if p and os.path.exists(p):
                content.append({"type": "image", "image": p})
        content.append({"type": "text", "text": user_text})
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

        # 直接用 PIL 读图传给 processor（实测比 qwen_vl_utils.process_vision_info 稳定，
        # 后者在本环境会让 Qwen3.5 视觉 mode-collapse）。
        max_edge = int(self.cfg.get("max_image_edge", 512))
        image_inputs = [_open_image_scaled(p, max_edge)
                        for p in image_paths[:max_imgs] if p and os.path.exists(p)]
        video_inputs = None

        text = self._processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True,
            enable_thinking=self.cfg.get("enable_thinking", False),
        )
        inputs = self._processor(
            text=[text],
            images=image_inputs if image_inputs else None,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self._model.device)

        gen = self._model.generate(
            **inputs,
            max_new_tokens=max_new_tokens or self.cfg.get("max_new_tokens", 512),
            do_sample=False,
        )
        gen_trimmed = gen[:, inputs.input_ids.shape[1]:]
        out = self._processor.batch_decode(
            gen_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False,
        )[0]
        # 关掉 think 模式：去掉 <think>...</think> 段
        out = re.sub(r"<think>[\s\S]*?</think>", "", out, flags=re.DOTALL).strip()
        return out

    def chat(self, system: str, user_text: str, image_paths: List[str]) -> str:
        return self._generate(system, user_text, image_paths)

    def _generate_batch(self, system: str, user_text: str,
                        image_paths: List[str],
                        max_new_tokens: Optional[int] = None) -> List[str]:
        """批量推理：每个样本一张图、同一 system/user_text，一次前向。

        用于相册逐图分类提速。返回与 image_paths 等长的输出文本列表。
        """
        self._load()
        from PIL import Image  # noqa: F401

        # 批量解码必须左 padding，否则生成续接错位
        try:
            self._processor.tokenizer.padding_side = "left"
        except Exception:                          # noqa: BLE001
            pass

        max_edge = int(self.cfg.get("max_image_edge", 512))
        texts: List[str] = []
        images_flat: List[Any] = []
        for p in image_paths:
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "image", "image": p},
                    {"type": "text", "text": user_text},
                ]},
            ]
            t = self._processor.apply_chat_template(
                messages, tokenize=False, add_generation_prompt=True,
                enable_thinking=self.cfg.get("enable_thinking", False),
            )
            texts.append(t)
            images_flat.append(_open_image_scaled(p, max_edge))

        inputs = self._processor(
            text=texts, images=images_flat, videos=None,
            padding=True, return_tensors="pt",
        ).to(self._model.device)

        gen = self._model.generate(
            **inputs,
            max_new_tokens=max_new_tokens or 64,
            do_sample=False,
        )
        gen_trimmed = gen[:, inputs.input_ids.shape[1]:]
        outs = self._processor.batch_decode(
            gen_trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False,
        )
        return [re.sub(r"<think>[\s\S]*?</think>", "", o, flags=re.DOTALL).strip()
                for o in outs]


# ============================================================
# 回退：无模型时的规则版
# ============================================================

class RuleBasedVLM:
    """无 GPU/无模型时的占位实现。"""

    def chat(self, system: str, user_text: str, image_paths: List[str]) -> str:
        if "OCR" in (system or "") or "OCR" in system.upper():
            return ""
        if "category" in (system or "").lower() or "类别" in system:
            return json.dumps({"category": "misc", "confidence": 0.0, "why": "no model"},
                              ensure_ascii=False)
        if "短语" in system or "caption" in system.lower():
            return ""
        # cluster decision
        body = user_text.split("样本:", 1)[1] if "样本:" in user_text else user_text
        words = re.findall(r"[\w\u4e00-\u9fff]{2,}", body)
        stop = {"代表", "样本", "用户", "预设", "类别", "cluster_id", "mode", "asset_id",
                "无", "txt", "png", "jpg", "jpeg", "pdf", "docx", "mp4", "mp3"}
        words = [w for w in words if w.lower() not in stop and not re.fullmatch(r"\d+", w)]
        cid_match = re.search(r"cluster_id=([\w:.\-]+)", user_text)
        cid = cid_match.group(1) if cid_match else "Group"
        name = words[0] if words else f"Group-{cid}"
        return json.dumps({
            "cluster_name": name[:12], "cluster_type": "misc",
            "confidence": 0.4, "needs_split": False, "discard_candidates": [],
            "rationale": "fallback rule-based vlm",
        }, ensure_ascii=False)


# ============================================================
# 对外门面：VLMReasoner 提供 4 类能力
# ============================================================

class VLMReasoner:
    def __init__(self, cfg: Dict[str, Any], force_fallback: bool = False):
        self.cfg = cfg
        self._is_fallback = force_fallback
        if force_fallback:
            logger.info("VLM: forced fallback (RuleBasedVLM)")
            self._impl: Any = RuleBasedVLM()
        else:
            backend = str((cfg.get("vlm", {}) or {}).get("backend", "transformers")).lower()
            try:
                if backend == "ollama":
                    from .ollama_vl import OllamaVLM
                    self._impl = OllamaVLM(cfg)
                    logger.info("VLM backend: ollama")
                else:
                    self._impl = QwenVLM(cfg)
                    logger.info("VLM backend: transformers")
            except Exception as e:                # noqa: BLE001
                logger.warning("VLM init failed (%s); using RuleBasedVLM", e)
                self._impl = RuleBasedVLM()
                self._is_fallback = True

    # ---- 能力 1：cluster 级语义判断 ----

    def review_cluster(
        self,
        cluster: Cluster,
        mode: str,
        categories: Optional[List[str]] = None,
    ) -> ClusterDecision:
        sys_prompt = _DESKTOP_SYS
        head = "用户预设类别：" + (", ".join(categories) if categories else "(无，请自行命名)")
        body = cluster.summary_text()
        rep_ids = ", ".join(a.asset_id for a in cluster.representative_assets())
        user = (f"{head}\n\n[mode={mode}] [cluster_id={cluster.cluster_id}]\n"
                f"代表 asset_id: {rep_ids}\n样本:\n{body}")
        reps = cluster.representative_assets(self.cfg["vlm"].get("max_reps_per_cluster", 6))
        image_paths = [a.path for a in reps if a.kind == "photo"
                       or a.ext in {"png", "jpg", "jpeg", "webp"}]
        try:
            raw = self._impl.chat(sys_prompt, user, image_paths)
        except Exception as e:                    # noqa: BLE001
            logger.warning("VLM chat failed (%s); fallback rule", e)
            raw = RuleBasedVLM().chat(sys_prompt, user, image_paths)
        return _parse_decision(raw)

    # ---- 能力 2：OCR / 截图内容理解 ----

    def ocr(self, image_path: str, max_chars: int = 800) -> str:
        if self._is_fallback:
            try:
                from ..extractors.image_extractor import ocr_image as _legacy_ocr
                return _legacy_ocr(image_path, max_chars=max_chars)
            except Exception:                     # noqa: BLE001
                return ""
        try:
            raw = self._impl._generate(_OCR_SYS, "图中文字：", [image_path], max_new_tokens=300)
            return (raw or "").strip()[:max_chars]
        except Exception as e:                    # noqa: BLE001
            logger.warning("VLM OCR failed: %s", e)
            return ""

    # ---- 能力 3：单图描述（cluster 命名兜底） ----

    @staticmethod
    def _clean_caption(raw: str) -> str:
        """把模型原始输出清洗成可用作描述/文件名的短语（caption 单/批共用）。"""
        line = (raw or "").strip().splitlines()[0] if raw else ""
        line = line.strip(" .。\"'`*-:：")
        for pref in ("The image shows", "This image shows", "The main subject is",
                     "A photo of", "An image of", "The image is"):
            if line.lower().startswith(pref.lower()):
                line = line[len(pref):].strip(" .,:")
        return safe_folder_name(line[:40])

    def caption(self, image_path: str) -> str:
        try:
            raw = self._impl._generate(_CAPTION_SYS, "Subject:", [image_path], max_new_tokens=32)
        except Exception as e:                    # noqa: BLE001
            logger.warning("VLM caption failed: %s", e)
            return ""
        return self._clean_caption(raw)

    def caption_batch(self, image_paths: List[str]) -> List[str]:
        """批量描述：一次前向多张图（transformers 后端真批处理，ollama 逐张），

        返回与 image_paths 等长的描述列表。用于 extract 等大批量场景提速。
        """
        if not image_paths:
            return []
        try:
            raws = self._impl._generate_batch(
                _CAPTION_SYS, "Subject:", image_paths, max_new_tokens=32)
        except Exception as e:                    # noqa: BLE001
            logger.warning("VLM caption_batch failed (%s); fallback per-image", e)
            return [self.caption(p) for p in image_paths]
        return [self._clean_caption(r) for r in raws]

    # ---- 能力 4：桌面文件按主题整批归类（核心：一次/分批调用） ----

    def organize_files(
        self,
        items: List[Dict[str, Any]],
        max_themes: int = 8,
        chunk_size: int = 80,
    ) -> Dict[str, str]:
        """把一批文件按主题归类。

        items: [{"id": str, "name": str, "snippet": str}, ...]
        返回 {id -> 文件夹名}。未覆盖到的 id 由上层兜底为 '杂项'。
        纯文本推理（不带图），文件名 + 摘要即可；超过 chunk_size 时分批，
        分批之间按文件夹名精确合并（同名即同主题）。
        """
        if not items:
            return {}
        if self._is_fallback:
            return {}

        sys_prompt = _ORGANIZE_SYS.format(max_themes=max_themes)
        result: Dict[str, str] = {}
        for start in range(0, len(items), chunk_size):
            chunk = items[start:start + chunk_size]
            lines = []
            for it in chunk:
                name = str(it.get("name", ""))[:80]
                snippet = str(it.get("snippet", "") or "").replace("\n", " ")[:160]
                lines.append(f"- id={it['id']} | {name} :: {snippet}")
            user = "文件清单：\n" + "\n".join(lines) + "\n\n请输出 assignments JSON。"
            try:
                raw = self._impl._generate(sys_prompt, user, [], max_new_tokens=1024)
            except Exception as e:                # noqa: BLE001
                logger.warning("organize_files chunk failed: %s", e)
                continue
            obj = _parse_json(raw, {})
            assigns = obj.get("assignments", {}) if isinstance(obj, dict) else {}
            if isinstance(assigns, dict):
                for k, v in assigns.items():
                    folder = safe_folder_name(str(v)) or "杂项"
                    result[str(k)] = folder
        return result

    @property
    def is_fallback(self) -> bool:
        return self._is_fallback
