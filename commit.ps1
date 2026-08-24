<#
Manual git commit + optional GitHub push helper for this App Lab app folder.
Run directly (double-click commit.bat) - does not go through Claude, so it costs 0 tokens.

Usage:
  .\commit.ps1                  -> asks for a commit message (or auto timestamp)
  .\commit.ps1 "my message"     -> commits with your own message
Either way, it then asks whether to push to GitHub.
#>

param(
    [string]$Message
)

$RemoteUrl = "https://github.com/HaTjmn/ArduinoUnoQ_MatrixLED_HEB_LETTER.git"

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".git")) {
    Write-Host "No git repo here yet - running 'git init'..." -ForegroundColor Yellow
    git init | Out-Null
}

$existingRemotes = git remote
if ($existingRemotes -notcontains "origin") {
    git remote add origin $RemoteUrl
    Write-Host "Connected to GitHub repo: $RemoteUrl" -ForegroundColor Cyan
}

git add -A

$staged = git diff --cached --name-only
if (-not $staged) {
    Write-Host "Nothing to commit - working tree clean." -ForegroundColor Yellow
} else {
    Write-Host "Changed files:" -ForegroundColor Cyan
    git diff --cached --name-status

    if (-not $Message) {
        $Message = Read-Host "Commit message (leave empty for auto timestamp)"
    }
    if (-not $Message) {
        $Message = "Update " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss")
    }

    git commit -m $Message
}

$answer = Read-Host "Push to GitHub now? (y/N)"
if ($answer -eq "y" -or $answer -eq "Y") {
    $branch = git branch --show-current
    Write-Host "Pushing branch '$branch' to origin..." -ForegroundColor Cyan
    git push -u origin $branch

    if ($LASTEXITCODE -ne 0) {
        Write-Host ""
        Write-Host "Push was rejected - the GitHub repo has content that doesn't match your local history (e.g. an auto-created README)." -ForegroundColor Yellow
        $force = Read-Host "Overwrite the GitHub repo with your local version instead? This discards whatever is currently there. (y/N)"
        if ($force -eq "y" -or $force -eq "Y") {
            git push -u origin $branch --force
        } else {
            Write-Host "Skipped - nothing was pushed." -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "Skipped push - commit stayed local only." -ForegroundColor Yellow
}

Read-Host "Press Enter to close"
