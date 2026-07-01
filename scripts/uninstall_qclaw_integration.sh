#!/usr/bin/env bash
# uninstall_qclaw_integration.sh — 清理 desktop-archive-assistant 在本机的所有安装痕迹
#
# 与 install_qclaw_integration.sh 一一对应，把它创建的东西删干净，方便干净重装。
#
# 默认（不加任何参数）会清理：
#   1. ~/.local/bin/archive、~/.local/bin/archive-cli 两个 wrapper 命令
#   2. ~/.zshrc / ~/.bashrc 里那段 `# desktop-archive-assistant` PATH（改前自动备份）
#   3. ~/.qclaw/skills/desktop-archive-assistant/       （skill 副本）
#   4. ~/.qclaw/agents/desktop-archiver/                （agent 目录 + models.json）
#   5. ~/.qclaw/workspace-desktop-archiver/             （workspace + 规则文件）
#   6. ~/.qclaw/openclaw.json 里的 desktop-archiver agent + skill 条目（改前自动备份）
#   —— 保留 qclaw 本体、ollama 大模型、Python 依赖、git 仓库（重装最快）
#
# 可选（更彻底）：
#   --purge-models   连 ollama 模型一起删：ornith-vision / ornith-9b:latest /
#                    hf.co/.../Ornith Q8_0 / qwen3.5:4b，以及 ~/ornith-vision-build
#   --purge-qclaw    ⚠️ 连整个 ~/.qclaw 一起删（会影响其他 qclaw agent！慎用）
#   --dry-run        只打印将要删什么，不真正执行
#   --yes            跳过 --purge-qclaw 的二次确认（非交互场景用）
#
# 用法（在 desktop-archive-assistant 目录下）：
#   bash scripts/uninstall_qclaw_integration.sh
#   bash scripts/uninstall_qclaw_integration.sh --purge-models
#   bash scripts/uninstall_qclaw_integration.sh --dry-run
#   bash scripts/uninstall_qclaw_integration.sh --purge-models --purge-qclaw --yes
#
# 幂等：可重复跑，缺失的项会跳过。
set -eo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
QCLAW_HOME="${HOME}/.qclaw"
AGENT_ID="desktop-archiver"
SKILL_NAME="desktop-archive-assistant"
WORKSPACE="${QCLAW_HOME}/workspace-${AGENT_ID}"
AGENT_DIR="${QCLAW_HOME}/agents/${AGENT_ID}"
SKILL_SYNC="${QCLAW_HOME}/skills/${SKILL_NAME}"
LOCAL_BIN="${HOME}/.local/bin"
MMPROJ_DIR="${HOME}/ornith-vision-build"
ORNITH_Q8="hf.co/deepreinforce-ai/Ornith-1.0-9B-GGUF:Q8_0"

PURGE_MODELS=0
PURGE_QCLAW=0
DRY_RUN=0
ASSUME_YES=0
for arg in "$@"; do
  case "$arg" in
    --purge-models) PURGE_MODELS=1 ;;
    --purge-qclaw)  PURGE_QCLAW=1 ;;
    --dry-run)      DRY_RUN=1 ;;
    --yes)          ASSUME_YES=1 ;;
    *) echo "unknown arg: $arg"; exit 1 ;;
  esac
done

echo "=========================================="
echo "  desktop-archive-assistant 卸载 / 清理"
echo "=========================================="
echo "QCLAW_HOME: $QCLAW_HOME"
[ "$DRY_RUN" -eq 1 ] && echo "模式:       DRY-RUN（只打印，不删除）"
echo ""

# 统一的删除辅助：dry-run 时只打印
run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    [dry-run] $*"
  else
    eval "$@"
  fi
}

removed=0
skipped=0
note_removed() { echo "  ✅ 已删除: $1"; removed=$((removed+1)); }
note_skip()    { echo "  ℹ️  不存在，跳过: $1"; skipped=$((skipped+1)); }

# ---------- 1. 删 wrapper 命令 ----------
echo "[1] 删除 wrapper 命令"
for bin in "$LOCAL_BIN/archive" "$LOCAL_BIN/archive-cli"; do
  if [ -e "$bin" ]; then
    run "rm -f \"$bin\""
    note_removed "$bin"
  else
    note_skip "$bin"
  fi
done

# ---------- 2. 清 shell rc 里的 PATH 段 ----------
echo ""
echo "[2] 清理 shell rc 里的 PATH 段（# desktop-archive-assistant）"
clean_shell_rc() {
  local rc="$1"
  [ -f "$rc" ] || { note_skip "$rc（文件不存在）"; return 0; }
  if ! grep -q '# desktop-archive-assistant' "$rc"; then
    note_skip "$rc（无本工具的 PATH 段）"
    return 0
  fi
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    [dry-run] 备份 $rc 并删除 '# desktop-archive-assistant' 及其下方 export PATH 行"
    return 0
  fi
  cp "$rc" "${rc}.bak.$(date +%Y%m%d%H%M%S)"
  # 删除注释标记行 + 紧随其后的那一行 export PATH（成对由 install 脚本写入）
  awk '
    /^# desktop-archive-assistant$/ { skip=1; next }
    skip==1 && /^export PATH="\$HOME\/\.local\/bin:\$PATH"$/ { skip=0; next }
    skip==1 { skip=0 }
    { print }
  ' "$rc" > "${rc}.tmp" && mv "${rc}.tmp" "$rc"
  note_removed "$rc 中的 PATH 段（原文件已备份为 ${rc}.bak.*）"
}
clean_shell_rc "${HOME}/.zshrc"
clean_shell_rc "${HOME}/.bashrc"

# ---------- 3. 删 qclaw skill / agent / workspace 目录 ----------
echo ""
echo "[3] 删除 qclaw 里的 skill / agent / workspace 目录"
for d in "$SKILL_SYNC" "$AGENT_DIR" "$WORKSPACE"; do
  if [ -d "$d" ]; then
    run "rm -rf \"$d\""
    note_removed "$d"
  else
    note_skip "$d"
  fi
done

# ---------- 4. 从 openclaw.json 移除 agent + skill 条目 ----------
echo ""
echo "[4] 从 openclaw.json 移除 desktop-archiver agent + skill 条目"
OPENCLAW="$QCLAW_HOME/openclaw.json"
if [ ! -f "$OPENCLAW" ]; then
  note_skip "$OPENCLAW（不存在）"
elif [ "$PURGE_QCLAW" -eq 1 ]; then
  echo "  ℹ️  已选 --purge-qclaw，将在第 [6] 步整体删除 ~/.qclaw，这里跳过单独修改"
elif [ "$DRY_RUN" -eq 1 ]; then
  echo "    [dry-run] 备份 openclaw.json 并移除 agents.list 里 id=$AGENT_ID 的项 + skills.$SKILL_NAME"
else
  cp "$OPENCLAW" "${OPENCLAW}.bak.$(date +%Y%m%d%H%M%S)"
  python3 -c "
import json, sys
p, agent_id, skill_name = sys.argv[1], sys.argv[2], sys.argv[3]
with open(p, 'r', encoding='utf-8') as f:
    cfg = json.load(f)
changed = False
alist = cfg.get('agents', {}).get('list', [])
new = [a for a in alist if a.get('id') != agent_id]
if len(new) != len(alist):
    cfg['agents']['list'] = new
    print('  ✅ 已移除 agents.list 中的 %s' % agent_id); changed = True
else:
    print('  ℹ️  agents.list 中无 %s，跳过' % agent_id)
skills = cfg.get('skills', {})
if skill_name in skills:
    del skills[skill_name]
    print('  ✅ 已移除 skills.%s' % skill_name); changed = True
else:
    print('  ℹ️  skills 中无 %s，跳过' % skill_name)
if changed:
    with open(p, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)
    print('  ✅ openclaw.json 已更新（原文件已备份）')
else:
    print('  ℹ️  openclaw.json 无需改动')
" "$OPENCLAW" "$AGENT_ID" "$SKILL_NAME"
fi

# ---------- 5. （可选）删 ollama 模型 ----------
echo ""
echo "[5] ollama 模型清理"
if [ "$PURGE_MODELS" -eq 0 ]; then
  echo "  ⏭️  未加 --purge-models，保留 ollama 模型（重装时无需重新下载，最快）"
else
  if command -v ollama >/dev/null 2>&1; then
    for m in "ornith-vision" "ornith-9b:latest" "$ORNITH_Q8" "qwen3.5:4b"; do
      if ollama list 2>/dev/null | awk '{print $1}' | grep -qF "$m"; then
        run "ollama rm \"$m\" >/dev/null 2>&1 || true"
        note_removed "ollama 模型 $m"
      else
        note_skip "ollama 模型 $m"
      fi
    done
  else
    echo "  ⚠️  未找到 ollama 命令，跳过模型删除"
  fi
  if [ -d "$MMPROJ_DIR" ]; then
    run "rm -rf \"$MMPROJ_DIR\""
    note_removed "$MMPROJ_DIR（mmproj 构建目录）"
  else
    note_skip "$MMPROJ_DIR"
  fi
fi

# ---------- 6. （可选）删整个 ~/.qclaw ----------
echo ""
echo "[6] qclaw 本体清理"
if [ "$PURGE_QCLAW" -eq 0 ]; then
  echo "  ⏭️  未加 --purge-qclaw，保留 ~/.qclaw（install 脚本需要它存在）"
else
  echo "  ⚠️  --purge-qclaw：将删除整个 $QCLAW_HOME —— 这会移除【所有】qclaw agent/配置！"
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "    [dry-run] rm -rf \"$QCLAW_HOME\""
  else
    if [ "$ASSUME_YES" -ne 1 ]; then
      printf "  确认删除整个 ~/.qclaw？输入大写 YES 继续: "
      read -r ans
      if [ "$ans" != "YES" ]; then
        echo "  ✋ 已取消 ~/.qclaw 删除（其余清理照常完成）"
        PURGE_QCLAW=0
      fi
    fi
    if [ "$PURGE_QCLAW" -eq 1 ]; then
      rm -rf "$QCLAW_HOME"
      note_removed "$QCLAW_HOME（整个 qclaw 目录）"
    fi
  fi
fi

# ---------- 完成 ----------
echo ""
echo "=========================================="
echo "  清理完成：删除 $removed 项，跳过 $skipped 项"
[ "$DRY_RUN" -eq 1 ] && echo "  （DRY-RUN，实际未删除任何东西）"
echo "=========================================="
echo ""
echo "接下来重装（在仓库目录）："
echo "  cd $SKILL_DIR && git pull && bash scripts/install_qclaw_integration.sh --force"
echo ""
echo "提醒："
echo "  - 全新/清空后首次装：先从 qclaw「专家广场」装任意一个专家，再跑 install（否则 agent 会被 qclaw 启动时覆盖）。"
echo "  - 重装后重启 qclaw、开新对话，才会加载到最新 SKILL.md。"
