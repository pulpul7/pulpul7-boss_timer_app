# Run from an ordinary terminal; only the recovery Python process is elevated.
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateRange(0, 2147483647)]
    [int]$Season,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9_-]+$')]
    [string]$ServerId,
    [string]$PythonPath = ''
)

$ErrorActionPreference = 'Stop'
$helperPath = Join-Path $PSScriptRoot 'profile_access.py'
if (-not (Test-Path -LiteralPath $helperPath -PathType Leaf)) {
    throw 'profile_access.py is missing beside this script.'
}
$dataBase = $env:APPDATA
if (-not $dataBase) { $dataBase = $env:LOCALAPPDATA }
if (-not $dataBase) { $dataBase = [Environment]::GetFolderPath('UserProfile') }
$profilesRoot = [IO.Path]::GetFullPath((Join-Path $dataBase 'BossTimer\server_profiles'))
$profilePath = Join-Path (Join-Path $profilesRoot ('season_' + $Season)) $ServerId

if (-not $PythonPath) {
    $pythonOutput = @(& python -B -c 'import sys; print(sys.executable)')
    if ($LASTEXITCODE -ne 0 -or $pythonOutput.Count -ne 1) {
        throw 'Python could not be found. Specify -PythonPath with your Python executable.'
    }
    $PythonPath = [string]$pythonOutput[0]
}
$taskPythonExecutable = [IO.Path]::GetFullPath($PythonPath.Trim())
if (-not (Test-Path -LiteralPath $taskPythonExecutable -PathType Leaf)) {
    throw 'The Python executable could not be found.'
}
# Start-Process joins ArgumentList strings; quote every filesystem argument.
$helperArguments = '-B "{0}" --profiles-root "{1}" --profile "{2}" --force-inheritance' -f $helperPath, $profilesRoot, $profilePath
Write-Host ('Repairing profile permissions: ' + $profilePath)
Write-Host 'Approve the Windows administrator prompt for the recovery tool.'
try {
    $repairProcess = Start-Process -FilePath $taskPythonExecutable -ArgumentList $helperArguments `
        -Verb RunAs -WindowStyle Hidden -Wait -PassThru
    if ($null -ne $repairProcess.ExitCode -and $repairProcess.ExitCode -ne 0) {
        throw ('Recovery failed. Exit code: ' + $repairProcess.ExitCode)
    }
} catch {
    throw ('Recovery did not complete: ' + $_.Exception.Message)
}

# Prove that the original non-elevated terminal can read the profile now.
& $taskPythonExecutable -B $helperPath --profiles-root $profilesRoot --profile $profilePath --check-only
if ($LASTEXITCODE -ne 0) {
    throw 'The current user still cannot read this profile. Its parent-folder permissions need checking.'
}
Write-Host 'Profile permissions repaired. Start debugging with F5 again.'
