#Requires -Version 5.1
param(
    [int[]]$TaskId,
    [int]$Limit = 3
)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Resolve-Python {
    if (Test-Path ".\.venv\Scripts\python.exe") {
        return ".\.venv\Scripts\python.exe"
    }
    return "python"
}

$python = Resolve-Python
$argsList = @("-m", "src.bitrix_board", "launch-chats", "--limit", "$Limit")
foreach ($id in $TaskId) {
    $argsList += @("--task-id", "$id")
}

Write-Host "Stopping dispatcher and launching visible Cursor chats ..."
& $python @argsList
