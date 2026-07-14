#Requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Resolve-Python {
    if (Test-Path ".\.venv\Scripts\python.exe") {
        return ".\.venv\Scripts\python.exe"
    }
    return "python"
}

$python = Resolve-Python
Write-Host "Resuming Bitrix24 board dispatcher ..."
& $python -m src.bitrix_board resume
