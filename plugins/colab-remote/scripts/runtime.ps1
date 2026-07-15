Set-StrictMode -Version Latest

$script:ColabRemotePluginRoot = Split-Path -Parent $PSScriptRoot

function Get-ColabRemoteDistro {
    if ($env:COLAB_REMOTE_WSL_DISTRO) {
        return $env:COLAB_REMOTE_WSL_DISTRO
    }

    $settingsPath = Join-Path $script:ColabRemotePluginRoot '.local\settings.json'
    if (Test-Path -LiteralPath $settingsPath) {
        try {
            $settings = Get-Content -Raw -LiteralPath $settingsPath | ConvertFrom-Json
            if ($settings.wslDistro) {
                return [string] $settings.wslDistro
            }
        }
        catch {
            throw "Invalid Colab Remote settings file: $settingsPath"
        }
    }

    $raw = & wsl.exe --list --quiet 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw 'WSL2 is not available. Run: wsl --install -d Ubuntu'
    }
    $distros = @(
        $raw |
            ForEach-Object { ([string] $_).Replace([string] [char] 0, [string] '').Trim() } |
            Where-Object { $_ }
    )
    if ($distros.Count -eq 0) {
        throw 'No WSL distribution is installed. Run: wsl --install -d Ubuntu'
    }
    if ($distros -contains 'Ubuntu') {
        return 'Ubuntu'
    }
    return $distros[0]
}

function Get-ColabRemoteWslHome {
    param([Parameter(Mandatory = $true)][string] $Distro)

    $wslHomePath = (& wsl.exe -d $Distro -- sh -lc 'printf %s "$HOME"').Trim()
    if ($LASTEXITCODE -ne 0 -or -not $wslHomePath.StartsWith('/')) {
        throw "Could not resolve the Linux home directory in WSL distribution '$Distro'."
    }
    return $wslHomePath
}

function ConvertTo-ColabRemoteWslPath {
    param(
        [Parameter(Mandatory = $true)][string] $Distro,
        [Parameter(Mandatory = $true)][string] $WindowsPath
    )

    $normalized = $WindowsPath.Replace('\', '/')
    $converted = (& wsl.exe -d $Distro -- wslpath -a -u -- $normalized).Trim()
    if ($LASTEXITCODE -ne 0 -or -not $converted) {
        throw "Could not convert Windows path for WSL: $WindowsPath"
    }
    return $converted
}
