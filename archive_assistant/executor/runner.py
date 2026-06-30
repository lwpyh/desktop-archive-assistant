"""执行计划 + 写日志 + rollback。

安全要点（硬性要求）：
- **绝不硬删除文件**：执行器不调用 os.remove / os.unlink / shutil.rmtree 等任何删除 API。
- 仅允许三类操作：mkdir / move / trash（移入回收站）；未知操作一律跳过，绝不删除。
- 同名冲突自动加 -1, -2 后缀，**绝不覆盖**已存在文件。
- "删除"语义统一降级为"移动到回收站"（trash_dir，默认 ~/.archive_assistant/trash/<ts>/）。
- 所有真实移动记录到 ~/.archive_assistant/log/<ts>.json，rollback 直接逆向 move（含回收站内文件）。
"""
from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path
from typing import Dict, List

from ..core import ArchivePlan
from ..utils import expand, ensure_dir, logger, track

# 硬白名单：执行器只认这些操作；任何其它（尤其删除类）一律拒绝执行
ALLOWED_OPS = {"mkdir", "move", "trash", "tag_duplicate"}
DEFAULT_TRASH_DIR = "~/.archive_assistant/trash"


def _resolve_collision(dst: str) -> str:
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while True:
        cand = f"{base}-{i}{ext}"
        if not os.path.exists(cand):
            return cand
        i += 1


def preview(plan: ArchivePlan) -> str:
    lines = [f"Plan: mode={plan.mode}, root={plan.root}, actions={len(plan.actions)}"]
    grouped: Dict[str, List[str]] = {}
    n_trash = 0
    for c in plan.clusters:
        bucket = c.label or c.cluster_id
        for a in c.assets:
            if getattr(a, "risk_level", "low") == "medium":
                n_trash += 1
            grouped.setdefault(bucket, []).append(os.path.basename(a.path))
    for bucket, files in grouped.items():
        lines.append(f"  📁 {bucket}  ({len(files)} files)")
        for fn in files[:5]:
            lines.append(f"     - {fn}")
        if len(files) > 5:
            lines.append(f"     ... +{len(files) - 5} more")
    if n_trash:
        lines.append(f"  🗑 {n_trash} 个重复/废弃文件将移入回收站（不删除，可 rollback 还原）")
    return "\n".join(lines)


def save_plan(plan: ArchivePlan, out_path: str) -> str:
    out_path = expand(out_path)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plan.to_dict(), f, ensure_ascii=False, indent=2)
    return out_path


def load_plan(path: str) -> dict:
    with open(expand(path), "r", encoding="utf-8") as f:
        return json.load(f)


def apply_plan(plan_dict: dict, log_dir: str, trash_dir: str = DEFAULT_TRASH_DIR) -> str:
    """真正执行；返回写出的日志文件路径。

    硬保证：只做 mkdir / move / trash；绝不删除文件；冲突改名不覆盖。
    """
    ts = time.strftime("%Y%m%d_%H%M%S")
    trash_root = os.path.join(expand(trash_dir), ts)
    log = {
        "ts": ts,
        "root": plan_dict.get("root"),
        "mode": plan_dict.get("mode"),
        "trash_root": trash_root,
        "moves": [],     # [{src, dst}]  —— move 与 trash 统一记录，便于 rollback
        "mkdirs": [],    # [path]
    }
    # 落盘阶段：逐条 mkdir/move/trash，文件多时耗时显著，加进度避免静默久等。
    actions = plan_dict.get("actions", [])
    for act in track(actions, label="整理落盘", est_per_item=0.05):
        op = act.get("op")
        if op not in ALLOWED_OPS:
            logger.error("拒绝未知/危险操作（绝不删除）: %s", op)
            continue
        try:
            if op == "mkdir":
                dst = expand(act["dst"])
                if not os.path.exists(dst):
                    os.makedirs(dst, exist_ok=True)
                    log["mkdirs"].append(dst)
            elif op == "move":
                src = expand(act["src"])
                dst = expand(act["dst"])
                if not os.path.exists(src):
                    logger.warning("missing src, skip: %s", src)
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                final_dst = _resolve_collision(dst)
                shutil.move(src, final_dst)
                log["moves"].append({"src": src, "dst": final_dst})
            elif op in ("trash", "tag_duplicate"):
                # 统一移入回收站：绝不删除
                src = expand(act["src"])
                if not os.path.exists(src):
                    logger.warning("missing src, skip: %s", src)
                    continue
                os.makedirs(trash_root, exist_ok=True)
                dst = os.path.join(trash_root, os.path.basename(src))
                final_dst = _resolve_collision(dst)
                shutil.move(src, final_dst)
                log["moves"].append({"src": src, "dst": final_dst})
                if trash_root not in log["mkdirs"]:
                    log["mkdirs"].append(trash_root)
        except OSError as e:
            logger.error("action failed (%s): %s", op, e)

    log_dir = ensure_dir(log_dir)
    log_path = os.path.join(log_dir, f"{ts}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(log, f, ensure_ascii=False, indent=2)
    n_trash = sum(1 for m in log["moves"] if m["dst"].startswith(trash_root))
    logger.info("apply done: %d moves (%d 入回收站), log=%s",
                len(log["moves"]), n_trash, log_path)
    return log_path


def list_logs(log_dir: str) -> List[str]:
    p = Path(expand(log_dir))
    if not p.exists():
        return []
    return sorted(str(x) for x in p.glob("*.json"))


def rollback(log_path: str) -> int:
    """读 log，把所有 move 反向执行；返回成功条数。"""
    with open(expand(log_path), "r", encoding="utf-8") as f:
        log = json.load(f)
    ok = 0
    for mv in reversed(log.get("moves", [])):
        src, dst = mv["src"], mv["dst"]      # 原 src/dst
        # rollback：把 dst 移回 src
        try:
            if not os.path.exists(dst):
                logger.warning("rollback skip, not exist: %s", dst)
                continue
            os.makedirs(os.path.dirname(src), exist_ok=True)
            final = _resolve_collision(src)
            shutil.move(dst, final)
            ok += 1
        except OSError as e:
            logger.error("rollback failed: %s", e)
    # 尝试清理空的归档目录
    for d in reversed(log.get("mkdirs", [])):
        try:
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
        except OSError:
            pass
    logger.info("rollback done: %d files restored from %s", ok, log_path)
    return ok
