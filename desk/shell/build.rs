fn main() {
    // UI оболочки собирается Vite в ../app/dist (npm --prefix app run build) и
    // запекается в exe через generate_context!: frontendDist в tauri.conf.json.
    // Сборка без dist — честная ошибка, не пустое окно.
    println!("cargo:rerun-if-changed=../app/dist");
    // Каталог значков сборки: icons/ (Hélène) или icons-praxis/ (Praxis) —
    // тот же выбор, что bundle.icon в TAURI_CONFIG; main.rs вшивает окно и трей отсюда.
    println!("cargo:rerun-if-env-changed=HELENE_ICON_DIR");
    let icons = std::env::var("HELENE_ICON_DIR").unwrap_or_else(|_| "icons".to_string());
    println!("cargo:rustc-env=HELENE_ICON_DIR={icons}");
    println!("cargo:rerun-if-changed={icons}");
    tauri_build::build()
}
