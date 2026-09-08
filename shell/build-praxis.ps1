# Сборка оболочки в варианте «Praxis» (Пульт к своему серверу).
#
# Та же оболочка, что helene.exe, но с другим именем продукта, identifier и значками:
# TAURI_CONFIG подмешивается в tauri.conf.json при сборке, HELENE_ICON_DIR читает build.rs.
# Собирается в СВОЙ target-praxis/, чтобы не затирать helene.exe в target/ и наоборот.
# Переменные живут только в этом процессе pwsh: после скрипта они не остаются в оболочке
# и не портят следующую сборку Hélène.
#
#   pwsh -File shell/build-praxis.ps1
#   installer/build_dist.py --variant praxis
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
$env:TAURI_CONFIG = '{"productName":"Praxis","identifier":"ru.praxis.pult","bundle":{"icon":["icons-praxis/icon.ico","icons-praxis/icon.png"]}}'
$env:HELENE_ICON_DIR = "icons-praxis"
# -j 2: на машине владельца рядом живёт VMware (~6 ГБ), и двенадцать параллельных
# rustc падали STATUS_STACK_BUFFER_OVERRUN на голом месте (09.09, свободно было 2 ГБ).
cargo build --release --features custom-protocol --target-dir target-praxis -j 2
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Write-Host "готово: $PSScriptRoot\target-praxis\release\helene.exe (productName=Praxis, ru.praxis.pult)"
