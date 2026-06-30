"""统一资产数据结构（asset / cluster / plan）。"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict, Any
import hashlib
import os


def _stable_id(path: str) -> str:
    return hashlib.sha1(path.encode("utf-8")).hexdigest()[:12]


@dataclass
class Asset:
    """单个文件/照片资产。file 级特征载体。"""
    asset_id: str
    path: str                                # 绝对路径
    kind: str                                # "file" | "photo"
    ext: str                                 # 不含点
    size_bytes: int
    mtime: float                             # 修改时间戳
    ctime: Optional[float] = None
    exif_time: Optional[float] = None        # 仅照片：拍摄时间
    ocr_text: str = ""
    body_text: str = ""                      # PDF/DOCX 抽出的正文（截断）
    perceptual_hash: Optional[str] = None    # 仅照片：imagehash 16-hex
    text_embedding: Optional[List[float]] = None
    visual_embedding: Optional[List[float]] = None   # CLIP 或颜色直方图
    cluster_id: Optional[str] = None
    risk_level: str = "low"                  # low | medium | high
    is_shortcut: bool = False                # 快捷方式：绝对不动
    atime: Optional[float] = None            # 最后访问时间（用于不常用文件检测）

    @classmethod
    def from_path(cls, path: str, kind: str) -> "Asset":
        st = os.stat(path)
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        return cls(
            asset_id=_stable_id(path),
            path=os.path.abspath(path),
            kind=kind,
            ext=ext,
            size_bytes=st.st_size,
            mtime=st.st_mtime,
            ctime=getattr(st, "st_ctime", None),
            atime=getattr(st, "st_atime", None),
        )

    def best_time(self) -> float:
        # 优先级：EXIF 拍摄时间 > mtime（最贴近"内容"时间）> ctime
        # 注意 Linux 下 ctime 是 inode 变更时间，对照片含义不大
        return self.exif_time or self.mtime or self.ctime or 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # embedding 太长，序列化时丢掉
        d.pop("text_embedding", None)
        d.pop("visual_embedding", None)
        return d


@dataclass
class Cluster:
    cluster_id: str
    assets: List[Asset] = field(default_factory=list)
    label: Optional[str] = None              # 由 VLM 或规则填入
    category: Optional[str] = None           # 用户预设类别名（照片场景）
    confidence: float = 0.0
    rationale: str = ""

    def representative_assets(self, n: int = 6) -> List[Asset]:
        # 简单采样：首、中、尾 + 时间分散
        if len(self.assets) <= n:
            return list(self.assets)
        sorted_a = sorted(self.assets, key=lambda a: a.best_time())
        step = max(1, len(sorted_a) // n)
        return sorted_a[::step][:n]

    def summary_text(self) -> str:
        bits = []
        for a in self.assets[:20]:
            name = os.path.basename(a.path)
            snippet = (a.ocr_text or a.body_text or "").strip().replace("\n", " ")[:120]
            bits.append(f"- {name} :: {snippet}")
        return "\n".join(bits)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "cluster_id": self.cluster_id,
            "label": self.label,
            "category": self.category,
            "confidence": self.confidence,
            "rationale": self.rationale,
            "assets": [a.to_dict() for a in self.assets],
        }


@dataclass
class PlanAction:
    op: str                                  # "mkdir" | "move" | "trash"
    src: Optional[str] = None
    dst: Optional[str] = None
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ArchivePlan:
    root: str                                # 操作根目录
    mode: str                                # "desktop" | "photo"
    actions: List[PlanAction] = field(default_factory=list)
    clusters: List[Cluster] = field(default_factory=list)
    created_at: float = 0.0
    feedback: Dict[str, Any] = field(default_factory=dict)   # 未归类反馈（多轮交互用）

    def to_dict(self) -> Dict[str, Any]:
        return {
            "root": self.root,
            "mode": self.mode,
            "created_at": self.created_at,
            "feedback": self.feedback,
            "actions": [a.to_dict() for a in self.actions],
            "clusters": [c.to_dict() for c in self.clusters],
        }
