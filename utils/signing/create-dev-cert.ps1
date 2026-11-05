#Requires -Version 5.1
<#
.SYNOPSIS
    Cria um certificado de assinatura de código para builds de desenvolvimento do Vectora.

.DESCRIPTION
    Gera um certificado auto-assinado (RSA 2048, SHA-256, 3 anos) e exporta como PFX
    em frontend/electron/build-resources/dev-cert.pfx.

    Quando executado como Administrador, também registra o certificado nos stores
    Root e TrustedPublisher (LocalMachine), eliminando o aviso do SmartScreen em
    builds locais assinados com este certificado.

    Sem admin, o PFX é gerado mas o Windows continua mostrando aviso de publisher
    desconhecido — ainda assim, a assinatura resolve o bloqueio de MSI por política
    do Windows Installer.

.PARAMETER Password
    Senha do PFX exportado. Padrão: "vectora-dev".
    Defina DEV_CSC_PASSWORD no ambiente para sobrescrever sem passar o parâmetro.

.EXAMPLE
    # Execução normal (sem admin — só gera o PFX):
    pwsh -ExecutionPolicy Bypass -File utils\signing\create-dev-cert.ps1

    # Com admin — adiciona ao store de confiança (SmartScreen silenciado localmente):
    Start-Process pwsh -Verb RunAs -ArgumentList '-ExecutionPolicy Bypass -File utils\signing\create-dev-cert.ps1'

    # Senha personalizada:
    pwsh -ExecutionPolicy Bypass -File utils\signing\create-dev-cert.ps1 -Password "minha-senha"
#>
param(
    [string]$Password = $env:DEV_CSC_PASSWORD ?? "vectora-dev"
)

$ErrorActionPreference = "Stop"

$scriptDir       = Split-Path -Parent $MyInvocation.MyCommand.Path
$buildResources  = Join-Path $scriptDir "..\..\frontend\electron\build-resources"
New-Item -ItemType Directory -Force -Path $buildResources | Out-Null

$pfxPath        = Join-Path $buildResources "dev-cert.pfx"
$securePassword = ConvertTo-SecureString -String $Password -Force -AsPlainText

Write-Host ""
Write-Host "=== Vectora — Certificado de Desenvolvedor ===" -ForegroundColor Cyan

$cert = New-SelfSignedCertificate `
    -Subject       "CN=Vectora Company (Dev), O=Vectora Company, C=BR" `
    -CertStoreLocation "Cert:\CurrentUser\My" `
    -Type          CodeSigning `
    -KeySpec       Signature `
    -KeyLength     2048 `
    -HashAlgorithm SHA256 `
    -NotAfter      (Get-Date).AddYears(3) `
    -FriendlyName  "Vectora Dev Code Signing"

Export-PfxCertificate -Cert $cert -FilePath $pfxPath -Password $securePassword | Out-Null

Write-Host "PFX gerado      : $pfxPath" -ForegroundColor Green
Write-Host "Impressao digital: $($cert.Thumbprint)"
Write-Host "Validade         : ate $($cert.NotAfter.ToString('yyyy-MM-dd'))"

# Registrar nos stores de confiança do Windows (requer Admin)
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)

if ($isAdmin) {
    foreach ($storeName in @("Root", "TrustedPublisher")) {
        $store = New-Object System.Security.Cryptography.X509Certificates.X509Store($storeName, "LocalMachine")
        $store.Open("ReadWrite")
        $store.Add($cert)
        $store.Close()
    }
    Write-Host ""
    Write-Host "Certificado adicionado ao Root e TrustedPublisher (LocalMachine)." -ForegroundColor Green
    Write-Host "Builds assinados com este certificado nao disparam SmartScreen nesta maquina." -ForegroundColor Green
} else {
    Write-Host ""
    Write-Warning "Nao foi executado como Administrador."
    Write-Warning "O PFX foi gerado e sera usado para assinar os builds, mas o SmartScreen"
    Write-Warning "ainda vai avisar nesta maquina (publisher desconhecido)."
    Write-Warning "Execute novamente como Admin para silenciar o aviso localmente."
}

Write-Host ""
Write-Host "Proximos passos:" -ForegroundColor Yellow
Write-Host "  scons release    (o SConstruct detecta dev-cert.pfx automaticamente)"
if (-not $isAdmin) {
    Write-Host "  Para suprimir SmartScreen nesta maquina: execute este script como Administrador"
}
if ($Password -ne "vectora-dev") {
    Write-Host "  Defina DEV_CSC_PASSWORD=$Password no ambiente antes de rodar scons release"
}
Write-Host ""
