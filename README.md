# Desktop Archive Assistant

端侧"桌面文件整理助手"。详见 [SKILL.md](./SKILL.md)。

## qclaw 一键安装（推荐）

把本工具装好并集成到 qclaw（拉模型 → 装依赖 → 装 `archive` 命令 → 同步 `SKILL.md` → 建 agent → merge `openclaw.json`），最后**自动自检**并明确报告哪一步 OK / 哪一步失败：

```bash
# 全新机器：先 clone 仓库
git clone https://github.com/lwpyh/desktop-archive-assistant.git
cd desktop-archive-assistant
bash scripts/install_qclaw_integration.sh

# 已装过、只想拉最新代码重装：一条命令搞定
cd desktop-archive-assistant && git pull && bash scripts/install_qclaw_integration.sh --force
```

> ⚠️ **全新机器首次部署**：请先从 qclaw「专家广场」安装任意一个其他专家（触发 qclaw 的 agent 持久化机制），**再**跑本脚本，否则 qclaw 启动会覆盖 `openclaw.json` 导致 `desktop-archiver` agent 消失。

安装脚本最后一步 `[7/7] 安装自检` 会逐项检查并打印结果：

- ✅/❌ Python 核心依赖、`archive_assistant` 包可导入、`archive` 命令、`archive` CLI 端到端可运行
- ✅/❌ `SKILL.md` 已同步到 `~/.qclaw/skills/`、`models.json` 合法、workspace 规则文件、`openclaw.json` 里 agent + skill
- ⚠️ ollama 服务与关键模型（缺失只告警，整理会降级为规则模式仍可跑）

任何 ❌ 都会附带「怎么修」的提示，且脚本以**非零退出码**结束，便于链式命令感知失败。

常用参数：`--force`（覆盖重装）、`--skip-pip`、`--skip-models`、`--skip-config`。

## 快速开始（纯 CLI，不经 qclaw）

```bash
# 1. 安装依赖（CPU 即可跑，OCR 全本地）
pip install -r requirements.txt

# 2. （可选）下载 Qwen3.5-4B 到本地
bash scripts/setup.sh

# 3. 桌面归档（先看计划）
python -m archive_assistant.cli.main desktop ~/Desktop --dry-run --out /tmp/desk_plan.json

# 4. 真的执行
python -m archive_assistant.cli.main desktop ~/Desktop --apply --plan /tmp/desk_plan.json

# 5. 一旦后悔
python -m archive_assistant.cli.main rollback --last
```

## 目录布局

```
desktop-archive-assistant/
├── SKILL.md
├── README.md
├── requirements.txt
├── archive_assistant/
│   ├── config.yaml              # 模型路径、路由规则、阈值
│   ├── core/                    # asset / cluster / plan 数据结构
│   ├── extractors/              # 扫描、OCR、PDF/DOCX 正文、文本 embedding
│   ├── clustering/              # desktop: VLM 主题归类
│   ├── vlm/                     # Qwen3.5-VL-4B 适配（organize/caption/ocr）
│   ├── planner/                 # 生成归档计划
│   ├── executor/                # dry-run / apply / rollback
│   ├── cli/                     # 命令行入口
│   └── utils/
├── scripts/
│   ├── setup.sh                 # 装模型
│   └── archive                  # bash wrapper
└── tests/
    └── test_smoke.py
```

## 安全策略

- 默认 dry-run；显式 `--apply` 才真正动文件
- **【硬性要求】绝不硬删除文件**：只做 mkdir/move/trash，重复/废弃文件移入回收站
  （`~/.archive_assistant/trash/<ts>/`），绝不调用删除 API
- 所有移动操作可 rollback（写入 `~/.archive_assistant/log/<ts>.json`），回收站内文件同样可还原
- 同名冲突自动改名，绝不覆盖已存在文件
- 不联网（除模型首次下载需 HF），核心流程纯本地
