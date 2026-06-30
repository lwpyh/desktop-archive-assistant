#!/usr/bin/env bash
# install_qclaw_integration.sh — 一键把 desktop-archive-assistant 装好并集成到 qclaw
#
# 在目标机器上跑一次，自动完成：
#   1. 装 Python 依赖（pip install -r requirements.txt）
#   2. 装 archive 命令到 ~/.local/bin（自动改 SKILL_DIR 路径 + 加到 PATH）
#   3. 同步 SKILL.md → ~/.qclaw/skills/desktop-archive-assistant/
#   4. 创建 agent 目录 + models.json（检测 ollama 模型自动生成）
#   5. 创建 workspace + 规则文件（SOUL.md/AGENTS.md/IDENTITY.md 等，路径自动替换）
#   6. 安全 merge openclaw.json（添加 desktop-archiver agent + 启用 skill）
#
# 用法（在 desktop-archive-assistant 目录下）：
#   bash scripts/install_qclaw_integration.sh              # 默认检测
#   bash scripts/install_qclaw_integration.sh --force       # 覆盖已有规则文件 + 重装依赖
#   bash scripts/install_qclaw_integration.sh --skip-pip    # 跳过 pip 安装
#   bash scripts/install_qclaw_integration.sh --skip-config # 跳过 openclaw.json merge
#
# 幂等：可重复跑，不会破坏已有配置。
set -euo pipefail

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
for arg in "$@"; do
  case "$arg" in
    --force)       FORCE=1 ;;
    --skip-pip)    SKIP_PIP=1 ;;
    --skip-config) SKIP_CONFIG=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

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
