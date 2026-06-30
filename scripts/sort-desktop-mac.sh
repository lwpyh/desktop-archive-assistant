#!/bin/bash
# sort-desktop-mac.sh — macOS 桌面图标排列
# 用法:
#   bash sort-desktop-mac.sh                # 仅紧凑排列（重启 Finder）
#   bash sort-desktop-mac.sh --sort-by ItemType  # 按类型排序+紧凑

SORT_BY="${1:-}"

# macOS: 删除 .DS_Store + 重启 Finder，触发系统自动重新排列图标
# 无需任何权限授权

# 删除桌面的 .DS_Store 文件
DESKTOP="$HOME/Desktop"
if [ -f "$DESKTOP/.DS_Store" ]; then
    rm -f "$DESKTOP/.DS_Store"
fi

# 如果指定了按类型排序，通过 defaults 设置排列方式
if [ "$SORT_BY" = "--sort-by ItemType" ] || [ "$SORT_BY" = "ItemType" ]; then
    # 设置桌面按种类排列
    defaults write com.apple.finder DesktopViewSettings -dict-add "arrangeBy" "kind"
fi

# 重启 Finder
killall Finder 2>/dev/null

echo "{\"status\": \"success\", \"platform\": \"macOS\", \"sort_by\": \"$SORT_BY\"}"
