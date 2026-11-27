<#
.SYNOPSIS
    Телу Праксис — дорога к её серверу через прокси без VPN.

.DESCRIPTION
    Тело (praxis-body) о прокси не знает, но умеет набирать сервер по заданным адресам,
    сохраняя его настоящее имя для TLS и WebSocket. Этот скрипт ставит первой такой дорогой
    прозрачный выход хранителя прокси (по умолчанию 127.0.0.1:443 — через туннель к серверу
    Праксис), а прежние адреса оставляет запасными: туннель не стоит — тело идёт напрямую.
    Прежние настройки копируются в %LOCALAPPDATA%\Praxis\Body\install-backups\<время>-proxy-dial.

    Соединение с сервером держит служба PraxisSystemRouter, поэтому нужны права администратора:
    с -Elevate скрипт сам просит их (запрос UAC). Годится и для служебного Windows PowerShell:
    только его штатные команды и schtasks.exe.

.PARAMETER Listen
    Адрес прозрачного выхода хранителя прокси (тот же, что dial_listen в настройках прокси).
.PARAMETER Elevate
    Перезапустить себя с правами администратора (запрос UAC).
.PARAMETER Undo
    Вернуть прежние настройки тела (из копии, сделанной до того, как прозрачный выход появился).

.EXAMPLE
    pwsh -File proxy\body-dial.ps1 -Elevate
#>
param(
    [string]$Listen = '127.0.0.1:443',
    [switch]$Elevate,
    [switch]$Undo
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.Encoding]::UTF8

$principal = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
$administrator = $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if ($Elevate -and !$administrator) {
    $arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" -Listen {1}' -f $PSCommandPath, $Listen
    if ($Undo) { $arguments += ' -Undo' }
    $child = Start-Process -FilePath "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" -Verb RunAs -PassThru -Wait -ArgumentList $arguments
    exit $child.ExitCode
}
if (!$administrator) { throw 'Нужны права администратора: соединение тела с сервером держит служба PraxisSystemRouter. Запусти с -Elevate.' }
# С повышением прав процесс наследует окружение запустившего (например, PowerShell 7 с его путями
# к модулям): служебному Windows PowerShell возвращаем его собственные пути.
if ($PSVersionTable.PSEdition -eq 'Desktop') {
    $env:PSModulePath = "$env:ProgramFiles\WindowsPowerShell\Modules;$env:SystemRoot\System32\WindowsPowerShell\v1.0\Modules"
}

$Root = Join-Path $env:ProgramData 'Praxis\Body'
$Files = @((Join-Path $Root 'body.json'), (Join-Path $Root 'service-body.json'))
$BackupRoot = Join-Path $env:LOCALAPPDATA 'Praxis\Body\install-backups'
$ServiceName = 'PraxisSystemRouter'
$TaskName = 'Praxis Body'
$Stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$Schtasks = "$env:SystemRoot\System32\schtasks.exe"

foreach ($file in $Files) { if (!(Test-Path -LiteralPath $file)) { throw "Нет настроек тела: $file" } }

function Read-Json([string]$Path) { Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json }
# Писать в тот же файл, а не новым поверх: у файла свои права (читать — владельцу, менять — только системе)
function Write-JsonInPlace([string]$Path, $Value) {
    $text = ($Value | ConvertTo-Json -Depth 8)
    $stream = [IO.File]::Open($Path, [IO.FileMode]::Create, [IO.FileAccess]::Write, [IO.FileShare]::Read)
    try {
        $bytes = (New-Object Text.UTF8Encoding $false).GetBytes($text + [Environment]::NewLine)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally { $stream.Dispose() }
}
function Stop-Body {
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') {
        Stop-Service -Name $ServiceName -Force
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
    try { & $Schtasks /End /TN $TaskName 2>$null | Out-Null } catch { }          # хозяин интерактивного сеанса
    # его тело, если осталось, уходит тоже (оно в задании перезапускается и перечитывает настройки).
    # Спросить процессы можно по-разному: Get-CimInstance (PowerShell 7) или Get-WmiObject (Windows PowerShell).
    try {
        $left = @()
        if (Get-Command Get-CimInstance -ErrorAction SilentlyContinue) { $left = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue) }
        elseif (Get-Command Get-WmiObject -ErrorAction SilentlyContinue) { $left = @(Get-WmiObject Win32_Process -ErrorAction SilentlyContinue) }
        foreach ($item in ($left | Where-Object { $_.Name -like 'praxis-body*.exe' -and $_.CommandLine -like "*$($Files[0])*" })) {
            if (Get-Command Invoke-CimMethod -ErrorAction SilentlyContinue) { $null = Invoke-CimMethod -InputObject $item -MethodName Terminate -ErrorAction SilentlyContinue }
            else { $null = $item.Terminate() }
        }
    } catch { }
}
function Start-Body {
    $failure = $null
    try {
        $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
        if ($service) {
            Start-Service -Name $ServiceName
            $service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
        }
    } catch { $failure = $_ }
    try { & $Schtasks /Run /TN $TaskName 2>$null | Out-Null } catch { }
    if ($failure) { throw $failure }
}

if ($Undo) {
    # копия с прежними настройками — та, где прозрачного выхода ещё не было (самая ранняя такая)
    $original = Get-ChildItem -LiteralPath $BackupRoot -Directory -Filter '*-proxy-dial' -ErrorAction SilentlyContinue |
        Sort-Object Name |
        Where-Object {
            $copy = Join-Path $_.FullName 'service-body.json'
            $old = $null
            if (Test-Path -LiteralPath $copy) { try { $old = (Read-Json $copy).dial_addresses } catch { $old = $null } }
            $null -ne $old -and -not (@($old) -contains $Listen)
        } |
        Select-Object -First 1
    if (!$original) { throw 'Копии с прежними настройками нет.' }
    Stop-Body
    foreach ($file in $Files) {
        $copy = Join-Path $original.FullName (Split-Path -Leaf $file)
        if (Test-Path -LiteralPath $copy) { Copy-Item -LiteralPath $copy -Destination $file -Force }
    }
    Start-Body
    Write-Host "Настройки тела возвращены из $($original.Name)."
    exit 0
}

# прозрачный выход должен уже слушать хранитель прокси
$listening = $false
try {
    $client = New-Object Net.Sockets.TcpClient
    $client.Connect($Listen.Split(':')[0], [int]$Listen.Split(':')[1])
    $listening = $true
    $client.Dispose()
} catch { $listening = $false }
if (!$listening) { throw "Прозрачный выход $Listen не слушается: сначала настрой прокси (install-proxy.ps1 -Dial)." }

$backup = Join-Path $BackupRoot "$Stamp-proxy-dial"
New-Item -ItemType Directory -Path $backup -Force | Out-Null
foreach ($file in $Files) { Copy-Item -LiteralPath $file -Destination (Join-Path $backup (Split-Path -Leaf $file)) }
$since = [DateTime]::UtcNow.AddSeconds(-2).ToString('yyyy-MM-ddTHH:mm:ss')
Stop-Body
try {
    foreach ($file in $Files) {
        $config = Read-Json $file
        $old = @(); if ($config.dial_addresses) { $old = @($config.dial_addresses) }
        $addresses = @($Listen) + @($old | Where-Object { $_ -ne $Listen })
        if (!$config.PSObject.Properties['dial_addresses']) { $config | Add-Member -NotePropertyName dial_addresses -NotePropertyValue $addresses }
        else { $config.dial_addresses = $addresses }
        Write-JsonInPlace -Path $file -Value $config
        Write-Host "$([IO.Path]::GetFileName($file)): дороги тела — $($addresses -join ', ')"
    }
} catch {
    # настройки не записались целиком — вернуть прежние и поднять тело как было
    foreach ($file in $Files) {
        $copy = Join-Path $backup (Split-Path -Leaf $file)
        if (Test-Path -LiteralPath $copy) { Copy-Item -LiteralPath $copy -Destination $file -Force }
    }
    Start-Body
    throw
}
Start-Body

# что сказало само тело: соединение с сервером установлено и по какому адресу (строки начинаются со времени UTC)
$connected = $null
$log = Join-Path $Root 'state\connection.log'
for ($i = 0; $i -lt 75 -and !$connected; $i++) {
    Start-Sleep -Seconds 1
    if (!(Test-Path -LiteralPath $log)) { break }
    $connected = Get-Content -LiteralPath $log -Tail 40 -Encoding UTF8 |
        Where-Object {
            $at = $_.Substring(0, [Math]::Min(19, $_.Length))
            $_.Length -gt 19 -and $at -ge $since -and $_ -match 'body connected to bridge' -and $_ -match [regex]::Escape($Listen)
        } |
        Select-Object -Last 1
}
if ($connected) {
    Write-Host "Тело соединилось с сервером через прокси: $($connected.Substring(0, [Math]::Min(220, $connected.Length)))"
    exit 0
}
$last = ''
if (Test-Path -LiteralPath $log) { $last = (Get-Content -LiteralPath $log -Tail 1 -Encoding UTF8) }
Write-Host "За минуту с четвертью тело не сообщило о соединении через $Listen — последняя строка журнала: $last  Прежние настройки: $backup"
exit 1
