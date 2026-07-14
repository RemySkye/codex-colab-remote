[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [switch] $SkipAuthentication,
    [switch] $RunSmokeTest
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repository = 'RemySkye/codex-colab-remote'
$Marketplace = 'colab-remote'
$Plugin = 'colab-ssh'

function Write-Step([string] $Message) {
    Write-Host "`n==> $Message" -ForegroundColor Cyan
}

function Get-InstalledDistros {
    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { return @() }
    return @(
        $raw |
            ForEach-Object { ([string] $_).Replace([string] [char] 0, [string] '').Trim() } |
            Where-Object { $_ }
    )
}

function Install-WindowsUv {
    if ((Get-Command uv -ErrorAction SilentlyContinue) -or
        (Test-Path -LiteralPath (Join-Path $HOME '.local\bin\uv.exe'))) {
        return
    }

    Write-Step 'Installing uv on Windows for the plugin MCP server'
    $installer = Join-Path ([IO.Path]::GetTempPath()) 'uv-install.ps1'
    try {
        Invoke-WebRequest -UseBasicParsing -Uri 'https://astral.sh/uv/install.ps1' -OutFile $installer
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
        if ($LASTEXITCODE -ne 0) { throw 'The Windows uv installer failed.' }
    }
    finally {
        Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
    }
}

if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw 'This bootstrap currently supports Windows 10/11 with WSL2.'
}

Write-Step 'Checking WSL2 and Ubuntu'
if (-not (Get-Command wsl.exe -ErrorAction SilentlyContinue)) {
    throw 'WSL is missing. In Administrator PowerShell run: wsl --install -d Ubuntu. Reboot if requested, finish Ubuntu username setup, then rerun this command.'
}
if ((Get-InstalledDistros) -notcontains $Distro) {
    throw "WSL distribution '$Distro' is missing. In Administrator PowerShell run: wsl --install -d $Distro. Finish Linux username setup, then rerun this command."
}

Install-WindowsUv

Write-Step "Installing uv and Google's official Colab CLI inside $Distro"
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
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($linuxInstall.Replace("`r`n", "`n")))
& wsl.exe -d $Distro -- bash -lc "printf %s '$encoded' | base64 -d | bash"
if ($LASTEXITCODE -ne 0) { throw 'Installing google-colab-cli inside WSL failed.' }

if (-not (Get-Command codex -ErrorAction SilentlyContinue)) {
    throw 'The Codex CLI was not found. Install or enable Codex CLI, reopen PowerShell, and rerun this command.'
}

Write-Step 'Adding or refreshing the Codex plugin marketplace'
& codex plugin marketplace add $Repository
if ($LASTEXITCODE -ne 0) {
    & codex plugin marketplace upgrade $Marketplace
    if ($LASTEXITCODE -ne 0) {
        throw "Could not add or refresh the '$Marketplace' marketplace."
    }
}

Write-Step 'Installing or updating the Colab Remote plugin'
& codex plugin add "$Plugin@$Marketplace"
if ($LASTEXITCODE -ne 0) { throw 'Codex could not install the Colab Remote plugin.' }

$linuxHome = (& wsl.exe -d $Distro -- sh -lc 'printf %s "$HOME"').Trim()
if (-not $linuxHome) { throw "Could not discover the Linux home directory in $Distro." }
$linuxColab = "$linuxHome/.local/bin/colab"

if (-not $SkipAuthentication) {
    Write-Step 'Authenticating directly with Google Colab'
    Write-Host 'Follow the Google sign-in link. If Google displays a one-time code, paste it back into this terminal.'
    & wsl.exe -d $Distro -- $linuxColab sessions
    if ($LASTEXITCODE -ne 0) { throw 'Google Colab authentication failed.' }
    & wsl.exe -d $Distro -- sh -lc 'token="$HOME/.config/colab-cli/token.json"; if [ -f "$token" ]; then chmod 600 "$token"; fi'
    if ($LASTEXITCODE -ne 0) { throw 'Could not secure the cached Colab OAuth token.' }
}

if ($RunSmokeTest) {
    Write-Step 'Creating a temporary CPU runtime for verification'
    $session = 'codex-install-smoke-' + (Get-Random -Minimum 1000 -Maximum 9999)
    $created = $false
    try {
        & wsl.exe -d $Distro -- $linuxColab new -s $session
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the smoke-test session.' }
        $created = $true
        'print("COLAB_REMOTE_INSTALL_OK")' | & wsl.exe -d $Distro -- $linuxColab exec -s $session --timeout 120
        if ($LASTEXITCODE -ne 0) { throw 'Remote smoke-test execution failed.' }
    }
    finally {
        if ($created) {
            & wsl.exe -d $Distro -- $linuxColab stop -s $session
            $stopExitCode = $LASTEXITCODE
            $sessionListing = @(& wsl.exe -d $Distro -- $linuxColab sessions 2>&1)
            $sessionsExitCode = $LASTEXITCODE
            $sessionListing | ForEach-Object { Write-Host $_ }
            if ($stopExitCode -ne 0 -or $sessionsExitCode -ne 0 -or ($sessionListing -join "`n") -match [regex]::Escape($session)) {
                throw "Smoke-test cleanup could not be verified. Run: wsl -d $Distro -- $linuxColab stop -s $session"
            }
        }
    }
}

Write-Host "`nColab Remote is installed." -ForegroundColor Green
Write-Host 'Restart Codex or start a new task so it loads the plugin.'
if ($SkipAuthentication) {
    Write-Host "Authenticate later with: wsl -d $Distro -- $linuxColab sessions"
}
