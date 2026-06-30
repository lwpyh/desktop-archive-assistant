"""扩展能力集 — 在原有 30 个能力之上，补齐日志中高频但缺失的「文件整理」需求。

设计原则（与全 skill 一致）：
- 移动型能力（group_by / classify_rules / classify_into / flatten）统一构造 ArchivePlan，
  走 executor.apply_plan：自动获得 dry-run、同名不覆盖、删除入回收站、可 rollback。
- 内容型能力（table_split / convert_image / pack / unpack）默认 dry-run，
  输出写到新文件/新目录，绝不覆盖原文件。
- 全部纯本地、无联网；依赖（pandas/PIL）按需懒加载，缺失时给出明确提示并优雅降级。

能力清单（8 个）：
  group_by        — 按 类型/扩展名/日期/首字母 把文件分组到子文件夹
  classify_rules  — 按用户给定关键词规则把文件归入指定文件夹（零成本、确定性）
  classify_into   — 按用户给定类别让 VLM 读文件名+正文 zero-shot 归类（无 VLM 回退关键词）
  flatten         — 把多层子目录里的文件平铺到一层（可选用来源目录名做前缀）
  table_split     — 表格按某列值拆分成多个文件（xlsx/csv）
  convert_image   — 图片批量格式转换 / 缩放 / 压缩（jpg/png/webp...）
  pack            — 把文件/目录打包成 zip
  unpack          — 解压 zip（含 zip-slip 路径穿越防护）
"""
from __future__ import annotations

import glob
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

from .core import ArchivePlan, PlanAction
from .executor import apply_plan
from .extractors.text_extractor import extract_body_text, filename_signal
from .utils import expand, ensure_dir, logger, safe_folder_name


# ============================================================
# 共用：扫描 + 构造移动计划
# ============================================================

# 大类映射（扩展名 → 中文大类名）
_TYPE_MAP: Dict[str, List[str]] = {
    "图片": ["jpg", "jpeg", "png", "gif", "bmp", "webp", "tiff", "tif", "heic", "svg", "ico", "raw"],
    "视频": ["mp4", "mov", "avi", "mkv", "flv", "wmv", "webm", "m4v", "mpg", "mpeg", "ts", "rmvb"],
    "音频": ["mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "ape", "aiff"],
    "文档": ["doc", "docx", "pdf", "txt", "md", "rtf", "odt", "pages", "wps", "epub"],
    "表格": ["xls", "xlsx", "xlsm", "csv", "tsv", "ods", "et"],
    "演示": ["ppt", "pptx", "key", "odp", "dps"],
    "压缩包": ["zip", "rar", "7z", "tar", "gz", "bz2", "xz", "tgz"],
    "安装包": ["exe", "msi", "dmg", "pkg", "deb", "rpm", "apk"],
    "代码": ["py", "js", "ts", "java", "c", "cpp", "h", "hpp", "go", "rs", "rb", "php",
             "html", "css", "json", "xml", "yaml", "yml", "sh", "sql"],
}
_EXT2TYPE = {ext: name for name, exts in _TYPE_MAP.items() for ext in exts}


def _scan_files(root: str, recursive: bool = False):
    """扫描目录，返回非快捷方式的 Asset 列表（复用主扫描器，自动跳过系统文件/快捷方式）。"""
    from .capabilities import capability_scan
    assets = capability_scan(root, recursive=recursive, max_depth=10 if recursive else 1)
    return [a for a in assets if not a.is_shortcut]


def _run_move_plan(
    root: str,
    assignments: List[Tuple[str, Optional[str]]],
    dry_run: bool,
    log_dir: str,
    trash_dir: str,
) -> Dict[str, Any]:
    """根据 (源文件, 目标子文件夹名) 列表构造并执行移动计划。

    folder=None 的文件保持不动。复用 executor：dry-run 不改文件；apply 写日志可 rollback。
    """
    plan = ArchivePlan(root=root, mode="desktop", created_at=time.time())
    seen_dirs: set = set()
    groups: Dict[str, List[str]] = defaultdict(list)
    skipped = 0

    for src, folder in assignments:
        if not folder:
            skipped += 1
            continue
        folder_safe = safe_folder_name(folder)
        target_dir = os.path.join(root, folder_safe)
        dst = os.path.join(target_dir, os.path.basename(src))
        if os.path.abspath(dst) == os.path.abspath(src):
            skipped += 1
            continue
        if target_dir not in seen_dirs:
            plan.actions.append(PlanAction(op="mkdir", dst=target_dir,
                                           note="extra:group"))
            seen_dirs.add(target_dir)
        plan.actions.append(PlanAction(op="move", src=src, dst=dst, note="extra:group"))
        groups[folder_safe].append(os.path.basename(src))

    result: Dict[str, Any] = {
        "root": root,
        "groups": {k: v for k, v in groups.items()},
        "folders": len(groups),
        "total": sum(len(v) for v in groups.values()),
        "skipped": skipped,
        "dry_run": dry_run,
        "log_path": None,
    }
    if not dry_run and plan.actions:
        result["log_path"] = apply_plan(plan.to_dict(), log_dir=log_dir, trash_dir=trash_dir)
    return result


# ============================================================
# 能力 1：group_by — 按 类型/扩展名/日期/首字母 分组
# ============================================================

def _group_label(a, by: str, granularity: str) -> Optional[str]:
    if by == "type":
        return _EXT2TYPE.get(a.ext, "其他")
    if by == "ext":
        return (a.ext.upper() + " 文件") if a.ext else "无扩展名"
    if by == "date":
        ts = a.best_time() or a.mtime or 0.0
        if not ts:
            return "未知日期"
        fmt = {"year": "%Y", "month": "%Y-%m", "day": "%Y-%m-%d"}.get(granularity, "%Y-%m")
        return time.strftime(fmt, time.localtime(ts))
    if by == "initial":
        name = os.path.basename(a.path)
        ch = name[0] if name else ""
        if ch.isascii() and ch.isalpha():
            return ch.upper()
        if ch.isdigit():
            return "0-9"
        return "其他"
    return "其他"


def capability_group_by(
    root: str,
    by: str = "type",
    granularity: str = "month",
    recursive: bool = False,
    dry_run: bool = False,
    log_dir: str = "~/.archive_assistant/log",
    trash_dir: str = "~/.archive_assistant/trash",
) -> Dict[str, Any]:
    """把目录下文件按确定性规则分组到子文件夹（无需 VLM，快且可控）。

    by: "type"(大类) | "ext"(扩展名) | "date"(日期) | "initial"(首字母)
    granularity: date 模式的粒度 year/month/day
    """
    root = expand(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}
    if by not in ("type", "ext", "date", "initial"):
        return {"error": f"不支持的分组方式: {by}（支持 type/ext/date/initial）"}

    assets = _scan_files(root, recursive=recursive)
    assignments = [(a.path, _group_label(a, by, granularity)) for a in assets]
    result = _run_move_plan(root, assignments, dry_run, log_dir, trash_dir)
    result["by"] = by
    return result


# ============================================================
# 能力 2：classify_rules — 关键词规则归类
# ============================================================

def capability_classify_rules(
    root: str,
    rules: List[Tuple[str, List[str]]],
    by_content: bool = False,
    unmatched_label: str = "未分类",
    keep_unmatched: bool = False,
    recursive: bool = False,
    dry_run: bool = False,
    log_dir: str = "~/.archive_assistant/log",
    trash_dir: str = "~/.archive_assistant/trash",
) -> Dict[str, Any]:
    """按用户给定的「文件夹: 关键词」规则把文件归类（零成本、确定性）。

    rules: [(文件夹名, [关键词...]), ...]，命中任一关键词即归入该文件夹（按顺序取第一个命中）。
    by_content: True 时同时读取 PDF/DOCX/txt 正文参与匹配（更准但更慢）。
    keep_unmatched: True 时未命中的文件保持不动；False 时归入 unmatched_label。
    """
    root = expand(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}
    if not rules:
        return {"error": "未提供任何分类规则"}

    norm_rules = [(folder, [k.lower() for k in kws if k.strip()]) for folder, kws in rules]

    assets = _scan_files(root, recursive=recursive)
    assignments: List[Tuple[str, Optional[str]]] = []
    for a in assets:
        text = (filename_signal(a.path) or os.path.basename(a.path)).lower()
        if by_content:
            body = extract_body_text(a.path)
            if body:
                text += " " + body.lower()
        hit: Optional[str] = None
        for folder, kws in norm_rules:
            if any(kw in text for kw in kws):
                hit = folder
                break
        if hit is None:
            hit = None if keep_unmatched else unmatched_label
        assignments.append((a.path, hit))

    result = _run_move_plan(root, assignments, dry_run, log_dir, trash_dir)
    result["mode"] = "rules"
    result["rules"] = [{"folder": f, "keywords": kws} for f, kws in rules]
    return result


# ============================================================
# 能力 3：classify_into — 自定义类别 zero-shot 归类（VLM）
# ============================================================

def _digest(a) -> str:
    signal = filename_signal(a.path) or os.path.basename(a.path)
    body = (getattr(a, "ocr_text", "") or getattr(a, "body_text", "") or "").strip().replace("\n", " ")
    return f"{signal} {body}".strip()[:200]


def _vlm_assign_categories(vlm, items: List[dict], categories: List[str]) -> Dict[str, str]:
    """让 VLM 把每个文件归到给定类别之一（不确定输出 null）。"""
    import re as _re
    import json as _json
    sys_prompt = (
        "你是文件归档助手。请把每个文件归入下面给定的类别之一：\n"
        + "、".join(categories)
        + "\n\n规则：\n"
        "- 依据文件名和内容摘要判断；只在明确匹配时归类。\n"
        "- 无法明确归入任何给定类别时输出 null。\n"
        "- 必须从给定类别里选，不要自创新类别。\n"
        '- 输出格式：{"assignments":{"<id>":"<类别名或null>"}}\n'
        "只输出 JSON，禁止解释。"
    )
    result: Dict[str, str] = {}
    chunk = 60
    for start in range(0, len(items), chunk):
        block = items[start:start + chunk]
        lines = []
        for it in block:
            name = str(it.get("name", ""))[:80]
            snippet = str(it.get("snippet", "") or "").replace("\n", " ")[:160]
            lines.append(f"- id={it['id']} | {name} :: {snippet}")
        user = "文件清单：\n" + "\n".join(lines) + "\n\n请输出 assignments JSON。"
        try:
            raw = vlm._impl._generate(sys_prompt, user, [], max_new_tokens=1024)
            m = _re.search(r"\{[\s\S]*\}", raw or "")
            obj = _json.loads(m.group(0)) if m else {}
            assigns = obj.get("assignments", {}) if isinstance(obj, dict) else {}
            if isinstance(assigns, dict):
                for k, v in assigns.items():
                    if v and str(v) != "null" and v in categories:
                        result[str(k)] = str(v)
        except Exception as e:  # noqa: BLE001
            logger.warning("VLM assign_categories failed: %s", e)
    return result


_IMG_EXTS_CLF = {"png", "jpg", "jpeg", "webp", "gif", "bmp"}


def _vlm_classify_images_by_view(vlm, imgs, categories: List[str], workers: int = 1) -> Dict[str, str]:
    """让 VLM 逐张「看图」在给定类别中选一个（关键：图像信息不丢失）。

    返回 {asset_id: category}。无法判断的图不写入（留给上层回退）。

    为何不复用 _vlm_assign_categories：那是「批量文本清单」模式，只喂 caption 文本、
    不带图，4B 模型据此做关键词式误判（房子→风景、含 people 字样→People）。
    这里改为每张图把真实图片路径喂进 VLM，让它看着画面主体选类别，实测显著更准。
    """
    import re as _re  # noqa: F401
    sys_prompt = (
        "你是图片分类助手。请仔细观察图片的画面主体，把它归入下面给定的类别之一。\n"
        "类别：" + "、".join(categories) + "\n"
        "规则：\n"
        "- 只输出一个类别名，必须从给定类别中选，禁止自创类别、禁止任何解释。\n"
        "- 以画面主体为准：建筑/房屋/楼宇/桥梁=Architecture；自然风光/山水/田野/海滩/天空/"
        "雪景/树木花草=Landscape；以人物为主体=People；动物为主体=Animals；食物/菜肴=Food；"
        "含明显文字的文档/票据=Documents_Text；手机或电脑截图=Screenshots_Others。\n"
        "- 实在无法判断时输出 null。"
    )
    user = "这张图属于哪个类别？只输出类别名。"

    def _one(a):
        try:
            raw = vlm._impl._generate(sys_prompt, user, [a.path], max_new_tokens=16)  # noqa: SLF001
        except Exception as e:  # noqa: BLE001
            logger.warning("view-classify failed on %s: %s", a.path, e)
            return a.asset_id, None
        raw = (raw or "").strip()
        for c in categories:                      # 先精确匹配
            if raw.lower() == c.lower():
                return a.asset_id, c
        for c in categories:                      # 再子串匹配
            if c.lower() in raw.lower():
                return a.asset_id, c
        return a.asset_id, None

    result: Dict[str, str] = {}
    if workers > 1:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for f in as_completed([ex.submit(_one, a) for a in imgs]):
                k, v = f.result()
                if v:
                    result[k] = v
    else:
        for a in imgs:
            k, v = _one(a)
            if v:
                result[k] = v
    return result


def capability_classify_into(
    root: str,
    categories: List[str],
    vlm=None,
    by_content: bool = True,
    unmatched_label: str = "其他",
    keep_unmatched: bool = False,
    recursive: bool = False,
    dry_run: bool = False,
    log_dir: str = "~/.archive_assistant/log",
    trash_dir: str = "~/.archive_assistant/trash",
) -> Dict[str, Any]:
    """按用户给定的类别列表归类文件（如「土建,安装,市政」）。

    VLM 可用时读文件名+正文做语义归类；不可用时回退为「类别名作为关键词」的字面匹配。
    """
    root = expand(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}
    categories = [c.strip() for c in categories if c.strip()]
    if not categories:
        return {"error": "未提供任何类别"}

    assets = _scan_files(root, recursive=recursive)

    imgs = [a for a in assets if a.ext in _IMG_EXTS_CLF]
    docs = [a for a in assets if a.ext not in _IMG_EXTS_CLF]

    # 灌注正文/OCR 特征（仅文档需要；图片改走「看图直接分类」，不再做 caption，避免重复 VLM 调用）
    if by_content and docs:
        try:
            from .capabilities import capability_enrich
            capability_enrich(docs, vlm=vlm)
        except Exception as e:  # noqa: BLE001
            logger.warning("enrich failed, fallback to filename only: %s", e)

    use_vlm = vlm is not None and not getattr(vlm, "is_fallback", True) and hasattr(vlm, "_impl")
    assign_map: Dict[str, str] = {}
    if use_vlm:
        # 图片：逐张「看图」选类别（图像信息不丢失，实测远优于看 caption 文本）。
        if imgs:
            impl = getattr(vlm, "_impl", None)
            workers = (max(1, int(getattr(impl, "_concurrency", 1)))
                       if type(impl).__name__ == "OllamaVLM" else 1)
            assign_map.update(_vlm_classify_images_by_view(vlm, imgs, categories, workers))
        # 文档：无法「看图」，用文件名 + 正文文本做语义归类。
        if docs:
            items = [{"id": a.asset_id, "name": os.path.basename(a.path), "snippet": _digest(a)}
                     for a in docs]
            assign_map.update(_vlm_assign_categories(vlm, items, categories))

    assignments: List[Tuple[str, Optional[str]]] = []
    cat_lower = [(c, c.lower()) for c in categories]
    for a in assets:
        folder = assign_map.get(a.asset_id)
        if not folder:
            # 关键词回退：类别名出现在文件名/正文里即归入
            text = _digest(a).lower()
            for orig, low in cat_lower:
                if low and low in text:
                    folder = orig
                    break
        if not folder:
            folder = None if keep_unmatched else unmatched_label
        assignments.append((a.path, folder))

    result = _run_move_plan(root, assignments, dry_run, log_dir, trash_dir)
    result["mode"] = "categories"
    result["categories"] = categories
    result["vlm_used"] = use_vlm
    return result


# ============================================================
# 能力 4：flatten — 子目录文件平铺到一层
# ============================================================

def _remove_empty_dirs(root: str) -> List[str]:
    """自底向上删除 root 下的空目录（不删 root 本身）。返回已删除目录列表。

    自底向上（topdown=False）确保「父目录在子目录被删后才判定」，
    因此「只含空子目录」的多层空壳也能被整条清掉。仅删真正空目录，
    对权限/占用等异常静默跳过，绝不误删含文件的目录。
    """
    removed: List[str] = []
    root_abs = os.path.abspath(root)
    for dirpath, _dirnames, _filenames in os.walk(root, topdown=False):
        if os.path.abspath(dirpath) == root_abs:
            continue
        try:
            if not os.listdir(dirpath):
                os.rmdir(dirpath)
                removed.append(dirpath)
        except OSError:
            pass
    return removed


def capability_flatten(
    root: str,
    prefix_with_dir: bool = False,
    clean_empty_dirs: bool = False,
    dry_run: bool = False,
    log_dir: str = "~/.archive_assistant/log",
    trash_dir: str = "~/.archive_assistant/trash",
) -> Dict[str, Any]:
    """把 root 下各级子目录里的文件全部移动到 root 顶层（同名自动改名，可 rollback）。

    prefix_with_dir: True 时用「来源子目录名_原文件名」命名，避免不同目录同名文件混淆。
    clean_empty_dirs: True 时在平铺后顺手删掉 root 下变空的子目录壳子
                      （apply 才真删；dry-run 仅预报，且只删真正空目录）。
    """
    root = expand(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}

    assets = _scan_files(root, recursive=True)
    plan = ArchivePlan(root=root, mode="desktop", created_at=time.time())
    moves: List[str] = []
    src_dirs: set = set()
    for a in assets:
        if os.path.abspath(os.path.dirname(a.path)) == os.path.abspath(root):
            continue  # 已在顶层
        name = os.path.basename(a.path)
        if prefix_with_dir:
            parent = os.path.basename(os.path.dirname(a.path))
            name = f"{safe_folder_name(parent)}_{name}"
        dst = os.path.join(root, name)
        plan.actions.append(PlanAction(op="move", src=a.path, dst=dst, note="extra:flatten"))
        moves.append(name)
        src_dirs.add(os.path.abspath(os.path.dirname(a.path)))

    result: Dict[str, Any] = {
        "root": root,
        "total": len(moves),
        "files": moves[:50],
        "dry_run": dry_run,
        "log_path": None,
        "cleaned_dirs": [],
    }
    if not dry_run and plan.actions:
        result["log_path"] = apply_plan(plan.to_dict(), log_dir=log_dir, trash_dir=trash_dir)

    if clean_empty_dirs:
        if dry_run:
            # 预报：列出 root 下当前所有子目录（平铺后将全部变空）
            preview: List[str] = []
            root_abs = os.path.abspath(root)
            for dirpath, _dn, _fn in os.walk(root, topdown=False):
                if os.path.abspath(dirpath) != root_abs:
                    preview.append(dirpath)
            result["cleaned_dirs"] = preview
        else:
            result["cleaned_dirs"] = _remove_empty_dirs(root)
    return result


# ============================================================
# 能力 5：table_split — 表格按列值拆分
# ============================================================

def _unique_path(dst: str) -> str:
    if not os.path.exists(dst):
        return dst
    base, ext = os.path.splitext(dst)
    i = 1
    while os.path.exists(f"{base}-{i}{ext}"):
        i += 1
    return f"{base}-{i}{ext}"


def capability_table_split(
    src: str,
    by_col: str,
    out_dir: Optional[str] = None,
    fmt: str = "xlsx",
    sheet: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """把一个表格按某列的不同取值拆分成多个文件（每个取值一个文件）。

    安全：原文件只读；输出写到新目录（默认 <name>_split/），不覆盖原文件。
    """
    try:
        import pandas as pd
    except ImportError:
        return {"error": "缺少依赖 pandas，请先安装：pip install pandas openpyxl"}

    src = expand(src)
    if not os.path.isfile(src):
        return {"error": f"文件不存在: {src}"}
    if fmt not in ("xlsx", "csv"):
        return {"error": f"不支持的输出格式: {fmt}（支持 xlsx/csv）"}

    ext = os.path.splitext(src)[1].lower()
    try:
        if ext in (".xlsx", ".xlsm", ".xls"):
            df = pd.read_excel(src, sheet_name=sheet or 0, dtype=object)
        elif ext in (".csv", ".tsv", ".txt"):
            sep = "\t" if ext == ".tsv" else None
            df = pd.read_csv(src, sep=sep, engine="python", dtype=object)
        else:
            return {"error": f"不支持的表格格式: {ext}（支持 xlsx/xls/csv/tsv）"}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读取失败: {e}"}

    if by_col not in df.columns:
        return {"error": f"列不存在: {by_col}；可用列: {list(df.columns)}"}

    base, _ = os.path.splitext(src)
    if not out_dir:
        out_dir = f"{base}_split"
    out_dir = expand(out_dir)

    groups = list(df.groupby(by_col, dropna=False))
    parts = []
    for value, sub in groups:
        val_name = safe_folder_name("空值" if pd.isna(value) else str(value)) or "空值"
        parts.append({"value": str(value), "rows": int(sub.shape[0]),
                      "file": f"{val_name}.{fmt}"})

    result = {
        "src": src,
        "by_col": by_col,
        "out_dir": out_dir,
        "parts": parts,
        "files": len(parts),
        "dry_run": dry_run,
    }
    if dry_run:
        return result

    ensure_dir(out_dir)
    for value, sub in groups:
        val_name = safe_folder_name("空值" if pd.isna(value) else str(value)) or "空值"
        out_path = _unique_path(os.path.join(out_dir, f"{val_name}.{fmt}"))
        try:
            if fmt == "csv":
                sub.to_csv(out_path, index=False, encoding="utf-8-sig")
            else:
                sub.to_excel(out_path, index=False)
        except Exception as e:  # noqa: BLE001
            return {"error": f"写出失败 ({val_name}): {e}"}
    return result


# ============================================================
# 能力 6：convert_image — 图片格式转换 / 缩放 / 压缩
# ============================================================

_IMG_EXTS = {"jpg", "jpeg", "png", "bmp", "webp", "gif", "tiff", "tif"}


def capability_convert_image(
    root: str,
    to: str = "jpg",
    max_edge: Optional[int] = None,
    quality: int = 85,
    out_dir: Optional[str] = None,
    recursive: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """批量图片格式转换 / 缩放 / 压缩。

    to: 目标格式 jpg/png/webp/bmp/tiff
    max_edge: 限制最长边像素（等比缩放），None=不缩放
    quality: jpg/webp 的压缩质量(1-100)
    安全：原图只读；输出写到新目录（默认 _converted/），不覆盖原图。
    """
    try:
        from PIL import Image
    except ImportError:
        return {"error": "缺少依赖 Pillow，请先安装：pip install Pillow"}

    root = expand(root)
    if not os.path.isdir(root):
        return {"error": f"目录不存在: {root}"}
    to = to.lower().lstrip(".")
    if to not in {"jpg", "jpeg", "png", "webp", "bmp", "tiff", "tif"}:
        return {"error": f"不支持的目标格式: {to}"}

    assets = _scan_files(root, recursive=recursive)
    images = [a for a in assets if a.ext in _IMG_EXTS]
    if not images:
        return {"error": f"未找到可转换的图片（支持 {sorted(_IMG_EXTS)}）"}

    if not out_dir:
        out_dir = os.path.join(root, "_converted")
    out_dir = expand(out_dir)

    planned = [{"src": os.path.basename(a.path),
                "dst": f"{os.path.splitext(os.path.basename(a.path))[0]}.{to}"}
               for a in images]
    result: Dict[str, Any] = {
        "root": root, "to": to, "out_dir": out_dir,
        "total": len(images), "converted": [], "failed": [],
        "planned": planned[:50], "dry_run": dry_run,
    }
    if dry_run:
        return result

    ensure_dir(out_dir)
    save_fmt = "JPEG" if to in ("jpg", "jpeg") else to.upper()
    for a in images:
        try:
            im = Image.open(a.path)
            if max_edge and max_edge > 0:
                im.thumbnail((max_edge, max_edge))
            if save_fmt in ("JPEG", "BMP") and im.mode in ("RGBA", "P", "LA"):
                im = im.convert("RGB")
            stem = os.path.splitext(os.path.basename(a.path))[0]
            out_path = _unique_path(os.path.join(out_dir, f"{stem}.{to}"))
            params: Dict[str, Any] = {}
            if save_fmt in ("JPEG", "WEBP"):
                params["quality"] = int(quality)
            im.save(out_path, save_fmt, **params)
            result["converted"].append(os.path.basename(out_path))
        except Exception as e:  # noqa: BLE001
            logger.warning("convert failed %s: %s", a.path, e)
            result["failed"].append(os.path.basename(a.path))
    return result


# ============================================================
# 能力 7 / 8：pack / unpack — zip 打包 / 解压
# ============================================================

def capability_pack(
    inputs: List[str],
    out_path: Optional[str] = None,
    base_dir: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """把若干文件/目录（支持 glob）打包成一个 zip。

    安全：输出写到新 zip（同名自动改名），不覆盖已存在文件。
    """
    import zipfile

    files: List[str] = []
    for pat in inputs:
        for p in sorted(glob.glob(expand(pat))):
            if os.path.isfile(p):
                files.append(p)
            elif os.path.isdir(p):
                for dirpath, _dirs, fnames in os.walk(p):
                    for fn in fnames:
                        files.append(os.path.join(dirpath, fn))
    files = sorted(set(files))
    if not files:
        return {"error": f"未匹配到任何文件: {inputs}"}

    if not base_dir:
        base_dir = os.path.commonpath([os.path.dirname(f) for f in files]) \
            if len(files) > 1 else os.path.dirname(files[0])
    base_dir = expand(base_dir)

    if not out_path:
        first = expand(inputs[0].rstrip("/*"))
        stem = os.path.basename(first.rstrip(os.sep)) or "archive"
        out_path = os.path.join(os.path.dirname(first) or ".", f"{stem}.zip")
    out_path = _unique_path(expand(out_path))

    result = {
        "inputs": inputs, "out_path": out_path, "file_count": len(files),
        "base_dir": base_dir, "dry_run": dry_run,
    }
    if dry_run:
        return result

    ensure_dir(os.path.dirname(out_path) or ".")
    try:
        with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in files:
                try:
                    arc = os.path.relpath(f, base_dir)
                except ValueError:
                    arc = os.path.basename(f)
                zf.write(f, arcname=arc)
    except Exception as e:  # noqa: BLE001
        return {"error": f"打包失败: {e}"}
    return result


def capability_unpack(
    archive: str,
    out_dir: Optional[str] = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """解压一个 zip 到目录（含 zip-slip 路径穿越防护）。

    安全：拒绝解压到目标目录之外的成员；输出到新目录（默认与压缩包同名）。
    """
    import zipfile

    archive = expand(archive)
    if not os.path.isfile(archive):
        return {"error": f"文件不存在: {archive}"}
    if not zipfile.is_zipfile(archive):
        return {"error": f"不是有效的 zip 文件: {archive}（当前仅支持 .zip）"}

    if not out_dir:
        out_dir = os.path.splitext(archive)[0]
    out_dir = expand(out_dir)
    out_real = os.path.realpath(out_dir)

    try:
        with zipfile.ZipFile(archive) as zf:
            members = zf.namelist()
            # zip-slip 防护：逐个校验目标路径不逃逸出 out_dir
            unsafe = []
            for m in members:
                target = os.path.realpath(os.path.join(out_dir, m))
                if target != out_real and not target.startswith(out_real + os.sep):
                    unsafe.append(m)
            if unsafe:
                return {"error": f"检测到不安全的压缩包成员（路径穿越），已拒绝解压: {unsafe[:5]}"}

            result = {
                "archive": archive, "out_dir": out_dir,
                "member_count": len(members), "members": members[:50],
                "dry_run": dry_run,
            }
            if dry_run:
                return result
            ensure_dir(out_dir)
            zf.extractall(out_dir)
    except Exception as e:  # noqa: BLE001
        return {"error": f"解压失败: {e}"}
    return result
