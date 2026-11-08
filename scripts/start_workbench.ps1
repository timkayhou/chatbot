# Start the loopback-only workbench, loading secrets from private\workbench.env.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

& (Join-Path $PSScriptRoot "start_inference.ps1")

$envFile = Join-Path $root "private\workbench.env"
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            Set-Item -Path "env:$($matches[1])" -Value $matches[2].Trim()
        }
    }
}
$env:PYTHONPATH = "src"

try {
    $existing = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/status" -Headers @{ Host = "127.0.0.1:8765" } -UseBasicParsing -TimeoutSec 3
    if ($existing.StatusCode -eq 200) {
        Write-Output "workbench already running at http://127.0.0.1:8765"
        exit 0
    }
} catch {
    # not running yet
}

Start-Process -FilePath ".venv\Scripts\python.exe" `
    -ArgumentList "-m", "workbench.app", "--database", "data\workbench.sqlite" `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput "data\workbench.out.log" `
    -RedirectStandardError "data\workbench.err.log"

for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Seconds 1
    try {
        $response = Invoke-WebRequest -Uri "http://127.0.0.1:8765/api/status" -Headers @{ Host = "127.0.0.1:8765" } -UseBasicParsing -TimeoutSec 3
        if ($response.StatusCode -eq 200) {
            Write-Output "workbench started at http://127.0.0.1:8765"
            exit 0
        }
    } catch {
        # still starting
    }
}

Write-Error "workbench did not become ready; see data\workbench.err.log"
exit 1
