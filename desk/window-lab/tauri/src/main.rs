// Tauri-оболочка лаборатории: тот же dist, что у Electron и WebKitGTK.
// LAB_AUTO=1 — прогнать стенд, напечатать BENCH и выйти.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

use tauri::{WebviewUrl, WebviewWindowBuilder};

#[tauri::command]
fn bench_done(app: tauri::AppHandle, json: String) {
    println!("BENCH {json}");
    if std::env::var("LAB_AUTO").is_ok() {
        app.exit(0);
    }
}

fn main() {
    let auto = std::env::var("LAB_AUTO").is_ok();
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![bench_done])
        .setup(move |app| {
            let url = if auto { "index.html?host=tauri&auto=1" } else { "index.html?host=tauri" };
            WebviewWindowBuilder::new(app, "main", WebviewUrl::App(url.into()))
                .title("Hélène · лаборатория (Tauri)")
                .inner_size(1440.0, 900.0)
                .build()?;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("tauri");
}
