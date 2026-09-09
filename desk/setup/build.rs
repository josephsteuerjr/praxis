fn main() {
    // UI собирается Vite в ./dist (npm --prefix ui run build) и запекается в exe
    // через generate_context!. Оговорка: гарантия «сборка без dist — честная
    // ошибка» верна только С фичей custom-protocol. Без неё Tauri считает сборку
    // dev и берёт devUrl из tauri.conf.json, dist не проверяя вовсе — поэтому в
    // src/main.rs стоит compile_error! на релиз без этой фичи.
    tauri_build::build()
}
