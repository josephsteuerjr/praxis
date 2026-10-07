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

#[cfg(target_os = "macos")]
fn mac_fixture(kind: &str) -> Result<scroll_session::Session, String> {
    use objc2_core_graphics::{
        CGEvent, CGEventField, CGMomentumScrollPhase, CGScrollEventUnit, CGScrollPhase,
    };
    let (phase, momentum, delta) = match kind {
        "manual" => (CGScrollPhase::Began, CGMomentumScrollPhase::None, 12),
        "stationary" => (CGScrollPhase::Changed, CGMomentumScrollPhase::None, 0),
        "end" => (CGScrollPhase::Ended, CGMomentumScrollPhase::None, 0),
        "cancel" => (CGScrollPhase::Cancelled, CGMomentumScrollPhase::None, 0),
        "inertia" => (CGScrollPhase(0), CGMomentumScrollPhase::Begin, 2),
        _ => return Ok(scroll_session::Session::default()),
    };
    let cg = CGEvent::new_scroll_wheel_event2(None, CGScrollEventUnit::Pixel, 1, delta, 0, 0)
        .ok_or("CGEvent fixture unavailable")?;
    CGEvent::set_integer_value_field(
        Some(&cg),
        CGEventField::ScrollWheelEventScrollPhase,
        phase.0.into(),
    );
    CGEvent::set_integer_value_field(
        Some(&cg),
        CGEventField::ScrollWheelEventMomentumPhase,
        momentum.0.into(),
    );
    let e = objc2_app_kit::NSEvent::eventWithCGEvent(&cg)
        .ok_or("NSEvent fixture conversion unavailable")?;
    let s = scroll_port::mac_event(&e);
    let valid = match kind {
        "manual" | "stationary" => s.available && s.active,
        "inertia" => s.available && s.momentum,
        _ => s.available && !s.active && !s.momentum,
    };
    if !valid {
        return Err(format!(
            "CGEvent -> NSEvent phase mismatch for {kind}: {s:?}"
        ));
    }
    Ok(s)
}

#[tauri::command]
#[cfg(any(target_os = "macos", target_os = "linux"))]
fn phase(app: tauri::AppHandle, kind: String, seq: u64) -> Result<(), String> {
    #[cfg(target_os = "macos")]
    let (state, source) = (mac_fixture(&kind)?, "macos");
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
            let expected = std::env::var("GDK_BACKEND").unwrap_or_else(|_| "macos".into());
            let window = tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("scroll-port-probe.html".into()),
            )
            .title("Scroll port automated probe")
            .inner_size(1000.0, 700.0)
            .initialization_script(&scroll_port::initial_support())
            .initialization_script(&format!(
                "window.__SCROLL_PROBE_EXPECT={};",
                serde_json::to_string(&expected)?
            ))
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
