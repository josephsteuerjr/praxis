<#
.SYNOPSIS
    Прокси без VPN для окна и тела Праксис: SSH-туннель до машины с tinyproxy.

.DESCRIPTION
    Берёт настройки сервера из start_server_proxy.bat проекта (или из параметров), кладёт их
    в %LOCALAPPDATA%\Praxis\DesktopWindow\proxy\config.json, находит или скачивает plink.exe
    (только официальный, с проверкой подписи), проверяет туннель пробным запуском хранителя
    и запускает хранитель насовсем. Тот же приём, что у Джарвиса в Hélène («Helene Relay Proxy»).
    Задачу «Praxis Proxy» при входе в Windows заводит само окно Праксис («Система -> Это окно»).

    Тело Праксис (praxis-body) о прокси не знает. С -Dial хранитель открывает ещё и прозрачный
    выход: всё, что пришло на 127.0.0.1:443, уходит через туннель к серверу Праксис под его
    настоящим именем (TLS, WebSocket — без изменений). Телу остаётся набирать сначала этот
    адрес — это делает proxy\body-dial.ps1 (нужны права администратора).

.PARAMETER Bat
    Путь к start_server_proxy.bat: оттуда берутся сервер, учётная запись, пароль и отпечаток ключа.
.PARAMETER Server
    Машина с tinyproxy (SSH), если батник не указан.
.PARAMETER User
    SSH-учётная запись туннеля.
.PARAMETER Password
    Её пароль. Лучше не писать в командной строке: без него скрипт спросит.
.PARAMETER HostKey
    Отпечаток ключа машины (SHA256:...). Без него скрипт откажется: не совпал — не входить.
.PARAMETER ListenPort
    Местный HTTP-порт прокси (по умолчанию 18890).
.PARAMETER Dial
    Открыть прозрачный выход для тела Праксис (DialListen -> DialTarget через туннель).
.PARAMETER DialTarget
    Куда вести прозрачный выход: «адрес:443» сервера Праксис. Без него берётся сервер окна
    из electron\server.json.
.PARAMETER Plink
    Готовый plink.exe. Без него берётся plink.exe рядом с батником, затем из папки инструментов,
    затем скачивается с официального сайта PuTTY.
.PARAMETER Exe
    praxis.exe установленной программы (для пробного запуска и хранителя).
.PARAMETER SkipTest
    Не запускать пробный запуск хранителя (хранитель всё равно перезапускается).
.PARAMETER Remove
    Убрать настройки прокси (вместе с паролем) и остановить хранителя.

.EXAMPLE
    pwsh -File proxy\install-proxy.ps1 -Bat C:\Users\you\Downloads\Praxis\start_server_proxy.bat -Dial
#>
param(
    [string]$Bat,
    [string]$Server,
    [int]$Port = 22,
    [string]$User,
    [string]$Password,
    [string]$HostKey,
    [int]$ListenPort = 18890,
    [string]$RemoteHost = '127.0.0.1',
    [int]$RemotePort = 8888,
    [switch]$Dial,
    [string]$DialListen = '127.0.0.1:443',
    [string]$DialTarget = '',
    [string]$Plink,
    [string]$Exe,
    [switch]$SkipTest,
    [switch]$Remove
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$Local = $env:LOCALAPPDATA
$Folder = Join-Path $Local 'Praxis\DesktopWindow\proxy'
$ConfigFile = Join-Path $Folder 'config.json'
$StatusFile = Join-Path $Folder 'status.json'
$ToolDir = Join-Path $Local 'Praxis\Proxy'
$ToolPlink = Join-Path $ToolDir 'plink.exe'

function Stop-Keeper {
    if (!(Test-Path -LiteralPath $StatusFile)) { return }
    try { $status = Get-Content -LiteralPath $StatusFile -Raw | ConvertFrom-Json } catch { return }
    if (!$status.pid) { return }
    $process = Get-Process -Id ([int]$status.pid) -ErrorAction SilentlyContinue
    if (!$process) { return }
    New-Item -ItemType Directory -Path $Folder -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $Folder 'stop') -Force | Out-Null     # хранитель читает его раз в секунду
    if (!$process.WaitForExit(8000)) { Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue }
    if ($status.plink_pid) { Stop-Process -Id ([int]$status.plink_pid) -Force -ErrorAction SilentlyContinue }
}

if ($Remove) {
    Stop-Keeper
    foreach ($file in @($ConfigFile, (Join-Path $Folder 'plink.pw'))) {
        if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force }
    }
    Write-Host 'Настройки прокси убраны: окно снова ходит напрямую.'
    return
}

if ($Bat) {
    $text = [IO.File]::ReadAllText($Bat)
    foreach ($pair in @(@('Server', 'Server'), @('SshPort', 'Port'), @('SshUser', 'User'), @('SshPassword', 'Password'), @('ServerHostKey', 'HostKey'))) {
        $match = [regex]::Match($text, '(?m)^\s*\$' + [regex]::Escape($pair[0]) + '\s*=\s*"?([^"\r\n]+)"?')
        if ($match.Success) {
            $value = $match.Groups[1].Value.Trim()
            if ($pair[1] -eq 'Port') { $Port = [int]$value } else { Set-Variable -Name $pair[1] -Value $value }
        }
    }
}
if (!$Server -or !$User -or !$HostKey) { throw 'Нужны сервер, учётная запись и отпечаток ключа машины (-Bat подставляет их из start_server_proxy.bat).' }
if ($HostKey -notmatch '^sha256:[0-9a-z+/]+={0,2}$') { throw "Отпечаток ключа выглядит не так: $HostKey (нужно SHA256:...). Без пина не вхожу." }
if ($Server -notmatch '^[0-9a-z.:-]+$' -or $User -notmatch '^[0-9a-z_-]+$') { throw 'Сервер и учётная запись записаны знаками, которых там быть не должно.' }
if ($Dial) {
    if (!$DialTarget) {
        # Куда вести — сервер Праксис из electron\server.json (адрес окна); без него — параметром -DialTarget.
        $own = Join-Path $PSScriptRoot '..\electron\server.json'
        if (Test-Path -LiteralPath $own) {
            try { $DialTarget = ([uri]([IO.File]::ReadAllText($own) | ConvertFrom-Json).server).DnsSafeHost + ':443' } catch { $DialTarget = '' }
        }
    }
    if (!$DialTarget) { throw 'Не задано, куда вести прозрачный выход: нет electron\server.json — укажи -DialTarget «адрес:443» сервера Праксис.' }
    if ($DialListen -notmatch '^[0-9a-z.]+:[0-9]{1,5}$' -or $DialTarget -notmatch '^[0-9a-z.]+:[0-9]{1,5}$') { throw 'Прозрачный выход задаётся парой «адрес:порт»: где слушать и куда вести.' }
    if ($DialTarget.Split(':')[-1] -ne '443') { throw "Куда вести должно быть на порту 443: тело Праксис набирает только порт своего сервера ($DialTarget)." }
}
if (!$Password) {
    $secure = Read-Host -Prompt "Пароль учётной записи $User на $Server" -AsSecureString
    $Password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
if (!$Password) { throw 'Без пароля туннель не открыть.' }

# --- прежний хранитель снимается сразу: у него в памяти прежние настройки, и он держит plink.exe
Stop-Keeper

# --- plink: только официальный, подписанный автором PuTTY
function Test-PlinkSignature([string]$Path) {
    if (!(Test-Path -LiteralPath $Path -PathType Leaf)) { return $false }
    $signature = Get-AuthenticodeSignature -LiteralPath $Path
    return ($signature.Status -eq 'Valid' -and $signature.SignerCertificate -and $signature.SignerCertificate.Subject -match 'CN=Simon Tatham')
}
if ($Plink) {
    if (!(Test-PlinkSignature $Plink)) { throw "plink.exe не подписан автором PuTTY: $Plink" }
} elseif ($Bat) {
    $beside = Join-Path (Split-Path -Parent $Bat) 'plink.exe'
    if (Test-Path -LiteralPath $beside) { $Plink = $beside }
}
if (!$Plink -and (Test-PlinkSignature $ToolPlink)) { $Plink = $ToolPlink }
if (!$Plink) {
    $arch = if ([Environment]::Is64BitOperatingSystem) { 'w64' } else { 'w32' }
    New-Item -ItemType Directory -Path $ToolDir -Force | Out-Null
    $download = Join-Path $ToolDir ('plink-' + [guid]::NewGuid().ToString() + '.exe')
    try {
        Write-Host 'Качаю plink.exe с официального сайта PuTTY...'
        Invoke-WebRequest -UseBasicParsing -Uri "https://the.earth.li/~sgtatham/putty/latest/$arch/plink.exe" -OutFile $download
        if (!(Test-PlinkSignature $download)) { throw 'Скачанный plink.exe не подписан автором PuTTY.' }
        Move-Item -LiteralPath $download -Destination $ToolPlink -Force
    } finally {
        if (Test-Path -LiteralPath $download) { Remove-Item -LiteralPath $download -Force }
    }
    $Plink = $ToolPlink
}
if ($Plink -ne $ToolPlink) {
    New-Item -ItemType Directory -Path $ToolDir -Force | Out-Null
    $wanted = (Get-FileHash -LiteralPath $Plink -Algorithm SHA256).Hash
    $current = if (Test-Path -LiteralPath $ToolPlink) { (Get-FileHash -LiteralPath $ToolPlink -Algorithm SHA256).Hash } else { '' }
    if ($current -ne $wanted) { Copy-Item -LiteralPath $Plink -Destination $ToolPlink -Force }
}

# --- настройки: папка только владельцу и системе (в ней лежит пароль туннеля)
New-Item -ItemType Directory -Path $Folder -Force | Out-Null
$me = [Security.Principal.WindowsIdentity]::GetCurrent().Name
& icacls $Folder /inheritance:r /grant:r "${me}:(OI)(CI)F" /grant:r 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE) { Write-Host "Не удалось сузить права на папку $Folder — она осталась с прежними правами." }
$config = [ordered]@{
    server = $Server; port = $Port; user = $User; password = $Password; hostkey = $HostKey
    listen_port = $ListenPort; remote_host = $RemoteHost; remote_port = $RemotePort; plink = $ToolPlink
}
if ($Dial) { $config['dial_listen'] = $DialListen; $config['dial_target'] = $DialTarget }
[IO.File]::WriteAllText($ConfigFile, ($config | ConvertTo-Json), (New-Object Text.UTF8Encoding $false))
Write-Host "Настройки прокси записаны: туннель $User@$Server`:$Port -> 127.0.0.1:$ListenPort (ключ машины закреплён отпечатком)."
if ($Dial) { Write-Host "Прозрачный выход для тела Праксис: $DialListen -> $DialTarget через туннель." }

# --- пробный запуск: хранитель 20 секунд держит туннель и говорит, отвечал ли прокси
if (!$Exe) {
    $candidate = Join-Path $Local 'Programs\Praxis\praxis.exe'
    if (Test-Path -LiteralPath $candidate) { $Exe = $candidate }
}
if ($SkipTest) {
    Write-Host 'Пробный запуск пропущен.'
} elseif (!$Exe) {
    Write-Host 'praxis.exe не найден: пробный запуск пропущен (-Exe задаёт путь).'
} else {
    $out = Join-Path $Folder 'install-test.out'
    $err = Join-Path $Folder 'install-test.err'
    $process = Start-Process -FilePath $Exe -ArgumentList '--proxy-keeper', '--proxy-test=20' -PassThru -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
    if (!$process.WaitForExit(60000)) { Stop-Process -Id $process.Id -Force; throw 'Пробный запуск хранителя не закончился за минуту.' }
    $text = ''
    if (Test-Path -LiteralPath $out) { $text = [IO.File]::ReadAllText($out).Trim() }
    foreach ($file in @($out, $err)) { if (Test-Path -LiteralPath $file) { Remove-Item -LiteralPath $file -Force } }
    if ($process.ExitCode -ne 0) { throw "Через туннель прокси не ответил. Журнал: $(Join-Path $Folder 'keeper.log'). $text" }
    Write-Host "Прокси отвечает через туннель без VPN: http://127.0.0.1:$ListenPort"
}
if ($Exe) {
    Start-Process -FilePath $Exe -ArgumentList '--proxy-keeper' -WindowStyle Hidden | Out-Null
    Write-Host 'Хранитель туннеля запущен. Задачу «Praxis Proxy» при входе в Windows заведёт само окно (Система -> Это окно -> Прокси без VPN).'
}
if ($Dial) { Write-Host 'Теперь телу Праксис: proxy\body-dial.ps1 — с правами администратора (перезапускает его службу).  Запусти: pwsh -File proxy\body-dial.ps1 -Elevate' }
