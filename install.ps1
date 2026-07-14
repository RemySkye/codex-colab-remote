[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [switch] $Authenticate,
    [switch] $RunSmokeTest,
    [switch] $NoOpenPluginPage
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

function Write-Step([string] $Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-InstalledDistros {
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        return @()
    }
    return @(
        $raw |
            ForEach-Object { ([string] $_).Replace([string] [char] 0, [string] '').Trim() } |
            Where-Object { $_ }
    )
}

function Install-WindowsUv {
    $command = Get-Command uv -ErrorAction SilentlyContinue
    if ($command) {
        return $command.Source
    }
    $candidate = Join-Path $HOME '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $candidate) {
        return $candidate
    }

    Write-Step 'Installing uv for the local MCP server'
    $installer = Join-Path ([IO.Path]::GetTempPath()) 'uv-install.ps1'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) {
            throw 'The uv installer failed.'
        }
    }
    finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw "uv was installed but not found at $candidate"
    }
    return $candidate
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This installer currently targets Windows 10/11. See README.md for Linux and macOS.'
}

$sourceRoot = $PSScriptRoot
$installRoot = Join-Path $HOME 'plugins\colab-ssh'
$marketplacePath = Join-Path $HOME '.agents\plugins\marketplace.json'

Write-Step 'Checking WSL2'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL is not installed. Open an Administrator PowerShell, run "wsl --install -d Ubuntu", reboot if requested, then rerun this installer.'
}
$distros = Get-InstalledDistros
if ($distros -notcontains $Distro) {
    Write-Host "WSL distribution '$Distro' is not installed."
    Write-Host "Run in Administrator PowerShell: wsl --install -d $Distro"
    Write-Host 'Complete the Linux username setup, then rerun this installer.'
    exit 10
}

Write-Step "Installing plugin files at $installRoot"
if ([IO.Path]::GetFullPath($sourceRoot) -ne [IO.Path]::GetFullPath($installRoot)) {
    New-Item -ItemType Directory -Force -Path $installRoot | Out-Null
    $excluded = @('.git', '.venv', '.ruff_cache', '.local', '__pycache__')
    Get-ChildItem -Force -LiteralPath $sourceRoot |
        Where-Object { $_.Name -notin $excluded } |
        ForEach-Object { Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $installRoot }
}

$settingsDir = Join-Path $installRoot '.local'
New-Item -ItemType Directory -Force -Path $settingsDir | Out-Null
@{ wslDistro = $Distro } | ConvertTo-Json | Set-Content -Encoding utf8 -LiteralPath (Join-Path $settingsDir 'settings.json')

[void] (Install-WindowsUv)

Write-Step "Installing Google's official Colab CLI in $Distro"
$linuxInstall = @'
set -euo pipefail
if [ ! -x "$HOME/.local/bin/uv" ]; then
  command -v curl >/dev/null 2>&1 || { echo "curl is required inside WSL" >&2; exit 12; }
  curl -LsSf https://astral.sh/uv/install.sh -o /tmp/colab-remote-uv-install.sh
  sh /tmp/colab-remote-uv-install.sh
  rm -f /tmp/colab-remote-uv-install.sh
fi
if [ -x "$HOME/.local/bin/colab" ]; then
  "$HOME/.local/bin/uv" tool upgrade google-colab-cli
else
  "$HOME/.local/bin/uv" tool install google-colab-cli
fi
"$HOME/.local/bin/colab" version
'@
$linuxBytes = [Text.Encoding]::UTF8.GetBytes($linuxInstall.Replace("`r`n", "`n"))
$linuxEncoded = [Convert]::ToBase64String($linuxBytes)
& wsl.exe -d $Distro -- bash -lc "printf %s '$linuxEncoded' | base64 -d | bash"
if ($LASTEXITCODE -ne 0) {
    throw 'Installing google-colab-cli inside WSL failed.'
}

Write-Step 'Registering the personal Codex marketplace entry'
$marketplaceDir = Split-Path -Parent $marketplacePath
New-Item -ItemType Directory -Force -Path $marketplaceDir | Out-Null
if (Test-Path -LiteralPath $marketplacePath) {
    $marketplace = Get-Content -Raw -LiteralPath $marketplacePath | ConvertFrom-Json
}
else {
    $marketplace = [pscustomobject]@{
        name = 'personal'
        interface = [pscustomobject]@{ displayName = 'Personal' }
        plugins = @()
    }
}
if (-not $marketplace.PSObject.Properties['interface']) {
    $marketplace | Add-Member -NotePropertyName interface -NotePropertyValue ([pscustomobject]@{ displayName = 'Personal' })
}
if (-not $marketplace.PSObject.Properties['plugins']) {
    $marketplace | Add-Member -NotePropertyName plugins -NotePropertyValue @()
}
$entry = [pscustomobject]@{
    name = 'colab-ssh'
    source = [pscustomobject]@{ source = 'local'; path = './plugins/colab-ssh' }
    policy = [pscustomobject]@{ installation = 'AVAILABLE'; authentication = 'ON_INSTALL' }
    category = 'Developer Tools'
}
$kept = @($marketplace.plugins | Where-Object { $_.name -ne 'colab-ssh' })
$marketplace.plugins = @($kept + $entry)
$marketplace | ConvertTo-Json -Depth 10 | Set-Content -Encoding utf8 -LiteralPath $marketplacePath

$wrapper = Join-Path $installRoot 'scripts\colab.ps1'
Write-Step 'Verifying the installed wrapper'
& $wrapper version
if ($LASTEXITCODE -ne 0) {
    throw 'The Colab CLI wrapper verification failed.'
}

if ($Authenticate -or $RunSmokeTest) {
    Write-Step 'Authenticating with Google Colab'
    & $wrapper sessions
    if ($LASTEXITCODE -ne 0) {
        throw 'Colab authentication failed.'
    }
    & wsl.exe -d $Distro -- sh -lc 'token="$HOME/.config/colab-cli/token.json"; if [ -f "$token" ]; then chmod 600 "$token"; fi'
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not secure the cached Colab OAuth token.'
    }
}

if ($RunSmokeTest) {
    Write-Step 'Running an optional CPU smoke test'
    $session = 'codex-install-smoke-' + (Get-Random -Minimum 1000 -Maximum 9999)
    $created = $false
    try {
        & $wrapper new -s $session
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the smoke-test session.' }
        $created = $true
        'print("COLAB_REMOTE_INSTALL_OK")' | & $wrapper exec -s $session --timeout 120
        if ($LASTEXITCODE -ne 0) { throw 'Remote smoke-test execution failed.' }
    }
    finally {
        if ($created) {
            & $wrapper stop -s $session
            $stopExitCode = $LASTEXITCODE
            $sessionListing = @(& $wrapper sessions 2>&1)
            $sessionsExitCode = $LASTEXITCODE
            $sessionListing | ForEach-Object { Write-Host $_ }
            if ($stopExitCode -ne 0 -or $sessionsExitCode -ne 0 -or ($sessionListing -join "`n") -match [regex]::Escape($session)) {
                throw "Smoke-test cleanup could not be verified. Run: & '$wrapper' stop -s $session"
            }
        }
    }
}

if (-not $NoOpenPluginPage) {
    $encodedMarketplace = [uri]::EscapeDataString($marketplacePath)
    Start-Process "codex://plugins/colab-ssh?marketplacePath=$encodedMarketplace"
}

Write-Host "`nColab Remote is ready." -ForegroundColor Green
Write-Host 'In the Codex plugin page, choose Install or Update, then start a new task.'
if (-not $Authenticate -and -not $RunSmokeTest) {
    Write-Host "Authenticate now or on first use: & '$wrapper' sessions"
}
