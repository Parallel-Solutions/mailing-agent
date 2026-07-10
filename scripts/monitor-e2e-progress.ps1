param(
    [int]$Total = 140,
    [int]$IntervalSeconds = 30,
    [string]$RunnerTerminal = "893655.txt"
)

$barWidth = 40
$lastDone = -1
$termPath = Join-Path $PSScriptRoot "..\..\Users\ventilator\.cursor\projects\c-random-forest-mailing-agent\terminals\$RunnerTerminal"
Write-Host "E2E matrix monitor started (target: $Total scenarios, poll every ${IntervalSeconds}s)"
Write-Host "Press Ctrl+C to stop."
Write-Host ""

while ($true) {
    $raw = docker compose exec app .venv/bin/python -c "import json;from pathlib import Path;p=Path('tests/e2e/out/e2e_report.json');s=json.load(open(p))['summary'] if p.exists() else {'total':0,'success':0,'failed':0,'pending':0};print(str(s['total'])+'|'+str(s['success'])+'|'+str(s['failed'])+'|'+str(s.get('pending',0)))" 2>$null
    if ($raw) {
        $parts = $raw.Trim() -split '\|'
        if ($parts.Count -ge 4) {
            $done = [int]$parts[0]
            $ok = [int]$parts[1]
            $fail = [int]$parts[2]
            $pending = [int]$parts[3]

            if ($done -ne $lastDone) {
                $pct = [math]::Min(100, [math]::Round(100.0 * $done / $Total))
                $filled = [math]::Round($barWidth * $done / $Total)
                $bar = ('#' * $filled).PadRight($barWidth, '-')
                $ts = Get-Date -Format 'HH:mm:ss'
                Write-Host "[$ts] [$bar] $done/$Total ($pct%) | OK $ok | FAIL $fail"
                $lastDone = $done
            }

            if ($done -ge $Total -and $pending -eq 0) {
                Write-Host ""
                Write-Host "[DONE] All $Total scenarios processed."
                break
            }
        }
    }

    if (Test-Path $termPath) {
        $tail = Get-Content $termPath -Tail 8 -ErrorAction SilentlyContinue
        if ($tail -match 'exit_code:') {
            Write-Host ""
            Write-Host "[DONE] Matrix runner process finished."
            break
        }
    }

    Start-Sleep -Seconds $IntervalSeconds
}
