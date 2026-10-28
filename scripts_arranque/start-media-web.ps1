param([switch]$Install)
$ErrorActionPreference = 'Stop'
$repoDir = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoDir 'services\media-api'
$webDir = Join-Path $repoDir 'apps\media-web'
$pythonExe = Join-Path $apiDir '.venv\Scripts\python.exe'
if ($Install) {
    if (-not (Test-Path $pythonExe)) { python -m venv (Join-Path $apiDir '.venv'); if ($LASTEXITCODE) { throw 'No se pudo crear el entorno Python.' } }
    & $pythonExe -m ensurepip --upgrade
    & $pythonExe -m pip install -r (Join-Path $apiDir 'requirements.lock')
    if ($LASTEXITCODE) { throw 'No se pudieron instalar las dependencias.' }
    & $pythonExe -m pip install --no-deps -e $apiDir
    if ($LASTEXITCODE) { throw 'No se pudieron instalar las dependencias de la API.' }
    Push-Location $webDir
    try { npm.cmd ci; if ($LASTEXITCODE) { throw 'No se pudieron instalar las dependencias web.' } } finally { Pop-Location }
}
if (-not (Test-Path $pythonExe)) { throw 'Ejecuta primero .\scripts\start-media-web.ps1 -Install' }
foreach ($port in @(8010,3000)) {
    if (Get-NetTCPConnection -State Listen -LocalPort $port -ErrorAction SilentlyContinue) { throw "El puerto $port ya está ocupado. Cierra la instancia anterior antes de iniciar otra." }
}
Write-Host 'El MCP media-core debe estar iniciado en el puerto 8001.'
Write-Host 'MedIA: http://127.0.0.1:3000   API: http://127.0.0.1:8010/docs'
Write-Host 'Pulsa Ctrl+C para detener ambos procesos.'
$apiProcess = Start-Process -FilePath $pythonExe -ArgumentList @('-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8010') -WorkingDirectory $apiDir -WindowStyle Hidden -PassThru
try {
    Push-Location $webDir
    try { npm.cmd run dev } finally { Pop-Location }
} finally {
    if (-not $apiProcess.HasExited) { Stop-Process -Id $apiProcess.Id }
}
