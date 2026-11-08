# Start the loopback-only llama.cpp inference service with the validated context size.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$envFile = Join-Path $root "private\workbench.env"

if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            Set-Item -Path "env:$($matches[1])" -Value $matches[2].Trim()
        }
    }
}

$server = Join-Path $root "resources\versions\llama-cpp-sycl\b12110\llama-server.exe"
$model = Join-Path $root "resources\models\Qwen3-14B-Q5_K_M.gguf"
$logDirectory = Join-Path $root "data\logs"
$stdoutLog = Join-Path $logDirectory "inference.out.log"
$stderrLog = Join-Path $logDirectory "inference.err.log"
$contextSize = 16384
$port = 8081

if (-not (Test-Path $server)) { throw "llama-server is not installed at the expected local path" }
if (-not (Test-Path $model)) { throw "Qwen3-14B-Q5_K_M is not installed at the expected local path" }
if (-not $env:WORKBENCH_INFERENCE_API_KEY) { throw "WORKBENCH_INFERENCE_API_KEY is missing from private\workbench.env" }

try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
    if ($health.status -eq "ok") {
        $existing = Get-CimInstance Win32_Process | Where-Object {
            $_.Name -eq "llama-server.exe" -and $_.CommandLine -match "--port\s+$port(?:\s|$)"
        } | Select-Object -First 1
        if ($existing -and $existing.CommandLine -match "-c\s+$contextSize(?:\s|$)" -and
            $existing.CommandLine -match [regex]::Escape((Split-Path -Leaf $model))) {
            Write-Output "inference already running at http://127.0.0.1:$port"
            exit 0
        }
        throw "port $port has a healthy service, but it is not the validated 16K inference configuration"
    }
} catch {
    if ($_.Exception.Message -like "port $port has a healthy service*") { throw }
    # not running yet
}

New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
Start-Process -FilePath $server `
    -ArgumentList @(
        "-m", $model,
        "--host", "127.0.0.1",
        "--port", $port,
        "-c", $contextSize,
        "-ngl", "999",
        "-dev", "SYCL0",
        "--jinja",
        "--api-key", $env:WORKBENCH_INFERENCE_API_KEY,
        "-lv", "2"
    ) `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog | Out-Null

for ($attempt = 0; $attempt -lt 60; $attempt++) {
    Start-Sleep -Seconds 1
    try {
        $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -TimeoutSec 3
        if ($health.status -eq "ok") {
            Write-Output "inference started at http://127.0.0.1:$port with context $contextSize"
            exit 0
        }
    } catch {
        # still loading
    }
}

Write-Error "inference did not become ready; see data\logs\inference.err.log"
exit 1
