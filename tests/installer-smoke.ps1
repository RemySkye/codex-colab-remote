$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$global:InstallerTestCalls = [System.Collections.Generic.List[string]]::new()
function global:colab-remote-test-python {
    $Arguments = [string[]] $args
    $global:InstallerTestCalls.Add($Arguments -join ' ')
    $global:LASTEXITCODE = 0
}

$previousPython = $env:COLAB_REMOTE_PYTHON
try {
    $env:COLAB_REMOTE_PYTHON = 'colab-remote-test-python'
    & (Join-Path $PSScriptRoot '..\install.ps1') `
        -Distro Ubuntu `
        -DefaultLanguage r `
        -DefaultRuntimeVersion 2026.04 `
        -DefaultMaxLifetimeMinutes 90 `
        -PreferHighRam

    if ($global:InstallerTestCalls.Count -ne 2) {
        throw "Expected a version check and one installer call: $($global:InstallerTestCalls -join '; ')"
    }
    $installerCall = $global:InstallerTestCalls[1]
    foreach ($expected in @(
        'install.py',
        '-Distro Ubuntu',
        '-DefaultLanguage r',
        '-DefaultRuntimeVersion 2026.04',
        '-DefaultMaxLifetimeMinutes 90',
        '-PreferHighRam'
    )) {
        if ($installerCall -notmatch [regex]::Escape($expected)) {
            throw "PowerShell launcher did not forward: $expected`n$installerCall"
        }
    }
    Write-Host 'PowerShell launcher smoke test passed.'
}
finally {
    $env:COLAB_REMOTE_PYTHON = $previousPython
    Remove-Item Function:\global:colab-remote-test-python -ErrorAction SilentlyContinue
    Remove-Variable InstallerTestCalls -Scope Global -ErrorAction SilentlyContinue
}
