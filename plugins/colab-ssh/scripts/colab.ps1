param(
    [Parameter(Position = 0, ValueFromRemainingArguments = $true)]
    [string[]] $ColabArguments,

    [Parameter(ValueFromPipeline = $true)]
    [AllowEmptyString()]
    [string] $PipelineInput
)

begin {
    . (Join-Path $PSScriptRoot 'runtime.ps1')
    $pipelineLines = [System.Collections.Generic.List[string]]::new()
}

process {
    if ($PSBoundParameters.ContainsKey('PipelineInput')) {
        $pipelineLines.Add($PipelineInput)
    }
}

end {
    $distro = Get-ColabRemoteDistro
    $wslHome = Get-ColabRemoteWslHome -Distro $distro
    $colabPath = "$wslHome/.local/bin/colab"

    & wsl.exe -d $distro -- test -x $colabPath
    if ($LASTEXITCODE -ne 0) {
        throw "Google Colab CLI is not installed in '$distro'. Run the repository install.ps1."
    }

    $converted = foreach ($argument in $ColabArguments) {
        if ($argument -match '^[A-Za-z]:[\\/]') {
            ConvertTo-ColabRemoteWslPath -Distro $distro -WindowsPath $argument
        }
        else {
            $argument
        }
    }

    $wslArguments = @('-d', $distro, '--', $colabPath) + [string[]] $converted
    if ($pipelineLines.Count -gt 0) {
        ($pipelineLines -join [Environment]::NewLine) | & wsl.exe @wslArguments
    }
    else {
        & wsl.exe @wslArguments
    }
    exit $LASTEXITCODE
}
