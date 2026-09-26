# Daily autonomous engineering run for ai-sdlc-assistant + sdlc-mcp-server.
# Triggered by the Windows scheduled task "AI-SDLC Daily Loop". Manual run:
#   powershell -ExecutionPolicy Bypass -File auto-sdlc\run-daily.ps1
# Flow: sync master -> branch auto/<date> -> claude -p (locked-down settings) -> gate -> gitleaks
#       -> push branch -> merge to master + push only if the gate is green.
# Git identity + GitHub credential are repo-local (.git/config), never global.

# 'Continue', not 'Stop': in PS 5.1 native stderr (git progress) under 2>&1 would throw. Exit codes are checked explicitly.
$ErrorActionPreference = 'Continue'
$Repo    = Split-Path $PSScriptRoot -Parent
$Remote  = 'Python-Learning'
$Date    = Get-Date -Format 'yyyy-MM-dd'
$Branch  = "auto/$Date"
$LogDir  = Join-Path $PSScriptRoot 'logs'
$Log     = Join-Path $LogDir "$Date.log"
$Lock    = Join-Path $LogDir 'run.lock'
$CountF  = Join-Path $LogDir 'last_green_test_count.txt'
$Py      = Join-Path $Repo 'ai-sdlc-assistant\.venv\Scripts\python.exe'
$ClaudeTimeoutMin = 240

New-Item -ItemType Directory -Force $LogDir | Out-Null
function Log($m) { $l = "$(Get-Date -Format 'HH:mm:ss') $m"; Write-Host $l; Add-Content -Path $Log -Value $l -Encoding utf8 }
function Git { & git -C $Repo @args 2>&1 | ForEach-Object { "$_" }; if ($LASTEXITCODE -ne 0) { throw "git $args failed ($LASTEXITCODE)" } }
function Run($name, $dir, $exe, [string[]]$argv) {
    Push-Location $dir
    try { $out = & $exe @argv 2>&1 | ForEach-Object { "$_" }; $code = $LASTEXITCODE } finally { Pop-Location }
    Add-Content -Path $Log -Value ($out -join "`n") -Encoding utf8
    Log "gate: $name -> exit $code"
    return @{ ok = ($code -eq 0); out = ($out -join "`n") }
}

if (Test-Path $Lock) { Log "lock exists ($Lock) - another run in progress or crashed; delete it to retry"; exit 1 }
New-Item -ItemType File $Lock | Out-Null
try {
    Log "=== run $Date ==="

    # Identity guard: refuse to run unless the repo-local identity is set (keeps work email out of commits).
    $email = (& git -C $Repo config --local user.email)
    if (-not $email -or $email -like '*@azilen.com') { Log "ABORT: repo-local user.email not set to personal address"; exit 1 }

    # Never touch the user's own uncommitted work.
    $dirty = (& git -C $Repo status --porcelain --untracked-files=no) + (& git -C $Repo status --porcelain -- ai-sdlc-assistant sdlc-mcp-server auto-sdlc)
    if ($dirty) { Log "SKIP: uncommitted changes in scope - commit or stash them first:`n$($dirty -join "`n")"; exit 0 }

    Git checkout master | Out-Null
    Git fetch $Remote | Out-Null
    Git merge --ff-only "$Remote/master" | Out-Null
    if ((& git -C $Repo branch --list $Branch)) { Log "SKIP: $Branch already exists (already ran today)"; exit 0 }
    Git checkout -b $Branch | Out-Null
    $base = (& git -C $Repo rev-parse HEAD)

    # Infra for retriever tests (best effort - if Docker is down, the gate fails and nothing merges).
    & docker compose -f (Join-Path $Repo 'ai-sdlc-assistant\docker-compose.yml') up -d qdrant redis 2>&1 | ForEach-Object { Add-Content $Log "$_" }

    # --- the engineering session ---
    Log "claude session start"
    $claude = (Get-Command claude).Source
    $p = Start-Process -FilePath $claude -WorkingDirectory $Repo -NoNewWindow -PassThru `
        -ArgumentList @('-p', '--settings', "`"$PSScriptRoot\settings.json`"", '--permission-mode', 'acceptEdits') `
        -RedirectStandardInput (Join-Path $PSScriptRoot 'DAILY_PROMPT.md') `
        -RedirectStandardOutput (Join-Path $LogDir "$Date.claude.out.log") `
        -RedirectStandardError  (Join-Path $LogDir "$Date.claude.err.log")
    if (-not $p.WaitForExit($ClaudeTimeoutMin * 60 * 1000)) { $p.Kill(); Log "claude timed out after $ClaudeTimeoutMin min - killed" }
    Log "claude session end"

    # Commit anything left uncommitted in scope (gitignore keeps .env / venvs out).
    if ((& git -C $Repo status --porcelain -- ai-sdlc-assistant sdlc-mcp-server auto-sdlc)) {
        Git add -- ai-sdlc-assistant sdlc-mcp-server auto-sdlc | Out-Null
        Git commit -m "chore(auto): uncommitted leftovers from $Date run" | Out-Null
    }
    if ((& git -C $Repo rev-parse HEAD) -eq $base) {
        Log "no commits today - dropping branch"
        Git checkout master | Out-Null; Git branch -D $Branch | Out-Null; exit 0
    }

    # --- secret scan: nothing leaves this machine if it trips ---
    & gitleaks git $Repo --log-opts="$base..HEAD" --redact --no-banner 2>&1 | ForEach-Object { Add-Content $Log "$_" }
    if ($LASTEXITCODE -ne 0) { Log "ABORT: gitleaks found secrets in $Branch - NOT pushed. Review locally."; Git checkout master | Out-Null; exit 1 }

    # --- gate ---
    $be  = Run 'backend unit' (Join-Path $Repo 'ai-sdlc-assistant') $Py @('-m','pytest','tests/unit','-q','-p','no:cacheprovider')
    $mcp = Run 'mcp server'   (Join-Path $Repo 'sdlc-mcp-server')   $Py @('-m','pytest','-q','-p','no:cacheprovider')
    $fe  = Run 'angular test' (Join-Path $Repo 'ai-sdlc-assistant\frontend-angular') 'npx.cmd' @('ng','test','--watch=false')
    $bld = Run 'angular build' (Join-Path $Repo 'ai-sdlc-assistant\frontend-angular') 'npx.cmd' @('ng','build')
    $green = $be.ok -and $mcp.ok -and $fe.ok -and $bld.ok

    # Feature-loss guard: backend passed-test count may never drop below the last green run.
    $passed = 0; if ($be.out -match '(\d+) passed') { $passed = [int]$Matches[1] }
    $prev = 0; if (Test-Path $CountF) { $prev = [int](Get-Content $CountF) }
    if ($passed -lt $prev) { Log "GATE: passed tests dropped $prev -> $passed"; $green = $false }

    Git push $Remote $Branch | Out-Null
    Log "pushed $Branch"
    Git checkout master | Out-Null
    if ($green) {
        Git merge --no-ff $Branch -m "Merge $Branch (daily auto run, gate green)" | Out-Null
        Git push $Remote master | Out-Null
        Set-Content -Path $CountF -Value $passed
        Log "GREEN - merged $Branch into master and pushed ($passed backend tests)"
    } else {
        Log "RED - $Branch pushed for review, master untouched"
    }
}
catch { Log "ERROR: $_"; try { & git -C $Repo checkout master 2>&1 | Out-Null } catch {} ; exit 1 }
finally { Remove-Item $Lock -ErrorAction SilentlyContinue }
