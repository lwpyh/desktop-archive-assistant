"""根据已打标的 cluster，生成可执行的 ArchivePlan。"""
from __future__ import annotations

import os
import time
from typing import Dict, List, Optional

from ..core import ArchivePlan, Cluster, PlanAction
from ..vlm import VLMReasoner
from ..utils import safe_folder_name, logger, track


def _category_root(root: str, mode: str, label: str) -> str:
    # 桌面整理：分类文件夹直接建在桌面根目录下，不再套 _archived 外层
    if mode == "desktop":
        return os.path.join(root, safe_folder_name(label))
    # 相册等其他模式：仍统一归入 _albums 便于集中管理
    return os.path.join(root, "_albums", safe_folder_name(label))


def _resolve_target_dir(
    root: str, mode: str, label: str, existing_folders: Optional[List[str]] = None,
) -> tuple:
    """决定目标目录路径。

    如果 label 匹配已有文件夹 → 直接归入已有文件夹
    否则 → 桌面模式建在 root 下，相册模式建在 _albums 下
    返回 (target_dir, is_existing_folder)
    """
    # 匹配已有文件夹（精确匹配或安全化后匹配）
    if existing_folders:
        safe_label = safe_folder_name(label)
        for folder_name in existing_folders:
            if safe_folder_name(folder_name) == safe_label or folder_name == label:
                target = os.path.join(root, folder_name)
                return target, True

    # 默认：桌面建在 root 下，相册建在 _albums 下
    return _category_root(root, mode, label), False


def build_plan(
    root: str,
    mode: str,
    clusters: List[Cluster],
    vlm: VLMReasoner,
    categories: Optional[List[str]] = None,
    existing_folders: Optional[List[str]] = None,
) -> ArchivePlan:
    plan = ArchivePlan(root=root, mode=mode, created_at=time.time())
    seen_dirs: set[str] = set()
    label_count: Dict[str, int] = {}     # 同名 label 自动加序号

    # 簇级 VLM 评审/命名是耗时点（每个非规则桶一次 VLM 推理），簇多时易超 30s。
    for cluster in track(clusters, label="归类命名", est_per_item=1.0):
        # 规则桶（已有 label 且 confidence 高）跳过 VLM
        if cluster.label and cluster.confidence >= 0.95:
            decision_label = cluster.label
            cluster.rationale = (cluster.rationale or "") + " | rule-based, skip VLM"
        # zero-shot 类别桶（已是用户预设 category）也跳过 VLM
        elif cluster.category and cluster.label and cluster.cluster_id.startswith("cat::"):
            decision_label = cluster.label
            cluster.rationale = (cluster.rationale or "") + " | zero-shot, skip VLM"
        else:
            decision = vlm.review_cluster(cluster, mode=mode, categories=categories)
            cluster.label = decision.cluster_name
            cluster.category = decision.cluster_type
            cluster.confidence = decision.confidence
            cluster.rationale = decision.rationale or cluster.rationale
            decision_label = decision.cluster_name
            disc = set(decision.discard_candidates or [])
            for a in cluster.assets:
                if a.asset_id in disc:
                    a.risk_level = "medium"
            # 命名兜底：review_cluster 返回 "Misc" 时再尝试 caption 一张代表图
            if (decision_label.lower() == "misc"
                    and not getattr(vlm, "is_fallback", True)
                    and cluster.assets):
                rep = cluster.representative_assets(1)[0]
                if rep.kind == "photo" or rep.ext in {"png", "jpg", "jpeg", "webp"}:
                    try:
                        cap = vlm.caption(rep.path)
                        if cap and cap.lower() != "misc":
                            decision_label = cap
                            cluster.label = cap
                            cluster.rationale += " | caption fallback"
                    except Exception:
                        pass

        # 同名 label 去重：第一次直用，第 2/3/... 次自动加序号
        base_label = decision_label or "Misc"
        used = label_count.get(base_label, 0)
        final_label = base_label if used == 0 else f"{base_label} {used + 1}"
        label_count[base_label] = used + 1
        cluster.label = final_label

        target_dir, is_existing = _resolve_target_dir(root, mode, final_label, existing_folders)
        if not is_existing and target_dir not in seen_dirs:
            plan.actions.append(PlanAction(op="mkdir", dst=target_dir,
                                           note=f"cluster={cluster.cluster_id}"))
            seen_dirs.add(target_dir)
        elif is_existing:
            # 已有文件夹：不创建 mkdir action，只往里放文件
            seen_dirs.add(target_dir)

        # 为每个 asset 生成 move
        for a in cluster.assets:
            if a.risk_level == "medium":
                # 重复/废片 → 回收站（绝不硬删除）；最终回收站路径由 executor 在 apply 时确定
                plan.actions.append(PlanAction(op="trash", src=a.path,
                                               dst=os.path.basename(a.path),
                                               note=f"duplicate/discard in {cluster.cluster_id}"))
            else:
                dst = os.path.join(target_dir, os.path.basename(a.path))
                if os.path.abspath(dst) == os.path.abspath(a.path):
                    continue
                plan.actions.append(PlanAction(op="move", src=a.path, dst=dst,
                                               note=cluster.cluster_id))

        plan.clusters.append(cluster)

    logger.info("plan built: %d actions, %d clusters", len(plan.actions), len(plan.clusters))
    return plan
