# Windows: right-click > Run with PowerShell, or: powershell -ExecutionPolicy Bypass -File run.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) {
    Write-Host "First run: creating virtual environment..."
    py -3 -m venv .venv
    if ($LASTEXITCODE -ne 0) { python -m venv .venv }
}
& .\.venv\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env; Write-Host "Created .env - add your ANTHROPIC_API_KEY there for real AI (mock AI runs without it)." }
Start-Job { Start-Sleep 2; Start-Process "http://127.0.0.1:8000" } | Out-Null
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
