#Requires -Version 5.1
param(
    [string]$Project = "mailing-agent",
    [string]$SourceStage = "готово для обработки ии",
    [string]$TargetStage = "прошел ревью",
    [int]$Concurrency = 3
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
$argsList = @("-m", "src.bitrix_board", "start", "--concurrency", "$Concurrency")

if ($Project -and $SourceStage -and $TargetStage) {
    $argsList += @("--project", $Project, "--source-stage", $SourceStage, "--target-stage", $TargetStage)
} else {
    $argsList += "--interactive"
}

Write-Host "Starting Bitrix24 board dispatcher ..."
& $python @argsList
