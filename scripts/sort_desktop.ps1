# sort_desktop.ps1 — 桌面图标排列脚本
# 用法:
#   powershell -ExecutionPolicy Bypass -File sort_desktop.ps1              # 仅紧凑排列
#   powershell -ExecutionPolicy Bypass -File sort_desktop.ps1 -SortBy ItemType  # 按类型排序+紧凑

param(
    [string]$SortBy = ""
)

# 通过 COM 接口 IFolderView2 排列桌面图标
# 方法：切换自动排列 → 触发紧凑排列 → 消除空位

Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;

public class DesktopSort {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr FindWindow(string lpClassName, string lpWindowName);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr FindWindowEx(IntPtr hwndParent, IntPtr hwndChildAfter, string lpszClass, string lpszWindow);

    [DllImport("user32.dll")]
    public static extern bool SendMessage(IntPtr hWnd, uint Msg, IntPtr wParam, IntPtr lParam);
}
"@

# 找到桌面窗口
$progman = [DesktopSort]::FindWindow("Progman", "Program Manager")
if ($progman -eq [IntPtr]::Zero) {
    Write-Host "Error: Cannot find Program Manager window"
    exit 1
}

# 发送 SendMessage 发送 WM_COMMAND 0x111 消息
# 桌面排列的命令 ID：
# 0x7041 = 自动排列 (Auto Arrange)
# 0x7042 = 对齐到网格 (Align to Grid)

# 切换自动排列：先关闭再开启，触发紧凑排列
$WM_COMMAND = 0x111

# 关闭自动排列
[DesktopSort]::SendMessage($progman, $WM_COMMAND, [IntPtr]0x7041, [IntPtr]::Zero) | Out-Null
Start-Sleep -Milliseconds 200

# 重新开启自动排列
[DesktopSort]::SendMessage($progman, $WM_COMMAND, [IntPtr]0x7041, [IntPtr]::Zero) | Out-Null
Start-Sleep -Milliseconds 200

# 如果指定了 -SortBy ItemType，发送按类型排序命令
if ($SortBy -eq "ItemType") {
    # 0x7044 = 按类型排序 (Sort by Type)
    [DesktopSort]::SendMessage($progman, $WM_COMMAND, [IntPtr]0x7044, [IntPtr]::Zero) | Out-Null
    Start-Sleep -Milliseconds 200
    # 再次触发自动排列以紧凑
    [DesktopSort]::SendMessage($progman, $WM_COMMAND, [IntPtr]0x7041, [IntPtr]::Zero) | Out-Null
}

Write-Host "{`"status`": `"success`", `"platform`": `"Windows`", `"sort_by`": `"$SortBy`"}"
