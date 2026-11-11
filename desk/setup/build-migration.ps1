# Build only the Python migration wizard; never embed the legacy setup dist/.
# generate_context!("migration.conf.json") alone still loads tauri.conf.json
# from that directory in Tauri 2. TAURI_CONFIG is the shared merge input used
# by both tauri-build and codegen. Preserve the caller's environment/location.
$ErrorActionPreference = "Stop"
$previousConfig = $env:TAURI_CONFIG
Push-Location $PSScriptRoot
try {
    $env:TAURI_CONFIG = Get-Content -Raw -Encoding UTF8 "$PSScriptRoot/migration.conf.json"
    cargo build --release --locked --offline --features custom-protocol --bin migration
    if ($LASTEXITCODE -ne 0) { throw "Migration build failed: $LASTEXITCODE" }
    Write-Host "Migration wizard: $PSScriptRoot/target/release/migration.exe"
} finally {
    $env:TAURI_CONFIG = $previousConfig
    Pop-Location
}
