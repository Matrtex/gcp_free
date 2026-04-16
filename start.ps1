param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsFromCaller
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$PreferredGcloud = "D:\Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"

function Write-Info($Message) {
    Write-Host "[INFO] $Message"
}

function Write-ErrorMessage($Message) {
    Write-Host "[ERROR] $Message" -ForegroundColor Red
}

function Resolve-GcloudCommand {
    if (Test-Path $PreferredGcloud) {
        return $PreferredGcloud
    }

    $existing = Get-Command gcloud -ErrorAction SilentlyContinue
    if ($existing) {
        return $existing.Source
    }

    return $null
}

$GcloudCommand = Resolve-GcloudCommand
if (-not $GcloudCommand) {
    Write-ErrorMessage "gcloud was not found. Please install Google Cloud SDK first."
    exit 1
}

if (-not (Test-Path $VenvPython)) {
    Write-Info "Creating virtual environment..."
    python -m venv .venv
}

Write-Info "Installing Python dependencies..."
& $VenvPython -m pip install google-cloud-compute google-cloud-resource-manager

Write-Info "Starting gcp.py ..."
& $VenvPython gcp.py @ArgsFromCaller
