# Desktop Archive Assistant — 能力详细参考

> 本文档是 `SKILL.md` 的详细补充，供强模型精细调参或人类查阅。
> 弱模型（Qwen3.5 等）只需使用 `auto` 命令即可，无需阅读本文档。

## 能力总览

本 skill 由 **38 个独立能力** 组成（17 通用 + 9 照片/视频 + 4 Office + 8 扩展整理），按需调用、灵活组合。

### 通用文件整理能力（17）

| 能力 | CLI 子命令 | 说明 |
|------|-----------|------|
| **scan** | `scan` | 扫描目录，列出文件（自动跳过快捷方式/系统文件） |
| **enrich** | （organize 内部） | 文件特征灌注（正文/OCR/VLM caption） |
| **classify** | `classify` | 主题归类（扩展名路由+不常用+已有文件夹匹配+VLM主题） |
| **rename** | `rename` | 批量重命名（模板/时间排序/序号/日期） |
| **find** | `find` | 查找文件（按名称/类型/时间/大小） |
| **dedupe** | `dedupe` | 文件去重（哈希/文件名模式/pHash，移入回收站不删除） |
| **sync** | `sync` | 增量同步（目录间，只增不减） |
| **inspect** | `inspect` | 文件巡检（扫描最近新增文件，列清单） |
| **move** | `move` | 批量移动（按通配符匹配移动到指定目录） |
| **clean** | `clean` | 清理（临时文件/空文件夹/被占用文件检测） |
| **backup** | `backup` | 整理前完整备份 |
| **plan** | `organize` / `classify` | 生成归档计划（dry-run JSON） |
| **apply** | `apply` | 执行计划（mkdir/move/trash，绝不删除） |
| **rollback** | `rollback` | 回撤操作 |
| **sort_desktop** | `sort` | 桌面图标排列（消除空位/按类型排序） |
| **report** | `report` | 输出 Markdown 整理报告 |
| **schedule** | `schedule` | 定时整理（cron 集成） |

### 照片/视频/像册专项能力（9）

| 能力 | CLI 子命令 | 说明 |
|------|-----------|------|
| **archive_by_date** | `archive-by-date` | 按 EXIF 拍摄日期归档到 `年/月/日` 三级目录（无 EXIF 用 mtime，检测拍摄日与文件日不符） |
| **dedupe_photos** | `dedupe --method phash` | 照片感知哈希(pHash)去重，视觉相似（含缩放/微改）也能识别，重复移入回收站 |
| **extract** | `extract` | VLM 识别照片内容/文字（caption/OCR）→ 结构化 txt/csv |
| **crop** | `crop` | 图片裁剪（按尺寸/比例，可选内容居中检测） |
| **to_ppt** | `to-ppt` | 图片批量转 PPT（16:9/4:3，居中铺满） |
| **collage** | `collage` | 多图拼接成一张（A4/自定义网格） |
| **video_rename_title** | `video-rename-title` | 视频 AI 重命名：**内置 ffmpeg 沿时间轴抽 6 帧 → VLM 看画面起名**（贴合真实内容、非标题党）；抽帧/看图失败时回退按原名文本清洗（删#标签/括号/审核尾巴、15-25字）；`--apply` 走带日志执行器，可 `rollback --last` 一键撤销 |
| **video_compose** | `video-compose` | 图片 + 音频 合成视频（FFmpeg，9:16/16:9，scale+pad+loudnorm） |
| **video_distribute** | `video-distribute` | 视频按规则均匀/按数量分发到多个子文件夹 |

### Office 文档/表格专项能力（4）

| 能力 | CLI 子命令 | 说明 |
|------|-----------|------|
| **table_clean** | `table-clean` | 表格列筛选/删列/按列去重/空值清洗/排序（xlsx/csv，原文件只读写到新文件） |
| **table_merge** | `table-merge` | 多个 txt/csv/xlsx 汇总成一个 xlsx（可加来源列、按时间/名称排序） |
| **docx_compose** | `docx-compose` | 清单/资料整理成 Word（标题+段落/列表；或用 docxtpl 模板填充） |
| **pdf_ops** | `pdf-ops` | PDF 合并/拆分/抽取文本（纯 pypdf，无需 Office，原 PDF 只读） |

### 扩展整理能力（8）

| 能力 | CLI 子命令 | 说明 |
|------|-----------|------|
| **group_by** | `group-by` | 按 类型/扩展名/日期/首字母 把文件分组到子文件夹（`--by type/ext/date/initial`，date 可选 `--granularity year/month/day`，走带日志执行器可回滚） |
| **classify_into** | `classify-into` | 按用户给定的自定义类别归类（`--categories "土建,安装,市政"`，VLM 读文件名+正文判类，VLM 不可用回退关键词匹配；`--no-content` 只看文件名提速） |
| **classify_rules** | `classify-rules` | 按关键词规则归类（`--rules "发票:发票,invoice;合同:合同,协议"`，`--by-content` 同时读正文匹配，确定性、无需 VLM） |
| **flatten** | `flatten` | 把多层子目录的文件平铺到顶层（`--prefix-with-dir` 用来源目录名做前缀防同名，走带日志执行器可回滚） |
| **table_split** | `table-split` | 表格按某列取值拆分成多个文件（`--by-col 部门 --format xlsx/csv`，原表只读、写到新目录） |
| **convert_image** | `convert` | 图片批量格式转换/缩放/压缩（`--to jpg/png/webp --max-edge 1920 --quality 85`，RGBA→RGB 处理，写新目录不覆盖原图） |
| **pack** | `pack` | 把文件/目录打包成 zip（`pack a b dir --out out.zip --base-dir <基准>`） |
| **unpack** | `unpack` | 解压 zip（含 zip-slip 路径穿越防护，`--out-dir <目标>` 默认与压缩包同名） |

## 核心架构（VLM-first）

```
scan: 目录扫描（跳过快捷方式/系统文件）→ Asset 列表
  ↓
enrich: 抽摘要(正文/文件名; 图片走 VLM caption/OCR)
  ↓
classify:
  1. 扩展名路由（视频/音频/安装包/压缩包 → 固定桶）
  2. 不常用文件检测（atime > 60天 → "不常用文件"）
  3. AI 语义归入已有文件夹（关键词匹配 + VLM 语义匹配）
  4. VLM 主题归类（剩余文件整批送 VLM）
  5. 无 VLM 回退（文件名关键词分组）
  ↓
plan: Archive Plan(JSON) → 可选 backup → Executor(dry-run/apply/rollback)
```

## 详细命令参考

### 全流程整理

```bash
# 1. 扫描查看文件
python -m archive_assistant.cli.main scan ~/Desktop

# 2. 整理（dry-run 先看计划）
python -m archive_assistant.cli.main organize ~/Desktop --out plan.json

# 3. 执行
python -m archive_assistant.cli.main apply --plan plan.json

# 4. 后悔了
python -m archive_assistant.cli.main rollback --last
```

### 通用能力

```bash
# 批量重命名（按时间排序，模板 IMG_序号）
python -m archive_assistant.cli.main rename ~/Desktop --template "IMG_{seq}" --sort-by time
python -m archive_assistant.cli.main rename ~/Photos --template "{date}_{name}" --ext jpg --apply

# 查找文件
python -m archive_assistant.cli.main find ~/Desktop --name "*.xlsx"
python -m archive_assistant.cli.main find ~/Desktop --ext xlsx,pdf --modified-since 24

# 文件去重
python -m archive_assistant.cli.main dedupe ~/Desktop --method hash
python -m archive_assistant.cli.main dedupe ~/Photos --method filename --apply

# 增量同步
python -m archive_assistant.cli.main sync ~/Desktop /backup/desktop --apply

# 巡检
python -m archive_assistant.cli.main inspect ~/Desktop --since 24

# 批量移动
python -m archive_assistant.cli.main move ~/Desktop --match "*.lnk" --to ~/Desktop/快捷方式 --apply

# 清理
python -m archive_assistant.cli.main clean ~/Desktop --temp --empty-dirs --apply

# 备份
python -m archive_assistant.cli.main backup ~/Desktop --backup-dir /tmp/desktop_backup

# 排列桌面图标
python -m archive_assistant.cli.main sort ~/Desktop
python -m archive_assistant.cli.main sort ~/Desktop --by ItemType

# 生成报告
python -m archive_assistant.cli.main report --plan plan.json --out report.md

# 定时整理
python -m archive_assistant.cli.main schedule ~/Desktop --cron "0 18 * * *"
python -m archive_assistant.cli.main schedule --list
python -m archive_assistant.cli.main schedule --remove
```

### 照片/视频/像册整理

```bash
# 按 EXIF 拍摄日期归档到 年/月/日 三级目录
python -m archive_assistant.cli.main archive-by-date ~/Photos --apply
python -m archive_assistant.cli.main archive-by-date ~/Photos --granularity year

# 照片感知哈希去重
python -m archive_assistant.cli.main dedupe ~/Photos --method phash
python -m archive_assistant.cli.main dedupe ~/Photos --method phash --phash-threshold 5 --apply

# VLM 识别照片内容/文字
python -m archive_assistant.cli.main extract ~/Photos --out content.csv
# 大批量提速：pHash 预聚类（同簇共用描述）+ 批量推理 + OCR 并发 + 结果缓存（默认开启）
python -m archive_assistant.cli.main extract ~/Photos --out content.csv \
  --cluster-threshold 5 --batch-size 8 --workers 4
python -m archive_assistant.cli.main extract ~/Photos --cluster-threshold -1  # 关闭预聚类（每图独立调用）
python -m archive_assistant.cli.main extract ~/Photos --no-cache              # 禁用结果缓存

# 图片裁剪
python -m archive_assistant.cli.main crop ~/Photos --size 1080x1920 --apply
python -m archive_assistant.cli.main crop ~/Photos --ratio 9:16 --apply

# 图片批量转 PPT
python -m archive_assistant.cli.main to-ppt ~/Photos --out album.pptx

# 多图拼接
python -m archive_assistant.cli.main collage ~/Photos --out collage.jpg --cols 3

# 视频 AI 重命名（默认抽 6 帧看画面起名；先 dry-run 预览）
python -m archive_assistant.cli.main video-rename-title ~/Videos
python -m archive_assistant.cli.main video-rename-title ~/Videos --apply   # 真改名，写日志可回滚
python -m archive_assistant.cli.main rollback --last                       # 改坏了一键还原

# 图片 + 音频 合成视频
python -m archive_assistant.cli.main video-compose --image cover.jpg --audio bgm.mp3 --out out.mp4 --ratio 9:16

# 视频分发到多个子文件夹
python -m archive_assistant.cli.main video-distribute ~/Videos --folders 5 --apply
```

### Office 文档/表格整理

```bash
# 表格清洗
python -m archive_assistant.cli.main table-clean data.xlsx \
  --keep-cols "公司名,法人,手机号" --dedup-by "手机号" --dropna-cols "手机号" --apply

# 多文件汇总成表
python -m archive_assistant.cli.main table-merge "D:/files" --pattern "*.txt" --sort-by time --out result.xlsx --apply

# 整理成 Word
python -m archive_assistant.cli.main docx-compose --out 论文清单.docx --title "论文清单" --from-files "papers/*.pdf" --apply
python -m archive_assistant.cli.main docx-compose --out 报告.docx --template 模板.docx --context '{"name":"张三"}' --apply

# PDF 合并 / 拆分 / 提取文本
python -m archive_assistant.cli.main pdf-ops merge "a.pdf" "b.pdf" --out merged.pdf --apply
python -m archive_assistant.cli.main pdf-ops split big.pdf --pages "1-3,5" --out part.pdf --apply
python -m archive_assistant.cli.main pdf-ops extract doc.pdf --out doc.txt --apply
```

### 扩展整理能力

```bash
# 按类型/扩展名/日期/首字母分组到子文件夹（默认 dry-run，加 --apply 真整理）
python -m archive_assistant.cli.main group-by ~/Downloads --by type --apply
python -m archive_assistant.cli.main group-by ~/Downloads --by date --granularity month --apply
python -m archive_assistant.cli.main group-by ~/Downloads --by ext

# 按自定义类别归类（VLM 读文件名+正文判类，VLM 不可用回退关键词）
python -m archive_assistant.cli.main classify-into ~/项目资料 --categories "土建,安装,市政" --apply
python -m archive_assistant.cli.main classify-into ~/项目资料 --categories "土建,安装,市政" --no-content --keep-unmatched

# 按关键词规则归类（确定性，无需 VLM）
python -m archive_assistant.cli.main classify-rules ~/Downloads --rules "发票:发票,invoice;合同:合同,协议" --by-content --apply

# 把多层子目录的文件平铺到顶层
python -m archive_assistant.cli.main flatten ~/导出 --prefix-with-dir --apply

# 表格按某列取值拆分成多个文件
python -m archive_assistant.cli.main table-split data.xlsx --by-col 部门 --format xlsx --apply

# 图片批量格式转换/缩放/压缩
python -m archive_assistant.cli.main convert ~/Photos --to jpg --max-edge 1920 --quality 85 --apply
python -m archive_assistant.cli.main convert ~/Photos --to webp --recursive --apply

# 打包成 zip / 解压 zip（解压含 zip-slip 防护）
python -m archive_assistant.cli.main pack ~/项目 report.pdf --out backup.zip --apply
python -m archive_assistant.cli.main unpack backup.zip --out-dir ~/还原 --apply
```

### organize 子命令选项

```bash
python -m archive_assistant.cli.main organize ~/Desktop --skip-existing
python -m archive_assistant.cli.main organize ~/Desktop --skip-infrequent
python -m archive_assistant.cli.main organize ~/Desktop --skip-vlm-theme
python -m archive_assistant.cli.main organize ~/Desktop --include-shortcuts
```

## auto 意图路由器

`auto` 命令是弱模型友好的核心设计——把意图识别从 LLM 移到确定性 Python 代码。

### 工作原理

1. 模型把用户原话传给 `auto "用户原话"`
2. `auto_router.py` 用关键词匹配识别意图（34 种）
3. 返回建议命令 + 自动执行选项
4. 模型只需读输出、转述给用户

### 查看所有可识别意图

```bash
python -m archive_assistant.cli.main auto --list-intents
```

### 路由规则优先级

- 更具体的关键词优先（如"照片去重"→phash，而非通用 hash）
- 更长关键词权重更高（4字以上 +1 权重）
- 多条规则命中取命中数最多的
- 无命中 → 默认 `organize`（最通用）

## 配置项（config.yaml）

| 配置 | 默认值 | 说明 |
|------|--------|------|
| `skip_extensions` | lnk/app/url/webloc/desktop | 快捷方式扩展名（绝对不动） |
| `skip_filenames` | desktop.ini/Thumbs.db/.DS_Store/.localized | 系统文件名（跳过扫描） |
| `infrequent_threshold_days` | 60 | 不常用文件阈值（天） |
| `desktop_routing_rules` | installers/archives/videos/audios | 扩展名路由规则 |
| `vlm.backend` | ollama | VLM 后端：`ollama`(默认) / `transformers` |
| `vlm.ollama.model` | qwen3.5:4b | ollama 后端使用的本地多模态模型 |
| `vlm.ollama.host` | http://127.0.0.1:11434 | 本地 ollama 服务地址 |
| `vlm.ollama.concurrency` | 4 | 批量逐图请求的并发数（ollama 服务端可并行处理） |
| `vlm.model_id` | Qwen/Qwen3.5-4B | 仅 `transformers` 后端用的 HF 模型 |
| `vlm.local_dir` | ~/models/Qwen3.5-4B | 仅 `transformers` 后端用的本地权重路径 |
| `vlm.device` | auto | `transformers` 设备：auto / cpu / cuda / mps(Apple Silicon) |
| `vlm.quantization` | "" | `transformers` 量化：""/4bit/8bit（仅 cuda 生效，需 bitsandbytes，省显存提吞吐） |
| `vlm.attn_implementation` | "" | `transformers` 注意力实现：""/flash_attention_2（装了 flash-attn 时填，省时延） |
| `vlm.max_image_edge` | 512 | 喂 VLM 前把图最长边缩到该值，降 visual token 提速；<=0 关闭 |
| `vlm.max_files_per_call` | 80 | VLM 单次调用文件数上限 |
| `clustering.desktop.max_themes` | 8 | VLM 主题归类最大主题数 |
| `executor.default_dry_run` | true | 默认 dry-run |
| `executor.trash_dir` | ~/.archive_assistant/trash | 回收站目录 |

## 大批量照片/视频 VLM 提速

VLM 读图/视频抽帧是整条链路最慢的环节。本 skill 从「**少调用 + 快单次 + 并发**」三个方向做了优化，对外接口不变、缺依赖（imagehash/PIL/量化库）时自动回退旧行为。

### 提速手段一览

| 手段 | 作用层 | 效果 | 开关 |
|------|--------|------|------|
| **pHash 预聚类** | `extract` caption | 视觉相似图归一簇，整簇共用一次 caption，调用从 N 次降到簇数（常省 70%~90%） | `--cluster-threshold`（汉明距离，<0 关闭） |
| **批量推理** | 代表图前向 | 一次前向喂多张图，transformers 单卡吞吐翻倍 | `--batch-size` |
| **结果缓存** | caption/ocr | 按 `路径+mtime+size` 命中历史结果，增量整理近乎零成本 | 默认开，`--no-cache` 关 |
| **缩图降 token** | 所有 VLM 调用 | 喂图前把最长边缩到 `max_image_edge`，visual token 随分辨率平方下降 | `vlm.max_image_edge` |
| **OCR 并发** | `extract` ocr | pytesseract / ollama 后端用线程池并发 | `--workers`（transformers 单卡自动串行，避免线程不安全） |
| **视频抽帧并发** | 视频命名/抽帧 | 多个时间点 ffmpeg 并行抽帧，缩短墙钟 | 自动（≤6 线程） |
| **ollama 并发请求** | ollama 批量 | 客户端并发逐图请求，服务端并行处理 | `vlm.ollama.concurrency` |
| **量化 / flash-attn / MPS** | transformers 加载 | 省显存、降时延、Apple Silicon 加速 | `vlm.quantization` / `vlm.attn_implementation` / `vlm.device` |

### 调参建议

- **照片高度相似（连拍/截图批）**：调大 `--cluster-threshold`（如 8~10），聚簇更激进、调用更少；内容差异大则调小或设 `-1` 关闭。
- **transformers 本地单卡**：调大 `--batch-size`（8~16）吃满吞吐；显存紧张时配 `vlm.quantization: 4bit`。
- **ollama 后端**：靠 `vlm.ollama.concurrency` 提并发（默认 4），OCR 的 `--workers` 同步生效。
- **重复整理同一批**：保持缓存开启（默认），第二次几乎瞬时完成。

> 说明：本地 transformers 单卡 GPU 推理受 GIL + 单模型实例限制，**不能靠多线程并行前向**；故 `--workers` 仅对 pytesseract / ollama 后端的 OCR 生效，transformers 后端自动串行以保证稳定。

## 模型

- 默认后端 **ollama**：复用本机 ollama 服务里的多模态模型（默认 `qwen3.5:4b`，text + image + video），
  **不下载任何 HF 权重、不依赖 transformers/torch**。配置见 `config.yaml` → `vlm.backend / vlm.ollama.*`。
- VLM 不可用时优雅降级：ollama 服务未启动 / 模型未 pull / 请求失败，或 `--no-vlm` → 桌面按文件名关键词分组。

### ollama 后端准备（默认，推荐）

```bash
# 1) 本机安装并启动 ollama（见 https://ollama.com）
# 2) 拉取多模态模型（仅首次）
ollama pull qwen3.5:4b
# 3) 直接跑，无需任何 setup/下载步骤
python -m archive_assistant.cli.main auto "整理桌面" --root ~/Desktop --execute
```

换模型：改 `config.yaml` → `vlm.ollama.model`，并确保对应模型已 `ollama pull`。

### transformers 后端（可选，离线本地推理）

把 `config.yaml` → `vlm.backend` 改为 `transformers`，并解开 `requirements.txt` 中
torch/transformers 等注释安装本地推理栈。此时首次运行会自动调用 `setup_model.ensure_model()`
下载约 4GB 权重（`Qwen/Qwen3.5-4B`）。**注意 transformers 版本极敏感**（`<5.0` 失败 /
`5.6.0` ✅ / `>=5.12` 视觉回归，详见 `docs/QWEN_VISION_DIAGNOSIS.md`）：

```bash
python -m archive_assistant.cli.main setup              # 检测+下载（仅 transformers 后端）
python -m archive_assistant.cli.main setup --install-deps  # 自动装缺失依赖
python -m archive_assistant.cli.main setup --check         # 仅检查状态
```

> 绝大多数场景用默认 ollama 后端即可，无需以上下载/版本校验步骤。
