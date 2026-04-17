param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ArgsFromCaller
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$RequirementsFile = Join-Path $ScriptDir "requirements.txt"
$DepsHashFile = Join-Path $ScriptDir ".deps.sha256"
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

function Add-CommandDirectoryToPath($CommandPath) {
    $commandDirectory = Split-Path -Parent $CommandPath
    if (-not $commandDirectory) {
        return
    }

    $existingEntries = @($env:Path -split ';' | Where-Object { $_ })
    if ($existingEntries -notcontains $commandDirectory) {
        $env:Path = "$commandDirectory;$env:Path"
    }
}

function Get-RequirementsHash {
    if (-not (Test-Path $RequirementsFile)) {
        Write-ErrorMessage "requirements.txt was not found."
        exit 1
    }

    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        $stream = [System.IO.File]::OpenRead($RequirementsFile)
        try {
            $hashBytes = $sha256.ComputeHash($stream)
        } finally {
            $stream.Dispose()
        }
    } finally {
        $sha256.Dispose()
    }

    return ([System.BitConverter]::ToString($hashBytes)).Replace("-", "").ToLowerInvariant()
}

$GcloudCommand = Resolve-GcloudCommand
if (-not $GcloudCommand) {
    Write-ErrorMessage "gcloud was not found. Please install Google Cloud SDK first."
    exit 1
}
$Utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::InputEncoding = $Utf8NoBom
[Console]::OutputEncoding = $Utf8NoBom
$OutputEncoding = $Utf8NoBom
$env:GCP_FREE_GCLOUD_COMMAND = $GcloudCommand
$env:PYTHONUNBUFFERED = "1"
$env:PYTHONIOENCODING = "utf-8"
Add-CommandDirectoryToPath $GcloudCommand

if (-not (Test-Path $VenvPython)) {
    Write-Info "Creating virtual environment..."
    python -m venv .venv
}

$CurrentDepsHash = Get-RequirementsHash
$InstalledDepsHash = ""
if (Test-Path $DepsHashFile) {
    $InstalledDepsHash = (Get-Content $DepsHashFile -Raw).Trim().ToLowerInvariant()
}

if ($CurrentDepsHash -ne $InstalledDepsHash) {
    Write-Info "Installing Python dependencies from requirements.txt ..."
    & $VenvPython -m pip install -r $RequirementsFile
    Set-Content -Path $DepsHashFile -Value $CurrentDepsHash -NoNewline
} else {
    Write-Info "Python dependencies are up to date."
}

Write-Info "Starting gcp.py ..."
& $VenvPython -u gcp.py @ArgsFromCaller
