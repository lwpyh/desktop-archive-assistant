---
name: desktop-archive-assistant
description: |
  ⚠️【铁律·启动协议】收到整理/归档/分类/改名/照片/视频/文件请求时，
  第一个动作必须用 read_file 读本 skill 的 SKILL.md 完整内容（location见下方），
  读完之前不回复方案、不调工具、不跑shell、不编结果。
  
  基础格式：python3 -m archive_assistant.cli.main <子命令> <目录> [参数]
  常用子命令：organize / classify / classify-into / dedupe / video-rename-title --ai / extract --mode ocr --vlm-ocr / rollback --last / sort
  核心规则：禁止自己用 mv/cp/mkdir/find 整理，一律走子命令。执行后原样转述输出给用户。
description_zh: "桌面整理专家：先读SKILL.md再执行，用子命令整理禁止手写shell"
description_en: "Desktop organizer: MUST read_file SKILL.md first, then use subcommands (never manual shell)"
location: plugin
version: 0.8.0
metadata:
  openclaw:
    always: true
    emoji: "🗂️"
entrypoints:
  - scripts/archive             # bash wrapper (macOS/Linux)
  - scripts/archive.cmd         # Windows wrapper
  - archive_assistant.cli.main  # python -m archive_assistant.cli.main
---

# Desktop Archive Assistant — 桌面文件整理助手

## 🚨 会话启动铁律（最高优先，违反 = 无效）

**收到任何整理/归档/分类/改名/去重/照片/视频/表格/文档相关请求时，第一个动作必须是 `read_file` 读取本 SKILL.md 完整内容。**

**为什么：** 你现在看到的只是 frontmatter 注入的摘要（常用命令列表）。完整 SKILL.md 里有意图速查表（38条命令）、强制流程、安全原则、OCR vs caption 区分等关键信息。

**执行规则：**
1. 收到整理相关请求 → 先 `read_file` 读 SKILL.md
2. 读完之前：不回复方案、不调工具、不跑shell、不编结果
3. 读完之后：按 SKILL.md 的"强制流程"执行

## ⚠️ SYSTEM RULES — 强制执行

**你直接选子命令执行，不要手写 shell。** 任何涉及"整理/归档/分类/照片/视频"的任务，
**绝对禁止自己用 exec 跑 mv/cp/mkdir/find**——用下方的子命令，内部自动调 VLM 看图归类、安全移动（可回滚）。

**基础格式：**
```bash
cd /Users/jian/desktop-archive-assistant && python3 -m archive_assistant.cli.main <子命令> <目录> [参数]
```

**强制流程：**
1. 收到用户请求 → 从下方"命令速查表"选最接近的子命令
2. 确定目标目录（默认 `~/Desktop`，用户指定了就用指定的）
3. 调用 `exec` 工具执行命令
4. 把命令输出原样转述给用户，**不要自己编造"已整理完成"等结论**

**绝对禁止：**
- ❌ 自己用 `mv`/`cp`/`mkdir`/`find` 整理文件
- ❌ 自己判断"这是视频、这是图片"然后手动归类
- ❌ 在命令没真正成功时回复"✅ 已整理完成"（必须看命令真实输出）
- ❌ 用 `~/桌面` 这种错误路径（macOS 桌面是 `~/Desktop`）

> **弱模型 fallback**：如果你拿不准该选哪个子命令，可以用 `auto "<用户原话>" --root <目录>` 让路由器自动匹配。但强模型应优先直接选子命令，更快更准。

## 📋 命令速查表（意图→子命令直接对照）

| 用户意图 | 子命令 | 说明 |
|---------|--------|------|
| 整理桌面 / 桌面太乱 | `organize ~/Desktop` | 全流程：扫描→归类→执行→排列图标 |
| 照片按日期归档 | `archive-by-date ~/Photos` | 按 EXIF 拍摄日期到 年/月/日 三级目录 |
| 照片去重 | `dedupe ~/Photos --method phash` | 感知哈希去重，视觉相似也识别 |
| 文件去重（哈希） | `dedupe ~/Downloads --method hash` | 完全相同才判定重复 |
| **自动按内容分类** | `classify ~/Photos` | VLM 自动生成类别并归类（无需指定类别） |
| **指定类别分类** | `classify-into ~/Photos --categories "风景,人物,美食"` | 按给定类别归类，不匹配的进"其他"文件夹 |
| 按关键词分类 | `classify-rules ~/Downloads` | 按关键词规则归类（确定性，零成本） |
| 按类型/扩展名/月份分组 | `group-by ~/Downloads` | 按类型/扩展名/日期/首字母分组 |
| 视频改名（规则清洗） | `video-rename-title ~/Videos` | 去下划线/括号/#标签，不抽帧 |
| **视频按内容改名** | `video-rename-title --ai ~/Videos` | VLM 抽帧→看画面→AI 起名 |
| 图片按文字改名 | `image-rename-by-ocr ~/Photos` | VLM OCR 识字→取关键词作文件名 |
| 图片 OCR 识字 | `extract ~/Photos --mode ocr --vlm-ocr` | 输出每图文字到 csv |
| 图片内容描述 | `extract ~/Photos --mode caption` | 一句话描述每张图 |
| 图片转 PPT | `to-ppt ~/Photos` | 每张一页居中铺满 |
| 多图拼接 | `collage ~/Photos` | 多图拼成一张（A4/网格） |
| 图音合成视频 | `video-compose ~/Photos` | 图片+音频→视频 |
| 视频分发 | `video-distribute ~/Videos` | 按数量分发到多个子文件夹 |
| 排列桌面图标 | `sort ~/Desktop` | 消除空位/按类型排序 |
| **移动文件/文件夹** | `move --src <源路径> --to <目标目录>` | 移动单个文件或文件夹（走执行器，可回滚） |
| **合并文件夹（冲突时）** | `move --src <源文件夹> --to <目标目录> --merge` | 目标已有同名文件夹时合并内容（文件冲突加后缀，绝不覆盖） |
| 批量移动（按通配符） | `move <目录> --match "*.jpg" --to <目标>` | 按文件名 pattern 批量移动 |
| 清理临时文件 | `clean ~/Desktop --temp --empty-dirs` | 清理临时/空目录 |
| 定时整理 | `schedule ~/Desktop` | cron 定时任务 |
| 表格清洗 | `table-clean <file.xlsx>` | 列筛选/去重/空值清洗 |
| 汇总成表 | `table-merge ~/files` | 多文件汇总成一个表 |
| 整理成 Word | `docx-compose` | 清单/资料整理成 Word |
| PDF 合并 | `pdf-ops merge` | 多 PDF 合一 |
| PDF 拆分 | `pdf-ops split` | PDF 拆多份 |
| 平铺子目录 | `flatten ~/Downloads` | 子目录文件提到顶层 |
| 表格按列拆分 | `table-split <file.xlsx>` | 按某列值拆成多文件 |
| 图片格式转换 | `convert ~/Photos` | 批量转 jpg/png/webp/压缩 |
| 打包 zip | `pack ~/folder` | 打包成 zip |
| 解压 zip | `unpack <file.zip>` | 解压（含 zip-slip 防护） |
| 回滚 / 撤销 | `rollback --last` | 撤销上次操作 |

> **默认直接执行**：以上命令不加额外参数即为真正执行（可 `rollback --last` 撤销）。加 `--dry-run` 只预览不改文件。

## 📁 照片/文件分类：两种模式（重要）

### 模式 1：自动按内容分类（VLM 自动生成类别）

用户说"按内容分类"/"自动分类"/"按主题分" → 用 `classify`，**不需要给类别**，VLM 自己看内容生成类别：

```bash
python3 -m archive_assistant.cli.main classify ~/Photos
```

VLM 会扫描所有文件 → 看图片画面/读文档正文 → 自动聚类生成文件夹名 → 移入对应文件夹。
适合：不知道有哪些类别、想让 AI 自己判断的场景。

### 模式 2：指定类别分类（用户给类别列表）

用户说"按这些类别分"/"分成 XX、YY、ZZ" → 用 `classify-into`，**给类别列表**，VLM 把每个文件归入最匹配的类别：

```bash
python3 -m archive_assistant.cli.main classify-into ~/Photos --categories "风景,人物,美食,证件"
```

**不匹配的文件自动进"其他"文件夹**（默认文件夹名就是"其他"，可用 `--unmatched "杂项"` 自定义）：

```bash
# 不匹配的进"其他"（默认行为）
classify-into ~/Photos --categories "风景,人物,美食"

# 自定义兜底文件夹名
classify-into ~/Photos --categories "发票,合同,报表" --unmatched "杂项"

# 不匹配的保持不动（不创建"其他"文件夹）
classify-into ~/Photos --categories "风景,人物" --keep-unmatched
```

适合：用户明确知道要分哪些类别、想要精确控制的场景。

### 两种模式对比

| | `classify`（自动） | `classify-into`（指定） |
|---|---|---|
| 类别来源 | VLM 自动生成 | 用户给定 |
| 需要给 `--categories` | ❌ 不需要 | ✅ 必须 |
| 不匹配的文件 | 单独成簇或跳过 | 进"其他"文件夹（可配置） |
| 适合场景 | 不知道有哪些类别 | 明确知道要分几类 |
| 速度 | 稍慢（要思考类别） | 较快（从列表选） |

> **classify 先生成 plan 再 apply**：`classify` 只生成计划不执行，需要 `classify ~/Photos` → 看 plan → `apply --plan <plan.json>` 执行。`classify-into` 默认直接执行。

## 🔍 文本密集图片：先 OCR 再做下游任务（重要，易错）

当图片是 **截图 / 扫描件 / 发票 / 合同 / 文档照片 / 证件 / 票据 / 纪念照 / 手写文字照片** 等**文字密集**内容，且用户要基于图片里的文字做下游任务（分类 / 汇总 / 改名 / 检索）时，**必须先用 VLM OCR 把文字识别出来**——不要只用 caption（一句话描述会丢失具体文字，且可能完全识别错误）。

> ⚠️ **典型错误案例**：一张写着"胡健 周慧敏 1982年10月7日 结婚纪念日"的纪念照，用 `--mode caption` 会识别成 "A printed note on a corkboard"（完全错误）；用 `--mode ocr --vlm-ocr` 能正确识字。

**OCR 命令（必须显式 `--mode ocr --vlm-ocr`，才会走 VLM 识字）：**

```bash
python3 -m archive_assistant.cli.main extract <目录> --mode ocr --vlm-ocr --out ocr_result.csv   # ✅ VLM OCR，输出每图文字
```

> ⚠️ **不要用 `auto "提取文字"` 做文本密集 OCR**：auto 路由到 extract 时默认 `--mode caption`（只描述不识字）。文字密集场景**必须显式** `extract --mode ocr --vlm-ocr`。

**识别场景判断（改名前必看）**：
- 图片里有**手写文字 / 打印文字 / 中文 / 日期 / 姓名 / 金额 / 表格** → **走 OCR**（`--mode ocr --vlm-ocr`）
- 图片是**纯风景 / 人物肖像 / 无明显文字** → 走 caption（`--mode caption`）
- **拿不准就 `--mode both`**（既描述又识字，最安全）

### 图片按内容改名的正确流程

| 图片类型 | 正确做法 | 错误做法 |
|---------|---------|---------|
| **文字密集**（截图/纪念照/票据/合同） | `extract --mode ocr --vlm-ocr` → 取识别出的文字改名 | ❌ `extract --mode caption`（会识别错误） |
| **纯画面**（风景/人物/动物） | `extract --mode caption` → 取描述改名 | ✅ 正确 |
| **拿不准** | `extract --mode both` → 同时获取 OCR 文字 + caption 描述 | — |

> **关键**：用户说"按内容改名"时，如果图片里有文字，**内容就是文字**——必须走 OCR。只有纯画面图片才走 caption。

**OCR 完成后的下游任务：**

| 下游需求 | 做法 |
|---|---|
| 按内容分类 | `classify-into <目录> --categories "类别1,类别2"`（OCR 文字会被自动读取作为归类依据） |
| 汇总成表 | `extract --mode ocr --vlm-ocr --out result.csv` 直接产出表格 |
| 按文字改名 | `image-rename-by-ocr <目录>`：VLM OCR 识别每张图文字 → 取关键文字作新文件名 |

## 🎬 视频改名：两种模式（重要，易混）

`video-rename-title` 有**两种模式**，根据用户意图选择：

| 用户意图 | 命令 | 做什么 |
|---------|------|--------|
| "改视频名"/"视频标题重命名" | `video-rename-title <目录>` | **规则清洗**：去下划线/括号/#标签/审核尾巴，不抽帧 |
| "**按内容改名**"/"**画面**改名"/"**AI**改名" | `video-rename-title --ai <目录>` | **VLM 抽帧→看画面→AI 起名**（ffmpeg 抽 6 帧 → VLM 看图 → 生成内容描述作文件名）|

> **关键区别**：用户说「按**内容**/画面/AI」→ 加 `--ai` 走 VLM；否则走快速规则清洗。
> 预览加 `--dry-run`（不要加 `--execute` 或 `--apply`，子命令不支持这两个参数）。

## 🛡️ 安全原则（硬性要求）

1. **默认直接执行**：不加额外参数即真正操作（可 `rollback --last` 撤销）
2. **绝不硬删除**：删除一律移入回收站 `~/.archive_assistant/trash/<ts>/`
3. **绝不覆盖**：同名冲突自动加 `-1/-2` 后缀；`move --merge` 模式下文件冲突同样加后缀，绝不覆盖目标已有文件
4. **可回滚**：每次执行写日志，`rollback --last` 撤销
5. **快捷方式绝对不动**：`.lnk`/`.app`/`.url`/`.webloc`/`.desktop` 自动跳过
6. **已有文件夹不拆解**：只往里放文件
7. **输出不污染用户目录**：所有过程性输出（plan.json / report.md / ocr.csv 等）一律写到 `~/.archive_assistant/output/`，**禁止写到被整理的目标目录**。只有用户明确要的产出（PPT、拼接图、合成视频）才按用户指定位置输出。

## 🔄 auto 路由器（弱模型 fallback）

如果你拿不准该选哪个子命令，可以用 `auto` 让路由器自动匹配：

```bash
python3 -m archive_assistant.cli.main auto "用户的原始请求" --root <目录>
```

auto 会用关键词匹配识别意图，输出建议命令。但**强模型应优先直接选子命令**——auto 的关键词匹配较死板，口语化表达可能匹配不准。

查看所有可识别意图：`auto --list-intents`

## 何时加载本 Skill

- "帮我整理桌面 / 把桌面归档一下 / 桌面太乱了"
- "把下载目录的文件按主题分类"
- "排列桌面图标 / 桌面图标排列"
- "定时整理桌面"
- "照片按拍摄日期/年份归档 / 照片去重 / 图片转PPT / 多图拼接"
- "视频标题重命名 / 视频按内容改名 / 图片配音乐做视频 / 视频分发"
- "表格列筛选/去重/清洗 / 多个文件汇总成表 / 整理成 Word / PDF 合并拆分"
- "按类型/扩展名/月份/首字母分组 / 按内容或自定义类别分类 / 按关键词归类"
- "平铺子目录 / 表格按列拆分 / 图片格式转换压缩 / 打包成 zip / 解压 zip"
- 关键词：`整理桌面 / 归档桌面 / 文件分类 / 照片整理 / 像册整理 / 照片归档 / 照片去重 / 图片转PPT / 视频重命名 / 视频按内容改名 / 图音合成 / 表格清洗 / 表格去重 / 汇总成表 / 整理成Word / PDF合并 / PDF拆分 / 按类型分类 / 按内容分类 / 按关键词分类 / 平铺 / 表格拆分 / 图片格式转换 / 打包zip / 解压zip`

## 跨平台支持

Windows / macOS / Linux 三平台均可运行：
- 通用整理/照片视频/Office 能力：三平台全支持
- 桌面图标排列(sort)：Windows ✅ / macOS ✅ / Linux ⚠️跳过（文件整理不受影响）
- 定时整理(schedule)：Windows schtasks / macOS+Linux crontab
- 快捷方式识别：Windows `.lnk`/`.url`、macOS `.app`/`.webloc`、Linux `.desktop`

## 安装与部署（Windows / macOS / Linux 通用）

> 目标：装好 Python 依赖 + 准备好本地 ollama 模型，即可跑通全部 38 个能力。**全程纯 Python，三平台命令一致，零系统级编译依赖。**

### 一键部署（推荐）

```bash
cd <skill-dir>

# 1) 装 pip 依赖（含自带 ffmpeg，无需系统安装）
pip install -r requirements.txt

# 2) 准备本地 ollama 多模态模型（仅首次）
ollama pull qwen3.5:4b

# 3) 跑通
python -m archive_assistant.cli.main organize ~/Desktop
```

### VLM 后端：本地 ollama（默认）

`config.yaml` → `vlm.backend: ollama`，相关配置：

```yaml
vlm:
  backend: "ollama"
  ollama:
    host: "http://127.0.0.1:11434"   # 本地 ollama 服务地址
    model: "qwen3.5:4b"              # 多模态模型，需先 ollama pull
    timeout: 120
```

- **不下载任何 HF 权重、不依赖 transformers/torch**——图片识别、主题归类、视频命名全部通过本地 ollama 服务完成。
- ollama 服务未启动 / 模型未 pull / 请求失败 → 自动降级为规则模式（按文件名/扩展名分类），整理流程不中断。
- 换模型只需改 `vlm.ollama.model`，并确保该模型已 `ollama pull`。

### ffmpeg：已内置，无需系统安装

`video-compose`（图音合成视频）所需的 ffmpeg 由 pip 包 `imageio-ffmpeg` **自带跨平台静态二进制**，
`pip install -r requirements.txt` 时一并装好，开箱即用。系统若已自行安装 ffmpeg（brew/apt/winget），
会优先使用系统版；否则自动回退到内置二进制。**无需再单独 `brew/apt install ffmpeg`。**

### 可选：transformers 本地推理后端

如需完全离线、不经 ollama，可把 `vlm.backend` 改为 `transformers` 并解开 `requirements.txt` 中
对应注释安装本地推理栈。**注意 `Qwen3.5-4B` 对 transformers 版本极敏感**（`<5.0` 加载失败 /
`5.6.0` ✅正常 / `>=5.12` 视觉回归，详见 `docs/QWEN_VISION_DIAGNOSIS.md`），且需手动下载约 4GB 权重。
绝大多数场景推荐用默认的 ollama 后端，省去这一切。
