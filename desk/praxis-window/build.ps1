param(
    [Parameter(Mandatory=$true)][string]$OutputDir,
    [string]$Checkout,
    [string]$ElectronZip,
    [switch]$Install
)
# Собрать программу окна Праксис для Windows; с -Install — заменить установленную
# и ярлыки (рабочий стол и меню «Пуск»). Программа тонкая: страница приезжает с
# сервера Праксис, здесь — только оболочка Electron и ключ подключения.
$ErrorActionPreference = 'Stop'
$version = '44.4.5'
$expected = '11C395820A5AAA8EBCC0686B476D0AC98A730274EBFBDC8CF5538A7C2815CB5D'
if (!$Checkout) { $Checkout = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..')) }   # репозиторий, в котором лежит эта папка
if (!(Test-Path -LiteralPath (Join-Path $PSScriptRoot 'electron/server.json'))) {
    Write-Warning 'electron\server.json не найден: адрес сервера в программу не вшит. Окно возьмёт его из прежнего подключения PraxisPult или скажет, что он не задан (образец: electron\server.example.json).'
}
if (!(Test-Path -LiteralPath (Join-Path $Checkout '.git'))) { throw "Checkout not found: $Checkout" }
if (!$ElectronZip) {
    $name = "electron-v$version-win32-x64.zip"
    $cached = Get-ChildItem (Join-Path $env:LOCALAPPDATA 'electron/Cache') -Filter $name -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($cached) { $ElectronZip = $cached.FullName }
    else {
        $ElectronZip = Join-Path $env:TEMP $name
        if (!(Test-Path -LiteralPath $ElectronZip)) {
            Invoke-WebRequest -UseBasicParsing "https://github.com/electron/electron/releases/download/v$version/$name" -OutFile $ElectronZip
        }
    }
}
if ((Get-FileHash -LiteralPath $ElectronZip).Hash -ne $expected) { throw 'Electron archive checksum mismatch.' }
$out = [IO.Directory]::CreateDirectory($OutputDir).FullName
$dist = Join-Path $out 'windows'
if (Test-Path -LiteralPath $dist) { Remove-Item -LiteralPath $dist -Recurse -Force }
& python (Join-Path $PSScriptRoot 'frontend.py') $Checkout (Join-Path $out 'ui.zip')
if ($LASTEXITCODE) { throw 'Page build failed.' }
[void][IO.Directory]::CreateDirectory($dist)
& python -m zipfile -e $ElectronZip $dist
if ($LASTEXITCODE) { throw 'Electron extraction failed.' }
$exe = Join-Path $dist 'praxis.exe'
Move-Item -LiteralPath (Join-Path $dist 'electron.exe') -Destination $exe -Force
$appFolder = [IO.Directory]::CreateDirectory((Join-Path $dist 'resources/app')).FullName
Copy-Item -Path (Join-Path $PSScriptRoot 'electron/*') -Destination $appFolder -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot '../shell/icons-praxis/icon.ico') -Destination (Join-Path $appFolder 'praxis.ico') -Force
if ($Install) {
    $installed = [IO.Directory]::CreateDirectory((Join-Path $env:LOCALAPPDATA 'Programs/Praxis')).FullName
    $target = Join-Path $installed 'praxis.exe'
    if (Get-Process -Name praxis -ErrorAction SilentlyContinue | Where-Object Path -eq $target) { throw 'Close the Praxis window before installing.' }
    Copy-Item -Path (Join-Path $dist '*') -Destination $installed -Recurse -Force
    $links = New-Object -ComObject WScript.Shell
    foreach ($dir in @([Environment]::GetFolderPath('DesktopDirectory'),[Environment]::GetFolderPath('Programs'))) {
        $link = $links.CreateShortcut((Join-Path $dir 'Praxis.lnk'))
        $link.TargetPath = $target
        $link.WorkingDirectory = $installed
        $link.IconLocation = (Join-Path $installed 'resources/app/praxis.ico') + ',0'
        $link.Save()
    }
    Write-Output $target
} else { Write-Output $exe }
