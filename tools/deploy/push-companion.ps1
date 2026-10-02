<#
.SYNOPSIS
  Copies the companion app installer from this PC to the Cheatscanner server, where the front page offers it.

.DESCRIPTION
  Sends companion\release\Cheatscanner-Setup-<version>.exe (the newest one, built with `npm run dist`) to
  /srv/cheatscanner/data/downloads on the server. The website serves the newest version in that folder at
  /download/companion and shows the download button; nothing restarts. The file is copied under a temporary
  name first and renamed when complete, so visitors never get half a file.
  Uses the ssh and scp programs that come with Windows 10/11 and your SSH key.

.EXAMPLE
  .\tools\deploy\push-companion.ps1
  .\tools\deploy\push-companion.ps1 -Installer C:\path\Cheatscanner-Setup-0.1.0.exe
  .\tools\deploy\push-companion.ps1 -Server deploy@203.0.113.10
#>
param(
    [string]$Installer,
    [string]$Server = $(if ($env:CHEATSCANNER_SSH) { $env:CHEATSCANNER_SSH } else { "deploy@cheatscanner.eu" })
)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$remote = "/srv/cheatscanner/data/downloads"

function Run($exe, [string[]]$arguments) {
    & $exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "$exe failed (exit code $LASTEXITCODE)" }
}

if (-not $Installer) {
    $release = Join-Path $root "companion\release"
    $newest = Get-ChildItem -Path $release -Filter "Cheatscanner-Setup-*.exe" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Cheatscanner-Setup-(\d+(\.\d+)*)\.exe$' } |
        Sort-Object { [version]($_.Name -replace '^Cheatscanner-Setup-|\.exe$', '' -replace '^(\d+)$', '$1.0') } |
        Select-Object -Last 1
    if (-not $newest) { throw "No Cheatscanner-Setup-<version>.exe in $release. Build it with: cd companion; npm run dist" }
    $Installer = $newest.FullName
}
$name = Split-Path -Leaf $Installer
if ($name -notmatch '^Cheatscanner-Setup-\d+(\.\d+)*\.exe$') { throw "The file must be named Cheatscanner-Setup-<version>.exe, not $name" }

$mb = [math]::Round((Get-Item $Installer).Length / 1MB, 1)
Write-Host "Sending $name ($mb MB) to $Server"
Run ssh @($Server, "mkdir -p $remote")
Run scp @("-q", $Installer, "${Server}:$remote/$name.part")
Run ssh @($Server, "mv -f $remote/$name.part $remote/$name && ls -l $remote")
Write-Host "Done. The front page now offers $name (https://cheatscanner.eu/download/companion)."
