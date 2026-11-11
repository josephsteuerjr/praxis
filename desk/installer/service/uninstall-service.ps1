# Снятие службы Hélène (права администратора).
$ErrorActionPreference = "Stop"
$svc = Join-Path $PSScriptRoot "helene-svc.exe"
if (-not (Test-Path $svc)) { throw "рядом нет helene-svc.exe" }
& $svc uninstall
if ($LASTEXITCODE -ne 0) { throw "helene-svc uninstall вернул $LASTEXITCODE" }
Write-Host "служба снята; продукт продолжает работать как раньше — из окна/трея"
