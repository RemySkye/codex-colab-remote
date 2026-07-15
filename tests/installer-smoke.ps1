$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$global:InstallerTestCalls = [System.Collections.Generic.List[string]]::new()

function global:wsl.exe {
    $Arguments = [string[]] $args
    $joined = $Arguments -join ' '
    $global:InstallerTestCalls.Add("wsl $joined")
    $global:LASTEXITCODE = 0
    if ($Arguments.Count -ge 2 -and $Arguments[0] -eq '--list') {
        Write-Output 'Ubuntu'
    }
    elseif ($joined -match 'printf %s.*HOME') {
        Write-Output '/home/tester'
    }
}

function global:codex {
    $Arguments = [string[]] $args
    $global:InstallerTestCalls.Add("codex $($Arguments -join ' ')")
    $global:LASTEXITCODE = 0
}

function global:icacls.exe {
    $global:LASTEXITCODE = 0
}

$testState = Join-Path ([IO.Path]::GetTempPath()) ('colab-remote-installer-test-' + [guid]::NewGuid())
try {
    & (Join-Path $PSScriptRoot '..\install.ps1') -Distro Ubuntu -StateRoot $testState -EnableSshTunnel -DefaultLanguage r -DefaultRuntimeVersion 2026.04 -DefaultMaxLifetimeMinutes 90 -PreferHighRam
    if ($LASTEXITCODE -ne 0) { throw "Installer returned exit code $LASTEXITCODE" }

    $calls = $global:InstallerTestCalls -join "`n"
    foreach ($expected in @(
        'codex plugin marketplace add RemySkye/codex-colab-remote',
        'codex plugin add colab-remote@colab-remote',
        'wsl -d Ubuntu bash -lc'
    )) {
        if ($calls -notmatch [regex]::Escape($expected)) {
            throw "Installer did not invoke: $expected`n$calls"
        }
    }
    $config = Get-Content -Raw (Join-Path $testState 'config.json') | ConvertFrom-Json
    if (-not $config.ssh_tunnel_enabled) { throw 'Installer did not enable the requested SSH option.' }
    if ($config.ssh_secret_name -ne 'NGROK_AUTHTOKEN') { throw 'Installer wrote the wrong SSH secret name.' }
    if ($config.default_language -ne 'r') { throw 'Installer did not write the R runtime default.' }
    if ($config.default_runtime_version -ne '2026.04') { throw 'Installer did not write the runtime version default.' }
    if ($config.default_max_lifetime_minutes -ne 90) { throw 'Installer did not write the session lifetime default.' }
    if (-not $config.prefer_high_ram) { throw 'Installer did not enable High-RAM.' }
    Write-Host 'Installer mock smoke test passed.'
}
finally {
    Remove-Item Function:\global:wsl.exe -ErrorAction SilentlyContinue
    Remove-Item Function:\global:codex -ErrorAction SilentlyContinue
    Remove-Item Function:\global:icacls.exe -ErrorAction SilentlyContinue
    Remove-Variable InstallerTestCalls -Scope Global -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $testState -Recurse -Force -ErrorAction SilentlyContinue
}
