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

Set-Location -Path $PSScriptRoot

if (-not (Test-Path ".git")) {
    Write-Host "No git repo here yet - running 'git init'..." -ForegroundColor Yellow
    git init | Out-Null
}

$existingRemotes = git remote
if ($existingRemotes -notcontains "origin") {
    $newUrl = Read-Host "No GitHub repo connected yet. Paste its URL (leave empty to skip)"
    if ($newUrl) {
        git remote add origin $newUrl
        Write-Host "Connected to GitHub repo: $newUrl" -ForegroundColor Cyan
    }
} else {
    $currentUrl = git remote get-url origin
    Write-Host "Currently connected to: $currentUrl" -ForegroundColor DarkGray
    $change = Read-Host "Change the GitHub repo link? (y/N)"
    if ($change -eq "y" -or $change -eq "Y") {
        $newUrl = Read-Host "New GitHub repo URL"
        if ($newUrl) {
            git remote set-url origin $newUrl
            Write-Host "Now connected to: $newUrl" -ForegroundColor Cyan
        } else {
            Write-Host "No URL given - keeping the current one." -ForegroundColor Yellow
        }
    }
}

$newBranch = Read-Host "Create a new branch? (y/N)"
if ($newBranch -eq "y" -or $newBranch -eq "Y") {
    $branchName = Read-Host "New branch name"
    if ($branchName) {
        git checkout -b $branchName
    } else {
        Write-Host "No name given - staying on current branch." -ForegroundColor Yellow
    }
}

$pullAnswer = Read-Host "Update local code to match GitHub first? (y/N)"
if ($pullAnswer -eq "y" -or $pullAnswer -eq "Y") {
    $branch = git branch --show-current
    $dirty = git status --porcelain
    $stashed = $false
    if ($dirty) {
        Write-Host "Stashing your local changes temporarily..." -ForegroundColor Cyan
        git stash push -u -m "auto-stash before pull" | Out-Null
        $stashed = $true
    }

    Write-Host "Pulling latest from origin/$branch..." -ForegroundColor Cyan
    git fetch origin
    git pull origin $branch

    if ($LASTEXITCODE -ne 0) {
        Write-Host "Pull failed or had conflicts - check the messages above before continuing." -ForegroundColor Red
    }

    if ($stashed) {
        Write-Host "Restoring your local changes..." -ForegroundColor Cyan
        git stash pop
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Could not auto-restore your changes cleanly (conflict). They are not lost - run 'git stash list' / 'git stash pop' manually, or ask Claude for help." -ForegroundColor Red
        }
    }
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
