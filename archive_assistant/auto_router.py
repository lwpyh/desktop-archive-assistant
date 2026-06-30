"""意图路由器 — 把用户的自然语言请求路由到正确的 CLI 能力。

这是"弱模型友好"的核心设计：交互模型（Qwen3.5 等）无需理解 30 个能力、
无需选择 flag、无需拼命令——只需把用户原话传给 `auto` 命令，
本模块用确定性关键词匹配完成意图识别，并返回/执行对应的命令。

路由策略：
  1. 规则按优先级排序（更具体的规则先匹配，如"照片去重"先于"照片归档"）
  2. 每条规则含 keywords 列表，命中即路由
  3. 多条命中时取 keywords 命中数最多的
  4. 无命中 → 返回 organize 作为默认（最通用）
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

from .utils import default_desktop_dir, logger, platform_name


# ---------- 意图规则表 ----------
# 每条规则：keywords(命中关键词), intent, subcommand, desc, workflow(多步流程),
# needs_root(是否需要目录参数), extra_args(固定参数)

_RULES: List[Dict[str, Any]] = [
    # ---- 整理桌面（最高频，默认兜底）----
    {
        "intent": "organize_desktop",
        "keywords": ["整理桌面", "归档桌面", "桌面整理", "整理文件", "文件分类",
                     "清理桌面", "桌面太乱", "整理一下", "归档", "桌面文件"],
        "subcommand": "organize",
        "desc": "全流程整理桌面：扫描→归类→生成计划→执行→排列图标",
        "workflow": ["organize", "apply", "sort"],
        "needs_root": True,
    },
    # ---- 照片按日期归档 ----
    {
        "intent": "archive_by_date",
        "keywords": ["照片归档", "按日期归档", "按时间归档", "按拍摄日期", "照片按年",
                     "照片按月", "按年份整理", "照片日期", "按日期整理照片", "照片按日期"],
        "subcommand": "archive-by-date",
        "desc": "按 EXIF 拍摄日期归档照片到 年/月/日 三级目录",
        "workflow": ["archive-by-date"],
        "needs_root": True,
    },
    # ---- 照片去重 ----
    {
        "intent": "dedupe_photos",
        "keywords": ["照片去重", "图片去重", "照片重复", "删重复照片", "去重照片",
                     "图片重复", "重复照片", "照片删重", "清理重复照片"],
        "subcommand": "dedupe",
        "desc": "照片感知哈希(pHash)去重，视觉相似也识别，重复移入回收站",
        "workflow": ["dedupe --method phash"],
        "needs_root": True,
    },
    # ---- 文件去重（通用哈希）----
    {
        "intent": "dedupe_files",
        "keywords": ["文件去重", "去重文件", "重复文件", "删重复文件", "文件删重"],
        "subcommand": "dedupe",
        "desc": "文件哈希去重，完全相同才判定重复",
        "workflow": ["dedupe --method hash"],
        "needs_root": True,
    },
    # ---- 视频标题重命名 ----
    {
        "intent": "video_rename",
        "keywords": ["视频重命名", "视频标题", "视频改名", "视频名字", "重命名视频",
                     "视频标题重命名", "改视频名", "视频文件名"],
        "subcommand": "video-rename-title",
        "desc": "视频标题 AI 重命名（删#标签/括号/审核尾巴，15-25字）",
        "workflow": ["video-rename-title"],
        "needs_root": True,
    },
    # ---- 图片按 OCR 文字改名 ----
    {
        "intent": "image_rename_ocr",
        "keywords": ["图片按内容改名", "图片按文字改名", "图片ocr改名", "截图改名",
                     "按图片文字改名", "图片识别改名", "照片按内容改名", "图片改名",
                     "按内容改名图片", "图片按ocr改名"],
        "subcommand": "image-rename-by-ocr",
        "desc": "图片按 VLM OCR 识别的文字重命名（走执行器可回滚，不手搓 shell）",
        "workflow": ["image-rename-by-ocr"],
        "needs_root": True,
    },
    # ---- 图片转PPT ----
    {
        "intent": "to_ppt",
        "keywords": ["图片转ppt", "照片转ppt", "转ppt", "做ppt", "图片ppt",
                     "照片ppt", "幻灯片", "图片做ppt", "照片做ppt"],
        "subcommand": "to-ppt",
        "desc": "图片批量转 PPT（每张一页居中铺满）",
        "workflow": ["to-ppt"],
        "needs_root": True,
    },
    # ---- 多图拼接 ----
    {
        "intent": "collage",
        "keywords": ["拼接", "拼图", "多图拼接", "图片拼接", "照片拼接", "拼一张"],
        "subcommand": "collage",
        "desc": "多图拼接成一张（A4/网格）",
        "workflow": ["collage"],
        "needs_root": True,
    },
    # ---- 图音合成视频 ----
    {
        "intent": "video_compose",
        "keywords": ["图音合成", "图片配音乐", "配音乐做视频", "图片做视频", "图配乐",
                     "图片加音乐", "做视频", "合成视频", "图片视频"],
        "subcommand": "video-compose",
        "desc": "图片+音频合成视频（FFmpeg，9:16/16:9）",
        "workflow": ["video-compose"],
        "needs_root": False,
    },
    # ---- 视频分发 ----
    {
        "intent": "video_distribute",
        "keywords": ["视频分发", "分发视频", "视频分文件夹", "视频分到", "视频分组"],
        "subcommand": "video-distribute",
        "desc": "视频按数量分发到多个子文件夹",
        "workflow": ["video-distribute"],
        "needs_root": True,
    },
    # ---- 照片内容提取 ----
    {
        "intent": "extract",
        "keywords": ["照片内容", "提取照片", "识别照片", "照片文字", "图片文字",
                     "图片内容", "照片识别", "图片识别", "提取文字"],
        "subcommand": "extract",
        "desc": "VLM 识别照片内容/文字 → 结构化 txt/csv",
        "workflow": ["extract"],
        "needs_root": True,
    },
    # ---- 图片裁剪 ----
    {
        "intent": "crop",
        "keywords": ["裁剪", "裁切", "剪裁", "图片裁剪", "照片裁剪", "裁图"],
        "subcommand": "crop",
        "desc": "图片裁剪（按尺寸/比例）",
        "workflow": ["crop"],
        "needs_root": True,
    },
    # ---- 排列桌面图标 ----
    {
        "intent": "sort_desktop",
        "keywords": ["排列桌面", "桌面图标", "排列图标", "图标排列", "桌面排序",
                     "排列桌面图标", "整理图标"],
        "subcommand": "sort",
        "desc": "排列桌面图标（消除空位/按类型排序）",
        "workflow": ["sort"],
        "needs_root": True,
    },
    # ---- 查找文件 ----
    {
        "intent": "find",
        "keywords": ["查找文件", "找文件", "搜索文件", "文件在哪", "找一下"],
        "subcommand": "find",
        "desc": "查找文件（按名称/类型/时间/大小）",
        "workflow": ["find"],
        "needs_root": True,
    },
    # ---- 清理临时文件 ----
    {
        "intent": "clean",
        "keywords": ["清理临时", "清空临时", "删临时文件", "清理垃圾", "临时文件",
                     "空文件夹", "空目录"],
        "subcommand": "clean",
        "desc": "清理临时文件/空目录",
        "workflow": ["clean --temp --empty-dirs"],
        "needs_root": True,
    },
    # ---- 定时整理 ----
    {
        "intent": "schedule",
        "keywords": ["定时整理", "定期整理", "自动整理", "每天整理", "定时归档",
                     "定时任务", "定期归档"],
        "subcommand": "schedule",
        "desc": "定时整理（cron 集成）",
        "workflow": ["schedule"],
        "needs_root": True,
    },
    # ---- 增量同步 ----
    {
        "intent": "sync",
        "keywords": ["同步", "增量同步", "备份同步", "目录同步"],
        "subcommand": "sync",
        "desc": "增量同步（目录间，只增不减）",
        "workflow": ["sync"],
        "needs_root": True,
    },
    # ---- 回滚 ----
    {
        "intent": "rollback",
        "keywords": ["回滚", "撤销", "后悔", "还原", "撤回", "undo"],
        "subcommand": "rollback",
        "desc": "回撤上一次操作",
        "workflow": ["rollback --last"],
        "needs_root": False,
    },
    # ---- 表格清洗 ----
    {
        "intent": "table_clean",
        "keywords": ["表格清洗", "表格去重", "列筛选", "删列", "空值清洗", "表格清理"],
        "subcommand": "table-clean",
        "desc": "表格列筛选/去重/清洗",
        "workflow": ["table-clean"],
        "needs_root": False,
    },
    # ---- 表格汇总 ----
    {
        "intent": "table_merge",
        "keywords": ["汇总成表", "合并表格", "多个文件汇总", "表格汇总", "汇总表格"],
        "subcommand": "table-merge",
        "desc": "多个 txt/csv/xlsx 汇总成一个表",
        "workflow": ["table-merge"],
        "needs_root": True,
    },
    # ---- 整理成Word ----
    {
        "intent": "docx_compose",
        "keywords": ["整理成word", "生成word", "做成word", "写word", "docx",
                     "整理成文档", "生成文档"],
        "subcommand": "docx-compose",
        "desc": "清单/资料整理成 Word 文档",
        "workflow": ["docx-compose"],
        "needs_root": False,
    },
    # ---- PDF操作 ----
    {
        "intent": "pdf_merge",
        "keywords": ["pdf合并", "合并pdf", "pdf合", "PDF合并"],
        "subcommand": "pdf-ops",
        "desc": "PDF 合并",
        "workflow": ["pdf-ops merge"],
        "needs_root": False,
    },
    {
        "intent": "pdf_split",
        "keywords": ["pdf拆分", "拆分pdf", "pdf拆", "PDF拆分"],
        "subcommand": "pdf-ops",
        "desc": "PDF 拆分",
        "workflow": ["pdf-ops split"],
        "needs_root": False,
    },
    {
        "intent": "pdf_extract",
        "keywords": ["pdf提取", "提取pdf", "pdf文字", "pdf文本", "pdf转文本"],
        "subcommand": "pdf-ops",
        "desc": "PDF 提取文本",
        "workflow": ["pdf-ops extract"],
        "needs_root": False,
    },
    # ---- 巡检 ----
    {
        "intent": "inspect",
        "keywords": ["巡检", "最近新增", "新文件", "检查新增"],
        "subcommand": "inspect",
        "desc": "文件巡检（扫描最近新增文件）",
        "workflow": ["inspect"],
        "needs_root": True,
    },
    # ---- 扫描 ----
    {
        "intent": "scan",
        "keywords": ["扫描", "看看桌面", "看看有什么", "列文件", "查看文件"],
        "subcommand": "scan",
        "desc": "扫描目录列出文件",
        "workflow": ["scan"],
        "needs_root": True,
    },
    # ---- 备份 ----
    {
        "intent": "backup",
        "keywords": ["备份", "backup"],
        "subcommand": "backup",
        "desc": "整理前备份",
        "workflow": ["backup"],
        "needs_root": True,
    },
    # ---- 按确定性规则分组（类型/扩展名/日期/首字母）----
    {
        "intent": "group_by",
        "keywords": ["按类型分类", "按类型整理", "按扩展名", "按格式分类", "按文件类型",
                     "按日期分组", "按月份归档", "按日期分文件夹", "分门别类", "按首字母",
                     "按类型", "按格式", "按种类", "按月份", "按年份"],
        # 组合：含"按X"维度词 且 含"分/归/整理"动作 → 即便中间插了字也能命中
        "any_all": [["按类型", "按格式", "按扩展名", "按文件类型", "按种类",
                     "按日期", "按月份", "按年份", "按首字母", "按时间"],
                    ["分", "归", "整理", "放", "归类", "归档"]],
        "subcommand": "group-by",
        "desc": "按 类型/扩展名/日期/首字母 把文件分组到子文件夹（确定性，无需 VLM，可回滚）",
        "workflow": ["group-by"],
        "needs_root": True,
    },
    # ---- 按用户给定类别归类（VLM zero-shot）----
    {
        "intent": "classify_into",
        "keywords": ["按内容分类", "按类别分类", "按业务分类", "按项目分类", "按主题分类",
                     "按工程类型", "按客户分类", "按部门分类", "识别内容分类"],
        "subcommand": "classify-into",
        "desc": "按给定类别让 VLM 读文件名+正文 zero-shot 归类（如招标文件按工程类型分类）",
        "workflow": ["classify-into"],
        "needs_root": True,
    },
    # ---- 按关键词规则归类 ----
    {
        "intent": "classify_rules",
        "keywords": ["按关键词分类", "关键词归类", "按规则分类", "按名称归类", "按关键字分类",
                     "分开放", "分别放", "分开归类", "分开存放", "分别归类"],
        "subcommand": "classify-rules",
        "desc": "按用户给定的关键词规则把文件归入指定文件夹（零成本、确定性，可回滚）",
        "workflow": ["classify-rules"],
        "needs_root": True,
    },
    # ---- 子目录文件平铺 ----
    {
        "intent": "flatten",
        "keywords": ["平铺", "摊平", "提取到一层", "展开子目录", "子目录文件提取", "拉平目录"],
        "subcommand": "flatten",
        "desc": "把多层子目录里的文件平铺到顶层（同名自动改名，可回滚）",
        "workflow": ["flatten"],
        "needs_root": True,
    },
    # ---- 表格按列拆分 ----
    {
        "intent": "table_split",
        "keywords": ["表格拆分", "拆分表格", "按列拆分", "表格按列", "拆表", "拆成多个表",
                     "按列拆表", "按列拆开", "拆成多个文件"],
        # 组合：含"表格/excel/表"且含"拆/分成"，专治"表格按【部门】拆开"
        "any_all": [["表格", "excel", "表", "sheet", "xlsx", "csv", "数据表"],
                    ["拆", "拆分", "拆开", "分成", "分割", "按列"]],
        "subcommand": "table-split",
        "desc": "表格按某列的取值拆分成多个文件（xlsx/csv，原文件只读）",
        "workflow": ["table-split"],
        "needs_root": False,
    },
    # ---- 图片格式转换 ----
    {
        "intent": "convert_image",
        "keywords": ["图片格式转换", "图片转格式", "转jpg", "转png", "转webp", "图片压缩",
                     "批量转格式", "照片格式转换", "压缩图片", "转成jpg", "转成png",
                     "转成webp", "转为jpg", "图片转换"],
        # 组合：含图片/格式词 且 含转换/压缩动作，专治"图片批量转【成】jpg"
        "any_all": [["图片", "照片", "图像", "png", "jpg", "jpeg", "webp", "bmp", "heic"],
                    ["转", "转成", "转为", "转换", "压缩", "缩放", "改成"]],
        "subcommand": "convert",
        "desc": "图片批量格式转换/缩放/压缩（jpg/png/webp，原图只读）",
        "workflow": ["convert"],
        "needs_root": True,
    },
    # ---- 打包 zip ----
    {
        "intent": "pack",
        "keywords": ["打包", "压缩成zip", "打成压缩包", "压缩文件夹", "打包成zip", "压缩成压缩包"],
        "subcommand": "pack",
        "desc": "把文件/目录打包成 zip",
        "workflow": ["pack"],
        "needs_root": False,
    },
    # ---- 解压 zip ----
    {
        "intent": "unpack",
        "keywords": ["解压", "解压缩", "unzip", "提取压缩包", "解开压缩包"],
        "subcommand": "unpack",
        "desc": "解压 zip（含 zip-slip 路径穿越防护）",
        "workflow": ["unpack"],
        "needs_root": False,
    },
]


def _match_intent(query: str) -> Tuple[Dict[str, Any], int]:
    """关键词匹配：返回 (最佳规则, 命中数)。无命中返回默认 organize 规则。

    两种命中方式（分数累加）：
    1. keywords 子串命中：短词 +1，长词(>=4) +2。
    2. any_all 分组组合命中：每组是一个"或"词表，所有组都至少命中一个词时 +3。
       专治"动词和宾语之间插了变量"的口语，如"表格按【部门】拆开"——
       靠 [["表格"...],["拆"...]] 两组同时命中即可路由，不依赖连续子串。
    """
    q = query.lower().strip()
    best = _RULES[0]  # 默认：organize_desktop
    best_score = 0
    for rule in _RULES:
        score = 0
        for kw in rule["keywords"]:
            if kw.lower() in q:
                score += 1
                # 更长/更具体的关键词权重更高
                if len(kw) >= 4:
                    score += 1
        groups = rule.get("any_all")
        if groups and all(any(w.lower() in q for w in grp) for grp in groups):
            score += 3
        if score > best_score:
            best_score = score
            best = rule
    return best, best_score


def route(query: str, root: Optional[str] = None) -> Dict[str, Any]:
    """路由用户请求 → 返回意图+建议命令。

    返回结构：
    {
        "intent": "archive_by_date",
        "description": "按 EXIF 拍摄日期归档...",
        "subcommand": "archive-by-date",
        "root": "/root",            # 解析后的目录
        "workflow": ["archive-by-date"],   # 多步流程的 CLI 子命令列表
        "dry_run_cmd": "python -m archive_assistant.cli.main archive-by-date /root",
        "apply_cmd":  "python -m archive_assistant.cli.main archive-by-date /root",
        "confidence": 3,           # 关键词命中数
        "needs_root": True,
    }
    """
    rule, score = _match_intent(query)

    # 解析目录
    if rule.get("needs_root", True):
        resolved_root = root or default_desktop_dir()
    else:
        resolved_root = root or ""

    # 构造建议命令
    base = "python -m archive_assistant.cli.main"
    wf = rule.get("workflow", [rule["subcommand"]])

    def _build_cmd(steps: List[str], dry_run: bool = False) -> str:
        parts = []
        for step in steps:
            parts_cmd = [base, step]
            if resolved_root:
                parts_cmd.append(f'"{resolved_root}"')
            if dry_run and step not in ("scan", "inspect", "find", "sort",
                                     "rollback", "schedule"):
                parts_cmd.append("--dry-run")
            parts.append(" ".join(parts_cmd))
        return " && ".join(parts)

    dry_cmd = _build_cmd(wf, dry_run=True)
    apply_cmd = _build_cmd(wf, dry_run=False)

    return {
        "intent": rule["intent"],
        "description": rule["desc"],
        "subcommand": rule["subcommand"],
        "root": resolved_root,
        "workflow": wf,
        "dry_run_cmd": dry_cmd,
        "apply_cmd": apply_cmd,
        "confidence": score,
        "needs_root": rule.get("needs_root", True),
    }


def list_intents() -> List[Dict[str, Any]]:
    """列出所有可识别的意图（供调试/展示用）。"""
    seen = set()
    result = []
    for r in _RULES:
        if r["intent"] in seen:
            continue
        seen.add(r["intent"])
        result.append({
            "intent": r["intent"],
            "keywords": r["keywords"][:5],  # 只展示前5个关键词
            "subcommand": r["subcommand"],
            "description": r["desc"],
        })
    return result


def format_route_result(r: Dict[str, Any]) -> str:
    """把路由结果格式化成模型友好的文本（弱模型容易解析）。"""
    lines = [
        f"意图: {r['intent']}",
        f"说明: {r['description']}",
        f"目录: {r['root'] or '(无需目录)'}",
        f"置信度: {r['confidence']} (关键词命中数)",
        "",
        "执行命令（默认即真正执行）:",
        f"  {r['apply_cmd']}",
        "",
        "预览命令（只看不改，可选）:",
        f"  {r['dry_run_cmd']}",
        "",
        "安全提示: 默认直接执行；加 --dry-run 只预览不改。"
        "删除一律移入回收站，可用 rollback 撤销。",
    ]
    return "\n".join(lines)
