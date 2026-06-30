#!/usr/bin/env bash
# 安装 pip 依赖；默认后端为本地 ollama（不下载任何 HF 权重）。
# 用法：
#   bash scripts/setup.sh                    # 装 pip 依赖 + 提示准备 ollama 模型（推荐）
#   bash scripts/setup.sh --no-pip           # 跳过 pip 依赖安装
#   bash scripts/setup.sh --with-transformers  # 额外下载 Qwen3.5-4B(~4GB) 给 transformers 后端用
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SKILL_DIR"

NO_PIP=0
WITH_TRANSFORMERS=0
for arg in "$@"; do
  case "$arg" in
    --no-pip)            NO_PIP=1 ;;
    --with-transformers) WITH_TRANSFORMERS=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

if [ "$NO_PIP" -eq 0 ]; then
  echo "[setup] installing python deps ..."
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
fi

if [ "$WITH_TRANSFORMERS" -eq 0 ]; then
  # 默认 ollama 后端：不下载 HF 权重，提示用户准备本地模型
  echo "[setup] 默认 VLM 后端为本地 ollama（config.yaml: vlm.backend=ollama），不下载 HF 权重。"
  echo "[setup] 请确保已安装并启动 ollama，然后拉取多模态模型（仅首次）："
  echo "          ollama pull qwen3.5:4b"
else
  # 可选 transformers 后端：下载 Qwen3.5-4B 到 ./models/Qwen3.5-4B
  echo "[setup] --with-transformers：安装本地推理栈并下载权重"
  python -m pip install 'torch>=2.1' 'transformers==5.6.0' 'huggingface-hub>=1.0' 'accelerate>=0.30' 'qwen-vl-utils>=0.0.10'
  TARGET="$SKILL_DIR/models/Qwen3.5-4B"
  if [ -d "$TARGET" ] && [ -f "$TARGET/config.json" ]; then
    echo "[setup] model already at $TARGET, skip"
  else
    echo "[setup] downloading Qwen/Qwen3.5-4B -> $TARGET"
    mkdir -p "$TARGET"
    if command -v huggingface-cli >/dev/null 2>&1; then
      huggingface-cli download Qwen/Qwen3.5-4B --local-dir "$TARGET" --local-dir-use-symlinks False
    else
      python - <<PY
from huggingface_hub import snapshot_download
snapshot_download(repo_id="Qwen/Qwen3.5-4B", local_dir="$TARGET", local_dir_use_symlinks=False)
PY
    fi
  fi
  echo "[setup] 记得把 config.yaml 的 vlm.backend 改为 transformers"
fi

echo "[setup] done."
echo "Try: python -m archive_assistant.cli.main auto \"整理桌面\" --root ~/Desktop --execute"
