---
name: desktop-archive-assistant
description: |
  【桌面/文件整理·一条命令搞定】用户要整理桌面/文件/照片/视频时，别自己写 mv/cp/mkdir/find（又长又容易错），
  直接用这个 1 条命令替代 10 行 shell：
  archive "<意图>" [目录]
  它内部自动调 VLM 看图归类、安全移动（进回收站可回滚），比你手写 shell 准得多、省事得多。
  <意图>取值：整理桌面 / 照片按日期归档 / 照片去重 / 视频按内容改名 / 图片转PPT / 多图拼接 / 图音合成视频 / 视频分发 / 排列桌面图标 / 回滚 / 清理临时文件 / 定时整理 / 表格清洗 / 汇总成表 / 整理成word / pdf合并 / pdf拆分 / 按类型分类 / 按内容分类 / 按关键词分类 / 平铺子目录 / 表格拆分 / 图片格式转换 / 打包zip / 解压zip。
  示例：
    archive "整理桌面"                    # 整理 ~/Desktop（默认）
    archive "整理桌面" ~/Downloads        # 整理指定目录
    archive "照片按日期归档" ~/Pictures    # 照片归档
    archive "视频按内容改名" ~/Movies       # 视频改名
    archive "回滚"                         # 撤销上次操作
  执行后把输出原样转述给用户即可，不要自己编"已整理完成"。
  注意：禁止自己用 mv/cp/mkdir/find 整理——用 archive 命令才正确、才安全、才省事。
description_zh: "桌面整理专家：执行 auto 命令整理桌面/照片/视频，禁止自己跑 shell 整理"
description_en: "Desktop organizer: run `auto` command to organize files/photos/videos, never shell out manually"
location: plugin
version: 0.6.0
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

## ⚠️ SYSTEM RULES — 强制执行，不可跳过（4B 小模型必读）

**你是一个路由器，不是执行器。** 任何涉及"整理/归档/分类/整理桌面/照片/视频"的任务，
**绝对禁止自己用 exec 跑 mv/cp/mkdir 之外的 shell 命令去整理文件**，也**禁止自己判断文件类型后手动归类**。
你**唯一该做的事**是把用户意图映射到下方的命令表，然后调用 `auto` 子命令执行：

```bash
cd /Users/jian/desktop-archive-assistant && python3 -m archive_assistant.cli.main auto "标准化意图词" --root <目录>
```

**强制流程（每次都必须走，不可省略）：**
1. 收到用户请求 → 从下方"工具清单"选最接近的意图词
2. 确定目标目录（默认 `~/Desktop`，用户指定了就用指定的）
3. 调用 `exec` 工具执行上面的 `auto` 命令
4. 把命令输出原样转述给用户，**不要自己编造"已整理完成"等结论**

**绝对禁止的行为（违反即错误）：**
- ❌ 自己用 `mv`/`cp`/`mkdir` 整理文件（skill 内部会安全地做，你不需要管）
- ❌ 自己判断"这是视频、这是图片"然后手动归类
- ❌ 在命令没真正成功时回复"✅ 已整理完成"（必须看命令真实输出）
- ❌ 用 `~/桌面` 这种错误路径（macOS 桌面是 `~/Desktop`）

**为什么这样设计：** skill 内部有专门的 VLM（Qwen3.5-4B）做图片识别、主题归类、视频命名，
比你直接跑 shell 准确得多，且删除会进回收站可回滚。你的路由 + skill 内部执行 = 最佳结果。
你只做路由，skill 干活。

## ⚡ 快速使用（推荐，所有模型通用）

**你不需要记忆 38 个子命令。** 只需把用户的原话传给 `auto`，它自动识别意图、选择命令、给出建议：

```bash
python -m archive_assistant.cli.main auto "用户的原始请求" --root <目录>
```

### 用户说了什么 → 你运行什么

| 用户说的话 | 你运行的命令 |
|----------|------------|
| "整理桌面" / "桌面太乱了" | `auto "整理桌面" --root ~/Desktop` |
| "照片按日期归档" / "照片按年整理" | `auto "照片按日期归档" --root ~/Photos` |
| "照片去重" / "删重复照片" | `auto "照片去重" --root ~/Photos` |
| "视频标题重命名" / "改视频名" | `auto "视频标题重命名" --root ~/Videos` |
| "图片转PPT" / "照片做PPT" | `auto "图片转PPT" --root ~/Photos` |
| "多图拼接" / "照片拼一张" | `auto "多图拼接" --root ~/Photos` |
| "图片配音乐做视频" | `auto "图音合成视频" --root ~/Photos` |
| "视频分发到5个文件夹" | `auto "视频分发" --root ~/Videos` |
| "排列桌面图标" | `auto "排列桌面图标" --root ~/Desktop` |
| "回滚" / "撤销" / "还原" | `auto "回滚"` |
| "清理临时文件" | `auto "清理临时文件" --root ~/Desktop` |
| "定时整理桌面" | `auto "定时整理" --root ~/Desktop` |
| "表格清洗" / "表格去重" | `auto "表格清洗"` |
| "多个文件汇总成表" | `auto "汇总成表" --root ~/files` |
| "整理成Word" / "生成文档" | `auto "整理成word"` |
| "PDF合并" / "PDF拆分" | `auto "pdf合并"` / `auto "pdf拆分"` |
| "按类型/扩展名分类" / "按月份归档" / "按首字母分" | `auto "按类型分类" --root <目录>` |
| "按内容/类别分类" / "按项目/客户/工程类型分" | `auto "按内容分类" --root <目录>` |
| "按关键词分类" / "发票合同分开放" | `auto "按关键词分类" --root <目录>` |
| "平铺子目录" / "把文件都提到一层" | `auto "平铺" --root <目录>` |
| "表格按列拆分" / "拆成多个表" | `auto "表格拆分"` |
| "图片转jpg/png/webp" / "压缩图片" | `auto "图片格式转换" --root <目录>` |
| "打包成zip" / "压缩文件夹" | `auto "打包"` |
| "解压zip" / "解压缩" | `auto "解压"` |

### 执行模式

| 你想要的 | 命令 | 说明 |
|---------|------|------|
| 先看建议命令（不执行） | `auto "请求" --root <目录>` | 安全预览，只输出建议 |
| 自动执行（dry-run 预览） | `auto "请求" --root <目录> --execute` | 跑一遍但不真正改文件 |
| 真正执行 | `auto "请求" --root <目录> --execute --apply` | 真正整理，可 rollback |
| 查看所有可识别意图 | `auto --list-intents` | 列出 34 种意图 |
| JSON 格式输出 | `auto "请求" --json` | 方便程序化处理 |

### 工作流程（推荐）

```
1. 用户说需求 → 你运行 auto "需求" --root <目录>（只看建议）
2. 看 auto 输出的建议命令，确认意图正确
3. 运行 auto "需求" --root <目录> --execute（dry-run 执行）
4. 看 dry-run 结果，如果 OK → 运行 auto "需求" --root <目录> --execute --apply
5. 后悔了 → 运行 auto "回滚"
```

> **整理桌面默认自动排列图标**：`auto "整理桌面" --execute` 流程末尾会自动调用 `sort` 排列桌面图标（删 `.DS_Store` + 重启 Finder，让整理后的文件夹按网格矩阵整齐排列）。预览模式（`--dry-run`）不触发排列，只有真正执行才排列。

> **VLM 后端默认走本地 ollama**（`config.yaml` → `vlm.backend: ollama`）：直接复用你本机
> ollama 服务里的多模态模型（默认 `qwen3.5:4b`），**不下载任何 HF 权重、不依赖 transformers**。
> 只要本机已 `ollama pull qwen3.5:4b` 并启动 ollama，照片识别 / 主题归类 / 视频命名即开箱可用；
> ollama 不可用时自动降级为规则模式（按文件名/扩展名分类），整理功能不受影响。

## 🔍 文本密集图片：先 OCR 再做下游任务（重要，易错）

当图片是 **截图 / 扫描件 / 发票 / 合同 / 文档照片 / 证件 / 票据** 等**文字密集**内容，且用户要基于图片里的文字做下游任务（分类 / 汇总 / 改名 / 检索）时，**必须先用 VLM OCR 把文字识别出来**——不要只用 caption（一句话描述会丢失具体文字）。

**OCR 命令（必须显式 `--mode ocr --vlm-ocr`，才会走 VLM 识字）：**

```bash
cd /Users/jian/desktop-archive-assistant && python3 -m archive_assistant.cli.main auto "识别图片文字" --root <目录>   # ❌ 不行：auto 路由到 extract 默认 caption，不识字
python3 -m archive_assistant.cli.main extract <目录> --mode ocr --vlm-ocr --out ocr_result.csv   # ✅ 正确：VLM OCR，输出每图文字
```

> ⚠️ **不要用 `auto "提取文字"` / `auto "图片文字"` 做文本密集 OCR**：auto 路由到 extract 时默认 `--mode caption`（只描述不识字），且 OCR 默认走 tesseract 回退。文字密集场景**必须显式** `extract --mode ocr --vlm-ocr`。

**识别场景判断**：用户说"图片里有字/截图/扫描/发票/合同/单据/文档照片"，或图片肉眼可见大量文字 → 走 OCR；用户说"照片拍的什么/风景/人物" → 走 caption。拿不准就 `--mode both`（既描述又识字）。

**OCR 完成后的下游任务：**

| 下游需求 | 做法 |
|---|---|
| 按内容分类 | OCR 文字会被 `classify-into` 自动读取作为归类依据 → `auto "按内容分类" --root <目录> --categories "类别1,类别2"`（也可先 extract 出 ocr_text 再 classify） |
| 汇总成表 | `extract --mode ocr --vlm-ocr --out result.csv` 直接产出表格，每行一张图+其文字 |
| 检索/查阅 | extract 出 txt/csv 后，用户可按文字检索图片 |
| 按文字改名 | `auto "图片按文字改名" --root <目录> --execute` 或 `image-rename-by-ocr <目录>`：VLM OCR 识别每张图文字 → 取关键文字作新文件名 → 走执行器改名（可回滚，不手搓 mv）。无文字的图片跳过不改 |

## 安全原则（硬性要求）

1. **dry-run 默认**：不加 `--apply` 只看不改
2. **绝不硬删除**：删除一律移入回收站 `~/.archive_assistant/trash/<ts>/`
3. **绝不覆盖**：同名冲突自动加 `-1/-2` 后缀
4. **可回滚**：每次 `--apply` 写日志，`auto "回滚"` 或 `rollback --last` 撤销
5. **快捷方式绝对不动**：`.lnk`/`.app`/`.url`/`.webloc`/`.desktop` 自动跳过
6. **已有文件夹不拆解**：只往里放文件
7. **输出不污染用户目录**：所有过程性输出（plan.json / report.md / ocr.csv / cleaned.xlsx / merged.xlsx 等）一律写到 `~/.archive_assistant/output/`，**禁止写到被整理的目标目录**。调用时用 `--out ~/.archive_assistant/output/xxx` 指定，或不指定走默认专属目录。只有用户明确要的产出（图片转PPT、拼接图、合成视频）才按用户指定位置输出。执行后被整理目录里不得留下任何新增的非整理产物文件。

## 何时加载本 Skill

- "帮我整理桌面 / 把桌面归档一下 / 桌面太乱了"
- "把下载目录的文件按主题分类"
- "排列桌面图标 / 桌面图标排列"
- "定时整理桌面"
- "照片按拍摄日期/年份归档 / 照片去重 / 图片转PPT / 多图拼接"
- "视频标题重命名 / 图片配音乐做视频 / 视频分发到多个文件夹"
- "表格列筛选/去重/清洗 / 多个文件汇总成表 / 整理成 Word / PDF 合并拆分"
- "按类型/扩展名/月份/首字母分组 / 按内容或自定义类别分类 / 按关键词归类"
- "平铺子目录 / 表格按列拆分 / 图片格式转换压缩 / 打包成 zip / 解压 zip"
- 关键词：`整理桌面 / 归档桌面 / 文件分类 / 照片整理 / 像册整理 / 照片归档 / 照片去重 / 图片转PPT / 视频重命名 / 图音合成 / 表格清洗 / 表格去重 / 汇总成表 / 整理成Word / PDF合并 / PDF拆分 / 按类型分类 / 按内容分类 / 按关键词分类 / 平铺 / 表格拆分 / 图片格式转换 / 打包zip / 解压zip`

## 高级用法（强模型可选）

如果 `auto` 路由的意图不对，或需要精细控制参数，可直接调用具体子命令。
**完整能力参考请见 `REFERENCE.md`**（38 个能力的详细说明、参数、示例）。

常用子命令速查：
```bash
# 整理桌面（全流程）
python -m archive_assistant.cli.main organize ~/Desktop --out plan.json
python -m archive_assistant.cli.main apply --plan plan.json

# 照片按日期归档
python -m archive_assistant.cli.main archive-by-date ~/Photos --apply

# 照片去重（pHash）
python -m archive_assistant.cli.main dedupe ~/Photos --method phash --apply

# 视频标题重命名
python -m archive_assistant.cli.main video-rename-title ~/Videos --apply

# 回滚
python -m archive_assistant.cli.main rollback --last
```

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
ollama pull qwen3.5:9b

# 3) 跑通
python -m archive_assistant.cli.main auto "整理桌面" --root ~/Desktop --execute
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
