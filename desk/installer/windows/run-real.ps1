# Запуск команды ВНЕ дерева Claude Desktop — в настоящем реестре (27.09.2026).
#
# Инструменты Claude (Bash/PowerShell, Windows-MCP и всё, что они запускают) живут под
# MSIX-пакетом Claude с виртуализацией реестра: записи в HKCU\Software и HKLM\Software
# уходят в приватный улей пакета, снаружи их нет. Установщик, запущенный из инструментов,
# файлы кладёт, а в «Параметры → Приложения» не попадает. Этот помощник запускает команду
# задачей планировщика от текущего пользователя (интерактивно, окна видны), ждёт её конца
# и печатает вывод. Так запускать установщики, uninstall.exe, helene-setup.exe и `reg query`
# для проверки «видно ли снаружи». Подробности — installer/RELEASE.md.
#
#   .\run-real.ps1 -Command '"C:\…\Helene-1.2.0-setup.exe" /S /CurrentUser'
#   .\run-real.ps1 -Command 'reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Helene'
#   .\run-real.ps1 -Command '"C:\…\probe.cmd"'   # длинные проверки — файлом .cmd (/tr ≤ 261 знака)
param(
  [Parameter(Mandatory)] [string] $Command,
  [string] $Name = "HeleneReal",
  [switch] $NoWait,
  [int] $TimeoutSec = 600
)
$out = Join-Path $env:TEMP "helene-real-$Name.txt"
if (Test-Path $out) { [IO.File]::Delete($out) }
# cmd снимает внешние кавычки целиком: внутри — команда с её кавычками и перенаправление.
$tr = "cmd /c `"$Command > `"$out`" 2>&1`""
schtasks /delete /tn $Name /f 2>$null | Out-Null
schtasks /create /tn $Name /tr $tr /sc once /st 23:59 /f | Out-Null
schtasks /run /tn $Name | Out-Null
if ($NoWait) { "started $Name"; exit 0 }
$sw = [Diagnostics.Stopwatch]::StartNew()
Start-Sleep 2
$state = "?"
while ($sw.Elapsed.TotalSeconds -lt $TimeoutSec) {
  $st = (schtasks /query /tn $Name /fo LIST 2>$null | Select-String -Pattern '^(Status|Состояние):\s*(.+)$').Matches
  $state = if ($st.Count) { $st[0].Groups[2].Value.Trim() } else { "?" }
  if ($state -notmatch 'Running|Выполняется') { break }
  Start-Sleep 2
}
schtasks /delete /tn $Name /f 2>$null | Out-Null
"[$Name] state=$state after $([int]$sw.Elapsed.TotalSeconds)s"
if (Test-Path $out) { Get-Content $out | Where-Object { $_.Trim() } }
