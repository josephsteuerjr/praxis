# Сборка мастера в варианте «Praxis» (1.2, 27.09): установщик окна к своему серверу.
#
# Тот же helene-setup, но с фичей `praxis` (имена продукта, praxis.exe, короткий маршрут
# «для кого → установка → открыть»), своим productName/identifier и значком Praxis:
# TAURI_CONFIG подмешивается в tauri.conf.json при сборке. Собирается в СВОЙ target-praxis/,
# чтобы не затирать мастер Hélène в target/. В поставку Praxis он едет под именем
# praxis-setup.exe, и к нему пришивается хвост (installer/build_dist.py --variant praxis).
#
#   pwsh -File setup/build-praxis.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:TAURI_CONFIG = '{"productName":"Praxis Setup","identifier":"app.praxis.setup","bundle":{"icon":["../shell/icons-praxis/icon.ico","../shell/icons-praxis/icon.png"]}}'
cargo build --release --features "custom-protocol praxis" --target-dir target-praxis
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "готово: $PSScriptRoot\target-praxis\release\helene-setup.exe (Praxis Setup, app.praxis.setup)"
