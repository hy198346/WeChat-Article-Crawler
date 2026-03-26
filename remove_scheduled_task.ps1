param(
  [string]$TaskName = "WeChat-Article-Crawler"
)

$ErrorActionPreference = "Stop"

Write-Host "=== 删除定时任务 ===" -ForegroundColor Cyan
Write-Host "[INFO] 任务名称: $TaskName" -ForegroundColor Green

# 检查任务是否存在
try {
  $existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if (-not $existing) {
    Write-Host "[WARNING] 任务 '$TaskName' 不存在，无需删除。" -ForegroundColor Yellow
    exit 0
  }
} catch {
  Write-Host "[WARNING] 任务 '$TaskName' 不存在，无需删除。" -ForegroundColor Yellow
  exit 0
}

# 显示任务信息
Write-Host "`n[INFO] 找到任务，信息如下:" -ForegroundColor Green
$existing | Select-Object TaskName, TaskPath, State | Format-Table -AutoSize

# 确认删除
$confirm = Read-Host "确定要删除任务 '$TaskName' 吗? (输入 'y' 确认)"
if ($confirm -ne 'y') {
  Write-Host "[INFO] 已取消删除操作。" -ForegroundColor Yellow
  exit 0
}

# 删除任务
try {
  Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
  Write-Host "`n[SUCCESS] 任务 '$TaskName' 已成功删除!" -ForegroundColor Green
} catch {
  Write-Host "`n[ERROR] 删除任务失败!" -ForegroundColor Red
  Write-Host "[ERROR] 详情: $($_.Exception.Message)" -ForegroundColor Red
  
  if ($_.Exception.Message -like "*access*denied*" -or $_.Exception.Message -like "*拒绝访问*") {
    Write-Host "`n[SOLUTION] 需要管理员权限，请尝试:" -ForegroundColor Yellow
    Write-Host "  1. 以管理员身份运行 PowerShell" -ForegroundColor Cyan
    Write-Host "  2. 或使用命令: schtasks /Delete /TN '$TaskName' /F" -ForegroundColor Cyan
  }
  
  exit 1
}

# 验证删除
try {
  $check = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($check) {
    Write-Host "[WARNING] 任务可能未完全删除，请手动检查。" -ForegroundColor Yellow
  } else {
    Write-Host "[SUCCESS] 验证通过，任务已完全删除。" -ForegroundColor Green
  }
} catch {
  Write-Host "[SUCCESS] 验证通过，任务已完全删除。" -ForegroundColor Green
}
