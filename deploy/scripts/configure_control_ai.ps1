param(
    [string]$ControlHost = "192.168.10.10",
    [string]$ControlUser = "control",
    [string]$RemoteRoot = "/home/control/mini-drop"
)

$ErrorActionPreference = "Stop"

if (-not (Get-Command ssh -ErrorAction SilentlyContinue)) {
    throw "Windows OpenSSH client is required."
}

$remoteCommand = @"
set -e
cd '$RemoteRoot'
.venv/bin/python deploy/scripts/configure_ai_provider.py --target-env '$RemoteRoot/deploy/env/control-native.env' --prompt-key
sudo systemctl restart mini-drop-server
sudo systemctl is-active mini-drop-server
echo 'DeepSeek configuration completed. The API key was not displayed.'
"@

Write-Host "Connecting to $ControlUser@$ControlHost ..." -ForegroundColor Cyan
Write-Host "Enter the DeepSeek API key only at the hidden 'DeepSeek API key' prompt." -ForegroundColor Yellow
& ssh -t "$ControlUser@$ControlHost" $remoteCommand
if ($LASTEXITCODE -ne 0) {
    throw "Remote configuration failed (exit code $LASTEXITCODE)."
}
