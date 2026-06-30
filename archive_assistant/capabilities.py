"""桌面整理能力集 — 每个能力独立可调用，按需组合。

能力清单（17 个）：
  scan         — 扫描目录，构建 Asset 列表（跳过快捷方式/系统文件）
  enrich       — 文件特征灌注（正文/OCR/VLM caption）
  classify     — 主题归类（扩展名路由 + 不常用检测 + 已有文件夹匹配 + VLM 主题）
  rename       — 批量重命名（按模板/时间排序/序号）
  find         — 查找/搜索文件（按名称/类型/时间/大小）
  dedupe       — 文件去重（哈希/文件名模式，移入回收站不删除）
  sync         — 增量同步（目录间同步，只增不减）
  inspect      — 文件巡检（扫描新增文件，列清单）
  move         — 批量移动（按规则/匹配移动文件到指定目录）
  clean        — 清理（临时文件/空文件夹/被占用文件检测）
  backup       — 整理前完整备份
  plan         — 生成归档计划（dry-run JSON）
  apply        — 执行计划（mkdir/move/trash，绝不删除）
  rollback     — 回撤
  sort_desktop — 桌面图标排列（消除空位/按类型排序）
  report       — 输出 Markdown 整理报告
  schedule     — 定时整理（cron 集成）
"""
from __future__ import annotations

import json
import os
import shutil
import time
from typing import Any, Dict, List, Optional

from .core import ArchivePlan, Asset, Cluster, PlanAction
from .extractors import enrich_files, scan_desktop
from .extractors.scanner import list_existing_folders
from .clustering import cluster_desktop
from .planner import build_plan
from .executor import preview, save_plan, load_plan, apply_plan, rollback, list_logs
from .feedback import build_feedback, format_feedback
from .utils import expand, ensure_dir, logger, platform_name, default_desktop_dir, track, ProgressTracker


# ============================================================
# 能力 1：scan — 扫描目录
# ============================================================

def capability_scan(root: str, recursive: bool = False, max_depth: int = 1) -> List[Asset]:
    """扫描目录，返回 Asset 列表。快捷方式/系统文件自动跳过。"""
    root = expand(root)
    assets = scan_desktop(root, recursive=recursive, max_depth=max_depth)
    n_shortcuts = sum(1 for a in assets if a.is_shortcut)
    logger.info("[scan] %d files (%d shortcuts skipped) under %s", len(assets), n_shortcuts, root)
    return assets


# ============================================================
# 能力 2：enrich — 文件特征灌注
# ============================================================

def capability_enrich(assets: List[Asset], vlm=None) -> None:
    """为文件抽取正文/OCR/caption，原地修改 assets。"""
    enrich_files(assets, vlm=vlm)
    logger.info("[enrich] enriched %d assets", len(assets))


# ============================================================
# 能力 3：classify — 主题归类
# ============================================================

def capability_classify(
    assets: List[Asset],
    cfg: dict,
    vlm=None,
    root: Optional[str] = None,
    skip_shortcuts: bool = True,
    use_existing_folders: bool = True,
    use_infrequent: bool = True,
    use_vlm_theme: bool = True,
) -> List[Cluster]:
    """主题归类：扩展名路由 → 不常用检测 → 已有文件夹匹配 → VLM 主题。

    可通过参数开关各子能力，灵活组合。
    """
    # 过滤快捷方式
    if skip_shortcuts:
        assets = [a for a in assets if not a.is_shortcut]

    # 已有文件夹列表
    existing_folders: List[str] = []
    if use_existing_folders and root:
        existing_folders = list_existing_folders(root)

    # 不常用文件：如果关闭，临时把 threshold 设为 0
    effective_cfg = dict(cfg)
    if not use_infrequent:
        effective_cfg["infrequent_threshold_days"] = 0

    # VLM 主题
    effective_vlm = vlm if use_vlm_theme else None

    clusters = cluster_desktop(
        assets, effective_cfg, vlm=effective_vlm, existing_folders=existing_folders,
    )
    logger.info("[classify] %d clusters generated", len(clusters))
    return clusters


# ============================================================
# 能力 4：rename — 批量重命名
# ============================================================

def capability_rename(
    root: str,
    template: str = "{seq}",
    sort_by: str = "time",
    start: int = 1,
    step: int = 1,
    ext_filter: Optional[List[str]] = None,
    dry_run: bool = True,
) -> List[Dict[str, str]]:
    """批量重命名文件。

    template 支持占位符:
      {seq}     — 序号（从 start 开始，步长 step）
      {seq2}    — 二级序号（组号_序号，如 1_1, 1_2, 2_1）
      {name}    — 原文件名（不含扩展名）
      {ext}     — 扩展名
      {date}    — 文件日期 YYYYMMDD
      {time}    — 文件时间 HHMMSS
    sort_by: "time"(创建时间) | "name"(文件名) | "size"(大小)
    ext_filter: 只处理这些扩展名，None=全部
    返回 [{old_path, new_path}] 列表
    """
    import re as _re
    root = expand(root)
    assets = scan_desktop(root, recursive=False, max_depth=1)
    # 过滤快捷方式
    assets = [a for a in assets if not a.is_shortcut]
    if ext_filter:
        ext_set = {e.lower().lstrip(".") for e in ext_filter}
        assets = [a for a in assets if a.ext in ext_set]

    # 排序
    if sort_by == "name":
        assets.sort(key=lambda a: os.path.basename(a.path).lower())
    elif sort_by == "size":
        assets.sort(key=lambda a: a.size_bytes)
    else:  # time
        assets.sort(key=lambda a: a.best_time())

    results = []
    seq = start
    group_size = step
    group_num = 1
    in_group = 0

    for a in track(assets, label="批量重命名", est_per_item=0.02):
        import datetime
        dt = datetime.datetime.fromtimestamp(a.best_time())
        old_name = os.path.splitext(os.path.basename(a.path))[0]

        # 计算 {seq2} 组号_序号
        in_group += 1
        if in_group > group_size:
            in_group = 1
            group_num += 1

        new_name = template.format(
            seq=seq,
            seq2=f"{group_num}_{in_group}",
            name=old_name,
            ext=a.ext,
            date=dt.strftime("%Y%m%d"),
            time=dt.strftime("%H%M%S"),
        )
        new_path = os.path.join(os.path.dirname(a.path), f"{new_name}.{a.ext}")
        if os.path.abspath(new_path) == os.path.abspath(a.path):
            seq += 1
            continue
        # 冲突检测
        if os.path.exists(new_path):
            base, ext = os.path.splitext(new_path)
            i = 1
            while os.path.exists(f"{base}-{i}{ext}"):
                i += 1
            new_path = f"{base}-{i}{ext}"

        results.append({"old_path": a.path, "new_path": new_path})
        if not dry_run:
            shutil.move(a.path, new_path)
        seq += 1

    action = "renamed" if not dry_run else "would rename"
    logger.info("[rename] %s %d files (template=%s, sort_by=%s)", action, len(results), template, sort_by)
    return results


# ============================================================
# 能力 5：find — 查找/搜索文件
# ============================================================

def capability_find(
    root: str,
    name: Optional[str] = None,
    ext: Optional[List[str]] = None,
    min_size: Optional[int] = None,
    max_size: Optional[int] = None,
    modified_since: Optional[float] = None,
    modified_before: Optional[float] = None,
    recursive: bool = True,
) -> List[Asset]:
    """按条件查找文件。

    name: 文件名模糊匹配（支持通配符）
    ext: 扩展名过滤，如 ["xlsx", "pdf"]
    min_size/max_size: 字节
    modified_since/before: Unix 时间戳
    """
    import fnmatch
    root = expand(root)
    assets = scan_desktop(root, recursive=recursive, max_depth=10 if recursive else 1)
    results = []

    for a in assets:
        if a.is_shortcut:
            continue
        fn = os.path.basename(a.path)
        if name and not fnmatch.fnmatch(fn.lower(), name.lower()):
            continue
        if ext and a.ext not in {e.lower().lstrip(".") for e in ext}:
            continue
        if min_size and a.size_bytes < min_size:
            continue
        if max_size and a.size_bytes > max_size:
            continue
        mt = a.mtime or 0
        if modified_since and mt < modified_since:
            continue
        if modified_before and mt > modified_before:
            continue
        results.append(a)

    logger.info("[find] %d files matched under %s", len(results), root)
    return results


# ============================================================
# 能力 6：dedupe — 文件去重
# ============================================================

def capability_dedupe(
    root: str,
    method: str = "hash",
    dry_run: bool = True,
    trash_dir: str = "~/.archive_assistant/trash",
    phash_threshold: int = 5,
) -> List[Dict[str, str]]:
    """文件去重。

    method: "hash"（内容哈希去重）| "filename"（文件名模式去重 xxx1.jpg）
            | "phash"（照片感知哈希，视觉相似去重）
    重复文件移入回收站，绝不硬删除。
    返回 [{original, duplicate, action}] 列表
    """
    import hashlib
    # phash 委托给照片专用能力（视觉相似去重）
    if method == "phash":
        from .photo_capabilities import capability_dedupe_photos
        res = capability_dedupe_photos(
            root, threshold=phash_threshold, dry_run=dry_run, trash_dir=trash_dir,
        )
        out = []
        for g in res.get("groups", []):
            for dup in g["duplicates"]:
                out.append({
                    "original": g["keep"], "duplicate": dup,
                    "action": "trash", "reason": f"visually similar (phash<={phash_threshold})",
                })
        return out

    root = expand(root)
    assets = scan_desktop(root, recursive=True, max_depth=5)
    assets = [a for a in assets if not a.is_shortcut]

    results = []

    if method == "hash":
        # 内容哈希去重
        seen: Dict[str, str] = {}  # hash -> first_path
        # 逐文件分块读全文算 md5，大文件/多文件耗时，加进度。
        for a in track(assets, label="去重·内容哈希", est_per_item=0.02):
            try:
                h = hashlib.md5()
                with open(a.path, "rb") as f:
                    for chunk in iter(lambda: f.read(8192), b""):
                        h.update(chunk)
                digest = h.hexdigest()
                if digest in seen:
                    results.append({
                        "original": seen[digest],
                        "duplicate": a.path,
                        "action": "trash",
                        "reason": f"identical content (md5={digest[:8]})",
                    })
                else:
                    seen[digest] = a.path
            except OSError:
                continue
    elif method == "filename":
        # 文件名模式去重：xxx1.jpg / xxx_1.jpg / 图片(1).jpg → 保留原始
        import re
        pattern1 = re.compile(r"^(.+?)[-_]?(\d+)\.(\w+)$")  # xxx1.jpg, xxx_1.jpg
        pattern2 = re.compile(r"^(.+?)\s*\((\d+)\)\.(\w+)$")  # 图片(1).jpg
        originals: Dict[str, str] = {}  # base_name -> path

        for a in assets:
            fn = os.path.basename(a.path)
            matched = False
            for pat in [pattern1, pattern2]:
                m = pat.match(fn)
                if m:
                    base = f"{m.group(1)}.{m.group(3)}"
                    base_key = base.lower()
                    if base_key not in originals:
                        # 检查原始文件是否存在
                        orig_path = os.path.join(os.path.dirname(a.path), base)
                        if os.path.exists(orig_path):
                            originals[base_key] = orig_path
                        else:
                            originals[base_key] = a.path  # 自己就是"原始"
                    else:
                        results.append({
                            "original": originals[base_key],
                            "duplicate": a.path,
                            "action": "trash",
                            "reason": f"numbered duplicate of {base}",
                        })
                    matched = True
                    break
            if not matched:
                pass  # 非重复模式

    # 执行去重
    if not dry_run and results:
        trash_root = expand(trash_dir)
        ts = time.strftime("%Y%m%d_%H%M%S")
        trash_path = os.path.join(trash_root, ts)
        os.makedirs(trash_path, exist_ok=True)
        for r in track(results, label="去重·移入回收站", est_per_item=0.03):
            try:
                dst = os.path.join(trash_path, os.path.basename(r["duplicate"]))
                if os.path.exists(dst):
                    base, ext = os.path.splitext(dst)
                    dst = f"{base}-{int(time.time())%10000}{ext}"
                shutil.move(r["duplicate"], dst)
                r["moved_to"] = dst
            except OSError as e:
                logger.error("[dedupe] move failed: %s", e)

    action = "deduped" if not dry_run else "would dedupe"
    logger.info("[dedupe] %s %d duplicate files (method=%s)", action, len(results), method)
    return results


# ============================================================
# 能力 7：sync — 增量同步
# ============================================================

def capability_sync(
    src: str,
    dst: str,
    direction: str = "oneway",
    dry_run: bool = True,
) -> Dict[str, List[str]]:
    """目录间增量同步（只增不减）。

    direction: "oneway"(src→dst) | "twoway"
    返回 {"copied": [...], "skipped": [...]}
    """
    src = expand(src)
    dst = expand(dst)
    result = {"copied": [], "skipped": []}

    if not os.path.isdir(src):
        logger.error("[sync] src not found: %s", src)
        return result
    os.makedirs(dst, exist_ok=True)

    # 两遍法：先 walk 建目录+收集待处理文件（拿到总量），再带进度逐个同步。
    jobs: List[tuple] = []
    for dirpath, dirnames, filenames in os.walk(src):
        rel = os.path.relpath(dirpath, src)
        dst_dir = dst if rel == "." else os.path.join(dst, rel)
        os.makedirs(dst_dir, exist_ok=True)
        for fn in filenames:
            if fn.startswith("."):
                continue
            jobs.append((os.path.join(dirpath, fn), os.path.join(dst_dir, fn)))

    tr = ProgressTracker(len(jobs), label="增量同步", est_per_item=0.03)
    for src_file, dst_file in jobs:
        # 只增不减：目标不存在或源比目标新才复制
        if not os.path.exists(dst_file):
            result["copied"].append(dst_file)
            if not dry_run:
                shutil.copy2(src_file, dst_file)
        elif os.path.getmtime(src_file) > os.path.getmtime(dst_file):
            result["copied"].append(dst_file)
            if not dry_run:
                shutil.copy2(src_file, dst_file)
        else:
            result["skipped"].append(dst_file)
        tr.advance()
    tr.finish()

    action = "synced" if not dry_run else "would sync"
    logger.info("[sync] %s: %d copied, %d skipped (%s -> %s)",
                action, len(result["copied"]), len(result["skipped"]), src, dst)
    return result


# ============================================================
# 能力 8：inspect — 文件巡检
# ============================================================

def capability_inspect(
    root: str,
    since_hours: float = 24,
) -> Dict[str, Any]:
    """扫描目录中最近新增/修改的文件，返回清单。

    返回 {total, new_count, new_files: [{name, path, size, mtime, ext}]}
    """
    import datetime
    root = expand(root)
    now = time.time()
    cutoff = now - since_hours * 3600

    assets = scan_desktop(root, recursive=False, max_depth=1)
    new_files = []

    for a in assets:
        mt = a.mtime or 0
        if mt >= cutoff:
            new_files.append({
                "name": os.path.basename(a.path),
                "path": a.path,
                "size": a.size_bytes,
                "mtime": datetime.datetime.fromtimestamp(mt).strftime("%Y-%m-%d %H:%M:%S"),
                "ext": a.ext,
                "is_shortcut": a.is_shortcut,
            })

    new_files.sort(key=lambda x: x["mtime"], reverse=True)

    result = {
        "root": root,
        "since_hours": since_hours,
        "total": len(assets),
        "new_count": len(new_files),
        "new_files": new_files,
    }
    logger.info("[inspect] %d new/modified files in last %.1fh under %s",
                len(new_files), since_hours, root)
    return result


# ============================================================
# 能力 9：move — 批量移动
# ============================================================

def capability_move(
    root: str,
    match: str,
    to: str,
    ext_filter: Optional[List[str]] = None,
    dry_run: bool = True,
) -> List[Dict[str, str]]:
    """按规则批量移动文件到指定目录。

    match: 通配符模式，如 "*.lnk" 或 "*.xlsx"
    ext_filter: 额外扩展名过滤
    返回 [{src, dst}]
    """
    import fnmatch
    root = expand(root)
    to_dir = expand(to)
    assets = scan_desktop(root, recursive=False, max_depth=1)

    results = []
    for a in assets:
        if a.is_shortcut:
            continue
        fn = os.path.basename(a.path)
        if not fnmatch.fnmatch(fn.lower(), match.lower()):
            continue
        if ext_filter and a.ext not in {e.lower().lstrip(".") for e in ext_filter}:
            continue
        dst = os.path.join(to_dir, fn)
        results.append({"src": a.path, "dst": dst})
        if not dry_run:
            os.makedirs(to_dir, exist_ok=True)
            if os.path.exists(dst):
                base, ext = os.path.splitext(dst)
                dst = f"{base}-{int(time.time())%10000}{ext}"
            shutil.move(a.path, dst)

    action = "moved" if not dry_run else "would move"
    logger.info("[move] %s %d files to %s", action, len(results), to_dir)
    return results


def capability_move_path(
    src: str,
    dst_dir: str,
    dry_run: bool = False,
    log_dir: str = "~/.archive_assistant/log",
    merge: bool = False,
) -> Dict[str, Any]:
    """移动单个文件或文件夹到目标目录（走执行器日志，可回滚）。

    与 capability_move 的区别：
    - capability_move：按通配符批量匹配（需要 root + match pattern）
    - capability_move_path：按完整路径移动单个文件/文件夹（用户明确指定源）

    冲突策略：
    - 默认（merge=False）：目标已存在同名 → 加 -1/-2 后缀创建新副本（绝不覆盖）
    - merge=True（仅文件夹）：目标已存在同名文件夹 → 把源文件夹内容合并进去
      （文件冲突仍加后缀，绝不覆盖；合并后源文件夹会被清空删除）

    返回 {src, dst, type, moved, log_path, merged, moves}
    """
    src = expand(src)
    dst_dir = expand(dst_dir)

    if not os.path.exists(src):
        return {"error": f"源路径不存在: {src}"}

    is_dir = os.path.isdir(src)
    name = os.path.basename(src.rstrip("/"))
    dst = os.path.join(dst_dir, name)

    result: Dict[str, Any] = {
        "src": src,
        "dst": dst,
        "type": "dir" if is_dir else "file",
        "moved": False,
        "merged": False,
        "moves": [],
        "log_path": None,
    }

    # 冲突处理
    dst_exists = os.path.exists(dst)
    do_merge = False

    if dst_exists:
        if merge and is_dir and os.path.isdir(dst):
            # 文件夹合并模式：目标已存在同名文件夹 → 合并内容
            do_merge = True
        else:
            # 默认策略：加后缀避让（绝不覆盖）
            i = 1
            while True:
                cand = f"{dst}-{i}"
                if not os.path.exists(cand):
                    dst = cand
                    break
                i += 1
            result["dst"] = dst

    if dry_run:
        if do_merge:
            logger.info("[move-path] would merge %s into %s", src, dst)
        else:
            logger.info("[move-path] would move %s → %s", src, dst)
        return result

    # 真正移动
    os.makedirs(dst_dir, exist_ok=True)

    # 收集所有 move 记录（用于日志，支持回滚）
    all_moves: List[Dict[str, str]] = []

    def _resolve_collision(path: str) -> str:
        if not os.path.exists(path):
            return path
        base, ext = os.path.splitext(path)
        i = 1
        while True:
            cand = f"{base}-{i}{ext}"
            if not os.path.exists(cand):
                return cand
            i += 1

    def _merge_tree(src_root: str, dst_root: str):
        """递归合并 src_root 内容到 dst_root（文件冲突加后缀）。"""
        for entry in os.listdir(src_root):
            s = os.path.join(src_root, entry)
            d = os.path.join(dst_root, entry)
            if os.path.isdir(s):
                os.makedirs(d, exist_ok=True)
                _merge_tree(s, d)
                # 合并后清空源子目录
                try:
                    os.rmdir(s)
                except OSError:
                    pass
            else:
                final_d = _resolve_collision(d)
                shutil.move(s, final_d)
                all_moves.append({"src": s, "dst": final_d})

    if do_merge:
        # 文件夹合并模式
        _merge_tree(src, dst)
        # 删除已清空的源文件夹壳
        try:
            os.rmdir(src)
        except OSError:
            pass
        result["merged"] = True
        result["moved"] = True
    else:
        # 普通移动（单文件或加后缀的文件夹）
        shutil.move(src, dst)
        all_moves.append({"src": src, "dst": dst})
        result["moved"] = True

    # 写日志（与 executor 格式一致，rollback 可识别）
    import json as _json
    import time as _time
    ts = _time.strftime("%Y%m%d_%H%M%S")
    log_dir = expand(log_dir)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{ts}.json")
    log = {
        "ts": ts,
        "root": os.path.dirname(src),
        "mode": "move_path_merge" if do_merge else "move_path",
        "trash_root": None,
        "moves": all_moves,
        "mkdirs": [dst_dir],
    }
    with open(log_path, "w", encoding="utf-8") as f:
        _json.dump(log, f, ensure_ascii=False, indent=2)
    result["log_path"] = log_path
    result["moves"] = all_moves
    if do_merge:
        logger.info("[move-path] merged %s into %s (%d files), log=%s",
                    src, dst, len(all_moves), log_path)
    else:
        logger.info("[move-path] moved %s → %s, log=%s", src, dst, log_path)
    return result


# ============================================================
# 能力 10：clean — 清理
# ============================================================

def capability_clean(
    root: str,
    temp_files: bool = True,
    empty_dirs: bool = True,
    locked_files: bool = False,
    dry_run: bool = True,
) -> Dict[str, List[str]]:
    """清理临时文件、空文件夹、检测被占用文件。

    temp_files: 清理 ~$ 开头的 Office 临时文件、.tmp、Thumbs.db
    empty_dirs: 清理空目录
    locked_files: 检测被占用的文件（不删除，仅报告）
    返回 {"trashed": [...], "empty_dirs_removed": [...], "locked": [...]}
    """
    root = expand(root)
    result = {"trashed": [], "empty_dirs_removed": [], "locked": []}

    # 临时文件模式
    temp_patterns = ["~$*", "*.tmp", "Thumbs.db", "desktop.ini", ".DS_Store"]

    # 先统计文件总数作为进度总量（locked 检测逐文件 open，大目录耗时）。
    total_files = sum(len(fs) for _, _, fs in os.walk(root))
    tr = ProgressTracker(total_files, label="清理扫描", est_per_item=0.005)
    for dirpath, dirnames, filenames in os.walk(root):
        for fn in filenames:
            fp = os.path.join(dirpath, fn)
            is_temp = False
            if temp_files:
                import fnmatch
                for pat in temp_patterns:
                    if fnmatch.fnmatch(fn, pat):
                        result["trashed"].append(fp)
                        is_temp = True
                        if not dry_run:
                            try:
                                os.remove(fp)
                            except OSError:
                                pass
                        break
            if not is_temp and locked_files:
                try:
                    # 尝试以独占模式打开
                    with open(fp, "a"):
                        pass
                except (IOError, OSError):
                    result["locked"].append(fp)
            tr.advance()

        if empty_dirs and dirpath != root:
            try:
                if not os.listdir(dirpath):
                    result["empty_dirs_removed"].append(dirpath)
                    if not dry_run:
                        os.rmdir(dirpath)
            except OSError:
                pass
    tr.finish()

    action = "cleaned" if not dry_run else "would clean"
    logger.info("[clean] %s: %d temp files, %d empty dirs, %d locked",
                action, len(result["trashed"]), len(result["empty_dirs_removed"]),
                len(result["locked"]))
    return result


# ============================================================
# 能力 11：backup — 整理前备份
# ============================================================

def capability_backup(root: str, backup_dir: Optional[str] = None) -> Optional[str]:
    """整理前完整备份目录到指定路径。返回备份目录路径。"""
    root = expand(root)
    if not backup_dir:
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup_dir = os.path.join(os.path.dirname(root), f"{os.path.basename(root)}_backup_{ts}")
    backup_dir = expand(backup_dir)

    if os.path.exists(backup_dir):
        logger.warning("[backup] backup dir already exists: %s", backup_dir)
        return backup_dir

    try:
        # copytree 一次性复制整目录，大目录极慢；先统计文件数，再用
        # copy_function 回调逐文件推进进度。
        total = sum(len(fs) for _, _, fs in os.walk(root))
        tr = ProgressTracker(total, label="整理前备份", est_per_item=0.02)

        def _cp(s: str, d: str):
            shutil.copy2(s, d)
            tr.advance()

        shutil.copytree(root, backup_dir, dirs_exist_ok=False, copy_function=_cp)
        tr.finish()
        logger.info("[backup] copied %s -> %s", root, backup_dir)
        return backup_dir
    except OSError as e:
        logger.error("[backup] failed: %s", e)
        return None


# ============================================================
# 能力 5：plan — 生成归档计划
# ============================================================

def capability_plan(
    root: str,
    clusters: List[Cluster],
    vlm=None,
    categories: Optional[List[str]] = None,
    existing_folders: Optional[List[str]] = None,
) -> ArchivePlan:
    """根据 clusters 生成 ArchivePlan（dry-run）。

    如果传入 existing_folders，匹配到的文件直接归入已有文件夹（不新建在 _archived 下）。
    """
    plan = build_plan(root, "desktop", clusters, vlm, categories=categories,
                      existing_folders=existing_folders)
    plan.feedback = build_feedback(plan, "desktop")
    logger.info("[plan] %d actions, %d clusters", len(plan.actions), len(plan.clusters))
    return plan


def capability_save_plan(plan: ArchivePlan, out_path: str) -> str:
    """保存 plan 到 JSON 文件。"""
    return save_plan(plan, out_path)


# ============================================================
# 能力 6：apply — 执行计划
# ============================================================

def capability_apply(
    plan_path: str,
    log_dir: str,
    trash_dir: str = "~/.archive_assistant/trash",
) -> str:
    """执行 plan.json，返回日志路径。"""
    plan_dict = load_plan(plan_path)
    return apply_plan(plan_dict, log_dir=log_dir, trash_dir=trash_dir)


# ============================================================
# 能力 7：rollback — 回撤
# ============================================================

def capability_rollback(log_dir: str, log_path: Optional[str] = None) -> int:
    """回撤最后一次或指定日志的操作。返回恢复文件数。"""
    if log_path:
        return rollback(log_path)
    logs = list_logs(log_dir)
    if not logs:
        logger.warning("[rollback] no log found")
        return 0
    return rollback(logs[-1])


# ============================================================
# 能力 8：sort_desktop — 桌面图标排列
# ============================================================

def capability_sort_desktop(root: str, sort_by: Optional[str] = None) -> bool:
    """排列桌面图标，消除空位。

    sort_by: None=仅紧凑排列, "ItemType"=按项目类型排序
    Windows: PowerShell COM 排列桌面图标
    macOS:   删除 .DS_Store + 设置排列方式 + 重启 Finder
    Linux:   无统一 API，给出提示并返回 False（文件整理不受影响）
    """
    system = platform_name()

    if system == "Windows":
        return _sort_desktop_windows(sort_by)
    elif system == "Darwin":
        return _sort_desktop_macos(root, sort_by)
    else:
        logger.info(
            "[sort_desktop] Linux 桌面环境（GNOME/KDE 等）无统一图标排列 API，"
            "已跳过图标排列；文件整理不受影响。"
        )
        return False


def _sort_desktop_windows(sort_by: Optional[str]) -> bool:
    """Windows: 通过 PowerShell COM 接口排列桌面。"""
    import subprocess
    # 寻找 skill 目录下的 sort_desktop.ps1
    here = os.path.dirname(os.path.abspath(__file__))
    skill_root = os.path.abspath(os.path.join(here, "..", ".."))
    ps_script = os.path.join(skill_root, "scripts", "sort_desktop.ps1")
    if not os.path.exists(ps_script):
        logger.error("[sort_desktop] script not found: %s", ps_script)
        return False
    # 安全：list 参数，绝不走 shell；空 sort_by 时不传多余参数
    ps_args = ["powershell", "-ExecutionPolicy", "Bypass", "-File", ps_script]
    if sort_by:
        ps_args += ["-SortBy", sort_by]
    try:
        subprocess.run(ps_args, check=True, capture_output=True, text=True)
        logger.info("[sort_desktop] Windows desktop sorted")
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        stderr = getattr(e, "stderr", str(e))
        logger.error("[sort_desktop] PowerShell failed: %s", stderr)
        return False


def _sort_desktop_macos(root: str, sort_by: Optional[str]) -> bool:
    """macOS: 删除 .DS_Store + 可选设置排列方式 + 重启 Finder。"""
    import subprocess
    ds_store = os.path.join(expand(root), ".DS_Store")
    if os.path.exists(ds_store):
        try:
            os.remove(ds_store)
        except OSError:
            pass
    # 按类型排序时通过 defaults 设置 Finder 桌面排列方式
    if sort_by and sort_by.lower() in ("itemtype", "kind", "type"):
        try:
            subprocess.run(
                ["defaults", "write", "com.apple.finder",
                 "DesktopViewSettings", "-dict-add", "arrangeBy", "kind"],
                check=True, capture_output=True,
            )
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass  # 不致命，继续重启 Finder
    try:
        subprocess.run(["killall", "Finder"], check=True, capture_output=True)
        logger.info("[sort_desktop] macOS Finder restarted (sort_by=%s)", sort_by)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.error("[sort_desktop] killall Finder failed")
        return False


# ============================================================
# 能力 9：report — 输出 Markdown 整理报告
# ============================================================

def capability_report(plan: ArchivePlan, log_path: Optional[str] = None) -> str:
    """生成 Markdown 格式的整理报告。"""
    total = len(plan.clusters)
    moved = sum(1 for a in plan.actions if a.op == "move")
    trashed = sum(1 for a in plan.actions if a.op == "trash")
    mkdirs = sum(1 for a in plan.actions if a.op == "mkdir")
    new_folders = [a.dst for a in plan.actions if a.op == "mkdir"]

    lines = [
        "# 📊 桌面整理报告",
        "",
        "## 概况",
        "",
        "| 项目 | 数量 |",
        "|------|------|",
        f"| 扫描文件总数 | {sum(len(c.assets) for c in plan.clusters)} |",
        f"| 自动整理 | {moved} |",
        f"| 移入回收站 | {trashed} |",
        f"| 新建文件夹 | {mkdirs} |",
        "",
    ]

    if new_folders:
        lines += ["### 新建文件夹", ""]
        for d in new_folders:
            lines.append(f"- `{os.path.basename(d)}`")
        lines.append("")

    # 调整清单
    lines += ["## 调整清单", "", "| 文件名 | 目标文件夹 | 状态 |", "|--------|------------|------|"]
    for c in plan.clusters:
        folder = os.path.basename(c.label or "misc")
        for a in c.assets[:20]:
            status = "🗑 回收站" if a.risk_level == "medium" else "✅ 已移动"
            lines.append(f"| {os.path.basename(a.path)} | {folder} | {status} |")
        if len(c.assets) > 20:
            lines.append(f"| ... | +{len(c.assets) - 20} more | |")
    lines.append("")

    # 回撤指引
    lines += [
        "## 🔙 回撤",
        "",
        "如整理操作有误，可回撤：",
        "```bash",
        "# 撤销本次所有整理",
        f"python -m archive_assistant.cli.main rollback --last",
        "",
        "# 撤销指定日志",
        f"python -m archive_assistant.cli.main rollback --log <LOG_PATH>",
        "```",
    ]

    if log_path:
        lines.append(f"\n> 操作日志: `{log_path}`")

    return "\n".join(lines)


# ============================================================
# 能力 10：schedule — 定时整理
# ============================================================

def capability_schedule(
    root: str,
    cron_expr: str,
    config_path: Optional[str] = None,
    action: str = "add",
) -> bool:
    """管理定时整理任务。

    action="add": 添加定时任务
    action="remove": 移除定时任务
    action="list": 列出定时任务

    cron_expr: cron 表达式，如 "0 18 * * *"（每天18:00）
    """
    system = platform_name()
    task_name = "desktop_archive_assistant"

    if action == "list":
        return _schedule_list(system, task_name)
    elif action == "remove":
        return _schedule_remove(system, task_name)
    elif action != "add":
        logger.error("[schedule] unknown action: %s", action)
        return False

    # 构建 dry-run + apply 命令
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    skill_root = os.path.abspath(os.path.join(here, "..", ".."))
    # 跨平台：用 tempfile.gettempdir()（Windows 下为 %TEMP%，Unix 下为 /tmp）
    plan_path = os.path.join(tempfile.gettempdir(), f"desktop_plan_{int(time.time())}.json")
    plan_path = os.path.abspath(plan_path)

    # 写一个一次性 runner 脚本，避免 schtasks/crontab 的引号转义地狱
    if system == "Windows":
        runner = os.path.join(tempfile.gettempdir(), f"archive_run_{int(time.time())}.bat")
        with open(runner, "w", encoding="utf-8", errors="ignore") as f:
            f.write(f'@echo off\r\n')
            f.write(f'cd /d "{skill_root}"\r\n')
            f.write(f'python -m archive_assistant.cli.main organize "{root}" --out "{plan_path}"\r\n')
            f.write(f'python -m archive_assistant.cli.main apply --plan "{plan_path}"\r\n')
        cmd = f'"{runner}"'
    else:
        runner = os.path.join(tempfile.gettempdir(), f"archive_run_{int(time.time())}.sh")
        with open(runner, "w", encoding="utf-8") as f:
            f.write(f'#!/usr/bin/env bash\n')
            f.write(f'cd "{skill_root}"\n')
            f.write(f'python -m archive_assistant.cli.main organize "{root}" --out "{plan_path}"\n')
            f.write(f'python -m archive_assistant.cli.main apply --plan "{plan_path}"\n')
        os.chmod(runner, 0o755)
        cmd = f'bash "{runner}"'

    if system == "Linux" or system == "Darwin":
        return _schedule_cron(task_name, cron_expr, cmd)
    elif system == "Windows":
        return _schedule_windows_task(task_name, cron_expr, cmd)
    else:
        logger.error("[schedule] unsupported platform: %s", system)
        return False


def _schedule_cron(task_name: str, cron_expr: str, cmd: str) -> bool:
    """Linux/macOS: 写入 crontab。"""
    import subprocess
    line = f"# {task_name}\n{cron_expr} {cmd}\n"
    try:
        existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout or ""
        # 移除旧的同名任务
        lines = []
        skip_next = False
        for l in existing.splitlines():
            if f"# {task_name}" in l:
                skip_next = True
                continue
            if skip_next:
                skip_next = False
                continue
            lines.append(l)
        lines.append(line.rstrip())
        new_crontab = "\n".join(lines) + "\n"
        subprocess.run(["crontab", "-"], input=new_crontab, text=True, check=True)
        logger.info("[schedule] cron job added: %s", cron_expr)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.error("[schedule] crontab failed: %s", e)
        return False


def _schedule_windows_task(task_name: str, cron_expr: str, cmd: str) -> bool:
    """Windows: 用 schtasks 注册定时任务。"""
    import subprocess
    # 简化：cron_expr 格式 "分 时 日 月 周" → schtasks /MO
    parts = cron_expr.split()
    if len(parts) != 5:
        logger.error("[schedule] invalid cron expr: %s", cron_expr)
        return False
    minute, hour, day, month, dow = parts
    time_str = f"{hour}:{minute}"
    try:
        subprocess.run([
            "schtasks", "/Create", "/TN", task_name, "/TR", cmd,
            "/SC", "DAILY", "/ST", time_str, "/F",
        ], check=True, capture_output=True, text=True)
        logger.info("[schedule] Windows task added: %s at %s", task_name, time_str)
        return True
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        stderr = getattr(e, "stderr", str(e))
        logger.error("[schedule] schtasks failed: %s", stderr)
        return False


def _schedule_list(system: str, task_name: str) -> bool:
    import subprocess
    if system in ("Linux", "Darwin"):
        try:
            result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True)
            for line in result.stdout.splitlines():
                if task_name in line or f"# {task_name}" in line:
                    print(line)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    elif system == "Windows":
        try:
            subprocess.run(["schtasks", "/Query", "/TN", task_name], check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    return False


def _schedule_remove(system: str, task_name: str) -> bool:
    import subprocess
    if system in ("Linux", "Darwin"):
        return _schedule_cron(task_name, "", "")  # 传入空会移除
    elif system == "Windows":
        try:
            subprocess.run(["schtasks", "/Delete", "/TN", task_name, "/F"], check=True)
            return True
        except (subprocess.CalledProcessError, FileNotFoundError):
            return False
    return False
