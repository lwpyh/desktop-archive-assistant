"""桌面文件整理 CLI — 能力驱动，按需组合。

每个子命令对应一个独立能力，可单独调用或组合使用。

Usage:
  # 扫描桌面（看有哪些文件）
  python -m archive_assistant.cli.main scan ~/Desktop

  # 整理桌面（全流程：扫描→归类→生成计划，dry-run）
  python -m archive_assistant.cli.main organize ~/Desktop --out plan.json

  # 仅归类（已有扫描结果时跳过扫描）
  python -m archive_assistant.cli.main classify ~/Desktop --out plan.json

  # 执行计划
  python -m archive_assistant.cli.main apply --plan plan.json

  # 回撤
  python -m archive_assistant.cli.main rollback --last

  # 整理前备份
  python -m archive_assistant.cli.main backup ~/Desktop

  # 排列桌面图标
  python -m archive_assistant.cli.main sort ~/Desktop
  python -m archive_assistant.cli.main sort ~/Desktop --by ItemType

  # 批量重命名
  python -m archive_assistant.cli.main rename ~/Desktop --template "1_{seq}" --sort-by time
  python -m archive_assistant.cli.main rename ~/Photos --template "{date}_{name}" --ext jpg

  # 查找文件
  python -m archive_assistant.cli.main find ~/Desktop --name "*.xlsx"
  python -m archive_assistant.cli.main find ~/Desktop --ext xlsx,pdf --modified-since 24

  # 去重
  python -m archive_assistant.cli.main dedupe ~/Desktop --method hash
  python -m archive_assistant.cli.main dedupe ~/Photos --method filename

  # 增量同步
  python -m archive_assistant.cli.main sync ~/Desktop /backup/desktop

  # 巡检（扫描最近新增文件）
  python -m archive_assistant.cli.main inspect ~/Desktop --since 24

  # 批量移动
  python -m archive_assistant.cli.main move ~/Desktop --match "*.lnk" --to ~/Desktop/快捷方式

  # 清理临时文件
  python -m archive_assistant.cli.main clean ~/Desktop --temp --empty-dirs

  # 生成报告
  python -m archive_assistant.cli.main report --plan plan.json

  # 定时整理
  python -m archive_assistant.cli.main schedule ~/Desktop --cron "0 18 * * *"
  python -m archive_assistant.cli.main schedule --list
  python -m archive_assistant.cli.main schedule --remove
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from ..capabilities import (
    capability_scan,
    capability_enrich,
    capability_classify,
    capability_rename,
    capability_find,
    capability_dedupe,
    capability_sync,
    capability_inspect,
    capability_move,
    capability_clean,
    capability_backup,
    capability_plan,
    capability_save_plan,
    capability_apply,
    capability_rollback,
    capability_sort_desktop,
    capability_report,
    capability_schedule,
)
from ..photo_capabilities import (
    capability_archive_by_date,
    capability_extract,
    capability_crop,
    capability_to_ppt,
    capability_collage,
    capability_video_rename_title,
    capability_video_compose,
    capability_video_distribute,
    capability_image_rename_by_ocr,
)
from ..office_capabilities import (
    capability_table_clean,
    capability_table_merge,
    capability_docx_compose,
    capability_pdf_ops,
)
from ..extra_capabilities import (
    capability_group_by,
    capability_classify_rules,
    capability_classify_into,
    capability_flatten,
    capability_table_split,
    capability_convert_image,
    capability_pack,
    capability_unpack,
)
from ..executor import preview, list_logs
from ..utils import expand, load_config, logger, default_desktop_dir
from ..auto_router import route as auto_route, format_route_result, list_intents
from ..vlm import VLMReasoner


# ============================================================
# scan — 扫描目录
# ============================================================

def cmd_scan(args: argparse.Namespace) -> int:
    assets = capability_scan(
        args.root, recursive=args.recursive, max_depth=args.max_depth,
    )
    print(f"\n📁 扫描到 {len(assets)} 个文件:")
    for a in assets[:30]:
        tag = " [快捷方式]" if a.is_shortcut else ""
        print(f"  {a.path}{tag}")
    if len(assets) > 30:
        print(f"  ... +{len(assets) - 30} more")
    return 0


# ============================================================
# organize — 全流程整理（scan + enrich + classify + plan）
# ============================================================

def cmd_organize(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    vlm = VLMReasoner(cfg, force_fallback=args.no_vlm)

    # 1. 扫描
    assets = capability_scan(args.root, recursive=args.recursive, max_depth=args.max_depth)
    if not assets:
        print("no files to archive.")
        return 0

    # 2. 特征灌注
    capability_enrich(assets, vlm=vlm)

    # 3. 归类
    clusters = capability_classify(
        assets, cfg, vlm=vlm, root=args.root,
        skip_shortcuts=not args.include_shortcuts,
        use_existing_folders=not args.skip_existing,
        use_infrequent=not args.skip_infrequent,
        use_vlm_theme=not args.skip_vlm_theme,
    )

    # 4. 生成计划
    from ..extractors.scanner import list_existing_folders
    existing_folders = list_existing_folders(args.root) if not args.skip_existing else []
    plan = capability_plan(args.root, clusters, vlm=vlm, existing_folders=existing_folders)

    # 5. 预览
    print(preview(plan))

    # 6. 保存
    if args.out:
        out = capability_save_plan(plan, args.out)
        print(f"\n[plan saved] {out}")
        print(f"\nNext: python -m archive_assistant.cli.main apply --plan {out}")
    return 0


# ============================================================
# classify — 仅归类（轻量版，跳过 enrich）
# ============================================================

def cmd_classify(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    vlm = VLMReasoner(cfg, force_fallback=args.no_vlm)

    assets = capability_scan(args.root, recursive=args.recursive, max_depth=args.max_depth)
    if not args.skip_enrich:
        capability_enrich(assets, vlm=vlm)

    clusters = capability_classify(
        assets, cfg, vlm=vlm, root=args.root,
    )
    from ..extractors.scanner import list_existing_folders
    existing_folders = list_existing_folders(args.root)
    plan = capability_plan(args.root, clusters, vlm=vlm, existing_folders=existing_folders)
    print(preview(plan))

    if args.out:
        out = capability_save_plan(plan, args.out)
        print(f"\n[plan saved] {out}")
    return 0


# ============================================================
# apply — 执行计划
# ============================================================

def cmd_apply(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)

    # 整理前备份（可选）
    if args.backup_dir:
        root = load_plan(args.plan).get("root", "")
        if root:
            b = capability_backup(root, args.backup_dir)
            if b:
                print(f"[backup] {root} -> {b}")

    log = capability_apply(
        args.plan,
        log_dir=cfg["executor"]["log_dir"],
        trash_dir=cfg["executor"].get("trash_dir", "~/.archive_assistant/trash"),
    )
    print(f"[apply] log -> {log}")
    return 0


# ============================================================
# rollback — 回撤
# ============================================================

def cmd_rollback(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    log_dir = cfg["executor"]["log_dir"]

    if args.log:
        n = capability_rollback(log_dir, log_path=args.log)
        print(f"rolled back from {args.log}")
    elif args.last:
        n = capability_rollback(log_dir)
        logs = list_logs(log_dir)
        if logs:
            print(f"rolled back from {logs[-1]}")
    else:
        print("specify --last or --log <path>")
        return 2
    print(f"restored {n} files.")
    return 0


# ============================================================
# rename — 批量重命名
# ============================================================

def cmd_rename(args: argparse.Namespace) -> int:
    ext_filter = args.ext.split(",") if args.ext else None
    results = capability_rename(
        args.root, template=args.template, sort_by=args.sort_by,
        start=args.start, step=args.step, ext_filter=ext_filter,
        dry_run=not args.apply,
    )
    print(f"\n📝 {'Renamed' if args.apply else 'Would rename'} {len(results)} files:")
    for r in results[:20]:
        print(f"  {r['old_path']} → {r['new_path']}")
    if len(results) > 20:
        print(f"  ... +{len(results) - 20} more")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


# ============================================================
# find — 查找文件
# ============================================================

def cmd_find(args: argparse.Namespace) -> int:
    import time as _time
    modified_since = None
    if args.modified_since:
        modified_since = _time.time() - args.modified_since * 3600
    ext_list = args.ext.split(",") if args.ext else None
    results = capability_find(
        args.root, name=args.name, ext=ext_list,
        min_size=args.min_size, max_size=args.max_size,
        modified_since=modified_since, recursive=not args.no_recursive,
    )
    print(f"\n🔍 Found {len(results)} files:")
    for a in results[:30]:
        import datetime
        dt = datetime.datetime.fromtimestamp(a.mtime or 0).strftime("%Y-%m-%d %H:%M")
        size_kb = (a.size_bytes + 1023) // 1024
        print(f"  [{dt}] {size_kb:>8}KB  {a.path}")
    if len(results) > 30:
        print(f"  ... +{len(results) - 30} more")
    return 0


# ============================================================
# dedupe — 文件去重
# ============================================================

def cmd_dedupe(args: argparse.Namespace) -> int:
    results = capability_dedupe(
        args.root, method=args.method,
        dry_run=not args.apply,
        trash_dir=args.trash_dir or "~/.archive_assistant/trash",
        phash_threshold=getattr(args, "phash_threshold", 5),
    )
    print(f"\n🗑️  {'Deduped' if args.apply else 'Would dedupe'} {len(results)} duplicate files:")
    for r in results[:20]:
        print(f"  original: {r['original']}")
        print(f"  duplicate: {r['duplicate']}  ({r.get('reason','')})")
        print()
    if len(results) > 20:
        print(f"  ... +{len(results) - 20} more")
    if not args.apply:
        print("（以上为预览；去掉 --dry-run 即真正执行，重复项移入回收站不会删除）")
    return 0


# ============================================================
# sync — 增量同步
# ============================================================

def cmd_sync(args: argparse.Namespace) -> int:
    result = capability_sync(args.src, args.dst, dry_run=not args.apply)
    print(f"\n🔄 {'Synced' if args.apply else 'Would sync'}: {len(result['copied'])} copied, {len(result['skipped'])} skipped")
    for p in result["copied"][:10]:
        print(f"  + {p}")
    if len(result["copied"]) > 10:
        print(f"  ... +{len(result['copied']) - 10} more")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


# ============================================================
# inspect — 文件巡检
# ============================================================

def cmd_inspect(args: argparse.Namespace) -> int:
    result = capability_inspect(args.root, since_hours=args.since)
    print(f"\n📋 Inspect: {result['root']}")
    print(f"  Total files: {result['total']}")
    print(f"  New/modified in last {result['since_hours']}h: {result['new_count']}")
    print()
    for f in result["new_files"][:30]:
        tag = " [shortcut]" if f.get("is_shortcut") else ""
        size_kb = (f["size"] + 1023) // 1024
        print(f"  [{f['mtime']}] {size_kb:>8}KB  {f['name']}{tag}")
    if len(result["new_files"]) > 30:
        print(f"  ... +{len(result['new_files']) - 30} more")
    return 0


# ============================================================
# move — 批量移动
# ============================================================

def cmd_move(args: argparse.Namespace) -> int:
    ext_filter = args.ext.split(",") if args.ext else None
    results = capability_move(
        args.root, match=args.match, to=args.to,
        ext_filter=ext_filter, dry_run=not args.apply,
    )
    print(f"\n📦 {'Moved' if args.apply else 'Would move'} {len(results)} files to {args.to}:")
    for r in results[:20]:
        print(f"  {r['src']} → {r['dst']}")
    if len(results) > 20:
        print(f"  ... +{len(results) - 20} more")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


# ============================================================
# clean — 清理
# ============================================================

def cmd_clean(args: argparse.Namespace) -> int:
    result = capability_clean(
        args.root, temp_files=args.temp, empty_dirs=args.empty_dirs,
        locked_files=args.locked, dry_run=not args.apply,
    )
    action = "Cleaned" if args.apply else "Would clean"
    print(f"\n🧹 {action} {args.root}:")
    print(f"  Temp files: {len(result['trashed'])}")
    for f in result["trashed"][:10]:
        print(f"    🗑 {f}")
    print(f"  Empty dirs: {len(result['empty_dirs_removed'])}")
    for d in result["empty_dirs_removed"][:10]:
        print(f"    📁 {d}")
    if args.locked:
        print(f"  Locked files: {len(result['locked'])}")
        for f in result["locked"][:10]:
            print(f"    🔒 {f}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


# ============================================================
# backup — 整理前备份
# ============================================================

def cmd_backup(args: argparse.Namespace) -> int:
    b = capability_backup(args.root, args.backup_dir)
    if b:
        print(f"[backup] {args.root} -> {b}")
        return 0
    else:
        print("[backup] failed")
        return 1


# ============================================================
# sort — 桌面图标排列
# ============================================================

def cmd_sort(args: argparse.Namespace) -> int:
    ok = capability_sort_desktop(args.root, sort_by=args.by)
    if ok:
        print("[sort] desktop icons sorted")
        return 0
    else:
        print("[sort] failed")
        return 1


# ============================================================
# report — 生成 Markdown 报告
# ============================================================

def cmd_report(args: argparse.Namespace) -> int:
    from ..executor import load_plan
    from ..core import ArchivePlan, Cluster, PlanAction, Asset
    import json

    plan_dict = load_plan(args.plan)
    # 重建 plan 对象（简化版）
    plan = ArchivePlan(
        root=plan_dict.get("root", ""),
        mode=plan_dict.get("mode", "desktop"),
        created_at=plan_dict.get("created_at", 0),
    )
    for c in plan_dict.get("clusters", []):
        cluster = Cluster(
            cluster_id=c["cluster_id"],
            label=c.get("label"),
            confidence=c.get("confidence", 0),
            rationale=c.get("rationale", ""),
        )
        for a in c.get("assets", []):
            asset = Asset(
                asset_id=a["asset_id"],
                path=a["path"],
                kind=a.get("kind", "file"),
                ext=a.get("ext", ""),
                size_bytes=a.get("size_bytes", 0),
                mtime=a.get("mtime", 0),
                risk_level=a.get("risk_level", "low"),
            )
            cluster.assets.append(asset)
        plan.clusters.append(cluster)
    for a in plan_dict.get("actions", []):
        plan.actions.append(PlanAction(
            op=a["op"], src=a.get("src"), dst=a.get("dst"), note=a.get("note", ""),
        ))

    report = capability_report(plan, log_path=args.log)
    print(report)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"\n[report saved] {args.out}")
    return 0


# ============================================================
# schedule — 定时整理
# ============================================================

def cmd_schedule(args: argparse.Namespace) -> int:
    if args.list:
        ok = capability_schedule("", "", action="list")
        return 0 if ok else 1
    if args.remove:
        ok = capability_schedule("", "", action="remove")
        return 0 if ok else 1
    if not args.root or not args.cron:
        print("schedule requires --root and --cron")
        return 2
    ok = capability_schedule(args.root, args.cron, action="add")
    return 0 if ok else 1


# ============================================================
# 照片/视频专项能力命令
# ============================================================

def cmd_archive_by_date(args: argparse.Namespace) -> int:
    result = capability_archive_by_date(
        args.root, level=args.level,
        use_mtime_fallback=not args.no_mtime_fallback,
        recursive=args.recursive, dry_run=not args.apply,
    )
    action = "Archived" if args.apply else "Would archive"
    print(f"\n📅 {action} {len(result['moves'])} photos by date (level={args.level}):")
    for m in result["moves"][:25]:
        print(f"  [{m['date']}|{m['date_source']}] {os.path.basename(m['src'])} → {os.path.relpath(m['dst'], result['root'])}")
    if len(result["moves"]) > 25:
        print(f"  ... +{len(result['moves']) - 25} more")
    if result["date_mismatch"]:
        print(f"\n⚠️  {len(result['date_mismatch'])} 个文件名年份与 EXIF 年份不一致（以 EXIF 为准）:")
        for d in result["date_mismatch"][:10]:
            print(f"    {os.path.basename(d['path'])}: 文件名={d['name_year']} / EXIF={d['exif_year']}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，文件只移动不删除）")
    return 0


def cmd_extract(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    vlm = None
    if args.mode in ("caption", "both") or args.vlm_ocr:
        vlm = VLMReasoner(cfg, force_fallback=args.no_vlm)
    result = capability_extract(
        args.root, mode=args.mode, vlm=vlm,
        out_path=args.out, recursive=args.recursive,
        cluster_threshold=args.cluster_threshold,
        batch_size=args.batch_size,
        max_workers=args.workers,
        use_cache=not args.no_cache,
    )
    print(f"\n🔎 Extracted from {len(result['records'])} photos (mode={args.mode}):")
    for r in result["records"][:15]:
        bits = []
        if r.get("caption"):
            bits.append(f"caption={r['caption']}")
        if r.get("ocr_text"):
            bits.append(f"text={r['ocr_text'][:40]}...")
        print(f"  {r['file']}: {'; '.join(bits)}")
    if result.get("out_path"):
        print(f"\n[saved] {result['out_path']}")
    return 0


def cmd_crop(args: argparse.Namespace) -> int:
    result = capability_crop(
        args.root, width=args.width, height=args.height, ratio=args.ratio,
        center_on_content=args.center_on_content, out_dir=args.out_dir,
        recursive=args.recursive, dry_run=not args.apply,
    )
    if result.get("error"):
        print(f"[crop] error: {result['error']}")
        return 1
    action = "Cropped" if args.apply else "Would crop"
    print(f"\n✂️  {action} {len(result['processed'])} images:")
    for p in result["processed"][:20]:
        print(f"  {os.path.basename(p['src'])} → {p['size']}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，原图只读，输出到 _cropped/）")
    return 0


def cmd_to_ppt(args: argparse.Namespace) -> int:
    result = capability_to_ppt(
        args.root, out_path=args.out, aspect=args.aspect,
        margin=args.margin, sort_by=args.sort_by, recursive=args.recursive,
    )
    if result.get("error"):
        print(f"[to_ppt] error: {result['error']}")
        return 1
    print(f"\n📊 PPT generated: {result['out_path']} ({result['slides']} slides, {args.aspect})")
    return 0


def cmd_collage(args: argparse.Namespace) -> int:
    result = capability_collage(
        args.root, out_path=args.out, cols=args.cols,
        page=args.page, gap=args.gap, dpi=args.dpi, recursive=args.recursive,
    )
    if result.get("error"):
        print(f"[collage] error: {result['error']}")
        return 1
    print(f"\n🖼️  Collage generated: {result['out_path']} ({result['images']} images, {args.page})")
    return 0


def cmd_video_rename_title(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    vlm = VLMReasoner(cfg, force_fallback=args.no_vlm) if args.ai else None
    result = capability_video_rename_title(
        args.root, vlm=vlm, use_ai=args.ai,
        max_len=args.max_len, min_len=args.min_len,
        preview_n=args.preview, dry_run=not args.apply,
        log_dir=cfg["executor"]["log_dir"],
    )
    action = "Renamed" if args.apply else "Would rename"
    print(f"\n🎬 {action} {result['total']} videos. 示例（默认即批量执行；加 --dry-run 仅预览）：")
    for ex in result["examples"]:
        print(f"  原：{ex['old']}")
        print(f"  新：{ex['new']}")
        print()
    if not args.apply:
        print(f"共 {result['total']} 个视频待处理（（以上为预览；去掉 --dry-run 即真正执行）。")
    elif result.get("log_path"):
        print(f"已记录操作日志，可一键回滚：python -m archive_assistant.cli.main rollback --last")
    return 0


# ============================================================
# image-rename-by-ocr — 图片按 OCR 文字改名
# ============================================================

def cmd_image_rename_by_ocr(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    vlm = VLMReasoner(cfg, force_fallback=args.no_vlm)
    result = capability_image_rename_by_ocr(
        args.root, vlm=vlm,
        max_len=args.max_len, preview_n=args.preview,
        dry_run=not args.apply, log_dir=cfg["executor"]["log_dir"],
    )
    action = "Renamed" if args.apply else "Would rename"
    print(f"\n🖼️  {action} {result['renamed']} / {result['total']} images (skipped {len(result['skipped'])} 无文字).")
    for ex in result["examples"]:
        print(f"  原：{ex['old']}")
        print(f"  新：{ex['new']}")
        if ex.get("ocr"):
            print(f"  OCR: {ex['ocr']}")
        print()
    if not args.apply:
        print("（以上为预览；去掉 --dry-run 即真正执行）")
    elif result.get("log_path"):
        print("已记录操作日志，可一键回滚：python -m archive_assistant.cli.main rollback --last")
    return 0


def cmd_video_compose(args: argparse.Namespace) -> int:
    result = capability_video_compose(
        args.image, args.audio, args.out,
        aspect=args.aspect, quality=args.quality, dry_run=not args.apply,
    )
    if result.get("error"):
        print(f"[video_compose] error: {result['error']}")
        return 1
    if result.get("dry_run"):
        print(f"\n🎞️  (dry-run) ffmpeg 命令已生成（去掉 --dry-run 即真正合成）：\n  {' '.join(result['cmd'])}")
        return 0
    print(f"\n🎞️  视频合成完成：{result['out_path']} ({args.aspect}, {args.quality})")
    return 0


def cmd_video_distribute(args: argparse.Namespace) -> int:
    result = capability_video_distribute(
        args.src, args.dst_base, per_folder=args.per_folder,
        folder_template=args.folder_template, sort_by=args.sort_by,
        dry_run=not args.apply,
    )
    action = "Distributed" if args.apply else "Would distribute"
    print(f"\n📦 {action} {result['total']} videos into {len(result['folders'])} folders (per_folder={args.per_folder}):")
    for m in result["moves"][:20]:
        print(f"  {os.path.basename(m['src'])} → {m['dst']}")
    if len(result["moves"]) > 20:
        print(f"  ... +{len(result['moves']) - 20} more")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


# ============================================================
# Office 文档/表格专项能力命令
# ============================================================

def cmd_table_clean(args: argparse.Namespace) -> int:
    keep = args.keep_cols.split(",") if args.keep_cols else None
    drop = args.drop_cols.split(",") if args.drop_cols else None
    dedup = args.dedup_by.split(",") if args.dedup_by else None
    dropna = args.dropna_cols.split(",") if args.dropna_cols else None
    result = capability_table_clean(
        args.src, keep_cols=keep, drop_cols=drop, dedup_by=dedup,
        dropna_cols=dropna, sort_by=args.sort_by, sort_desc=args.desc,
        sheet=args.sheet, out_path=args.out, dry_run=not args.apply,
    )
    if result.get("error"):
        print(f"[table-clean] error: {result['error']}")
        return 1
    action = "Cleaned" if args.apply else "Would clean"
    b, a = result["before"], result["after"]
    print(f"\n📊 {action}: {result['src']}")
    print(f"  行 {b['rows']}→{a['rows']}  列 {b['cols']}→{a['cols']}")
    for s in result["steps"]:
        print(f"    · {s}")
    print(f"  输出列: {result['columns']}")
    print(f"  → {result['out_path']}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，原文件只读，写到新文件）。")
    return 0


def cmd_table_merge(args: argparse.Namespace) -> int:
    result = capability_table_merge(
        args.root, pattern=args.pattern, out_path=args.out,
        recursive=args.recursive, sort_by=args.sort_by,
        add_source_col=not args.no_source_col, dry_run=not args.apply,
    )
    if result.get("error"):
        print(f"[table-merge] error: {result['error']}")
        return 1
    action = "Merged" if args.apply else "Would merge"
    print(f"\n📊 {action} {result['files_merged']} files → {result['rows']} rows x {result['cols']} cols")
    print(f"  列: {result['columns']}")
    if result.get("skipped"):
        print(f"  跳过 {len(result['skipped'])} 个无法解析的文件")
    print(f"  → {result['out_path']}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


def cmd_docx_compose(args: argparse.Namespace) -> int:
    context = None
    if args.context:
        import json
        try:
            context = json.loads(args.context)
        except json.JSONDecodeError as e:
            print(f"[docx-compose] --context 不是合法 JSON: {e}")
            return 2
    from_files = args.from_files.split(",") if args.from_files else None
    body = None
    if args.body_file:
        with open(expand(args.body_file), "r", encoding="utf-8") as f:
            body = f.read()
    elif args.body:
        body = args.body
    result = capability_docx_compose(
        args.out, title=args.title, from_files=from_files, body=body,
        template=args.template, context=context, dry_run=not args.apply,
    )
    if result.get("error"):
        print(f"[docx-compose] error: {result['error']}")
        return 1
    action = "Generated" if args.apply else "Would generate"
    print(f"\n📄 {action} Word ({result['mode']}): {result['out_path']}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）")
    return 0


def cmd_pdf_ops(args: argparse.Namespace) -> int:
    result = capability_pdf_ops(
        args.op, inputs=args.inputs, out_path=args.out,
        pages=args.pages, dry_run=not args.apply,
    )
    if result.get("error"):
        print(f"[pdf-ops] error: {result['error']}")
        return 1
    action = "Done" if args.apply else "Would run"
    target = result.get("out_path") or result.get("out_dir")
    print(f"\n📕 {action} pdf {result['op']} → {target}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，原 PDF 只读，写到新文件）。")
    return 0


# ============================================================
# 扩展能力命令（group-by / classify-rules / classify-into / flatten /
#               table-split / convert / pack / unpack）
# ============================================================

def _exec_dirs(args: argparse.Namespace):
    cfg = load_config(args.config)
    return (cfg["executor"]["log_dir"],
            cfg["executor"].get("trash_dir", "~/.archive_assistant/trash"))


def _print_groups(r: dict, action: str, by_note: str = "") -> None:
    print(f"\n🗂️  {action} {r['total']} files into {r['folders']} folders{by_note}:")
    for folder, files in list(r["groups"].items())[:20]:
        print(f"  📁 {folder}  ({len(files)})")
        for fn in files[:3]:
            print(f"     - {fn}")
        if len(files) > 3:
            print(f"     ... +{len(files) - 3} more")


def _parse_rules(spec: str):
    rules = []
    for part in spec.split(";"):
        part = part.strip()
        if not part or ":" not in part:
            continue
        folder, kws = part.split(":", 1)
        folder = folder.strip()
        kw_list = [k.strip() for k in kws.split(",") if k.strip()]
        if folder and kw_list:
            rules.append((folder, kw_list))
    return rules


def cmd_group_by(args: argparse.Namespace) -> int:
    log_dir, trash_dir = _exec_dirs(args)
    r = capability_group_by(
        args.root, by=args.by, granularity=args.granularity,
        recursive=args.recursive, dry_run=not args.apply,
        log_dir=log_dir, trash_dir=trash_dir,
    )
    if r.get("error"):
        print(f"[group-by] error: {r['error']}")
        return 1
    _print_groups(r, "Grouped" if args.apply else "Would group", f" (by={r['by']})")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，删除入回收站，可 rollback）。")
    elif r.get("log_path"):
        print("已记录日志，可回滚：python -m archive_assistant.cli.main rollback --last")
    return 0


def cmd_classify_rules(args: argparse.Namespace) -> int:
    log_dir, trash_dir = _exec_dirs(args)
    rules = _parse_rules(args.rules)
    if not rules:
        print('[classify-rules] --rules 格式错误。示例: "发票:发票,invoice;合同:合同,协议"')
        return 2
    r = capability_classify_rules(
        args.root, rules=rules, by_content=args.by_content,
        unmatched_label=args.unmatched, keep_unmatched=args.keep_unmatched,
        recursive=args.recursive, dry_run=not args.apply,
        log_dir=log_dir, trash_dir=trash_dir,
    )
    if r.get("error"):
        print(f"[classify-rules] error: {r['error']}")
        return 1
    _print_groups(r, "Classified" if args.apply else "Would classify")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，可 rollback）。")
    elif r.get("log_path"):
        print("已记录日志，可回滚：python -m archive_assistant.cli.main rollback --last")
    return 0


def cmd_classify_into(args: argparse.Namespace) -> int:
    cfg = load_config(args.config)
    log_dir = cfg["executor"]["log_dir"]
    trash_dir = cfg["executor"].get("trash_dir", "~/.archive_assistant/trash")
    by_content = not args.no_content
    vlm = VLMReasoner(cfg, force_fallback=args.no_vlm) if by_content else None
    cats = [c for c in args.categories.split(",") if c.strip()]
    r = capability_classify_into(
        args.root, categories=cats, vlm=vlm, by_content=by_content,
        unmatched_label=args.unmatched, keep_unmatched=args.keep_unmatched,
        recursive=args.recursive, dry_run=not args.apply,
        log_dir=log_dir, trash_dir=trash_dir,
    )
    if r.get("error"):
        print(f"[classify-into] error: {r['error']}")
        return 1
    _print_groups(r, "Classified" if args.apply else "Would classify",
                  f" (VLM={r.get('vlm_used')})")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，可 rollback）。")
    elif r.get("log_path"):
        print("已记录日志，可回滚：python -m archive_assistant.cli.main rollback --last")
    return 0


def cmd_flatten(args: argparse.Namespace) -> int:
    log_dir, trash_dir = _exec_dirs(args)
    r = capability_flatten(
        args.root, prefix_with_dir=args.prefix_with_dir,
        clean_empty_dirs=args.clean_empty_dirs,
        dry_run=not args.apply, log_dir=log_dir, trash_dir=trash_dir,
    )
    if r.get("error"):
        print(f"[flatten] error: {r['error']}")
        return 1
    action = "Flattened" if args.apply else "Would flatten"
    print(f"\n🗂️  {action} {r['total']} files to top level of {r['root']}:")
    for fn in r["files"][:20]:
        print(f"  - {fn}")
    cleaned = r.get("cleaned_dirs") or []
    if args.clean_empty_dirs and cleaned:
        verb = "Removed" if args.apply else "Would remove"
        print(f"\n🧹 {verb} {len(cleaned)} empty dir(s):")
        for d in cleaned[:20]:
            print(f"  - {d}")
    if r["total"] > 20:
        print(f"  ... +{r['total'] - 20} more")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，同名自动改名，可 rollback）。")
    elif r.get("log_path"):
        print("已记录日志，可回滚：python -m archive_assistant.cli.main rollback --last")
    return 0


def cmd_table_split(args: argparse.Namespace) -> int:
    r = capability_table_split(
        args.src, by_col=args.by_col, out_dir=args.out_dir,
        fmt=args.format, sheet=args.sheet, dry_run=not args.apply,
    )
    if r.get("error"):
        print(f"[table-split] error: {r['error']}")
        return 1
    action = "Split" if args.apply else "Would split"
    print(f"\n📊 {action} {r['src']} by '{r['by_col']}' → {r['files']} files in {r['out_dir']}:")
    for p in r["parts"][:20]:
        print(f"  {p['file']}  ({p['rows']} rows)")
    if r["files"] > 20:
        print(f"  ... +{r['files'] - 20} more")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行，原文件只读，写到新目录）。")
    return 0


def cmd_convert(args: argparse.Namespace) -> int:
    r = capability_convert_image(
        args.root, to=args.to, max_edge=args.max_edge, quality=args.quality,
        out_dir=args.out_dir, recursive=args.recursive, dry_run=not args.apply,
    )
    if r.get("error"):
        print(f"[convert] error: {r['error']}")
        return 1
    action = "Converted" if args.apply else "Would convert"
    print(f"\n🖼️  {action} {r['total']} images → {args.to} in {r['out_dir']}")
    if args.apply:
        print(f"  成功 {len(r['converted'])}，失败 {len(r['failed'])}")
    else:
        for p in r["planned"][:10]:
            print(f"  {p['src']} → {p['dst']}")
        print("\n（以上为预览；去掉 --dry-run 即真正执行，原图只读，写到新目录）。")
    return 0


def cmd_pack(args: argparse.Namespace) -> int:
    r = capability_pack(args.inputs, out_path=args.out, base_dir=args.base_dir,
                        dry_run=not args.apply)
    if r.get("error"):
        print(f"[pack] error: {r['error']}")
        return 1
    action = "Packed" if args.apply else "Would pack"
    print(f"\n📦 {action} {r['file_count']} files → {r['out_path']}")
    if not args.apply:
        print("\n（以上为预览；去掉 --dry-run 即真正执行）。")
    return 0


def cmd_unpack(args: argparse.Namespace) -> int:
    r = capability_unpack(args.archive, out_dir=args.out_dir, dry_run=not args.apply)
    if r.get("error"):
        print(f"[unpack] error: {r['error']}")
        return 1
    action = "Unpacked" if args.apply else "Would unpack"
    print(f"\n📦 {action} {r['member_count']} members → {r['out_dir']}")
    if not args.apply:
        for m in r["members"][:10]:
            print(f"  - {m}")
        if r["member_count"] > 10:
            print(f"  ... +{r['member_count'] - 10} more")
        print("\n（以上为预览；去掉 --dry-run 即真正执行，含 zip-slip 路径穿越防护）。")
    return 0


# ============================================================
# Parser
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        "desktop-archive",
        description="Desktop Archive Assistant — 能力驱动，按需组合",
    )
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--no-vlm", action="store_true", help="force rule-based fallback")
    sub = p.add_subparsers(dest="cmd", required=True)

    # auto — 意图路由（弱模型友好，放在第一个最显眼位置）
    au = sub.add_parser("auto", help="意图路由：一句话自动选命令（弱模型首选入口）")
    au.add_argument("query", nargs="?", default="", help="用户的自然语言请求，如'整理桌面''照片去重'")
    au.add_argument("--root", default=None, help="操作目录（默认自动检测桌面）")
    au.add_argument("--execute", action="store_true", help="路由后直接执行命令（默认真正执行）")
    au.add_argument("--dry-run", action="store_true", help="配合 --execute：只预览不实际执行")
    au.add_argument("--json", action="store_true", help="输出 JSON 格式路由结果")
    au.add_argument("--list-intents", action="store_true", help="列出所有可识别意图")
    au.set_defaults(func=cmd_auto)

    # setup — 跨平台自动下载 VLM 模型（弱模型可自主触发）
    st = sub.add_parser("setup", help="检测/下载 VLM 模型权重（跨平台自动安装）")
    st.add_argument("--check", action="store_true", help="仅检查状态，不下载")
    st.add_argument("--install-deps", action="store_true", help="自动安装缺失的 pip 依赖")
    st.add_argument("--no-download", action="store_true", help="不自动下载，只输出指引")
    st.set_defaults(func=cmd_setup)

    # scan
    s = sub.add_parser("scan", help="扫描目录，列出文件")
    s.add_argument("root", help="dir to scan")
    s.add_argument("--recursive", action="store_true")
    s.add_argument("--max-depth", type=int, default=1)
    s.set_defaults(func=cmd_scan)

    # organize（全流程）
    o = sub.add_parser("organize", help="全流程整理（scan+enrich+classify+plan）")
    o.add_argument("root", help="dir to organize")
    o.add_argument("--out", default=None, help="save plan to this path")
    o.add_argument("--recursive", action="store_true")
    o.add_argument("--max-depth", type=int, default=1)
    o.add_argument("--include-shortcuts", action="store_true", help="include shortcuts (dangerous!)")
    o.add_argument("--skip-existing", action="store_true", help="skip matching to existing folders")
    o.add_argument("--skip-infrequent", action="store_true", help="skip infrequent file detection")
    o.add_argument("--skip-vlm-theme", action="store_true", help="skip VLM theme clustering")
    o.set_defaults(func=cmd_organize)

    # classify（轻量归类）
    c = sub.add_parser("classify", help="仅归类，生成 plan")
    c.add_argument("root", help="dir to classify")
    c.add_argument("--out", default=None)
    c.add_argument("--recursive", action="store_true")
    c.add_argument("--max-depth", type=int, default=1)
    c.add_argument("--skip-enrich", action="store_true", help="skip content extraction")
    c.set_defaults(func=cmd_classify)

    # apply
    a = sub.add_parser("apply", help="执行 plan.json")
    a.add_argument("--plan", required=True, help="plan.json path")
    a.add_argument("--backup-dir", default=None, help="backup before apply")
    a.set_defaults(func=cmd_apply)

    # rollback
    r = sub.add_parser("rollback", help="回撤操作")
    r.add_argument("--last", action="store_true")
    r.add_argument("--log", default=None)
    r.set_defaults(func=cmd_rollback)

    # rename
    rn = sub.add_parser("rename", help="批量重命名文件")
    rn.add_argument("root", help="dir to rename files in")
    rn.add_argument("--template", default="{seq}", help="name template: {seq} {seq2} {name} {ext} {date} {time}")
    rn.add_argument("--sort-by", default="time", choices=["time", "name", "size"])
    rn.add_argument("--start", type=int, default=1, help="sequence start")
    rn.add_argument("--step", type=int, default=2, help="group size for {seq2}")
    rn.add_argument("--ext", default=None, help="comma-separated extensions, e.g. jpg,png")
    rn.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    rn.set_defaults(func=cmd_rename)

    # find
    fd = sub.add_parser("find", help="查找文件")
    fd.add_argument("root", help="dir to search")
    fd.add_argument("--name", default=None, help="filename pattern, e.g. *.xlsx")
    fd.add_argument("--ext", default=None, help="comma-separated extensions")
    fd.add_argument("--min-size", type=int, default=None, help="min size in bytes")
    fd.add_argument("--max-size", type=int, default=None, help="max size in bytes")
    fd.add_argument("--modified-since", type=float, default=None, help="modified within last N hours")
    fd.add_argument("--no-recursive", action="store_true")
    fd.set_defaults(func=cmd_find)

    # dedupe
    dd = sub.add_parser("dedupe", help="文件去重")
    dd.add_argument("root", help="dir to dedupe")
    dd.add_argument("--method", default="hash", choices=["hash", "filename", "phash"])
    dd.add_argument("--phash-threshold", type=int, default=5, help="phash 汉明距离阈值(越小越严格)")
    dd.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    dd.add_argument("--trash-dir", default=None)
    dd.set_defaults(func=cmd_dedupe)

    # sync
    sy = sub.add_parser("sync", help="增量同步目录")
    sy.add_argument("src", help="source dir")
    sy.add_argument("dst", help="destination dir")
    sy.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    sy.set_defaults(func=cmd_sync)

    # inspect
    ins = sub.add_parser("inspect", help="巡检新增文件")
    ins.add_argument("root", help="dir to inspect")
    ins.add_argument("--since", type=float, default=24, help="hours to look back")
    ins.set_defaults(func=cmd_inspect)

    # move
    mv = sub.add_parser("move", help="批量移动文件")
    mv.add_argument("root", help="dir to move from")
    mv.add_argument("--match", required=True, help="filename pattern, e.g. *.lnk")
    mv.add_argument("--to", required=True, help="destination dir")
    mv.add_argument("--ext", default=None, help="comma-separated extensions")
    mv.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    mv.set_defaults(func=cmd_move)

    # clean
    cl = sub.add_parser("clean", help="清理临时文件/空目录")
    cl.add_argument("root", help="dir to clean")
    cl.add_argument("--temp", action="store_true", help="clean temp files (~$*, *.tmp, Thumbs.db)")
    cl.add_argument("--empty-dirs", action="store_true", help="remove empty directories")
    cl.add_argument("--locked", action="store_true", help="detect locked files (report only)")
    cl.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    cl.set_defaults(func=cmd_clean)

    # backup
    b = sub.add_parser("backup", help="整理前备份")
    b.add_argument("root", help="dir to backup")
    b.add_argument("--backup-dir", default=None, help="backup target")
    b.set_defaults(func=cmd_backup)

    # sort
    so = sub.add_parser("sort", help="排列桌面图标")
    so.add_argument("root", help="desktop dir")
    so.add_argument("--by", default=None, help="sort by: ItemType")
    so.set_defaults(func=cmd_sort)

    # report
    rep = sub.add_parser("report", help="生成 Markdown 整理报告")
    rep.add_argument("--plan", required=True, help="plan.json path")
    rep.add_argument("--log", default=None, help="log path")
    rep.add_argument("--out", default=None, help="save report to file")
    rep.set_defaults(func=cmd_report)

    # schedule
    sch = sub.add_parser("schedule", help="定时整理")
    sch.add_argument("root", nargs="?", default=None, help="dir to schedule")
    sch.add_argument("--cron", default=None, help='cron expr, e.g. "0 18 * * *"')
    sch.add_argument("--list", action="store_true", help="list scheduled tasks")
    sch.add_argument("--remove", action="store_true", help="remove scheduled task")
    sch.set_defaults(func=cmd_schedule)

    # ---- 照片/视频专项能力 ----

    # archive-by-date（按 EXIF 日期归档）
    abd = sub.add_parser("archive-by-date", help="按 EXIF 拍摄日期归档照片到 年/月/日")
    abd.add_argument("root", help="照片目录")
    abd.add_argument("--level", default="day", choices=["day", "month", "year"])
    abd.add_argument("--no-mtime-fallback", action="store_true", help="无 EXIF 时不回退到修改时间")
    abd.add_argument("--recursive", action="store_true")
    abd.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    abd.set_defaults(func=cmd_archive_by_date)

    # extract（照片内容/文字识别）
    ext = sub.add_parser("extract", help="VLM 识别照片内容/文字 → txt/csv")
    ext.add_argument("root", help="照片目录")
    ext.add_argument("--mode", default="caption", choices=["caption", "ocr", "both"])
    ext.add_argument("--vlm-ocr", action="store_true", help="OCR 也走 VLM（默认 tesseract 回退）")
    ext.add_argument("--out", default=None, help="输出文件(.csv 或 .txt/.md)")
    ext.add_argument("--recursive", action="store_true")
    ext.add_argument("--cluster-threshold", type=int, default=5,
                     help="caption 预聚类 pHash 汉明距离阈值；同簇共用描述省调用。<0 关闭预聚类")
    ext.add_argument("--batch-size", type=int, default=8,
                     help="代表图批量推理的每批张数（transformers 后端一次多图前向）")
    ext.add_argument("--workers", type=int, default=4,
                     help="OCR 并发线程数（pytesseract/ollama 后端生效，transformers 单卡自动串行）")
    ext.add_argument("--no-cache", action="store_true",
                     help="禁用 VLM 结果缓存（默认按 路径+mtime 命中复用）")
    ext.set_defaults(func=cmd_extract)

    # crop（图片裁剪）
    cr = sub.add_parser("crop", help="批量裁剪图片(按尺寸/比例)")
    cr.add_argument("root", help="图片目录")
    cr.add_argument("--width", type=int, default=None)
    cr.add_argument("--height", type=int, default=None)
    cr.add_argument("--ratio", default=None, help='目标比例，如 "16:9" "9:16" "1:1"')
    cr.add_argument("--center-on-content", action="store_true", help="检测深色/主体区域居中")
    cr.add_argument("--out-dir", default=None, help="输出目录(默认 _cropped/)")
    cr.add_argument("--recursive", action="store_true")
    cr.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    cr.set_defaults(func=cmd_crop)

    # to-ppt（图片转 PPT）
    tp = sub.add_parser("to-ppt", help="图片批量转 PPT(每张一页居中)")
    tp.add_argument("root", help="图片目录")
    tp.add_argument("--out", default=None, help="输出 .pptx 路径")
    tp.add_argument("--aspect", default="16:9", choices=["16:9", "4:3"])
    tp.add_argument("--margin", type=float, default=0.08, help="边距比例")
    tp.add_argument("--sort-by", default="name", choices=["name", "time"])
    tp.add_argument("--recursive", action="store_true")
    tp.set_defaults(func=cmd_to_ppt)

    # collage（图片拼接）
    co = sub.add_parser("collage", help="多图拼接成一张(A4/网格)")
    co.add_argument("root", help="图片目录")
    co.add_argument("--out", default=None, help="输出图片路径")
    co.add_argument("--cols", type=int, default=0, help="每行列数(0=自动)")
    co.add_argument("--page", default="A4", choices=["A4", "A4L", "none"])
    co.add_argument("--gap", type=int, default=50, help="间距像素")
    co.add_argument("--dpi", type=int, default=300)
    co.add_argument("--recursive", action="store_true")
    co.set_defaults(func=cmd_collage)

    # video-rename-title（视频标题 AI 重命名）
    vrt = sub.add_parser("video-rename-title", help="视频标题 AI 重命名(删#标签/括号,15-25字)")
    vrt.add_argument("root", help="视频目录")
    vrt.add_argument("--ai", action="store_true", help="用 VLM 生成意思相近的新标题(默认仅规则清洗)")
    vrt.add_argument("--max-len", type=int, default=25)
    vrt.add_argument("--min-len", type=int, default=15)
    vrt.add_argument("--preview", type=int, default=3, help="先展示几个示例")
    vrt.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    vrt.set_defaults(func=cmd_video_rename_title)

    # image-rename-by-ocr（图片按 OCR 文字改名）
    iro = sub.add_parser("image-rename-by-ocr", help="图片按 VLM OCR 文字重命名(可回滚)")
    iro.add_argument("root", help="图片目录")
    iro.add_argument("--max-len", type=int, default=30, help="新文件名最大字符数")
    iro.add_argument("--preview", type=int, default=5, help="先展示几个示例")
    iro.add_argument("--no-vlm", action="store_true", help="不调 VLM(无文字则跳过)")
    iro.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    iro.set_defaults(func=cmd_image_rename_by_ocr)

    # video-compose（图音合成视频）
    vc = sub.add_parser("video-compose", help="图片+音频合成视频(FFmpeg)")
    vc.add_argument("--image", required=True, help="图片路径")
    vc.add_argument("--audio", required=True, help="音频路径")
    vc.add_argument("--out", required=True, help="输出视频路径")
    vc.add_argument("--aspect", default="9:16", choices=["9:16", "16:9", "1:1"])
    vc.add_argument("--quality", default="high", choices=["high", "standard"])
    vc.add_argument("--dry-run", dest="apply", action="store_false", help="只打印 ffmpeg 命令，不实际合成（默认直接合成）")
    vc.set_defaults(func=cmd_video_compose)

    # video-distribute（视频分发到多文件夹）
    vd = sub.add_parser("video-distribute", help="视频分发到多个子文件夹")
    vd.add_argument("src", help="源视频目录")
    vd.add_argument("dst_base", help="目标基目录")
    vd.add_argument("--per-folder", type=int, default=1, help="每个子文件夹放几个视频")
    vd.add_argument("--folder-template", default="{seq}", help="子文件夹命名模板, {seq}=序号")
    vd.add_argument("--sort-by", default="name", choices=["name", "time"])
    vd.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    vd.set_defaults(func=cmd_video_distribute)

    # ---- Office 文档/表格专项能力 ----

    # table-clean（表格列筛选/去重/清洗/排序）
    tc = sub.add_parser("table-clean", help="表格列筛选/去重/空值清洗/排序(xlsx/csv)")
    tc.add_argument("src", help="表格文件 .xlsx/.xls/.csv/.tsv")
    tc.add_argument("--keep-cols", default=None, help="仅保留这些列(逗号分隔)")
    tc.add_argument("--drop-cols", default=None, help="删除这些列(逗号分隔)")
    tc.add_argument("--dedup-by", default=None, help="按这些列去重(逗号分隔,留空=整行去重需配合本选项)")
    tc.add_argument("--dropna-cols", default=None, help="这些列为空则丢弃整行(逗号分隔)")
    tc.add_argument("--sort-by", default=None, help="按该列排序")
    tc.add_argument("--desc", action="store_true", help="降序排序")
    tc.add_argument("--sheet", default=None, help="xlsx sheet 名(默认第一个)")
    tc.add_argument("--out", default=None, help="输出路径(默认 <name>_cleaned.xlsx)")
    tc.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    tc.set_defaults(func=cmd_table_clean)

    # table-merge（多文件汇总成表）
    tm = sub.add_parser("table-merge", help="多个 txt/csv/xlsx 汇总成一个 xlsx")
    tm.add_argument("root", help="源目录")
    tm.add_argument("--pattern", default="*.txt", help='匹配模式, 如 "*.csv" "*.txt"')
    tm.add_argument("--out", default=None, help="输出路径(默认 root/merged.xlsx)")
    tm.add_argument("--recursive", action="store_true")
    tm.add_argument("--sort-by", default="name", choices=["name", "time"])
    tm.add_argument("--no-source-col", action="store_true", help="不添加来源文件名列")
    tm.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    tm.set_defaults(func=cmd_table_merge)

    # docx-compose（清单/资料整理成 Word）
    dc = sub.add_parser("docx-compose", help="清单/资料整理成 Word(标题+段落/列表,支持模板)")
    dc.add_argument("--out", required=True, help="输出 .docx 路径")
    dc.add_argument("--title", default=None, help="文档标题")
    dc.add_argument("--body", default=None, help="正文文本(换行分段)")
    dc.add_argument("--body-file", default=None, help="从文本文件读正文")
    dc.add_argument("--from-files", default=None, help="收集文件清单(逗号分隔的 glob)")
    dc.add_argument("--template", default=None, help="Word 模板 .docx(用 docxtpl 渲染)")
    dc.add_argument("--context", default=None, help="模板上下文 JSON 字符串")
    dc.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    dc.set_defaults(func=cmd_docx_compose)

    # pdf-ops（PDF 合并/拆分/抽取文本）
    po = sub.add_parser("pdf-ops", help="PDF 合并/拆分/抽取文本(纯 pypdf)")
    po.add_argument("op", choices=["merge", "split", "extract"])
    po.add_argument("inputs", nargs="+", help="输入 PDF(支持 glob; merge 需多个)")
    po.add_argument("--out", default=None, help="输出路径/目录")
    po.add_argument("--pages", default=None, help='split 时抽取页, 如 "1-3,5"(留空=每页拆一个)')
    po.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    po.set_defaults(func=cmd_pdf_ops)

    # ---- 扩展能力 ----

    # group-by（按 类型/扩展名/日期/首字母 分组到子文件夹）
    gb = sub.add_parser("group-by", help="按 类型/扩展名/日期/首字母 把文件分组到子文件夹")
    gb.add_argument("root", help="目录")
    gb.add_argument("--by", default="type", choices=["type", "ext", "date", "initial"],
                    help="分组方式：type 大类 / ext 扩展名 / date 日期 / initial 首字母")
    gb.add_argument("--granularity", default="month", choices=["year", "month", "day"],
                    help="date 模式的粒度")
    gb.add_argument("--recursive", action="store_true")
    gb.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    gb.set_defaults(func=cmd_group_by)

    # classify-rules（关键词规则归类）
    crl = sub.add_parser("classify-rules", help='按关键词规则归类，如 "发票:发票,invoice;合同:合同,协议"')
    crl.add_argument("root", help="目录")
    crl.add_argument("--rules", required=True, help='规则: "文件夹:kw1,kw2;文件夹2:kw3"')
    crl.add_argument("--by-content", action="store_true", help="同时读 PDF/DOCX/txt 正文匹配")
    crl.add_argument("--unmatched", default="未分类", help="未命中文件归入的文件夹名")
    crl.add_argument("--keep-unmatched", action="store_true", help="未命中文件保持不动")
    crl.add_argument("--recursive", action="store_true")
    crl.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    crl.set_defaults(func=cmd_classify_rules)

    # classify-into（自定义类别 VLM 归类）
    cin = sub.add_parser("classify-into", help='按给定类别归类(VLM)，如 --categories "土建,安装,市政"')
    cin.add_argument("root", help="目录")
    cin.add_argument("--categories", required=True, help="逗号分隔的类别列表")
    cin.add_argument("--no-content", action="store_true", help="只看文件名，不读正文(更快)")
    cin.add_argument("--unmatched", default="其他", help="未归类文件夹名")
    cin.add_argument("--keep-unmatched", action="store_true", help="未归类文件保持不动")
    cin.add_argument("--recursive", action="store_true")
    cin.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    cin.set_defaults(func=cmd_classify_into)

    # flatten（子目录文件平铺到顶层）
    fl = sub.add_parser("flatten", help="把多层子目录的文件平铺到顶层")
    fl.add_argument("root", help="目录")
    fl.add_argument("--prefix-with-dir", action="store_true", help="用来源目录名做文件名前缀")
    fl.add_argument("--clean-empty-dirs", action="store_true", help="平铺后删除变空的子目录壳子")
    fl.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    fl.set_defaults(func=cmd_flatten)

    # table-split（表格按列值拆分）
    ts2 = sub.add_parser("table-split", help="表格按某列的取值拆分成多个文件")
    ts2.add_argument("src", help="表格文件 xlsx/csv")
    ts2.add_argument("--by-col", required=True, help="按该列的取值拆分")
    ts2.add_argument("--out-dir", default=None, help="输出目录(默认 <name>_split/)")
    ts2.add_argument("--format", default="xlsx", choices=["xlsx", "csv"])
    ts2.add_argument("--sheet", default=None, help="xlsx sheet 名(默认第一个)")
    ts2.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    ts2.set_defaults(func=cmd_table_split)

    # convert（图片格式转换/缩放/压缩）
    cv = sub.add_parser("convert", help="图片批量格式转换/缩放/压缩(jpg/png/webp...)")
    cv.add_argument("root", help="图片目录")
    cv.add_argument("--to", default="jpg", help="目标格式 jpg/png/webp/bmp/tiff")
    cv.add_argument("--max-edge", type=int, default=None, help="最长边像素(等比缩放)")
    cv.add_argument("--quality", type=int, default=85, help="jpg/webp 质量 1-100")
    cv.add_argument("--out-dir", default=None, help="输出目录(默认 _converted/)")
    cv.add_argument("--recursive", action="store_true")
    cv.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    cv.set_defaults(func=cmd_convert)

    # pack（zip 打包）
    pk = sub.add_parser("pack", help="把文件/目录打包成 zip")
    pk.add_argument("inputs", nargs="+", help="文件/目录/glob(可多个)")
    pk.add_argument("--out", default=None, help="输出 zip 路径")
    pk.add_argument("--base-dir", default=None, help="zip 内相对路径基准目录")
    pk.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    pk.set_defaults(func=cmd_pack)

    # unpack（zip 解压）
    up = sub.add_parser("unpack", help="解压 zip(含 zip-slip 路径穿越防护)")
    up.add_argument("archive", help="zip 文件")
    up.add_argument("--out-dir", default=None, help="解压目标目录(默认与压缩包同名)")
    up.add_argument("--dry-run", dest="apply", action="store_false", help="只预览不执行（默认直接执行）")
    up.set_defaults(func=cmd_unpack)

    return p


# ============================================================
# auto — 意图路由（弱模型友好：一句话自动选命令）
# ============================================================

def cmd_auto(args: argparse.Namespace) -> int:
    """意图路由：把用户自然语言请求路由到正确的 CLI 能力。

    两种模式：
    1. 仅路由（默认）：分析意图，输出建议命令，不执行
    2. 直接执行（--execute）：路由后自动运行对应命令
    """
    query = args.query
    root = args.root or default_desktop_dir()

    if args.list_intents:
        intents = list_intents()
        print(f"可识别意图（{len(intents)} 个）：")
        for it in intents:
            kws = " / ".join(it["keywords"])
            print(f"  • {it['intent']:20s} → {it['subcommand']:20s}  [{kws}]")
        return 0

    result = auto_route(query, root=root)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    print(format_route_result(result))

    if args.execute:
        import subprocess
        import sys as _sys
        import tempfile
        steps = result["workflow"]
        root = result["root"]

        # organize+apply 多步工作流需要共享 plan 文件
        plan_path = os.path.join(tempfile.gettempdir(), f"auto_plan_{os.getpid()}.json")

        rc = 0
        for i, step in enumerate(steps):
            # dry-run 预览模式跳过 sort（排列桌面图标有实际副作用，预览不应触发）
            if args.dry_run and step.split()[0] == "sort":
                print(f"\n▶ 跳过(预览模式): {step}")
                continue
            cmd_parts = [_sys.executable, "-m", "archive_assistant.cli.main"]
            parts = step.split()

            # organize 步骤：加 --out <plan> 保存计划
            if parts[0] == "organize" and len(steps) > 1:
                cmd_parts += parts
                if root:
                    cmd_parts.append(root)
                cmd_parts += ["--out", plan_path]
            # apply 步骤：用 --plan <plan> 加载计划（不需要 root）
            elif parts[0] == "apply":
                cmd_parts += parts
                cmd_parts += ["--plan", plan_path]
            # 其他单步命令
            else:
                cmd_parts += parts
                if root:
                    cmd_parts.append(root)
                # 仅在预览模式给支持 --dry-run 的命令加该标志
                if args.dry_run and parts[0] not in (
                    "scan", "inspect", "find", "sort", "rollback", "schedule",
                    "extract", "to-ppt", "collage",
                ):
                    cmd_parts.append("--dry-run")

            print(f"\n▶ 执行: {' '.join(cmd_parts)}")
            proc = subprocess.run(cmd_parts)
            if proc.returncode != 0:
                print(f"  ⚠ 步骤失败 (exit={proc.returncode})，后续步骤跳过")
                rc = proc.returncode
                break

        # 清理临时 plan 文件
        try:
            if os.path.exists(plan_path):
                os.remove(plan_path)
        except OSError:
            pass
        return rc

    print("\n💡 如需自动执行，添加 --execute（默认真正执行）；仅预览加 --execute --dry-run")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """跨平台 VLM 模型安装/检测。"""
    from ..setup_model import main as setup_main
    argv = []
    if args.check:
        argv.append("--check")
    if args.install_deps:
        argv.append("--install-deps")
    if getattr(args, "no_download", False):
        argv.append("--no-download")
    return setup_main(argv)


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        logger.warning("interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
