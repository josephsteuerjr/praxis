// A separate, reusable view: hiding it must never stop the main harness.
const TRAY_POPUP: &str = "tray-popup";
const TRAY_WIDTH: f64 = 368.0;
const TRAY_PAD: f64 = 24.0;
static TRAY_ANCHOR: std::sync::Mutex<Option<(f64, f64)>> = std::sync::Mutex::new(None);
static TRAY_BUILDING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn create_tray_popup(app: &tauri::AppHandle) -> tauri::Result<()> {
    let origin = if cfg!(windows) { "http://helene.localhost" } else { "helene://localhost" };
    tauri::WebviewWindowBuilder::new(app, TRAY_POPUP,
        tauri::WebviewUrl::CustomProtocol(tauri::Url::parse(&format!("{origin}/tray.html")).expect("tray URL")))
        .title(product_ui()).inner_size(TRAY_WIDTH, 320.0)
        .decorations(false).resizable(false).skip_taskbar(true).always_on_top(true)
        .transparent(true).background_color(tauri::utils::config::Color(0, 0, 0, 0))
        .shadow(false).visible(false).focused(false).build()?;
    Ok(())
}

fn request_tray_popup(app: tauri::AppHandle, x: f64, y: f64) {
    use std::sync::atomic::Ordering;
    if TRAY_BUILDING.swap(true, Ordering::AcqRel) { return; }
    // WebView2 construction cannot block its own native event callback.
    // Create only on the first request, while the app event loop is running.
    std::thread::spawn(move || {
        let result = (|| -> Result<(), String> {
            if app.get_webview_window(TRAY_POPUP).is_none() {
                create_tray_popup(&app).map_err(|e| e.to_string())?;
            }
            show_tray_popup(&app, x, y)
        })();
        TRAY_BUILDING.store(false, Ordering::Release);
        if let Err(why) = result {
            log_line(&format!("карточка трея: {why}"));
            show_main(&app);
        }
    });
}

fn tray_monitor(app: &tauri::AppHandle) -> Result<tauri::Monitor, String> {
    let monitors = app.available_monitors().map_err(|e| e.to_string())?;
    let anchor = TRAY_ANCHOR.lock().ok().and_then(|a| *a);
    if let Some((x, y)) = anchor {
        if let Some(found) = monitors.iter().find(|m| {
            let p = m.position(); let s = m.size();
            x >= p.x as f64 && y >= p.y as f64 && x < p.x as f64 + s.width as f64 && y < p.y as f64 + s.height as f64
        }) { return Ok(found.clone()); }
    }
    app.primary_monitor().map_err(|e| e.to_string())?
        .or_else(|| monitors.into_iter().next()).ok_or_else(|| "Не удалось определить экран".into())
}

fn position_tray(app: &tauri::AppHandle, height: f64) -> Result<(), String> {
    let window = app.get_webview_window(TRAY_POPUP).ok_or("Карточка трея не готова")?;
    let monitor = tray_monitor(app)?; let area = monitor.work_area(); let scale = monitor.scale_factor();
    let pad = TRAY_PAD * scale; let gap = 8.0 * scale;
    let width = TRAY_WIDTH.min(area.size.width as f64 / scale + 2.0 * TRAY_PAD - 16.0);
    let height = height.clamp(160.0, 580.0).min(area.size.height as f64 / scale + 2.0 * TRAY_PAD - 16.0);
    let w = width * scale; let h = height * scale;
    let left = area.position.x as f64; let top = area.position.y as f64;
    let right = left + area.size.width as f64; let bottom = top + area.size.height as f64;
    let (ax, ay) = TRAY_ANCHOR.lock().ok().and_then(|a| *a).unwrap_or((right, bottom));
    let x = (ax - w + pad + 20.0 * scale).clamp(left + gap - pad, (right - gap + pad - w).max(left + gap - pad));
    let wanted_y = if ay < top + area.size.height as f64 / 2.0 { ay + gap - pad } else { ay - gap - h + pad };
    let y = wanted_y.clamp(top + gap - pad, (bottom - gap + pad - h).max(top + gap - pad));
    let wanted = tauri::LogicalSize::new(width, height).to_physical::<u32>(scale);
    if window.inner_size().map_err(|e| e.to_string())? != wanted {
        window.set_size(tauri::LogicalSize::new(width, height)).map_err(|e| e.to_string())?;
    }
    window.set_position(tauri::PhysicalPosition::new(x.round() as i32, y.round() as i32)).map_err(|e| e.to_string())
}

fn show_tray_popup(app: &tauri::AppHandle, x: f64, y: f64) -> Result<(), String> {
    let window = app.get_webview_window(TRAY_POPUP).ok_or("Карточка трея не готова")?;
    if window.is_visible().unwrap_or(false) && window.is_focused().unwrap_or(false) {
        return window.hide().map_err(|e| e.to_string());
    }
    if let Ok(mut anchor) = TRAY_ANCHOR.lock() { *anchor = Some((x, y)); }
    let height = window.inner_size().ok().map(|s| s.height as f64 / window.scale_factor().unwrap_or(1.0)).unwrap_or(320.0);
    position_tray(app, height)?;
    window.show().map_err(|e| e.to_string())?;
    window.set_focus().map_err(|e| e.to_string())
}

#[tauri::command]
fn tray_context(app: tauri::AppHandle, state: ShellState<LocalHarness>) -> serde_json::Value {
    let agents = agents_list(state);
    let background = match read_config(&install_root().join(CONFIG_NAME)) {
        ConfigRead::Ok(cfg) => Some(cfg.get("installed").and_then(|v| v.get("service")).and_then(|v| v.as_bool()).unwrap_or(false)),
        _ => None,
    };
    let max_height = tray_monitor(&app).ok().map(|m| m.work_area().size.height as f64 / m.scale_factor() + 2.0 * TRAY_PAD - 16.0);
    serde_json::json!({"owner": owner_state(), "background": background,
        "agents": agents["agents"], "current": agents["current"], "max_height": max_height})
}

#[tauri::command]
fn tray_hide(app: tauri::AppHandle) -> Result<(), String> {
    app.get_webview_window(TRAY_POPUP).map(|w| w.hide().map_err(|e| e.to_string())).unwrap_or(Ok(()))
}

#[tauri::command]
async fn tray_fit(app: tauri::AppHandle, height: f64) -> Result<(), String> {
    if !height.is_finite() { return Err("Некорректная высота карточки".into()); }
    position_tray(&app, height)
}

#[tauri::command]
fn tray_open_main(app: tauri::AppHandle) {
    let _ = tray_hide(app.clone()); show_main(&app);
}

#[tauri::command]
fn tray_exit(app: tauri::AppHandle) {
    let state = app.state::<LocalHarness>(); kill_children(&state); app.exit(0);
}
