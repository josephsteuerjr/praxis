//! Standalone native WebView probe. No harness, service, user data or agent.
#[path = "../src/scroll_port.rs"]
#[cfg(any(target_os = "macos", target_os = "linux"))]
mod scroll_port;
#[path = "../src/scroll_session.rs"]
#[cfg(any(target_os = "macos", target_os = "linux"))]
mod scroll_session;
#[cfg(any(target_os = "macos", target_os = "linux"))]
use tauri::Manager;
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn log_line(s: &str) {
    println!("{s}");
}

#[tauri::command]
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn phase(app: tauri::AppHandle, kind: String, seq: u64) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let (state, source) = (
        match kind.as_str() {
            "manual" => scroll_session::Session::mac(1, 0, true),
            "stationary" => scroll_session::Session::mac(2, 0, true),
            "end" => scroll_session::Session::mac(8, 0, true),
            "cancel" => scroll_session::Session::mac(16, 0, true),
            "inertia" => scroll_session::Session::mac(0, 1, true),
            _ => scroll_session::Session::default(),
        },
        "macos",
    );
    #[cfg(target_os = "linux")]
    let (state, source) = (
        match kind.as_str() {
            "manual" | "stationary" => scroll_session::Session::wayland(true, true, false),
            "end" | "cancel" => scroll_session::Session::wayland(true, true, true),
            // Linux normally lets the widget generate kinetic motion; this fixture
            // additionally checks the generic channel's momentum handling.
            "inertia" => scroll_session::Session {
                available: true,
                active: false,
                momentum: true,
            },
            _ => scroll_session::Session::default(),
        },
        "wayland",
    );
    app.get_webview_window("main")
        .ok_or("no probe window")?
        .eval(&scroll_port::script(state, seq, source))
        .map_err(|e| e.to_string())
}

#[tauri::command]
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn finish(app: tauri::AppHandle, result: serde_json::Value) {
    let ok = result.get("ok").and_then(|v| v.as_bool()) == Some(true);
    let text = serde_json::to_string_pretty(&result).unwrap();
    println!("{text}");
    if let Ok(path) = std::env::var("SCROLL_PROBE_RESULT") {
        std::fs::write(path, &text).unwrap();
    }
    app.exit(if ok { 0 } else { 1 });
}

#[cfg(any(target_os = "macos", target_os = "linux"))]
fn main() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![phase, finish])
        .setup(|app| {
            let window = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("scroll-port-probe.html".into()),
            )
            .title("Scroll port automated probe")
            .inner_size(1000.0, 700.0)
            .initialization_script(&scroll_port::initial_support())
            .build()?;
            #[cfg(target_os = "macos")]
            scroll_port::start(app.handle().clone());
            #[cfg(target_os = "linux")]
            scroll_port::attach(&window);
            let _ = window;
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("native scroll probe failed");
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn main() {
    eprintln!("This probe exercises WKWebView/WebKitGTK. Windows contact tests are separate.");
}
