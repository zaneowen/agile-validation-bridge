# Windows: right-click > Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

function Test-Python($exe, [string[]]$pre) {
    # True when $exe runs a real Python 3.11+ (the Microsoft Store "python" stub fails this).
    if (-not (Get-Command $exe -ErrorAction SilentlyContinue)) { return $false }
    try { & $exe @pre -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" 2>$null | Out-Null } catch { return $false }
    return $LASTEXITCODE -eq 0
}

if (-not (Test-Path .venv\Scripts\python.exe)) {
    Write-Host "First run: creating virtual environment..."
    if (Test-Python py @("-3")) { py -3 -m venv .venv }
    elseif (Test-Python python @()) { python -m venv .venv }
    elseif (Get-Command uv -ErrorAction SilentlyContinue) { uv venv --seed --python ">=3.11" .venv }
    else {
        Write-Host ""
        Write-Host "Python 3.11 or newer was not found." -ForegroundColor Red
        Write-Host "Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run this again."
        exit 1
    }
    if (-not (Test-Path .venv\Scripts\python.exe)) { Write-Host "Could not create .venv" -ForegroundColor Red; exit 1 }
}
& .\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { Write-Host "Installing dependencies failed (see above)." -ForegroundColor Red; exit 1 }
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - add your ANTHROPIC_API_KEY there for real AI (mock AI runs without it)." }
Start-Job { Start-Sleep 2; Start-Process "http://127.0.0.1:8000" } | Out-Null
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
