$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$PythonCommand = $null
$PythonPrefix = @()
if ($env:COLAB_REMOTE_PYTHON) {
    $PythonCommand = $env:COLAB_REMOTE_PYTHON
}
elseif (Get-Command python -ErrorAction SilentlyContinue) {
    $PythonCommand = 'python'
}
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    $PythonCommand = 'py'
    $PythonPrefix = @('-3.11')
}
else {
    throw 'Python 3.11 or newer is required. Install Python, reopen PowerShell, and retry.'
}

& $PythonCommand @PythonPrefix -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.11 or newer is required.'
}

$TemporaryInstaller = $null
$LocalInstaller = if ($PSScriptRoot) { Join-Path $PSScriptRoot 'install.py' } else { $null }
try {
    if ($LocalInstaller -and (Test-Path -LiteralPath $LocalInstaller -PathType Leaf)) {
        $Installer = $LocalInstaller
    }
    else {
        $TemporaryInstaller = Join-Path ([IO.Path]::GetTempPath()) ('colab-remote-install-' + [guid]::NewGuid() + '.py')
        Invoke-WebRequest -UseBasicParsing `
            -Uri 'https://raw.githubusercontent.com/RemySkye/codex-colab-remote/main/install.py' `
            -OutFile $TemporaryInstaller
        $Installer = $TemporaryInstaller
    }
    & $PythonCommand @PythonPrefix $Installer @args
    if ($LASTEXITCODE -ne 0) {
        throw "Colab Remote installer failed with exit code $LASTEXITCODE."
    }
}
finally {
    if ($TemporaryInstaller) {
        Remove-Item -LiteralPath $TemporaryInstaller -Force -ErrorAction SilentlyContinue
    }
}
