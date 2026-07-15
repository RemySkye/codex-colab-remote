$ErrorActionPreference = 'Stop'

$uv = Get-Command uv -ErrorAction SilentlyContinue
if ($uv) {
    $uvPath = $uv.Source
}
else {
    $candidate = Join-Path $HOME '.local\bin\uv.exe'
    if (-not (Test-Path -LiteralPath $candidate)) {
        throw 'uv is required for Colab Remote. Run the repository install.ps1.'
    }
    $uvPath = $candidate
}

$pluginRoot = Split-Path -Parent $PSScriptRoot
& $uvPath run --project $pluginRoot python (Join-Path $pluginRoot 'mcp\server.py')
exit $LASTEXITCODE
