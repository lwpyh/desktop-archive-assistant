"""桌面文件聚类（VLM-first）。

流程：
1. 过滤快捷方式（.lnk/.app/.url 等 → 绝不动，直接跳过）
2. 扩展名路由：视频/音频/安装包/压缩包等直接进固定桶
3. 不常用文件检测：atime > 60 天 → "不常用文件"桶
4. AI 语义归入已有文件夹：文件名/内容匹配桌面已有文件夹名 → 直接归入
5. 其余文件 → VLM 主题归类
6. 无 VLM 时回退：按文件名关键词分组
"""
from __future__ import annotations

import os
import re
import time
from collections import defaultdict
from typing import Dict, List, Optional

from ..core import Asset, Cluster
from ..extractors.text_extractor import filename_signal
from ..utils import logger, safe_folder_name


# ---------- 快捷方式过滤 ----------

def _filter_shortcuts(assets: List[Asset]) -> tuple:
    """分离快捷方式和普通文件。快捷方式绝对不动。"""
    shortcuts: List[Asset] = []
    remain: List[Asset] = []
    for a in assets:
        if a.is_shortcut:
            shortcuts.append(a)
        else:
            remain.append(a)
    if shortcuts:
        logger.info("skipped %d shortcuts (lnk/app/url/webloc/desktop)", len(shortcuts))
    return shortcuts, remain


# ---------- 扩展名路由 ----------

def _route_by_extension(assets: List[Asset], rules: Dict[str, List[str]]):
    """把固定扩展名族路由到固定桶；返回 (routed: {bucket->assets}, remain)。"""
    buckets: Dict[str, List[Asset]] = defaultdict(list)
    remain: List[Asset] = []
    ext2bucket = {ext.lower(): name for name, exts in rules.items() for ext in exts}
    for a in assets:
        b = ext2bucket.get(a.ext)
        if b:
            buckets[b].append(a)
        else:
            remain.append(a)
    return buckets, remain


# ---------- 不常用文件检测 ----------

def _route_infrequent(assets: List[Asset], threshold_days: int) -> tuple:
    """按最后访问时间分离不常用文件。返回 (infrequent, remain)。"""
    if threshold_days <= 0:
        return [], assets
    now = time.time()
    cutoff = now - threshold_days * 86400
    infrequent: List[Asset] = []
    remain: List[Asset] = []
    for a in assets:
        at = a.atime or a.mtime or 0.0
        if at and at < cutoff:
            infrequent.append(a)
        else:
            remain.append(a)
    if infrequent:
        logger.info("detected %d infrequently-used files (>%d days)", len(infrequent), threshold_days)
    return infrequent, remain


# ---------- AI 语义归入已有文件夹 ----------

# 含中文字符判定
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")

# 通用/无区分度的文件夹名：不参与零成本关键词匹配（误吞率极高），
# 交给后续 VLM 语义匹配判断；小写比较。
_GENERIC_FOLDERS = {
    "新建文件夹", "未命名文件夹", "未命名", "文档", "文件", "资料", "下载",
    "桌面", "图片", "照片", "截图", "杂项", "其他", "其它", "临时", "备份",
    "documents", "downloads", "desktop", "pictures", "photos", "screenshots",
    "misc", "new folder", "untitled", "untitled folder", "files", "stuff",
    "doc", "docs", "temp", "tmp", "backup", "archive", "archives",
}


def _tokenize_ascii(text: str) -> set:
    """把字符串切成小写 ASCII 词 token（按非字母数字 + 驼峰边界切分）。"""
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    return {t for t in re.split(r"[^A-Za-z0-9]+", text.lower()) if t}


def _keyword_hit(stem: str, folders_norm: List[tuple]) -> Optional[str]:
    """文件名 stem 与已有文件夹做「高精度」关键词匹配（宁缺毋滥）。

    folders_norm: [(folder_lower, original_name), ...]，已过滤通用名。
    规则：
    - 中文文件夹名：长度 >= 2 且作为整体子串出现在 stem 中。
    - 英文/数字文件夹名：长度 >= 2 且作为「完整 token」出现（不再裸子串包含，
      避免 'AI' 命中 email/main、'doc' 命中 docker 之类的误匹配；完整 token 匹配
      下 2 字母短名如 AI/PR/QA 也安全，因为只匹配独立 token 而非任意子串）。
    - 多个命中时取最长（最具体）的文件夹名，降低歧义。
    """
    stem_lower = stem.lower()
    stem_tokens = _tokenize_ascii(stem)
    best: Optional[str] = None
    best_len = 0
    for fl, orig in folders_norm:
        hit = False
        if _CJK_RE.search(fl):
            if len(fl) >= 2 and fl in stem_lower:
                hit = True
        else:
            if len(fl) >= 2 and fl in stem_tokens:
                hit = True
        if hit and len(fl) > best_len:
            best, best_len = orig, len(fl)
    return best


def _match_existing_folders(
    assets: List[Asset],
    existing_folders: List[str],
    vlm=None,
) -> tuple:
    """尝试将文件语义匹配到桌面上已有的文件夹。

    策略：
    1. 先做文件名关键词匹配（零成本，高精度，宁缺毋滥）
    2. VLM 可用时，对未匹配文件做语义匹配
    返回 (matched: {folder_name -> [assets]}, remain)
    """
    if not existing_folders:
        return {}, assets

    matched: Dict[str, List[Asset]] = defaultdict(list)
    remain: List[Asset] = []

    # 1) 关键词匹配：仅用「文件名本身」（不含父目录名/正文），中文整词子串、
    #    英文完整 token，并过滤通用文件夹名 —— 避免把无关文件误塞进已有文件夹。
    folders_norm = [
        (f.lower(), f) for f in existing_folders
        if f.lower() not in _GENERIC_FOLDERS and len(f.strip()) >= 2
    ]
    for a in assets:
        stem = os.path.splitext(os.path.basename(a.path))[0]
        hit = _keyword_hit(stem, folders_norm) if folders_norm else None
        if hit:
            matched[hit].append(a)
        else:
            remain.append(a)

    if matched:
        total = sum(len(v) for v in matched.values())
        logger.info("keyword-matched %d files into %d existing folders", total, len(matched))

    # 2) VLM 语义匹配：对剩余文件让 VLM 判断归属
    if remain and vlm and not getattr(vlm, "is_fallback", True) and existing_folders:
        items = [{"id": a.asset_id, "name": os.path.basename(a.path),
                  "snippet": _asset_digest(a)} for a in remain]
        # 用 organize_files 但传入已有文件夹名作为约束
        assign = _vlm_match_to_folders(vlm, items, existing_folders)
        still_remain: List[Asset] = []
        for a in remain:
            folder = assign.get(a.asset_id)
            if folder and folder in existing_folders:
                matched[folder].append(a)
            else:
                still_remain.append(a)
        remain = still_remain
        if matched:
            total = sum(len(v) for v in matched.values())
            logger.info("VLM-matched total %d files into existing folders", total)

    return dict(matched), remain


def _vlm_match_to_folders(vlm, items: List[dict], existing_folders: List[str]) -> Dict[str, str]:
    """让 VLM 判断每个文件应归入哪个已有文件夹（或都不匹配）。"""
    import json
    sys_prompt = (
        "你是文件归档助手。桌面上已有以下文件夹：\n"
        + ", ".join(existing_folders)
        + "\n\n给你一批文件（每条含 id、文件名、内容摘要），请判断每个文件是否应该归入上面某个已有文件夹。\n"
        "- 只在语义明确匹配时才归入（如 'Q4预算表.xlsx' → '工作'）\n"
        "- 不确定的不归入，输出 null\n"
        "- 输出格式：{\"assignments\":{\"<id>\":\"<文件夹名或null>\"}}\n"
        "只输出 JSON，禁止解释。"
    )
    result: Dict[str, str] = {}
    chunk = 80
    for start in range(0, len(items), chunk):
        chunk_items = items[start:start + chunk]
        lines = []
        for it in chunk_items:
            name = str(it.get("name", ""))[:80]
            snippet = str(it.get("snippet", "") or "").replace("\n", " ")[:160]
            lines.append(f"- id={it['id']} | {name} :: {snippet}")
        user = "文件清单：\n" + "\n".join(lines) + "\n\n请输出 assignments JSON。"
        try:
            raw = vlm._impl._generate(sys_prompt, user, [], max_new_tokens=1024)
            obj = _parse_json_safe(raw)
            assigns = obj.get("assignments", {}) if isinstance(obj, dict) else {}
            if isinstance(assigns, dict):
                for k, v in assigns.items():
                    if v and v != "null":
                        result[str(k)] = str(v)
        except Exception as e:
            logger.warning("VLM match_to_folders failed: %s", e)
    return result


def _parse_json_safe(raw: str) -> dict:
    import json
    if not raw:
        return {}
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        return {}


def _asset_digest(a: Asset) -> str:
    """文件内容摘要：文件名信号 + OCR/正文片段（截断）。"""
    signal = filename_signal(a.path) or os.path.basename(a.path)
    body = (a.ocr_text or a.body_text or "").strip().replace("\n", " ")
    return (f"{signal} {body}").strip()[:200]


# ---------- 回退：文件名关键词分组 ----------

_WORD_RE = re.compile(r"[\w\u4e00-\u9fff]{2,}")
_STOP = {"img", "image", "screenshot", "screen", "shot", "photo", "copy", "final",
         "draft", "new", "untitled", "document", "file", "download"}


def _fallback_group(assets: List[Asset]) -> List[Cluster]:
    """无 VLM 时：用文件名第一个有意义词 + 扩展名做粗分组。"""
    groups: Dict[str, List[Asset]] = defaultdict(list)
    for a in assets:
        sig = filename_signal(a.path).lower()
        words = [w for w in _WORD_RE.findall(sig)
                 if w not in _STOP and not w.isdigit()]
        key = words[0] if words else (a.ext or "misc")
        groups[key].append(a)
    clusters: List[Cluster] = []
    for i, (key, items) in enumerate(groups.items()):
        clusters.append(Cluster(
            cluster_id=f"kw::{i:03d}",
            assets=items,
            label=safe_folder_name(key).capitalize(),
            confidence=0.95,
            rationale=f"keyword group '{key}' (no-vlm fallback)",
        ))
    return clusters


# ---------- 入口 ----------

def cluster_desktop(
    assets: List[Asset],
    cfg: dict,
    vlm=None,
    existing_folders: Optional[List[str]] = None,
) -> List[Cluster]:
    # 1) 过滤快捷方式
    shortcuts, assets = _filter_shortcuts(assets)

    # 2) 扩展名路由
    rules = cfg.get("desktop_routing_rules", {})
    buckets, remain = _route_by_extension(assets, rules)

    clusters: List[Cluster] = []

    # 2a) 规则桶
    for name, items in buckets.items():
        if not items:
            continue
        clusters.append(Cluster(
            cluster_id=f"rule::{name}",
            assets=items,
            label=name.capitalize(),
            confidence=1.0,
            rationale=f"routed by extension rule '{name}'",
        ))

    # 3) 不常用文件检测
    infrequent_threshold = cfg.get("infrequent_threshold_days", 60)
    infrequent, remain = _route_infrequent(remain, infrequent_threshold)
    if infrequent:
        clusters.append(Cluster(
            cluster_id="rule::infrequent",
            assets=infrequent,
            label="不常用文件",
            confidence=1.0,
            rationale=f"last accessed > {infrequent_threshold} days ago",
        ))

    if not remain:
        logger.info("desktop clustering -> %d clusters (rule+infrequent only)", len(clusters))
        return clusters

    # 4) AI 语义归入已有文件夹
    if existing_folders is None:
        existing_folders = []
    matched, remain = _match_existing_folders(remain, existing_folders, vlm=vlm)
    for folder_name, items in matched.items():
        if not items:
            continue
        clusters.append(Cluster(
            cluster_id=f"existing::{folder_name}",
            assets=items,
            label=folder_name,
            confidence=1.0,
            rationale=f"matched existing folder '{folder_name}'",
        ))

    if not remain:
        logger.info("desktop clustering -> %d clusters (all matched to existing folders)", len(clusters))
        return clusters

    # 5) VLM 主题归类
    use_vlm = vlm is not None and not getattr(vlm, "is_fallback", True)
    if use_vlm:
        items = [{"id": a.asset_id, "name": os.path.basename(a.path),
                  "snippet": _asset_digest(a)} for a in remain]
        max_themes = int(cfg.get("clustering", {}).get("desktop", {}).get("max_themes", 8))
        chunk = int(cfg.get("vlm", {}).get("max_files_per_call", 80))
        assign = vlm.organize_files(items, max_themes=max_themes, chunk_size=chunk)

        by_theme: Dict[str, List[Asset]] = defaultdict(list)
        for a in remain:
            theme = assign.get(a.asset_id) or "杂项"
            by_theme[theme].append(a)

        for i, (theme, items_) in enumerate(by_theme.items()):
            clusters.append(Cluster(
                cluster_id=f"theme::{i:03d}",
                assets=items_,
                label=safe_folder_name(theme),
                confidence=0.95,
                rationale=f"VLM theme grouping -> '{theme}'",
            ))
        logger.info("desktop clustering -> %d clusters (rule=%d, infrequent=%d, existing=%d, vlm-theme=%d, shortcuts_skipped=%d)",
                    len(clusters), len(buckets), len(infrequent), len(matched), len(by_theme), len(shortcuts))
        return clusters

    # 6) 无 VLM 回退
    clusters.extend(_fallback_group(remain))
    logger.info("desktop clustering (no-vlm) -> %d clusters (shortcuts_skipped=%d)", len(clusters), len(shortcuts))
    return clusters
