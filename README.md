# Desktop Archive Assistant

端侧"桌面文件整理助手"。详见 [SKILL.md](./SKILL.md)。

## 快速开始

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
