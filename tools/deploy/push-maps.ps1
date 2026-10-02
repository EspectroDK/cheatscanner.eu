<#
.SYNOPSIS
  Copies map meshes from this PC to the Cheatscanner server and installs them there.

.DESCRIPTION
  Sends data\maps\<map>.tri (+ .tri.json) and data\maps\render\<map>.tri to the server, which checks
  every file and only then swaps them in. Analyses load the mesh per match, so nothing restarts.
  Uses the ssh and scp programs that come with Windows 10/11 and your SSH key.

.EXAMPLE
  .\tools\deploy\push-maps.ps1
  .\tools\deploy\push-maps.ps1 de_mirage de_train
  .\tools\deploy\push-maps.ps1 -Server deploy@203.0.113.10
#>
param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]]$Maps,
    [string]$Server = $(if ($env:CHEATSCANNER_SSH) { $env:CHEATSCANNER_SSH } else { "deploy@cheatscanner.eu" })
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$mapsDir = Join-Path $root "data\maps"
$renderDir = Join-Path $mapsDir "render"
$remote = "/srv/cheatscanner/data/maps/incoming"

function Wanted($file) {
    $name = [IO.Path]::GetFileNameWithoutExtension($file.Name)
    # Only <map>.tri: side files such as <map>.removed.tri (tools/geometry/audit_mesh.py) or backup copies
    # like <map>.game.tri are never loaded by the analyzer, so they stay on this PC.
    if ($name.Contains(".")) { return $false }
    if (-not $Maps) { return $true }
    return ($Maps -contains $name) -or ($Maps -contains "de_$name") -or ($Maps -contains ($name -replace '^de_', ''))
}

function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe failed (exit code $LASTEXITCODE)" }
}

$meshes = @(Get-ChildItem -Path $mapsDir -Filter *.tri -File | Where-Object { Wanted $_ })
$renders = @()
if (Test-Path $renderDir) { $renders = @(Get-ChildItem -Path $renderDir -Filter *.tri -File | Where-Object { Wanted $_ }) }
if ($meshes.Count + $renders.Count -eq 0) { throw "No .tri files found in $mapsDir for: $Maps" }

$send = @()
foreach ($m in $meshes) {
    $send += $m.FullName
    $meta = "$($m.FullName).json"
    if (Test-Path $meta) { $send += $meta }
}

Write-Host "Sending $($meshes.Count) mesh(es) and $($renders.Count) clip mesh(es) to $Server"
Run ssh @($Server, "rm -rf $remote && mkdir -p $remote/render")
if ($send.Count) { Run scp (@("-q") + $send + "${Server}:$remote/") }
if ($renders.Count) { Run scp (@("-q") + ($renders | ForEach-Object { $_.FullName }) + "${Server}:$remote/render/") }
Run ssh @($Server, "bash /srv/cheatscanner/deploy/install-maps.sh")
