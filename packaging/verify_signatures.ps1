$ErrorActionPreference = 'Stop'
$tools = @(
    @{ Name = 'python-3.13.16-amd64.exe'; Publisher = 'Python Software Foundation' },
    @{ Name = 'innosetup-6.7.3.exe'; Publisher = 'Pyrsys B.V.' }
)
foreach ($tool in $tools) {
    $signature = Get-AuthenticodeSignature -LiteralPath (Join-Path $PSScriptRoot "vendor\tools\$($tool.Name)")
    if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike "*$($tool.Publisher)*") {
        throw "Unverified installer: $($tool.Name); status=$($signature.Status)"
    }
    [pscustomobject]@{ File = $tool.Name; Status = [string]$signature.Status; Publisher = $signature.SignerCertificate.Subject }
}
