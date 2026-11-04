param(
    [string]$OutputDirectory = ".\vectora-release-key",
    [string]$Subject = "CN=Vectora Internal Code Signing"
)

$ErrorActionPreference = "Stop"
$resolvedOutput = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $resolvedOutput -Force | Out-Null
$password = Read-Host "Password for the internal test PFX" -AsSecureString
if ($password.Length -eq 0) {
    throw "A non-empty PFX password is required."
}

$certificate = New-SelfSignedCertificate `
    -Subject $Subject `
    -Type CodeSigningCert `
    -KeyAlgorithm RSA `
    -KeyLength 3072 `
    -HashAlgorithm SHA256 `
    -KeyExportPolicy Exportable `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -NotAfter (Get-Date).AddYears(5)

$pfxPath = Join-Path $resolvedOutput "cert_privado.pfx"
$cerPath = Join-Path $resolvedOutput "vectora-codesign-public.cer"
$base64Path = Join-Path $resolvedOutput "cert_base64.txt"
Export-PfxCertificate -Cert $certificate -FilePath $pfxPath -Password $password | Out-Null
Export-Certificate -Cert $certificate -FilePath $cerPath | Out-Null
[Convert]::ToBase64String([IO.File]::ReadAllBytes($pfxPath)) |
    Set-Content -LiteralPath $base64Path -NoNewline -Encoding ascii

Write-Host "Created an internal-only self-signed certificate:"
Write-Host "  PFX: $pfxPath"
Write-Host "  Public certificate: $cerPath"
Write-Host "  GitHub secret payload: $base64Path"
Write-Warning "This certificate is not publicly trusted and does not remove SmartScreen warnings. Do not install its private key on end-user machines."
