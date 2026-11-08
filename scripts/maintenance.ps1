# Daily maintenance: database backup, log rotation, candidate refresh.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONPATH = "src"
$python = Join-Path $root ".venv\Scripts\python.exe"
$log = Join-Path $root "data\maintenance.log"

"$(Get-Date -Format o) maintenance start" | Out-File -FilePath $log -Append -Encoding utf8
& $python scripts\backup_db.py --database data\workbench.sqlite --target data\backups --keep 14 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
& $python scripts\rotate_logs.py --directory data --days 30 --keep-newest 4 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
& $python scripts\local_loop.py --once 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
& $python -c "import sys; sys.path.insert(0,'src'); from workbench.store import Store; Store('data/workbench.sqlite').build_faq_candidates()" 2>&1 | Out-File -FilePath $log -Append -Encoding utf8
"$(Get-Date -Format o) maintenance done" | Out-File -FilePath $log -Append -Encoding utf8
