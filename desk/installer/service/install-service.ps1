# Установка ОПЦИОНАЛЬНОЙ службы Hélène (нужны права администратора).
#
# Служба добавляет одно: агент жив до логина и без окна. По умолчанию продукт
# работает и без неё — харнесс живёт дочерним процессом окна/трея. Руки
# computer-use службой не заменяются: они требуют интерактивной сессии (трея).
#
# Запуск из папки поставки:  powershell -ExecutionPolicy Bypass -File .\install-service.ps1
param(
    [string] $Config = (Join-Path $PSScriptRoot "helene.json")
)
$ErrorActionPreference = "Stop"

$svc = Join-Path $PSScriptRoot "helene-svc.exe"
if (-not (Test-Path $svc)) { throw "рядом нет helene-svc.exe" }
if (-not (Test-Path $Config)) { throw "нет конфига: $Config" }
$identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "нужны права администратора (установка службы — единственный шаг с UAC)"
}

& $svc install --config (Resolve-Path $Config)
if ($LASTEXITCODE -ne 0) { throw "helene-svc install вернул $LASTEXITCODE" }

# Перезапуск при падении самой службы (детей внутри перезапускает супервизор).
sc.exe failure Helene reset= 86400 actions= restart/5000/restart/15000/restart/60000 | Out-Null
Write-Host "готово: служба Hélène установлена (autostart) и запущена"
Write-Host "лог службы: <tree>\service.log · снять: .\uninstall-service.ps1"
