# run_local.ps1 — daily local run for Windows Task Scheduler.
# Ensures the Ollama server is up, runs the scan + local analyst, logs output.
# Token-free: all inference happens on this machine.

Set-Location -Path $PSScriptRoot

# 1. Make sure the Ollama server is running (it serves the local model).
if (-not (Get-Process ollama -ErrorAction SilentlyContinue)) {
    $ollama = Join-Path $env:LOCALAPPDATA "Programs\Ollama\ollama.exe"
    if (Test-Path $ollama) {
        Start-Process -FilePath $ollama -ArgumentList "serve" -WindowStyle Hidden
        Start-Sleep -Seconds 6
    }
}

# 2. Run the scan (analyst layer is enabled in config.json).
New-Item -ItemType Directory -Force -Path "logs" | Out-Null
$log = "logs\run-$(Get-Date -Format yyyy-MM-dd).log"
python run_daily.py *>> $log

# 3. Optional: also publish the report to GitHub (uncomment if desired).
# git add reports/ ; git commit -m "Daily market-gap brief $(Get-Date -Format yyyy-MM-dd)" ; git push
