param(
  [string]$ProfileDir = "./my_wechat_profile",
  [switch]$Headless,
  [int]$MaxWait = 600,
  [string]$Account = "",
  [string]$AccountsFile = "",
  [ValidateSet("push-latest-all","extract-latest","refresh-only")]
  [string]$RunMode = "push-latest-all",
  [string]$TargetUrl = "",
  [string]$ServerChanSendKey = "",
  [switch]$PromptServerChan,
  [switch]$PauseOnError,
  [switch]$PauseOnFinish,
  [string]$LogFile = ""
)

$ErrorActionPreference = "Stop"

Set-Location $PSScriptRoot

$logsDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Path $logsDir -Force | Out-Null

if ($LogFile -eq "") {
  $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
  $LogFile = Join-Path $logsDir "run_project_$stamp.log"
}

try {
  Start-Transcript -Path $LogFile -Append -ErrorAction SilentlyContinue | Out-Null
} catch {
}

function Invoke-Step([string]$Name, [scriptblock]$Block) {
  Write-Host "\n==> $Name" -ForegroundColor Cyan
  & $Block
  if ($LASTEXITCODE -ne $null -and $LASTEXITCODE -ne 0) {
    throw "$Name failed with exit code $LASTEXITCODE"
  }
}

try {

function Invoke-PipInstallWithFallback {
  $mirrors = @(
    "https://pypi.tuna.tsinghua.edu.cn/simple",
    "https://mirrors.aliyun.com/pypi/simple",
    "https://pypi.mirrors.ustc.edu.cn/simple",
    "https://mirrors.cloud.tencent.com/pypi/simple"
  )

  foreach ($idx in $mirrors) {
    try {
      Write-Host "pip mirror: $idx" -ForegroundColor DarkCyan
      python -m pip install -r requirements.txt --disable-pip-version-check --timeout 15 --retries 1 -i $idx
      if ($LASTEXITCODE -eq 0) { return }
    } catch {
    }
  }

  Write-Host "pip official: https://pypi.org/simple" -ForegroundColor DarkCyan
  python -m pip install -r requirements.txt --disable-pip-version-check --timeout 30 --retries 2
}

function Invoke-PlaywrightInstallWithFallback {
  $attempts = @(
    @{ PLAYWRIGHT_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/playwright"; PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/chrome-for-testing" },
    @{ PLAYWRIGHT_DOWNLOAD_HOST = "https://npmmirror.com/mirrors/playwright"; PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/chrome-for-testing" },
    @{ PLAYWRIGHT_DOWNLOAD_HOST = "https://registry.npmmirror.com/-/binary/playwright"; PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST = "https://cdn.npmmirror.com/binaries/chrome-for-testing" }
  )

  foreach ($a in $attempts) {
    try {
      Write-Host "playwright mirror: $($a.PLAYWRIGHT_DOWNLOAD_HOST)" -ForegroundColor DarkCyan
      $env:PLAYWRIGHT_DOWNLOAD_HOST = $a.PLAYWRIGHT_DOWNLOAD_HOST
      $env:PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST = $a.PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST
      python -m playwright install chromium
      if ($LASTEXITCODE -eq 0) { return }
    } catch {
    }
  }

  Write-Host "playwright official" -ForegroundColor DarkCyan
  Remove-Item Env:\PLAYWRIGHT_DOWNLOAD_HOST -ErrorAction SilentlyContinue
  Remove-Item Env:\PLAYWRIGHT_CHROMIUM_DOWNLOAD_HOST -ErrorAction SilentlyContinue
  python -m playwright install chromium
}

Invoke-Step "Install Python deps" { Invoke-PipInstallWithFallback }
Invoke-Step "Install Playwright Chromium" { Invoke-PlaywrightInstallWithFallback }

if ($Account -ne "") {
  $env:WECHAT_ACCOUNT = $Account
}
if ($AccountsFile -ne "") {
  $env:WECHAT_ACCOUNTS_FILE = $AccountsFile
}
if ($TargetUrl -ne "") {
  $env:WECHAT_REFRESH_TARGET_URL = $TargetUrl
}

if ($ServerChanSendKey -ne "") {
  $env:SERVERCHAN_SENDKEY = $ServerChanSendKey
} elseif ($PromptServerChan) {
  $secure = Read-Host "Input ServerChan SendKey" -AsSecureString
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    $env:SERVERCHAN_SENDKEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

if (-not $env:SERVERCHAN_SENDKEY -or $env:SERVERCHAN_SENDKEY -eq "") {
  Write-Host "\n[INFO] SERVERCHAN_SENDKEY is not set. Push notifications will be skipped (reason: no_sendkey)." -ForegroundColor Yellow
  Write-Host "[INFO] Set it using one of the following:" -ForegroundColor Yellow
  Write-Host "  Option 1 (current session):" -ForegroundColor Yellow
  Write-Host "    `$env:SERVERCHAN_SENDKEY=`"SCTxxxxxxxxxxxxxxxx`"" -ForegroundColor DarkYellow
  Write-Host "  Option 2 (pass to script):" -ForegroundColor Yellow
  Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File .\\run_project.ps1 -ServerChanSendKey 'SCTxxxxxxxxxxxxxxxx'" -ForegroundColor DarkYellow
  Write-Host "  Option 3 (prompt, hidden input):" -ForegroundColor Yellow
  Write-Host "    powershell -NoProfile -ExecutionPolicy Bypass -File .\\run_project.ps1 -PromptServerChan" -ForegroundColor DarkYellow
}

$env:WECHAT_PROFILE_DIR = $ProfileDir
$env:WECHAT_REFRESH_MAX_WAIT = "$MaxWait"
$env:WECHAT_HEADLESS = $(if ($Headless) { "1" } else { "0" })
$env:WECHAT_RUN_MODE = $RunMode

Invoke-Step "Run project (refresh-auth + $RunMode)" { python bootstrap_refresh_auth.py }

if (Test-Path "config.json") {
  try {
    $cfg = Get-Content "config.json" -Raw | ConvertFrom-Json
    $t = "$($cfg.token)"
    $c = "$($cfg.cookie)"
    Write-Host "\nconfig.json updated: token_len=$($t.Length) cookie_len=$($c.Length)" -ForegroundColor Green
  } catch {
  }
}

} catch {
  Write-Host "\n[ERROR] $($_.Exception.Message)" -ForegroundColor Red
  Write-Host "Log: $LogFile" -ForegroundColor Yellow
  if ($PauseOnError) {
    Read-Host "Error occurred. Press Enter to exit" | Out-Null
  }
  exit 1
} finally {
  try { Stop-Transcript -ErrorAction SilentlyContinue | Out-Null } catch {}
}

if ($PauseOnFinish) {
  Read-Host "Finished. Press Enter to exit" | Out-Null
}
