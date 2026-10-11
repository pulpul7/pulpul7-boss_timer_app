"""Windows HTTPS rule scoped to the owned name, executable, LAN address and port."""
from __future__ import annotations

import base64
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess

SCRIPT = r'''
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$task = $env:BOSSTIMER_WEB_RULE_JSON | ConvertFrom-Json
try {
    $rule = Get-NetFirewallRule -PolicyStore PersistentStore -Name $task.name -ErrorAction SilentlyContinue
    if ($rule -and ($rule.Description -ne $task.marker -or $rule.Group -ne 'BossTimerWeb')) {
        throw 'ownership_conflict'
    }
    if ($task.operation -eq 'remove') {
        if ($rule) { $rule | Remove-NetFirewallRule }
    } else {
        if ($rule) { $rule | Remove-NetFirewallRule }
        New-NetFirewallRule -PolicyStore PersistentStore -Name $task.name -DisplayName 'BossTimer Web HTTPS' `
          -Group 'BossTimerWeb' -Description $task.marker -Direction Inbound -Action Allow -Enabled True `
          -Profile Any -Protocol TCP -LocalPort ([int]$task.port) -LocalAddress $task.address `
          -Program $task.program -EdgeTraversalPolicy Block | Out-Null
        $active = Get-NetFirewallRule -PolicyStore ActiveStore -Name $task.name
        $ports = $active | Get-NetFirewallPortFilter
        $app = $active | Get-NetFirewallApplicationFilter
        $addresses = $active | Get-NetFirewallAddressFilter
        if (-not $active -or [string]$active.Enabled -ne 'True' -or [string]$active.Action -ne 'Allow' `
           -or [string]$active.Direction -ne 'Inbound' -or [string]$ports.LocalPort -ne [string]$task.port `
           -or [string]$ports.Protocol -notin @('TCP','6') -or $app.Program -ne $task.program `
           -or [string]$addresses.LocalAddress -notin @($task.address, "$($task.address)/32")) {
            throw 'verification_failed'
        }
    }
    @{ok=$true; code='ok'} | ConvertTo-Json -Compress
} catch {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $administrator = ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    $code = if (-not $administrator) {'administrator_required'} elseif ($_.Exception.Message -eq 'ownership_conflict') {'ownership_conflict'} else {'firewall_failed'}
    @{ok=$false; code=$code} | ConvertTo-Json -Compress
}
'''


class FirewallError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__("firewall action failed")


def rule_data(owner, program, address, port):
    if not re.fullmatch(r"[0-9a-f]{32}", owner):
        raise ValueError("invalid owner")
    ip = ipaddress.ip_address(address)
    if ip.version != 4 or not ip.is_private or ip.is_loopback or ip.is_unspecified:
        raise ValueError("invalid firewall address")
    if type(port) is not int or not 1024 <= port <= 65535:
        raise ValueError("invalid firewall port")
    if not isinstance(program, str) or not Path(program).is_absolute() or len(program) > 32768:
        raise ValueError("invalid program")
    return dict(name="BossTimer-Web-" + owner, marker="BossTimer managed HTTPS " + owner,
                program=program, address=str(ip), port=port)


def apply(rule, operation):
    if os.name != "nt" or operation not in {"allow", "remove"}:
        raise ValueError("unsupported firewall operation")
    expected = rule_data(rule["name"].removeprefix("BossTimer-Web-"), rule["program"], rule["address"], rule["port"])
    if rule != expected:
        raise ValueError("invalid firewall ownership")
    executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")
    environment = dict(os.environ, BOSSTIMER_WEB_RULE_JSON=json.dumps(dict(rule, operation=operation)))
    result = subprocess.run([str(executable), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                            env=environment, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            timeout=15, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
    if len(result.stdout) > 4096:
        raise ValueError("unexpected firewall response")
    answer = json.loads(result.stdout.decode("utf-8-sig"))
    if answer.get("ok") is not True:
        code = answer.get("code")
        raise FirewallError(code if code in {"administrator_required", "ownership_conflict", "firewall_failed"} else "firewall_failed")
