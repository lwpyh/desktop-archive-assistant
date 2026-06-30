"""Ollama 后端 VLM —— 用本地 ollama 服务替代 transformers 本地加载。

适用场景：已通过 ollama 拉取了支持视觉的模型（如 qwen3.5:4b，family=qwen35，
capabilities 含 vision），想让 skill 直接调用 ollama 而不是本地 transformers
加载 HuggingFace 权重。

设计要点：
- 接口与 QwenVLM 完全一致（chat / _generate / _generate_batch），可被
  VLMReasoner 透明替换，上层 VLMReasoner 的 4 类能力无需改动。
- 图像经 base64 编码放入 ollama chat 的 images 字段（纯文本请求不带图）。
- 仅依赖标准库 urllib，不新增 requests 依赖。
- _load 仅检查 ollama 可达 + 模型存在，不下载任何 HF 权重。
- 失败抛 RuntimeError，由 VLMReasoner 捕获后降级为 RuleBasedVLM。
"""
from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from ..utils import logger


# 用拼接构造 think 标签正则，避免标签字面量在源码中被误处理
_THINK_OPEN = "<" + "think" + ">"
_THINK_CLOSE = "</" + "think" + ">"
_THINK_RE = re.compile(_THINK_OPEN + r"[\s\S]*?" + _THINK_CLOSE, re.DOTALL)


def _strip_think(text: str) -> str:
    """去掉模型可能输出的 think 思考段（与 QwenVLM 行为一致）。"""
    return _THINK_RE.sub("", text or "").strip()


def _image_to_b64(path: str, max_edge: int = 512) -> str:
    """读图并 base64 编码；可选把最长边缩到 max_edge 以降低上传体积/视觉 token。

    PIL 不可用或缩放失败时，回退为原始字节，保证可用性。
    """
    if max_edge and max_edge > 0:
        try:
            import io
            from PIL import Image
            with Image.open(path) as im:
                im = im.convert("RGB")
                if max(im.size) > max_edge:
                    im.thumbnail((max_edge, max_edge), Image.LANCZOS)
                buf = io.BytesIO()
                im.save(buf, format="JPEG", quality=90)
                return base64.b64encode(buf.getvalue()).decode("ascii")
        except Exception:  # noqa: BLE001
            pass
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


class OllamaVLM:
    """走 ollama /api/chat 的 VLM 后端。延迟加载，失败抛 RuntimeError。

    配置（config.yaml -> vlm）：
      backend: "ollama"
      ollama:
        host: "http://127.0.0.1:11434"
        model: "qwen3.5:4b"
        timeout: 120
    """

    def __init__(self, cfg: Dict[str, Any]):
        self.cfg = cfg.get("vlm", cfg)
        # 兼容两种写法：嵌套 vlm.ollama.{host,model,...} 与平铺 vlm.ollama_{host,model}。
        # 历史 config 用平铺键，若只认嵌套键会导致配置被静默忽略、回退到默认模型。
        oc = self.cfg.get("ollama", {}) or {}
        self._host = str(
            oc.get("host") or self.cfg.get("ollama_host") or "http://127.0.0.1:11434"
        ).rstrip("/")
        self._model = str(
            oc.get("model") or self.cfg.get("ollama_model") or "qwen3.5:4b"
        )
        self._timeout = int(oc.get("timeout", self.cfg.get("ollama_timeout", 120)))
        self._concurrency = max(
            1, int(oc.get("concurrency", self.cfg.get("ollama_concurrency", 4)))
        )
        self._max_images = int(self.cfg.get("max_reps_per_cluster", 6))
        self._default_tokens = int(self.cfg.get("max_new_tokens", 512))
        self._enable_thinking = bool(self.cfg.get("enable_thinking", False))
        self._ready: Optional[bool] = None

    # ---------- 连通性 / 模型就绪 ----------

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self._host}/api/chat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:  # noqa: S310
            body = resp.read().decode("utf-8")
        obj = json.loads(body)
        if not isinstance(obj, dict):
            raise RuntimeError(f"ollama 返回非对象: {body[:200]}")
        return obj

    def _list_models(self) -> List[str]:
        req = urllib.request.Request(f"{self._host}/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
            obj = json.loads(resp.read().decode("utf-8"))
        return [str(m.get("name", "")) for m in obj.get("models", [])]

    def _load(self) -> None:
        """检查 ollama 可达且目标模型已存在。失败抛 RuntimeError。"""
        if self._ready:
            return
        try:
            models = self._list_models()
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"无法连接 ollama 服务 ({self._host})：{e.reason}。"
                f"请确认 ollama 已启动（ollama serve）且 host 配置正确。"
            )
        except Exception as e:  # noqa: BLE001
            raise RuntimeError(f"查询 ollama 模型列表失败: {e}")

        base = self._model.split(":", 1)[0]
        exists = self._model in models or any(
            m.split(":", 1)[0] == base for m in models
        )
        if not exists:
            raise RuntimeError(
                f"ollama 中未找到模型 {self._model}（可用: {', '.join(models) or '无'}）。"
                f"请运行: ollama pull {self._model}"
            )
        self._ready = True
        logger.info("OllamaVLM 就绪: host=%s model=%s", self._host, self._model)

    # ---------- 推理 ----------

    def _build_messages(
        self, system: str, user_text: str, image_paths: List[str],
        max_images: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        imgs: List[str] = []
        max_edge = int(self.cfg.get("max_image_edge", 512))
        limit = max_images if (max_images and max_images > 0) else self._max_images
        for p in image_paths[:limit]:
            if p and os.path.exists(p):
                try:
                    imgs.append(_image_to_b64(p, max_edge))
                except OSError as e:
                    logger.warning("读取图像失败 %s: %s", p, e)
        user_msg: Dict[str, Any] = {"role": "user", "content": user_text}
        if imgs:
            user_msg["images"] = imgs
        messages: List[Dict[str, Any]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(user_msg)
        return messages

    def _generate(
        self,
        system: str,
        user_text: str,
        image_paths: List[str],
        max_new_tokens: Optional[int] = None,
        max_images: Optional[int] = None,
    ) -> str:
        self._load()
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": self._build_messages(system, user_text, image_paths, max_images),
            "stream": False,
            "think": self._enable_thinking,
            "options": {
                "num_predict": max_new_tokens or self._default_tokens,
                "temperature": 0,
            },
        }
        obj = self._post(payload)
        if obj.get("error"):
            raise RuntimeError(f"ollama 返回错误: {obj['error']}")
        content = str(obj.get("message", {}).get("content", ""))
        return _strip_think(content)

    def chat(self, system: str, user_text: str, image_paths: List[str]) -> str:
        return self._generate(system, user_text, image_paths)

    def _generate_batch(
        self,
        system: str,
        user_text: str,
        image_paths: List[str],
        max_new_tokens: Optional[int] = None,
    ) -> List[str]:
        """ollama 无原生批量接口：用线程池并发逐张请求，返回等长输出列表。

        ollama 服务端可并行处理多请求，客户端并发能显著缩短大批量墙钟。
        并发度由 config.yaml -> vlm.ollama.concurrency 控制（默认 4）。
        """
        self._load()
        if not image_paths:
            return []

        def _one(p: str) -> str:
            try:
                return self._generate(system, user_text, [p], max_new_tokens=max_new_tokens)
            except Exception as e:  # noqa: BLE001
                logger.warning("ollama 批量推理单条失败: %s", e)
                return ""

        workers = min(self._concurrency, len(image_paths))
        if workers <= 1:
            return [_one(p) for p in image_paths]

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=workers) as ex:
            return list(ex.map(_one, image_paths))
