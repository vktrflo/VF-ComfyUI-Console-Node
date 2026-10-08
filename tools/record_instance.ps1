# Start/stop the isolated recording instance used to capture demo GIFs.
# Runs ComfyUI with every custom node disabled except the console node and a
# throwaway emit-helper, on a dedicated port so the user's own instances and
# workflows are untouched.
#
#   pwsh -File tools/record_instance.ps1 start
#   pwsh -File tools/record_instance.ps1 stop
#   pwsh -File tools/record_instance.ps1 status

param([Parameter(Position = 0)][string]$Action = 'status')

$ComfyRoot = 'E:\comfyui_instances\SMALL_DESKTOP\ComfyUI'
$Python = Join-Path $ComfyRoot '.venv\Scripts\python.exe'
$Port = 8198
$Log = Join-Path $PSScriptRoot '..\.recording\comfy.log'
$Helper = Join-Path $ComfyRoot 'custom_nodes\__cn_recorder_helper'

function Get-RecordPid {
    $conn = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($conn) { return $conn[0].OwningProcess }
    return $null
}

switch ($Action.ToLower()) {
    'start' {
        $existing = Get-RecordPid
        if ($existing) { Write-Output "already listening on $Port (pid $existing)"; exit 0 }
        if (-not (Test-Path $Helper)) {
            Write-Output "recording helper missing at $Helper" -ForegroundColor Red
            exit 1
        }
        New-Item -ItemType Directory -Force -Path (Split-Path $Log) | Out-Null
        $args = @(
            'main.py', '--disable-all-custom-nodes',
            '--whitelist-custom-nodes', 'VF-ComfyUI-Console-Node', '__cn_recorder_helper',
            '--disable-partner-nodes', '--port', $Port
        )
        Start-Process -FilePath $Python -ArgumentList $args -WorkingDirectory $ComfyRoot `
            -RedirectStandardOutput $Log -RedirectStandardError "$Log.err" -WindowStyle Hidden | Out-Null
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep -Seconds 2
            try {
                $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -UseBasicParsing -TimeoutSec 3
                if ($r.StatusCode -eq 200) { Write-Output "ready on $Port after ~$(($i+1)*2)s"; exit 0 }
            } catch { }
        }
        Write-Output "timed out waiting for $Port" -ForegroundColor Red
        exit 1
    }
    'stop' {
        $pidToStop = Get-RecordPid
        if ($pidToStop) {
            Stop-Process -Id $pidToStop -Force -ErrorAction SilentlyContinue
            Write-Output "stopped pid $pidToStop"
        } else {
            Write-Output "nothing listening on $Port"
        }
    }
    'status' {
        $pidToStop = Get-RecordPid
        if ($pidToStop) { Write-Output "listening on $Port (pid $pidToStop)" }
        else { Write-Output "not listening on $Port" }
    }
}