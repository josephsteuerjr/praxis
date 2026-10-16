fn main() {
    // Tauri's build helper does not track edits to the ICO reliably: cargo
    // reused an old 16/24px RT_ICON after icon.ico changed and `cargo build`
    // exited green. This is the input that must invalidate the setup binary.
    println!("cargo:rerun-if-changed=icons/icon.ico");
    // UI собирается Vite в ./dist (npm --prefix ui run build) и запекается в exe
    // через generate_context!. Оговорка: гарантия «сборка без dist — честная
    // ошибка» верна только С фичей custom-protocol. Без неё Tauri считает сборку
    // dev и берёт devUrl из tauri.conf.json, dist не проверяя вовсе — поэтому в
    // src/main.rs стоит compile_error! на релиз без этой фичи.
    tauri_build::build()
}
