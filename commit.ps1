<#
Manual git commit helper for this App Lab app folder.
Run directly in PowerShell - does not go through Claude, so it costs 0 tokens.

Usage:
  .\commit.ps1                  -> commits with an auto timestamp message
  .\commit.ps1 "my message"     -> commits with your own message
#>

param(
    [string]$Message
)

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".git")) {
    Write-Host "No git repo here yet - running 'git init'..." -ForegroundColor Yellow
    git init | Out-Null
}

git add -A

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Nothing to commit - working tree clean." -ForegroundColor Yellow
    Read-Host "Press Enter to close"
    exit 0
}

Write-Host "Changed files:" -ForegroundColor Cyan
git diff --cached --name-status

if (-not $Message) {
    $Message = Read-Host "Commit message (leave empty for auto timestamp)"
}

if (-not $Message) {
    $Message = "Update " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
}

git commit -m $Message

Read-Host "Press Enter to close"
