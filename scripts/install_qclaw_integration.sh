#!/usr/bin/env bash
# install_qclaw_integration.sh — 一键把 desktop-archive-assistant 装好并集成到 qclaw
#
# 在目标机器上跑一次，自动完成：
#   0. 检查并拉取 ollama 模型（ornith-9b Q8_0 9.5GB + ornith-vision + qwen3.5:4b）
#   1. 装 Python 依赖（pip install -r requirements.txt）
#   2. 装 archive 命令到 ~/.local/bin（自动改 SKILL_DIR 路径 + 加到 PATH）
#   3. 同步 SKILL.md → ~/.qclaw/skills/desktop-archive-assistant/
#   4. 创建 agent 目录 + models.json（检测 ollama 模型自动生成）
#   5. 创建 workspace + 规则文件（SOUL.md/AGENTS.md/IDENTITY.md 等，路径自动替换）
#   6. 安全 merge openclaw.json（添加 desktop-archiver agent + 启用 skill）
#
# 用法（在 desktop-archive-assistant 目录下）：
#   bash scripts/install_qclaw_integration.sh                 # 默认检测
#   bash scripts/install_qclaw_integration.sh --force         # 覆盖已有规则文件 + 重装依赖
#   bash scripts/install_qclaw_integration.sh --skip-pip      # 跳过 pip 安装
#   bash scripts/install_qclaw_integration.sh --skip-models    # 跳过 ollama 模型拉取
#   bash scripts/install_qclaw_integration.sh --skip-config   # 跳过 openclaw.json merge
#
# ⚠️ 重要：全新机器首次部署时，请先从 qclaw「专家广场」安装任意一个其他专家，
#    再跑此脚本。否则 qclaw 启动时会覆盖 openclaw.json，导致 desktop-archiver agent 消失。
#
# 幂等：可重复跑，不会破坏已有配置。
set -eo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATES="$SKILL_DIR/scripts/qclaw_templates"
QCLAW_HOME="${HOME}/.qclaw"
AGENT_ID="desktop-archiver"
WORKSPACE="${QCLAW_HOME}/workspace-${AGENT_ID}"
AGENT_DIR="${QCLAW_HOME}/agents/${AGENT_ID}/agent"
SKILL_SYNC="${QCLAW_HOME}/skills/desktop-archive-assistant"

FORCE=0
SKIP_PIP=0
SKIP_CONFIG=0
SKIP_MODELS=0
for arg in "$@"; do
  case "$arg" in
    --force)        FORCE=1 ;;
    --skip-pip)     SKIP_PIP=1 ;;
    --skip-config)  SKIP_CONFIG=1 ;;
    --skip-models)  SKIP_MODELS=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

# 模型名称常量（提前定义，避免 set -u 问题）
ORNITH_Q8="hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q8_0"

echo "=========================================="
echo "  desktop-archive-assistant 一键安装"
echo "=========================================="
echo "SKILL_DIR:  $SKILL_DIR"
echo "QCLAW_HOME: $QCLAW_HOME"
echo ""

# ---------- 0. 前置检查 ----------
if [ ! -d "$QCLAW_HOME" ]; then
  echo "❌ 未找到 ~/.qclaw 目录，请先安装 qclaw。"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未找到 python3，请先安装 Python 3.10+"
  exit 1
fi

# ---------- 0.5 拉 ollama 模型（ornith + qwen3.5）----------
echo "[0/6] 检查并拉取 ollama 模型"
if [ "$SKIP_MODELS" -eq 1 ]; then
  echo "  ⏭️  --skip-models，跳过"
else
  if ! command -v ollama >/dev/null 2>&1; then
    echo "  ⚠️  未安装 ollama，请先运行: curl -fsSL https://ollama.com/install.sh | sh"
    echo "      装完后手动拉模型:"
    echo "      ollama pull hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q8_0"
    echo "      ollama pull qwen3.5:4b"
  else
    # 确认 ollama 服务在跑
    if ! ollama list >/dev/null 2>&1; then
      echo "  ⚠️  ollama 服务未启动，尝试启动..."
      ollama serve >/dev/null 2>&1 &
      sleep 3
    fi

    if ollama list >/dev/null 2>&1; then
      echo "  ollama 服务正常"

      # 检查并拉取模型
      pull_if_missing() {
        local model="$1"
        local desc="$2"
        if ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "$model"; then
          echo "  ✅ $model 已存在（$desc）"
        else
          echo "  ⏳ 拉取 $model（$desc）..."
          echo "     这可能需要几分钟到几十分钟，取决于网速"
          if ollama pull "$model"; then
            echo "  ✅ $model 拉取完成"
          else
            echo "  ❌ $model 拉取失败，请手动运行: ollama pull $model"
          fi
        fi
      }

      # ornith-9b: 9B 文本模型（Q8_0 量化，9.5GB，qclaw 交互用）
      # 从 HuggingFace 仓库拉取 Q8_0 版本，再创建别名 ornith-9b:latest
      # 如果已有 ornith-9b:latest 但不是 Q8_0 量化，覆盖重建

      # 先确保 Q8_0 基础权重存在
      if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "$ORNITH_Q8"; then
        echo "  ⏳ 拉取 $ORNITH_Q8（9.5GB，Q8_0 量化）..."
        if ollama pull "$ORNITH_Q8"; then
          echo "  ✅ Ornith-1.0-9B Q8_0 拉取完成"
        else
          echo "  ❌ Ornith Q8_0 拉取失败，请手动运行: ollama pull $ORNITH_Q8"
        fi
      else
        echo "  ✅ $ORNITH_Q8 已存在"
      fi

      # 检查 ornith-9b:latest 是否存在且是 Q8_0 量化
      NEED_RECREATE=0
      if ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "ornith-9b:latest"; then
        # 检查现有 ornith-9b:latest 的量化版本
        EXISTING_QUANT=$(ollama show ornith-9b:latest 2>/dev/null | grep -i "quantization" | awk '{print $2}' || true)
        if [ "$EXISTING_QUANT" = "Q8_0" ]; then
          echo "  ✅ ornith-9b:latest 已是 Q8_0 量化（$EXISTING_QUANT），跳过"
        else
          echo "  ⚠️  ornith-9b:latest 量化版本为 $EXISTING_QUANT（非 Q8_0），将覆盖重建"
          # 先删除旧版本
          ollama rm ornith-9b:latest 2>/dev/null || true
          NEED_RECREATE=1
        fi
      else
        NEED_RECREATE=1
      fi

      # 创建/重建 ornith-9b:latest（从 Q8_0 基础权重）
      if [ "$NEED_RECREATE" -eq 1 ] && ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "$ORNITH_Q8"; then
        # 用临时文件写 Modelfile，避免 -f - 的兼容性问题
        MODFILE=$(mktemp /tmp/ornith_modelfile.XXXXXX)
        echo "FROM $ORNITH_Q8" > "$MODFILE"
        if ollama create ornith-9b:latest -f "$MODFILE"; then
          echo "  ✅ ornith-9b:latest 已创建（Q8_0，9.5GB）"
        else
          echo "  ❌ ornith-9b:latest 创建失败，请手动运行:"
          echo "     echo 'FROM $ORNITH_Q8' > /tmp/Modelfile"
          echo "     ollama create ornith-9b:latest -f /tmp/Modelfile"
        fi
        rm -f "$MODFILE"
      fi

      # ornith-vision: 9B + mmproj 视觉投影器（Q8_0 主权重 + mmproj，VLM 视觉用）
      # 创建流程：拉取 Q8_0 主权重 → 下载 mmproj → 用 ADAPTER 组合创建
      # 注意：主权重用 Q8_0（和 ornith-9b 一致），mmproj 来自 bartowski 仓库
      MMPROJ_DIR="${HOME}/ornith-vision-build"
      MMPROJ_FILE="${MMPROJ_DIR}/mmproj-f16.gguf"
      MMPROJ_URL="https://huggingface.co/bartowski/deepreinforce-ai_Ornith-1.0-9B-GGUF/resolve/main/mmproj-deepreinforce-ai_Ornith-1.0-9B-f16.gguf?download=true"
      MMPROJ_SIZE=918165728  # 校验字节数

      # Q8_0 基础权重在上面 ornith-9b 步骤已拉取，这里复用
      # （如果上面没拉成功，这里再检查一次）
      if ! ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "$ORNITH_Q8"; then
        echo "  ⏳ 拉取 $ORNITH_Q8（9.5GB，Q8_0 量化，视觉版基础）..."
        if ollama pull "$ORNITH_Q8"; then
          echo "  ✅ Ornith Q8_0 拉取完成"
        else
          echo "  ❌ Ornith Q8_0 拉取失败，请手动运行: ollama pull $ORNITH_Q8"
        fi
      else
        echo "  ✅ $ORNITH_Q8 已存在（复用 ornith-9b 的基础权重）"
      fi

      # 下载 mmproj 视觉投影器（如果不存在）
      if [ ! -f "$MMPROJ_FILE" ]; then
        echo "  ⏳ 下载 mmproj 视觉投影器（876MB）..."
        mkdir -p "$MMPROJ_DIR"
        if curl -L -o "$MMPROJ_FILE" "$MMPROJ_URL" 2>/dev/null; then
          # 校验文件大小
          ACTUAL_SIZE=$(stat -f "%z" "$MMPROJ_FILE" 2>/dev/null || stat -c "%s" "$MMPROJ_FILE" 2>/dev/null || echo 0)
          if [ "$ACTUAL_SIZE" = "$MMPROJ_SIZE" ]; then
            echo "  ✅ mmproj 下载完成（${ACTUAL_SIZE} 字节）"
          else
            echo "  ⚠️  mmproj 大小不匹配（期望 $MMPROJ_SIZE，实际 $ACTUAL_SIZE），可能损坏"
            echo "     请手动下载: curl -L -o \"$MMPROJ_FILE\" \"$MMPROJ_URL\""
          fi
        else
          echo "  ❌ mmproj 下载失败，请手动运行:"
          echo "     mkdir -p $MMPROJ_DIR"
          echo "     curl -L -o \"$MMPROJ_FILE\" \"$MMPROJ_URL\""
          echo "     或直接用 qwen3.5:4b 替代（原生支持 vision）"
        fi
      else
        echo "  ✅ mmproj 已存在: $MMPROJ_FILE"
      fi

      # 检查 ornith-vision 是否存在且带 vision 能力
      NEED_VISION_RECREATE=0
      if ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "ornith-vision"; then
        if ollama show ornith-vision 2>/dev/null | grep -qi "vision"; then
          echo "  ✅ ornith-vision 已存在（带 vision 能力），跳过"
        else
          echo "  ⚠️  ornith-vision 不带 vision 能力，将覆盖重建"
          ollama rm ornith-vision 2>/dev/null || true
          NEED_VISION_RECREATE=1
        fi
      else
        NEED_VISION_RECREATE=1
      fi

      # 创建/重建 ornith-vision（Q8_0 + mmproj）
      if [ "$NEED_VISION_RECREATE" -eq 1 ] && [ -f "$MMPROJ_FILE" ] && ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "$ORNITH_Q8"; then
        MODFILE=$(mktemp /tmp/ornith_vision_modelfile.XXXXXX)
        echo "FROM $ORNITH_Q8" > "$MODFILE"
        echo "ADAPTER $MMPROJ_FILE" >> "$MODFILE"
        echo 'PARAMETER temperature 0.6' >> "$MODFILE"
        echo 'PARAMETER top_p 0.95' >> "$MODFILE"
        echo 'PARAMETER top_k 20' >> "$MODFILE"
        if ollama create ornith-vision -f "$MODFILE"; then
          echo "  ✅ ornith-vision 已创建（Q8_0 + mmproj，~10.4GB）"
        else
          echo "  ❌ ornith-vision 创建失败"
          echo "     手动创建:"
          echo "     echo 'FROM $ORNITH_Q8' > /tmp/Modelfile.vision"
          echo "     echo 'ADAPTER $MMPROJ_FILE' >> /tmp/Modelfile.vision"
          echo "     ollama create ornith-vision -f /tmp/Modelfile.vision"
        fi
        rm -f "$MODFILE"
      elif [ "$NEED_VISION_RECREATE" -eq 1 ] && [ ! -f "$MMPROJ_FILE" ]; then
        echo "  ℹ️  无 mmproj 文件，ornith-vision 跳过。建议用 qwen3.5:4b 做视觉模型"
      fi

      # qwen3.5:4b: 4B 视觉模型（3.4GB，轻量级 VLM，差机器推荐）
      pull_if_missing "qwen3.5:4b" "3.4GB，轻量级视觉模型，差机器推荐"

      echo "  ✅ 模型检查完成"
    else
      echo "  ❌ ollama 服务无法启动，请手动运行: ollama serve &"
      echo "     然后重跑此脚本或手动拉模型"
    fi
  fi
fi

# ---------- 1. 装 Python 依赖 ----------
echo "[1/6] 装 Python 依赖"
if [ "$SKIP_PIP" -eq 1 ]; then
  echo "  ⏭️  --skip-pip，跳过"
elif [ "$FORCE" -eq 0 ] && python3 -c "import yaml, PIL, numpy, tqdm" 2>/dev/null; then
  echo "  ℹ️  核心依赖已装，跳过（用 --force 重装）"
else
  cd "$SKILL_DIR"
  python3 -m pip install --upgrade pip
  python3 -m pip install -r requirements.txt
  echo "  ✅ Python 依赖已装"
fi

# ---------- 2. 装 archive 命令 ----------
echo ""
echo "[2/6] 装 archive 命令到 ~/.local/bin"
LOCAL_BIN="${HOME}/.local/bin"
mkdir -p "$LOCAL_BIN"

# 生成 archive wrapper（用目标机器的实际 SKILL_DIR）
ARCHIVE_BIN="$LOCAL_BIN/archive"
cat > "$ARCHIVE_BIN" <<EOF
#!/usr/bin/env bash
# archive — 桌面整理超短入口（由 install_qclaw_integration.sh 生成）
set -e
SKILL_DIR="$SKILL_DIR"

if [ \$# -eq 0 ]; then
  echo "用法: archive \"<意图>\" [目录]"
  echo "  archive \"整理桌面\"            # 整理桌面（默认 ~/Desktop）"
  echo "  archive \"整理桌面\" ~/Downloads # 整理指定目录"
  echo "  archive \"回滚\"                 # 撤销上次操作"
  echo "  archive list                    # 列出所有意图"
  exit 0
fi

cd "\$SKILL_DIR"

if [ "\$1" = "list" ] || [ "\$1" = "--list" ]; then
  exec python3 -m archive_assistant.cli.main auto --list-intents
fi

INTENT="\$1"
ROOT="\${2:-\$HOME/Desktop}"

if [ "\$INTENT" = "回滚" ] || [ "\$INTENT" = "rollback" ]; then
  exec python3 -m archive_assistant.cli.main auto "回滚" --execute --apply
fi

exec python3 -m archive_assistant.cli.main auto "\$INTENT" --root "\$ROOT" --execute --apply
EOF
chmod +x "$ARCHIVE_BIN"
echo "  ✅ archive → $ARCHIVE_BIN"

# 确保 ~/.local/bin 在 PATH
SHELL_RC=""
if [ -n "${ZSH_VERSION:-}" ] || [ "$SHELL" = "/bin/zsh" ] || [ "$SHELL" = "/usr/bin/zsh" ]; then
  SHELL_RC="${HOME}/.zshrc"
elif [ -n "${BASH_VERSION:-}" ] || [ "$SHELL" = "/bin/bash" ]; then
  SHELL_RC="${HOME}/.bashrc"
fi

if [ -n "$SHELL_RC" ]; then
  if ! grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$SHELL_RC" 2>/dev/null; then
    echo '' >> "$SHELL_RC"
    echo '# desktop-archive-assistant' >> "$SHELL_RC"
    echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$SHELL_RC"
    echo "  ✅ PATH 已加入 $SHELL_RC（新终端生效）"
  else
    echo "  ℹ️  PATH 已在 $SHELL_RC 中"
  fi
fi

# 当前 session 也加一下
export PATH="$LOCAL_BIN:$PATH"

# 测试 archive 命令
if command -v archive >/dev/null 2>&1; then
  echo "  ✅ archive 命令可用"
else
  echo "  ⚠️  archive 未在 PATH 中，请开新终端或执行: export PATH=\"$LOCAL_BIN:\$PATH\""
fi

# ---------- 3. 同步 SKILL.md ----------
echo ""
echo "[3/6] 同步 SKILL.md → ${SKILL_SYNC}/"
mkdir -p "$SKILL_SYNC"
cp "$SKILL_DIR/SKILL.md" "$SKILL_SYNC/SKILL.md"
echo "  ✅ SKILL.md 已同步（qclaw 从这里读取 agent prompt）"

# ---------- 4. 创建 agent 目录 + models.json ----------
echo ""
echo "[4/6] 创建 agent 目录 + models.json"
mkdir -p "$AGENT_DIR"

# 检测 ollama 可用模型，动态生成 models.json
generate_models_json() {
  local ollama_host="${OLLAMA_HOST:-http://127.0.0.1:11434}"
  local models_json=""

  if command -v ollama >/dev/null 2>&1; then
    local model_list
    model_list=$(ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | grep -v '^$' || true)
    if [ -n "$model_list" ]; then
      echo "  检测到 ollama 模型："
      echo "$model_list" | sed 's/^/    - /'
      echo ""

      # 用 python 生成 models.json
      models_json=$(OLLAMA_HOST="$ollama_host" MODEL_LIST="$model_list" python3 -c "
import json, os, sys
models = [line.strip() for line in os.environ.get('MODEL_LIST', '').splitlines() if line.strip()]
model_entries = []
for m in models:
    has_vision = any(k in m.lower() for k in ['qwen3.5', 'qwen3.6', 'ornith-vision', 'qwen2.5vl', 'qwen2.5-vl', 'vl'])
    has_think = 'nothink' not in m.lower() and any(k in m.lower() for k in ['qwen3.5', 'qwen3.6', 'ornith'])
    entry = {
        'id': m,
        'name': m,
        'reasoning': has_think,
        'input': ['text', 'image'] if has_vision else ['text'],
        'contextWindow': 16000,
        'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
        'maxTokens': 8192,
        'api': 'ollama'
    }
    model_entries.append(entry)
result = {
    'providers': {
        'qclaw': {
            'baseUrl': 'http://127.0.0.1:19000/proxy/llm',
            'apiKey': '__QCLAW_AUTH_GATEWAY_MANAGED__',
            'api': 'openai-completions',
            'models': [{
                'id': 'modelroute',
                'name': 'modelroute',
                'reasoning': True,
                'input': ['text', 'image'],
                'cost': {'input': 0, 'output': 0, 'cacheRead': 0, 'cacheWrite': 0},
                'contextWindow': 200000,
                'maxTokens': 8192,
                'api': 'openai-completions'
            }]
        },
        'ollama': {
            'baseUrl': 'http://localhost:11434',
            'apiKey': 'ollama-local',
            'api': 'ollama',
            'models': model_entries
        }
    }
}
print(json.dumps(result, indent=2, ensure_ascii=False))
" || true)
    fi
  fi

  if [ -z "$models_json" ]; then
    echo "  ⚠️  未检测到 ollama 或无可用模型，使用默认配置（qwen3.5:4b）"
    models_json=$(cat <<'JSON'
{
  "providers": {
    "qclaw": {
      "baseUrl": "http://127.0.0.1:19000/proxy/llm",
      "apiKey": "__QCLAW_AUTH_GATEWAY_MANAGED__",
      "api": "openai-completions",
      "models": [{
        "id": "modelroute",
        "name": "modelroute",
        "reasoning": true,
        "input": ["text", "image"],
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "contextWindow": 200000,
        "maxTokens": 8192,
        "api": "openai-completions"
      }]
    },
    "ollama": {
      "baseUrl": "http://localhost:11434",
      "apiKey": "ollama-local",
      "api": "ollama",
      "models": [{
        "id": "qwen3.5:4b",
        "name": "Qwen3.5-4B",
        "reasoning": false,
        "input": ["text", "image"],
        "contextWindow": 16000,
        "cost": {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0},
        "maxTokens": 8192,
        "api": "ollama"
      }]
    }
  }
}
JSON
    )
  fi

  echo "$models_json"
}

if [ "$FORCE" -eq 1 ] || [ ! -f "$AGENT_DIR/models.json" ]; then
  generate_models_json > "$AGENT_DIR/models.json"
  echo "  ✅ models.json 已生成 → $AGENT_DIR/models.json"
else
  echo "  ℹ️  models.json 已存在，跳过（用 --force 覆盖）"
fi

# ---------- 5. 创建 workspace + 规则文件 ----------
echo ""
echo "[5/6] 创建 workspace + 规则文件"
mkdir -p "$WORKSPACE/memory"

install_template() {
  local src="$TEMPLATES/$1"
  local dst="$WORKSPACE/$1"
  if [ "$FORCE" -eq 1 ] || [ ! -f "$dst" ]; then
    # 替换路径占位符 __SKILL_DIR__ → 实际路径
    sed "s|__SKILL_DIR__|${SKILL_DIR}|g" "$src" > "$dst"
    echo "  ✅ $1"
  else
    echo "  ℹ️  $1 已存在，跳过（用 --force 覆盖）"
  fi
}

install_template "SOUL.md"
install_template "AGENTS.md"
install_template "IDENTITY.md"
install_template "USER.md"
install_template "MEMORY.md"
install_template "HEARTBEAT.md"
install_template "TOOLS.md"

# ---------- 6. merge openclaw.json ----------
echo ""
echo "[6/6] merge openclaw.json"
if [ "$SKIP_CONFIG" -eq 0 ]; then
  OPENCLAW="$QCLAW_HOME/openclaw.json"

  if [ ! -f "$OPENCLAW" ]; then
    echo "  ⚠️  $OPENCLAW 不存在，跳过（qclaw 首次启动后重跑此脚本）"
  else
    # 备份
    cp "$OPENCLAW" "${OPENCLAW}.bak.$(date +%Y%m%d%H%M%S)"

    # 用 python 安全 merge
    python3 -c "
import json, sys

config_path = sys.argv[1]
workspace = sys.argv[2]
agent_dir = sys.argv[3]
agent_id = 'desktop-archiver'

with open(config_path, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

# 1. 添加 agent 到 agents.list（如果不存在）
agents_list = cfg.setdefault('agents', {}).setdefault('list', [])
exists = any(a.get('id') == agent_id for a in agents_list)
if not exists:
    agents_list.append({
        'id': agent_id,
        'name': '桌面整理助手',
        'workspace': workspace,
        'identity': {
            'name': '桌面整理助手',
            'emoji': '🗂️',
            'avatar': '🗂️'
        },
        'agentDir': agent_dir,
        'reasoningDefault': 'stream',
        'skills': [
            'desktop-archive-assistant',
            'find-skills',
            'qclaw-env',
            'qclaw-rules',
            'qclaw-cron-skill'
        ]
    })
    print('  ✅ agent desktop-archiver 已添加到 agents.list')
else:
    print('  ℹ️  agent desktop-archiver 已存在，跳过')

# 2. 启用 skill
skills = cfg.setdefault('skills', {})
if 'desktop-archive-assistant' not in skills:
    skills['desktop-archive-assistant'] = {'enabled': True}
    print('  ✅ skill desktop-archive-assistant 已启用')
else:
    skills['desktop-archive-assistant']['enabled'] = True
    print('  ℹ️  skill desktop-archive-assistant 已启用（更新）')

with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)

print('  ✅ openclaw.json 已更新（备份已保存）')
" "$OPENCLAW" "$WORKSPACE" "$AGENT_DIR"
  fi
else
  echo "  ⏭️  --skip-config，跳过"
fi

# ---------- 完成 ----------
echo ""
echo "=========================================="
echo "  ✅ 安装完成！"
echo "=========================================="
echo ""
echo "验证步骤："
echo "  1. 确保 ollama 已启动:  ollama serve &"
echo "  2. 测试 archive 命令:   archive list"
echo "  3. 启动/重启 qclaw，选「桌面整理助手」agent"
echo "  4. 说「整理桌面」测试"
echo ""
echo "如需重装:  bash scripts/install_qclaw_integration.sh --force"
