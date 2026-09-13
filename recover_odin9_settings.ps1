# One-time, explicit recovery for the September 13 season migration incident.
# Does not modify schedule_state.json, Discord credentials, global INI or voices.
$ErrorActionPreference = 'Stop'
$running = @(Get-Process -Name python,pythonw,boss_timer_gui -ErrorAction SilentlyContinue)
if ($running.Count -gt 0) { throw 'Close BossTimer and its Python debugger before recovery.' }

$profileRoot = 'C:\Users\pulpu\AppData\Roaming\BossTimer\server_profiles'
$sourceProfile = Join-Path $profileRoot 'season_17\9'
$targetProfile = Join-Path $profileRoot 'season_18\9'
function Assert-Within([string]$candidate, [string]$parent) {
    $resolved = [IO.Path]::GetFullPath($candidate)
    $boundary = [IO.Path]::GetFullPath($parent).TrimEnd('\') + '\'
    if (-not $resolved.StartsWith($boundary, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Unsafe recovery path: $resolved"
    }
    return $resolved
}
$sourceProfile = Assert-Within (Resolve-Path -LiteralPath $sourceProfile).Path $profileRoot
$targetProfile = Assert-Within (Resolve-Path -LiteralPath $targetProfile).Path $profileRoot
$relativeFiles = @(
    'init\schedule_area_definitions.txt', 'init\schedule_boss_definitions.txt',
    'init\schedule_fixed_bosses.txt', 'init\schedule_boss_metrics.json',
    'init\schedule_break_rules.json', 'schedule_alarm_settings.json'
)
$schedulePath = Join-Path $targetProfile 'schedule_state.json'
$scheduleHash = (Get-FileHash -LiteralPath $schedulePath -Algorithm SHA256).Hash
foreach ($relative in $relativeFiles) {
    $file = Assert-Within (Join-Path $sourceProfile $relative) $sourceProfile
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { throw "Missing source setting: $relative" }
}
$stamp = (Get-Date -Format 'yyyyMMdd_HHmmss') + '_' + [guid]::NewGuid().ToString('N').Substring(0,8)
$backup = Assert-Within (Join-Path $profileRoot ('season_18\settings_recovery_9_' + $stamp)) $profileRoot
New-Item -ItemType Directory -Path $backup | Out-Null
$original = Join-Path $backup 'original_profile'
Copy-Item -LiteralPath $targetProfile -Destination $original -Recurse
foreach ($file in Get-ChildItem -LiteralPath $targetProfile -File -Recurse) {
    $relative = $file.FullName.Substring($targetProfile.Length + 1)
    if ((Get-FileHash -LiteralPath $file.FullName).Hash -ne (Get-FileHash -LiteralPath (Join-Path $original $relative)).Hash) {
        throw "Backup verification failed: $relative"
    }
}
$stagedBaseline = Join-Path $backup 'prepared_baseline'
python -B -c "import sys; from schedule_profile_migration import copy_baseline; assert copy_baseline(*sys.argv[1:]), 'Source baseline missing'" $sourceProfile $targetProfile $stagedBaseline
if ($LASTEXITCODE -ne 0) { throw 'Baseline preparation failed; live profile untouched.' }
$currentBaseline = Assert-Within (Join-Path $targetProfile 'settings_rollback\baseline') $targetProfile
$previousBaseline = Assert-Within (Join-Path $targetProfile ('settings_rollback\baseline_before_recovery_' + $stamp)) $targetProfile
$baselineMoved = $false
try {
    foreach ($relative in $relativeFiles) {
        $destination = Assert-Within (Join-Path $targetProfile $relative) $targetProfile
        Copy-Item -LiteralPath (Join-Path $sourceProfile $relative) -Destination $destination -Force
    }
    if (Test-Path -LiteralPath $currentBaseline) {
        Rename-Item -LiteralPath $currentBaseline -NewName ([IO.Path]::GetFileName($previousBaseline))
        $baselineMoved = $true
    }
    Copy-Item -LiteralPath $stagedBaseline -Destination $currentBaseline -Recurse
    foreach ($relative in $relativeFiles) {
        if ((Get-FileHash -LiteralPath (Join-Path $sourceProfile $relative)).Hash -ne (Get-FileHash -LiteralPath (Join-Path $targetProfile $relative)).Hash) {
            throw "Setting verification failed: $relative"
        }
    }
    if ((Get-FileHash -LiteralPath $schedulePath).Hash -ne $scheduleHash) { throw 'Schedule changed during recovery.' }
} catch {
    # Preserve failed output too; rollback never deletes the only copy of a file.
    $failure = Join-Path $backup 'failed_output'
    New-Item -ItemType Directory -Path $failure | Out-Null
    foreach ($relative in $relativeFiles) {
        $destination = Assert-Within (Join-Path $targetProfile $relative) $targetProfile
        $saved = Join-Path $original $relative
        if (Test-Path -LiteralPath $saved -PathType Leaf) {
            Copy-Item -LiteralPath $saved -Destination $destination -Force
        } elseif (Test-Path -LiteralPath $destination -PathType Leaf) {
            $retained = Assert-Within (Join-Path $failure $relative) $backup
            New-Item -ItemType Directory -Path (Split-Path -Parent $retained) -Force | Out-Null
            Move-Item -LiteralPath $destination -Destination $retained
        }
    }
    if ($baselineMoved) {
        if (Test-Path -LiteralPath $currentBaseline) {
            $failedBaseline = Assert-Within (Join-Path $backup 'failed_baseline') $backup
            Move-Item -LiteralPath $currentBaseline -Destination $failedBaseline
        }
        Rename-Item -LiteralPath $previousBaseline -NewName 'baseline'
    }
    throw
}
$data = Get-Content -LiteralPath $schedulePath -Encoding UTF8 -Raw | ConvertFrom-Json
[pscustomobject]@{ Result='Recovered'; SettingsFiles=$relativeFiles.Count; ScheduleEvents=$data.schedule_events.Count; ScheduleHashUnchanged=$true; Backup=$backup } | Format-List
