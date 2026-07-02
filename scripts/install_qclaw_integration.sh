#!/usr/bin/env bash
# install_qclaw_integration.sh — 一键把 desktop-archive-assistant 装好并集成到 qclaw
#
# 在目标机器上跑一次，自动完成：
#   0.  检查并拉取 ollama 模型（ornith-9b Q8_0 9.5GB + ornith-vision + qwen3.5:4b）
#   0.2 探测「要改造的目标 agent」（见下方 ★核心原理）
#   1.  装 Python 依赖（pip install -r requirements.txt）
#   2.  装 archive + archive-cli 命令到 ~/.local/bin（自动填本机 SKILL_DIR 路径 + 加到 PATH）
#       - archive     : 意图串入口（走 auto 路由，一句话整理）
#       - archive-cli : 子命令透传入口（路径无关，助手精确控制子命令+flag，跨机器通用）
#   3.  同步 SKILL.md → ~/.qclaw/skills/desktop-archive-assistant/
#   4.  目标 agent 的 models.json（缺失才生成，已有则保留不动）
#   5.  把目标 agent 的 workspace 人设改造成「桌面整理助手」
#       （SOUL.md/AGENTS.md/IDENTITY.md 覆盖，原文件自动备份；USER/MEMORY 等保留）
#   6.  改造 openclaw.json 里的目标 agent（改名「桌面整理助手」+ 挂 desktop-archive-assistant 技能）
#   7.  安装自检（verify）：逐项检查 依赖/代码/archive命令/SKILL.md/目标agent/skill/模型
#
# ★ 核心原理（血泪实证，务必先读）：
#   qclaw 里 agent 能否「重启后存活」，取决于它是否被 qclaw 正规创建过（有注册出身）。
#   纯脚本 new 一个自定义 id 的 agent 直接落盘（改 openclaw.json / 伪造 workspace-state /
#   伪造 sync_state 全都没用）→ qclaw 启动 reconciliation 判定「本地凭空多出、无正规出身」→
#   连 openclaw 带 sync_state 一起清除（这就是远端 desktop-archiver 反复消失的真正根因，已实证）。
#   ✅ 正确做法 = 改造一个「已由 qclaw 正规创建」的 agent：改名 / 挂技能 / 写 SOUL 都是对
#      已注册 agent 的字段修改，reconciliation 视为「已知 agent 的正常变更」，重启保留、
#      name 不被回滚（已在远端实证）。所以本脚本不再新建 agent，而是改造你手动建好的 agent。
#
# 用法（在 desktop-archive-assistant 目录下）：
#   ① 先在 qclaw 里手动创建一个智能体（随便建个「智能Agent」，让它获得正规注册出身）
#   ② 再跑本脚本，它会自动探测并把那个 agent 改造成「桌面整理助手」：
#   bash scripts/install_qclaw_integration.sh                 # 自动探测目标 agent
#   bash scripts/install_qclaw_integration.sh --agent-id=xxx  # 指定要改造哪个 agent
#   bash scripts/install_qclaw_integration.sh --force         # 重装依赖（人设文件总会覆盖）
#   bash scripts/install_qclaw_integration.sh --skip-pip      # 跳过 pip 安装
#   bash scripts/install_qclaw_integration.sh --skip-models   # 跳过 ollama 模型拉取
#   bash scripts/install_qclaw_integration.sh --skip-config   # 跳过 openclaw.json 改造
#
# 幂等：可重复跑；已改造过的 agent 会被自动识别复用，不会重复建。
set -eo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMPLATES="$SKILL_DIR/scripts/qclaw_templates"
QCLAW_HOME="${HOME}/.qclaw"
SKILL_ID="desktop-archive-assistant"
SKILL_SYNC="${QCLAW_HOME}/skills/${SKILL_ID}"
OPENCLAW="${QCLAW_HOME}/openclaw.json"
# 目标 agent：要被改造成「桌面整理助手」的、已由 qclaw 正规创建的 agent。
# 由 --agent-id 指定，或在第 0.2 步自动探测后填充；WORKSPACE/AGENT_DIR 随之确定。
TARGET_AGENT_ID=""
WORKSPACE=""
AGENT_DIR=""

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
    --agent-id=*)   TARGET_AGENT_ID="${arg#*=}" ;;
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

# ---------- 0.2 探测要改造的目标 agent ----------
# 见头部 ★核心原理：不新建 agent，只改造一个已由 qclaw 正规创建的 agent。
# 探测优先级：
#   1) --agent-id=xxx 显式指定
#   2) 幂等：已有 name==「桌面整理助手」的 agent → 复用（重复跑不会认错人）
#   3) 自动挑：排除内置 main 和专家广场(op-)，取剩下第一个（通常是你手动建的智能Agent）
# 定位后从其 openclaw 条目读 workspace/agentDir（缺字段则回退默认路径）。
echo "[0.2] 探测要改造的目标 agent"
if [ ! -f "$OPENCLAW" ]; then
  echo "  ❌ 未找到 $OPENCLAW"
  echo "     请先启动一次 qclaw，并在 UI 里手动创建一个智能体，再重跑本脚本。"
  exit 1
fi

DETECT=$(python3 - "$OPENCLAW" "$TARGET_AGENT_ID" "$QCLAW_HOME" <<'PY'
import json, sys, os
openclaw, want_id, qhome = sys.argv[1:4]
with open(openclaw, encoding='utf-8') as f:
    cfg = json.load(f)
alist = cfg.get('agents', {}).get('list', [])

def resolve(a):
    aid = a.get('id')
    ws = a.get('workspace') or os.path.join(qhome, 'workspace-' + aid)
    ad = a.get('agentDir') or os.path.join(qhome, 'agents', aid, 'agent')
    return aid, ws, ad

target = None
if want_id:                                   # 1) 显式指定
    target = next((a for a in alist if a.get('id') == want_id), None)
    if target is None:
        print("ERR\t指定的 --agent-id=%s 不在 openclaw" % want_id); sys.exit(0)
if target is None:                            # 2) 幂等：已改造过
    target = next((a for a in alist if a.get('name') == '桌面整理助手'), None)
if target is None:                            # 3) 自动挑（排除 main / op-）
    for a in alist:
        aid = a.get('id', '')
        if aid == 'main' or aid.startswith('op-'):
            continue
        target = a; break
if target is None:
    print("NONE\t"); sys.exit(0)
aid, ws, ad = resolve(target)
print("OK\t%s\t%s\t%s\t%s" % (aid, ws, ad, target.get('name', '')))
PY
)

STATUS=$(printf '%s' "$DETECT" | cut -f1)
case "$STATUS" in
  OK)
    TARGET_AGENT_ID=$(printf '%s' "$DETECT" | cut -f2)
    WORKSPACE=$(printf '%s' "$DETECT" | cut -f3)
    AGENT_DIR=$(printf '%s' "$DETECT" | cut -f4)
    OLD_NAME=$(printf '%s' "$DETECT" | cut -f5)
    echo "  ✅ 目标 agent: $TARGET_AGENT_ID（原名: ${OLD_NAME:-未命名}）"
    echo "     workspace: $WORKSPACE"
    echo "     agentDir : $AGENT_DIR"
    ;;
  NONE)
    echo "  ❌ 没找到可改造的 agent（除内置 main 与专家广场 op- 外，没有其它 agent）"
    echo "     请先在 qclaw UI 手动创建一个智能体（随便建个「智能Agent」，让它获得正规注册出身），"
    echo "     再重跑本脚本；或用 --agent-id=<id> 指定要改造哪个。"
    exit 1
    ;;
  *)
    echo "  ❌ $(printf '%s' "$DETECT" | cut -f2)"
    echo "     用 --agent-id=<id> 指定一个存在的 agent。"
    exit 1
    ;;
esac

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

      # 检查 ornith-9b:latest 是否存在、是 Q8_0 量化、且用简单模板
      # ⚠️ 关键：不能只查量化！GGUF 自带的复杂模板含 multi_step_tool，
      # 在多轮工具调用时会报 400 "No user query found in messages"。
      # 旧脚本只查量化就跳过，导致坏模板永远留着修不好——这里必须同时查模板。
      NEED_RECREATE=0
      if ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "ornith-9b:latest"; then
        # 检查现有 ornith-9b:latest 的量化版本
        EXISTING_QUANT=$(ollama show ornith-9b:latest 2>/dev/null | grep -i "quantization" | awk '{print $2}' || true)
        # 检查真正生效的模板（--modelfile 的 TEMPLATE 段，而非 --template）
        # ⚠️ 注意：ollama show --template 显示的是 GGUF 内嵌 chat_template，
        # 它永远含 multi_step_tool（本地能工作的机器也一样），不能用来判断！
        # 真正生效的是 Modelfile 里的 TEMPLATE，用 --modelfile 才看得到。
        BAD_TEMPLATE=0
        if ollama show ornith-9b:latest --modelfile 2>/dev/null | grep -q "multi_step_tool"; then
          BAD_TEMPLATE=1
        fi
        if [ "$EXISTING_QUANT" = "Q8_0" ] && [ "$BAD_TEMPLATE" -eq 0 ]; then
          echo "  ✅ ornith-9b:latest 已是 Q8_0 量化 + 简单模板，跳过"
        else
          if [ "$BAD_TEMPLATE" -eq 1 ]; then
            echo "  ⚠️  ornith-9b:latest 用了复杂模板（含 multi_step_tool，会报 400），将覆盖重建"
          else
            echo "  ⚠️  ornith-9b:latest 量化版本为 $EXISTING_QUANT（非 Q8_0），将覆盖重建"
          fi
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
        # 显式指定简单 TEMPLATE，覆盖 GGUF 里的复杂模板
        # Qwen3.5 的默认 template 有 multi_step_tool 检查，会在多轮工具调用时报错
        # "No user query found in messages"
        echo 'TEMPLATE """{{ if .System }}<|im_start|>system' >> "$MODFILE"
        echo '{{ .System }}<|im_end|>' >> "$MODFILE"
        echo '{{ end }}{{ if .Prompt }}<|im_start|>user' >> "$MODFILE"
        echo '{{ .Prompt }}<|im_end|>' >> "$MODFILE"
        echo '{{ end }}<|im_start|>assistant' >> "$MODFILE"
        echo '{{ .Response }}<|im_end|>"""' >> "$MODFILE"
        if ollama create ornith-9b:latest -f "$MODFILE"; then
          echo "  ✅ ornith-9b:latest 已创建（Q8_0，9.5GB，简单模板）"
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
      # 注意：ollama list 显示为 "ornith-vision:latest"，用前缀匹配
      NEED_VISION_RECREATE=0
      if ollama list 2>/dev/null | awk '{print $1}' | grep -q "^ornith-vision"; then
        # 同时检查 vision 能力 + 模板（复杂模板含 multi_step_tool 会报 400）
        # 同 ornith-9b：用 --modelfile 查真正生效的模板，不用 --template（GGUF内嵌，永远命中）
        VISION_BAD_TEMPLATE=0
        if ollama show ornith-vision --modelfile 2>/dev/null | grep -q "multi_step_tool"; then
          VISION_BAD_TEMPLATE=1
        fi
        if ollama show ornith-vision 2>/dev/null | grep -qi "vision" && [ "$VISION_BAD_TEMPLATE" -eq 0 ]; then
          echo "  ✅ ornith-vision 已存在（带 vision 能力 + 简单模板），跳过"
        else
          if [ "$VISION_BAD_TEMPLATE" -eq 1 ]; then
            echo "  ⚠️  ornith-vision 用了复杂模板（含 multi_step_tool，会报 400），将覆盖重建"
          else
            echo "  ⚠️  ornith-vision 不带 vision 能力，将覆盖重建"
          fi
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
        # 显式指定简单 TEMPLATE，覆盖 GGUF 里的复杂模板
        # Qwen3.5 的默认 template 有 multi_step_tool 检查，会在多轮工具调用时报错
        # "No user query found in messages"
        echo 'TEMPLATE """{{ if .System }}<|im_start|>system' >> "$MODFILE"
        echo '{{ .System }}<|im_end|>' >> "$MODFILE"
        echo '{{ end }}{{ if .Prompt }}<|im_start|>user' >> "$MODFILE"
        echo '{{ .Prompt }}<|im_end|>' >> "$MODFILE"
        echo '{{ end }}<|im_start|>assistant' >> "$MODFILE"
        echo '{{ .Response }}<|im_end|>"""' >> "$MODFILE"
        echo 'PARAMETER temperature 0.6' >> "$MODFILE"
        echo 'PARAMETER top_p 0.95' >> "$MODFILE"
        echo 'PARAMETER top_k 20' >> "$MODFILE"
        if ollama create ornith-vision -f "$MODFILE"; then
          echo "  ✅ ornith-vision 已创建（Q8_0 + mmproj，~10.4GB，简单模板）"
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

# 生成 archive-cli 透传 wrapper（路径无关：自动 cd 到本机 SKILL_DIR，把所有参数原样转发给模块）
# 目的：让 qclaw 助手用 `archive-cli <子命令> <目录> [flag]` 精确控制，
#       无需知道仓库路径、无需 cd、无需摸 python -m 的调用方式（跨机器通用）。
ARCHIVE_CLI_BIN="$LOCAL_BIN/archive-cli"
cat > "$ARCHIVE_CLI_BIN" <<EOF
#!/usr/bin/env bash
# archive-cli — 桌面整理 CLI 透传入口（由 install_qclaw_integration.sh 生成）
# 用法: archive-cli <子命令> <目录> [参数...]，等价于在仓库目录里跑
#        python3 -m archive_assistant.cli.main <子命令> <目录> [参数...]
set -e
SKILL_DIR="$SKILL_DIR"
cd "\$SKILL_DIR"
exec python3 -m archive_assistant.cli.main "\$@"
EOF
chmod +x "$ARCHIVE_CLI_BIN"
echo "  ✅ archive-cli → $ARCHIVE_CLI_BIN"

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

# 测试 archive / archive-cli 命令
if command -v archive >/dev/null 2>&1 && command -v archive-cli >/dev/null 2>&1; then
  echo "  ✅ archive / archive-cli 命令可用"
else
  echo "  ⚠️  archive/archive-cli 未在 PATH 中，请开新终端或执行: export PATH=\"$LOCAL_BIN:\$PATH\""
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
      # ⚠️ 这些是日志，必须走 stderr(>&2)！函数 stdout 会被重定向进 models.json，
      # 若走 stdout 会把日志文本混入 JSON 顶部导致文件损坏(曾踩坑:193字节垃圾前缀)。
      echo "  检测到 ollama 模型：" >&2
      echo "$model_list" | sed 's/^/    - /' >&2
      echo "" >&2

      # 用 python 生成 models.json
      models_json=$(OLLAMA_HOST="$ollama_host" MODEL_LIST="$model_list" python3 -c "
import json, os, sys
models = [line.strip() for line in os.environ.get('MODEL_LIST', '').splitlines() if line.strip()]
model_entries = []
for m in models:
    has_vision = any(k in m.lower() for k in ['qwen3.5', 'qwen3.6', 'ornith-vision', 'qwen2.5vl', 'qwen2.5-vl', 'vl'])
    has_think = 'nothink' not in m.lower() and any(k in m.lower() for k in ['qwen3.5', 'qwen3.6', 'ornith'])
    # ornith 系列上下文窗设 32768（与本地一致，避免长会话截断丢失 SKILL.md 铁律），其余 16000
    ctx = 32768 if 'ornith' in m.lower() else 16000
    entry = {
        'id': m,
        'name': m,
        'reasoning': has_think,
        'input': ['text', 'image'] if has_vision else ['text'],
        'contextWindow': ctx,
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
    echo "  ⚠️  未检测到 ollama 或无可用模型，使用默认配置（qwen3.5:4b）" >&2
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

# 人设/规则文件：改造语义下总是覆盖成「桌面整理助手」的版本（原文件自动备份，防误伤）
install_persona() {
  local src="$TEMPLATES/$1"
  local dst="$WORKSPACE/$1"
  if [ -f "$dst" ]; then
    cp "$dst" "${dst}.bak.$(date +%Y%m%d%H%M%S)"
    sed "s|__SKILL_DIR__|${SKILL_DIR}|g" "$src" > "$dst"
    echo "  ✅ $1（人设已覆盖，原文件已备份）"
  else
    sed "s|__SKILL_DIR__|${SKILL_DIR}|g" "$src" > "$dst"
    echo "  ✅ $1（人设已写入）"
  fi
}

# 用户数据文件：缺失才建，已有则保留（不覆盖用户积累的记忆/偏好）
install_userdata() {
  local src="$TEMPLATES/$1"
  local dst="$WORKSPACE/$1"
  if [ ! -f "$dst" ]; then
    sed "s|__SKILL_DIR__|${SKILL_DIR}|g" "$src" > "$dst"
    echo "  ✅ $1（新建）"
  else
    echo "  ℹ️  $1 已存在，保留用户数据（不覆盖）"
  fi
}

install_persona  "SOUL.md"
install_persona  "AGENTS.md"
install_persona  "IDENTITY.md"
install_persona  "HEARTBEAT.md"
install_persona  "TOOLS.md"
install_userdata "USER.md"
install_userdata "MEMORY.md"

# ---------- 6. merge openclaw.json ----------
echo ""
echo "[6/6] 改造 openclaw.json：把 $TARGET_AGENT_ID 变成「桌面整理助手」"
if [ "$SKIP_CONFIG" -eq 0 ]; then
  if [ ! -f "$OPENCLAW" ]; then
    echo "  ⚠️  $OPENCLAW 不存在，跳过"
  else
    # 备份
    cp "$OPENCLAW" "${OPENCLAW}.bak.$(date +%Y%m%d%H%M%S)"

    # 改造目标 agent（不新建！只改已注册 agent 的字段：改名 + 身份 + 挂技能）
    python3 - "$OPENCLAW" "$TARGET_AGENT_ID" "$SKILL_ID" <<'PY'
import json, sys

config_path, target_id, skill_id = sys.argv[1:4]
with open(config_path, 'r', encoding='utf-8') as f:
    cfg = json.load(f)

alist = cfg.setdefault('agents', {}).setdefault('list', [])
a = next((x for x in alist if x.get('id') == target_id), None)
if a is None:
    print(f'  ❌ 目标 agent {target_id} 不在 openclaw，改造中止')
    sys.exit(1)

# 1) 改名 + 身份 → 桌面整理助手
a['name'] = '桌面整理助手'
idn = a.setdefault('identity', {})
idn['name'] = '桌面整理助手'
idn['emoji'] = '🗂️'
idn['avatar'] = '🗂️'
a.setdefault('reasoningDefault', 'stream')

# 2) 挂技能（保留 agent 原有其他技能，去重，放最前）
skills = a.setdefault('skills', [])
if skill_id not in skills:
    skills.insert(0, skill_id)
    print(f'  ✅ 已挂载技能 {skill_id}')
else:
    print(f'  ℹ️  技能 {skill_id} 已挂载')

# 3) 全局启用该 skill
sk = cfg.setdefault('skills', {})
sk[skill_id] = {'enabled': True}

with open(config_path, 'w', encoding='utf-8') as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)

print(f'  ✅ agent {target_id} 已改造为「桌面整理助手」（name/identity/skill 已更新，备份已保存）')
PY
  fi
else
  echo "  ⏭️  --skip-config，跳过"
fi

# ---------- 7. 安装自检（verify） ----------
echo ""
echo "[7/7] 安装自检（verify）—— 逐项检查是否真的装好"
FAIL=0
WARN=0
pass(){ echo "  ✅ $1"; }
fail(){ echo "  ❌ $1"; FAIL=$((FAIL+1)); }
warn(){ echo "  ⚠️  $1"; WARN=$((WARN+1)); }

# 1) Python 核心依赖可导入
if python3 -c "import yaml, PIL, numpy, tqdm" 2>/dev/null; then
  pass "Python 核心依赖 (PyYAML/Pillow/numpy/tqdm) 已就绪"
else
  fail "Python 核心依赖缺失 → 修复: cd $SKILL_DIR && python3 -m pip install -r requirements.txt（或重跑本脚本 --force）"
fi

# 2) archive_assistant 包可导入（代码是否真的在位）
if ( cd "$SKILL_DIR" && python3 -c "import archive_assistant" ) 2>/dev/null; then
  pass "archive_assistant 包可导入（代码就位）"
else
  fail "archive_assistant 包无法导入 → 确认仓库完整: cd $SKILL_DIR && git pull"
fi

# 3) archive 命令已安装
if [ -x "$ARCHIVE_BIN" ]; then
  pass "archive 命令已安装: $ARCHIVE_BIN"
else
  fail "archive 命令缺失 → 重跑本脚本（第 2 步会生成 $ARCHIVE_BIN）"
fi

# 3b) archive-cli 透传命令已安装（路径无关入口，SKILL.md 里的命令都靠它）
if [ -x "$ARCHIVE_CLI_BIN" ]; then
  pass "archive-cli 透传命令已安装: $ARCHIVE_CLI_BIN"
else
  fail "archive-cli 缺失 → 重跑本脚本（第 2 步会生成 $ARCHIVE_CLI_BIN）"
fi

# 4) 端到端：archive CLI 真能起来（列出意图，只读、不动文件）
if ( cd "$SKILL_DIR" && python3 -m archive_assistant.cli.main auto --list-intents ) >/dev/null 2>&1; then
  pass "archive CLI 端到端可运行（archive list 通过）"
else
  fail "archive CLI 无法运行 → 手动排查: cd $SKILL_DIR && python3 -m archive_assistant.cli.main auto --list-intents"
fi

# 5) SKILL.md 已同步到 qclaw（agent prompt 来源）
if [ -f "$SKILL_SYNC/SKILL.md" ]; then
  pass "SKILL.md 已同步到 qclaw: $SKILL_SYNC/SKILL.md"
else
  fail "SKILL.md 未同步 → 重跑本脚本（第 3 步会 cp 到 $SKILL_SYNC）"
fi

# 6) models.json 存在且为合法 JSON
if [ -f "$AGENT_DIR/models.json" ] && python3 -c "import json;json.load(open('$AGENT_DIR/models.json'))" 2>/dev/null; then
  pass "models.json 存在且为合法 JSON"
else
  fail "models.json 缺失或损坏 → 重跑本脚本 --force（第 4 步重建）"
fi

# 7) workspace 规则文件已就位
if [ -f "$WORKSPACE/SOUL.md" ]; then
  pass "workspace 规则文件已就位: $WORKSPACE"
else
  fail "workspace 规则文件缺失 → 重跑本脚本 --force（第 5 步生成）"
fi

# 8) openclaw.json 里目标 agent 已被改造成「桌面整理助手」且 skill 已挂载启用
if [ -f "$OPENCLAW" ]; then
  AGENT_CHECK=$(python3 - "$OPENCLAW" "$TARGET_AGENT_ID" "$SKILL_ID" <<'PY' 2>/dev/null || true
import json, sys
openclaw, tid, sid = sys.argv[1:4]
cfg = json.load(open(openclaw))
alist = cfg.get('agents', {}).get('list', [])
a = next((x for x in alist if x.get('id') == tid), None)
if a is None:
    print('NOAGENT'); sys.exit(0)
name_ok = (a.get('name') == '桌面整理助手')
skill_on_agent = sid in (a.get('skills') or [])
skill_enabled = bool(cfg.get('skills', {}).get(sid, {}).get('enabled'))
print('OK' if (name_ok and skill_on_agent and skill_enabled)
      else ('NONAME' if not name_ok else 'NOSKILL'))
PY
)
  [ -z "$AGENT_CHECK" ] && AGENT_CHECK="ERR"
  case "$AGENT_CHECK" in
    OK)      pass "openclaw.json：$TARGET_AGENT_ID 已改造为「桌面整理助手」且 skill 已启用" ;;
    NOAGENT) fail "openclaw.json 里找不到目标 agent $TARGET_AGENT_ID → 确认它存在，或用 --agent-id 指定" ;;
    NONAME)  fail "目标 agent 未改名成「桌面整理助手」→ 重跑本脚本（第 6 步）" ;;
    NOSKILL) fail "技能 $SKILL_ID 未挂载/未启用 → 重跑本脚本（第 6 步）" ;;
    *)       fail "openclaw.json 解析失败 → 检查文件是否损坏: $OPENCLAW" ;;
  esac
else
  fail "openclaw.json 不存在 → 先启动一次 qclaw 生成配置，再重跑本脚本"
fi

# 9) ollama 服务 + 关键模型（WARN 级：缺了整理会降级为规则模式，仍可跑，不致命）
if command -v ollama >/dev/null 2>&1 && ollama list >/dev/null 2>&1; then
  pass "ollama 服务可达"
  for m in "ornith-9b:latest" "qwen3.5:4b"; do
    if ollama list 2>/dev/null | awk '{print $1}' | grep -qFx "$m"; then
      pass "模型就绪: $m"
    else
      warn "模型缺失: $m（整理会降级为规则模式仍可跑；补装: ollama pull $m，或重跑本脚本）"
    fi
  done
else
  warn "ollama 服务不可达（整理将降级为规则模式；启动: ollama serve & 后 pull 模型，或重跑本脚本）"
fi

# ---------- 完成 ----------
echo ""
echo "=========================================="
if [ "$FAIL" -eq 0 ]; then
  echo "  ✅ 安装完成，自检全部通过！"
  if [ "$WARN" -gt 0 ]; then
    echo "  （有 $WARN 项警告，见上；通常不影响基本整理，仅影响 VLM 智能识别）"
  fi
else
  echo "  ❌ 安装未完全通过：$FAIL 项失败$( [ "$WARN" -gt 0 ] && echo "，$WARN 项警告" )"
  echo "     请按上面每个 ❌ 后面的提示修复对应那一步，然后重跑本脚本。"
fi
echo "=========================================="
echo ""
echo "验证步骤（自检已自动跑过一遍）："
echo "  1. 确保 ollama 已启动:  ollama serve &"
echo "  2. 测试 archive 命令:   archive list"
echo "  3. 启动/重启 qclaw，选「桌面整理助手」agent"
echo "  4. 说「整理桌面」测试"
echo ""
echo "远端一键重装（拉最新代码后一条命令搞定）："
echo "  cd $SKILL_DIR && git pull && bash scripts/install_qclaw_integration.sh --force"
echo ""
echo "如需重装:  bash scripts/install_qclaw_integration.sh --force"

# 自检有失败项则以非零退出码结束，便于「一条命令」链式调用感知失败
[ "$FAIL" -eq 0 ] || exit 2
