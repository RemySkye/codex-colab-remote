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

try {
    & (Join-Path $PSScriptRoot '..\install.ps1') -Distro Ubuntu
    if ($LASTEXITCODE -ne 0) { throw "Installer returned exit code $LASTEXITCODE" }

    $calls = $global:InstallerTestCalls -join "`n"
    foreach ($expected in @(
        'codex plugin marketplace add RemySkye/codex-colab-remote',
        'codex plugin add colab-ssh@colab-remote',
        'wsl -d Ubuntu /home/tester/.local/bin/colab sessions'
    )) {
        if ($calls -notmatch [regex]::Escape($expected)) {
            throw "Installer did not invoke: $expected`n$calls"
        }
    }
    Write-Host 'Installer mock smoke test passed.'
}
finally {
    Remove-Item Function:\global:wsl.exe -ErrorAction SilentlyContinue
    Remove-Item Function:\global:codex -ErrorAction SilentlyContinue
    Remove-Variable InstallerTestCalls -Scope Global -ErrorAction SilentlyContinue
}
