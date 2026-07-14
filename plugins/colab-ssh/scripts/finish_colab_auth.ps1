param(
    [Parameter(Mandatory = $true)]
    [string] $AuthorizationCode
)

. (Join-Path $PSScriptRoot 'runtime.ps1')
$distro = Get-ColabRemoteDistro
$submitScript = ConvertTo-ColabRemoteWslPath -Distro $distro -WindowsPath (Join-Path $PSScriptRoot 'submit_colab_auth.sh')

$AuthorizationCode | wsl.exe -d $distro -- bash $submitScript
exit $LASTEXITCODE
