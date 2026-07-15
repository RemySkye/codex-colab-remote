[CmdletBinding()]
param(
    [string] $Distro = 'Ubuntu',
    [ValidateSet('cpu', 't4', 'l4', 'g4', 'h100', 'a100', 'v5e-1', 'v6e-1')]
    [string] $DefaultAccelerator = 'cpu',
    [ValidateSet('python', 'julia', 'r')]
    [string] $DefaultLanguage = 'python',
    [ValidatePattern('^(latest|20\d{2}\.\d{2})$')]
    [string] $DefaultRuntimeVersion = 'latest',
    [ValidateRange(0, 1440)]
    [int] $DefaultMaxLifetimeMinutes = 0,
    [switch] $PreferHighRam,
    [string[]] $AllowedLocalRoot = @(),
    [switch] $DisableNotifications,
    [switch] $EnableSshTunnel,
    [string] $SshSecretName = 'NGROK_AUTHTOKEN',
    [switch] $SkipAuthentication,
    [switch] $RunSmokeTest,
    [string] $StateRoot = (Join-Path $HOME '.codex\colab-remote')
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$Repository = 'RemySkye/codex-colab-remote'
$Marketplace = 'colab-remote'
$Plugin = 'colab-remote'
$UvVersion = '0.11.28'
$ColabCliVersion = '0.6.0'
$UvWindowsSha256 = '09AC738E5C5EEA1D94284B80CEB49B81097891218A79751D08116CD8552B492D'
$UvShellSha256 = 'B7B3FE80CAD1142A2A5794050B7DB7B3291D1BAC1423B0732571DD9366E8CA8B'

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
        Invoke-WebRequest -UseBasicParsing -Uri "https://astral.sh/uv/$UvVersion/install.ps1" -OutFile $installer
        if ((Get-FileHash -LiteralPath $installer -Algorithm SHA256).Hash -ne $UvWindowsSha256) {
            throw 'The downloaded Windows uv installer checksum did not match.'
        }
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
if ($SshSecretName -notmatch '^[A-Za-z][A-Za-z0-9_]{2,63}$') {
    throw 'SshSecretName must start with a letter and contain only letters, numbers, and underscores.'
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
$linuxInstall = @"
set -euo pipefail
if [ ! -x "`$HOME/.local/bin/uv" ]; then
  command -v curl >/dev/null 2>&1 || { echo "curl is required inside WSL" >&2; exit 12; }
  curl -LsSf https://astral.sh/uv/$UvVersion/install.sh -o /tmp/colab-remote-uv-install.sh
  printf '%s  %s\n' '$UvShellSha256' /tmp/colab-remote-uv-install.sh | sha256sum -c -
  sh /tmp/colab-remote-uv-install.sh
  rm -f /tmp/colab-remote-uv-install.sh
fi
"`$HOME/.local/bin/uv" tool install --force "google-colab-cli==$ColabCliVersion"
"`$HOME/.local/bin/colab" version
"@
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

Write-Step 'Saving safe Colab Remote defaults'
$approvedRoots = @()
foreach ($root in $AllowedLocalRoot) {
    $resolved = (Resolve-Path -LiteralPath $root -ErrorAction Stop).Path
    if (-not (Test-Path -LiteralPath $resolved -PathType Container)) {
        throw "Allowed local root must be a directory: $root"
    }
    $approvedRoots += $resolved
}
New-Item -ItemType Directory -Path $StateRoot -Force | Out-Null
$config = [ordered]@{
    distro = $Distro
    default_accelerator = $DefaultAccelerator
    default_language = $DefaultLanguage
    default_runtime_version = $DefaultRuntimeVersion
    default_high_ram = [bool] $PreferHighRam
    default_timeout_seconds = 3600
    compute_warning_minutes = 60
    default_max_lifetime_minutes = $DefaultMaxLifetimeMinutes
    notifications_enabled = -not [bool] $DisableNotifications
    require_cost_acknowledgement = $true
    allowed_local_roots = @($approvedRoots | Sort-Object -Unique)
    ssh_tunnel_enabled = [bool] $EnableSshTunnel
    ssh_secret_name = $SshSecretName
}
$configJson = ($config | ConvertTo-Json -Depth 4) + "`n"
$utf8NoBom = [Text.UTF8Encoding]::new($false)
[IO.File]::WriteAllText((Join-Path $StateRoot 'config.json'), $configJson, $utf8NoBom)
$windowsIdentity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls.exe $StateRoot /inheritance:r /grant:r "${windowsIdentity}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not restrict access to the Colab Remote state directory.' }

$linuxHome = (& wsl.exe -d $Distro -- sh -lc 'printf %s "$HOME"').Trim()
if (-not $linuxHome) { throw "Could not discover the Linux home directory in $Distro." }
$linuxColab = "$linuxHome/.local/bin/colab"

if (-not $SkipAuthentication) {
    Write-Step 'Authenticating directly with Google Colab'
    Write-Host 'Follow the Google sign-in link. If Google displays a one-time code, paste it back into this terminal.'
    $authentication = @'
set -e
umask 077
env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG '__COLAB__' --auth oauth2 sessions
token="$HOME/.config/colab-cli/token.json"
if [ -f "$token" ]; then chmod 600 "$token"; fi
'@.Replace('__COLAB__', $linuxColab)
    & wsl.exe -d $Distro -- bash -lc $authentication
    if ($LASTEXITCODE -ne 0) { throw 'Google Colab authentication failed.' }
}

if ($RunSmokeTest) {
    Write-Step 'Creating a temporary CPU runtime for verification'
    $session = 'codex-install-smoke-' + (Get-Random -Minimum 1000 -Maximum 9999)
    $created = $false
    try {
        & wsl.exe -d $Distro -- env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG $linuxColab --auth oauth2 new -s $session
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the smoke-test session.' }
        $created = $true
        'print("COLAB_REMOTE_INSTALL_OK")' | & wsl.exe -d $Distro -- env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG $linuxColab --auth oauth2 exec -s $session --timeout 120
        if ($LASTEXITCODE -ne 0) { throw 'Remote smoke-test execution failed.' }
    }
    finally {
        if ($created) {
            & wsl.exe -d $Distro -- env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG $linuxColab --auth oauth2 stop -s $session
            $stopExitCode = $LASTEXITCODE
            $sessionListing = @(& wsl.exe -d $Distro -- env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG $linuxColab --auth oauth2 sessions 2>&1)
            $sessionsExitCode = $LASTEXITCODE
            $sessionListing | ForEach-Object { Write-Host $_ }
            if ($stopExitCode -ne 0 -or $sessionsExitCode -ne 0 -or ($sessionListing -join "`n") -match [regex]::Escape($session)) {
                throw "Smoke-test cleanup could not be verified. Run: wsl -d $Distro -- $linuxColab --auth oauth2 stop -s $session"
            }
        }
    }
}

Write-Host "`nColab Remote is installed." -ForegroundColor Green
Write-Host 'Restart Codex or start a new task so it loads the plugin.'
if ($EnableSshTunnel) {
    Write-Host 'SSH is opt-in. Add your ngrok token to Colab Secrets and use a paid Colab plan with positive compute units.' -ForegroundColor Yellow
}
if ($SkipAuthentication) {
    Write-Host "Authenticate later inside WSL with: umask 077; env -u GOOGLE_APPLICATION_CREDENTIALS -u CLOUDSDK_CONFIG $linuxColab --auth oauth2 sessions; chmod 600 ~/.config/colab-cli/token.json"
}
