"""未归类反馈：分类完成后，识别落入「其他/杂项」的资产，生成结构化反馈。

用途：
- 当用户给的类别不全时，明确告诉用户有多少、哪些照片/文件没归进给定类别；
- 把这些信息结构化写进 plan.json，为后续多轮交互（用户补充类别 → 重新分类）做准备。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from .core import ArchivePlan, Cluster

# 视为「未归类」桶的标识
_MISC_LABELS = {"其他", "杂项", "misc", "Misc"}
_MISC_ID_SUFFIX = "__misc__"


def _is_misc_cluster(c: Cluster) -> bool:
    if c.cluster_id and c.cluster_id.endswith(_MISC_ID_SUFFIX):
        return True
    return (c.label or "").strip() in _MISC_LABELS


def build_feedback(
    plan: ArchivePlan,
    mode: str,
    categories: Optional[List[str]] = None,
    sample_n: int = 10,
) -> Dict[str, Any]:
    """汇总落入「其他/杂项」的资产，返回结构化反馈 dict。无未归类时返回 {}。"""
    misc_assets = []
    misc_label = "其他" if mode == "photo" else "杂项"
    for c in plan.clusters:
        if _is_misc_cluster(c):
            misc_label = c.label or misc_label
            misc_assets.extend(c.assets)

    total = sum(len(c.assets) for c in plan.clusters)
    n_misc = len(misc_assets)
    if n_misc == 0:
        return {}

    sample = [os.path.basename(a.path) for a in misc_assets[:sample_n]]
    feedback = {
        "uncategorized_label": misc_label,
        "uncategorized_count": n_misc,
        "total": total,
        "ratio": round(n_misc / total, 3) if total else 0.0,
        "given_categories": list(categories) if categories else [],
        "sample_files": sample,
        # 为多轮交互保留完整路径，便于用户补充类别后只对这些重分
        "uncategorized_paths": [a.path for a in misc_assets],
    }
    return feedback


def format_feedback(feedback: Dict[str, Any]) -> str:
    """把反馈 dict 渲染成给用户看的提示文本。"""
    if not feedback:
        return ""
    label = feedback.get("uncategorized_label", "其他")
    n = feedback.get("uncategorized_count", 0)
    total = feedback.get("total", 0)
    cats = feedback.get("given_categories") or []
    samples = feedback.get("sample_files") or []

    lines = [
        "",
        "─" * 48,
        f"⚠ 有 {n}/{total} 个文件不属于你给定的类别"
        + (f"（{', '.join(cats)}）" if cats else "")
        + f"，已归入「{label}」。",
    ]
    if samples:
        shown = ", ".join(samples)
        more = n - len(samples)
        lines.append(f"  示例：{shown}" + (f" 等，另有 {more} 个" if more > 0 else ""))
    lines.append("  如需更精细的整理，可补充类别后重跑（仅会重新分配这些文件）。")
    lines.append("─" * 48)
    return "\n".join(lines)
