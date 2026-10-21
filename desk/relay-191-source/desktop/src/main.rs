#![cfg_attr(target_os = "windows", windows_subsystem = "windows")]

use praxis_relay::service::Relay;
use serde_json::Value;
use std::sync::Arc;
use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Manager,
};

#[tauri::command]
async fn relay_command(
    window: tauri::WebviewWindow,
    relay: tauri::State<'_, Arc<Relay>>,
    op: String,
    slot: Option<String>,
    port: Option<u16>,
    settings: Option<Value>,
) -> Result<Value, String> {
    if op == "configure" {
        return relay
            .configure(settings.unwrap_or(Value::Null))
            .await
            .map_err(|e| format!("{e:#}"));
    }
    let mut value = relay
        .inner()
        .command(&op, slot.as_deref(), port)
        .await
        .map_err(|e| format!("{e:#}"))?;
    if value.get("product").is_some() {
        value["window_visible"] = Value::Bool(window.is_visible().unwrap_or(false));
    }
    Ok(value)
}

#[tauri::command]
fn window_command(window: tauri::WebviewWindow, action: String) -> Result<bool, String> {
    let result = match action.as_str() {
        "drag" => window.start_dragging(),
        "minimize" | "close" => window.set_skip_taskbar(true).and_then(|_| window.hide()),
        "maximize" => window.is_maximized().and_then(|maximized| {
            if maximized {
                window.unmaximize()
            } else {
                window.maximize()
            }
        }),
        "quit" => {
            window.app_handle().exit(0);
            Ok(())
        }
        _ => return Err("Unknown window action".into()),
    };
    result.map_err(|e| e.to_string())?;
    Ok(window.is_maximized().unwrap_or(false))
}

fn show(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.set_skip_taskbar(false);
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let minimized = arguments.iter().any(|argument| argument == "--minimized");
    let start_api = arguments.iter().any(|argument| argument == "--start-api");
    let mut context = tauri::generate_context!();
    let auth_root = praxis_relay::auth_root().expect("Relay auth directory unavailable");
    std::fs::create_dir_all(&auth_root).expect("Relay auth directory cannot be created");
    let identity = std::fs::canonicalize(auth_root).expect("Relay auth directory cannot be resolved");
    use sha2::{Digest, Sha256};
    let digest = Sha256::digest(identity.to_string_lossy().to_lowercase().as_bytes());
    context.config_mut().identifier = format!("app.helene.relay.{digest:x}");
    let application = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, arguments, _| {
            let relay = app.state::<Arc<Relay>>().inner().clone();
            let options: Vec<String> = arguments.into_iter().skip(1).collect();
            let hidden = options.iter().any(|argument| argument == "--minimized");
            let handle = app.clone();
            tauri::async_runtime::spawn(async move {
                let result = async {
                    let defaults = praxis_relay::settings::launch_defaults(&options)?;
                    if !defaults.is_empty() {
                        relay.configure(serde_json::json!({"defaults":defaults})).await?;
                    }
                    if options.iter().any(|argument| argument == "--start-api") {
                        relay.start().await?;
                    }
                    Ok::<(), anyhow::Error>(())
                }.await;
                if let Err(error) = result {
                    relay.startup_error(format!("{error:#}")).await;
                    show(&handle);
                } else if !hidden {
                    show(&handle);
                }
            });
        }))
        .setup(move |app| {
            let relay = tauri::async_runtime::block_on(Relay::new())?;
            let defaults = praxis_relay::settings::launch_defaults(&arguments)?;
            if !defaults.is_empty() {
                tauri::async_runtime::block_on(relay.configure(serde_json::json!({"defaults":defaults})))?;
            }
            tauri::async_runtime::block_on(relay.start_management())?;
            // Open the management window even before login or when the API port
            // is occupied. An actionable startup error remains visible in the UI.
            let preferences = tauri::async_runtime::block_on(relay.preferences());
            app.manage(relay.clone());
            if let Some(window) = app.get_webview_window("main") {
                if let Some(icon) = app.default_window_icon() {
                    window.set_icon(icon.clone())?;
                }
                if let Some(monitor) = window.current_monitor()? {
                    let area = monitor.work_area();
                    let scale = monitor.scale_factor();
                    let width = (area.size.width as f64 / scale - 32.0).max(1.0).min(1120.0);
                    let height = (area.size.height as f64 / scale - 32.0).max(1.0).min(760.0);
                    window.set_min_size(Some(tauri::LogicalSize::new(
                        width.min(720.0),
                        height.min(560.0),
                    )))?;
                    window.set_size(tauri::LogicalSize::new(width, height))?;
                    window.set_position(tauri::PhysicalPosition::new(
                        area.position.x
                            + (area.size.width as f64 - width * scale).max(0.0) as i32 / 2,
                        area.position.y
                            + (area.size.height as f64 - height * scale).max(0.0) as i32 / 2,
                    ))?;
                }
            }
            let open = MenuItem::with_id(app, "open", "Открыть praxis-relay", true, None::<&str>)?;
            let quit =
                MenuItem::with_id(app, "quit", "Выйти и остановить реле", true, None::<&str>)?;
            let menu = Menu::with_items(app, &[&open, &quit])?;
            let icon = app
                .default_window_icon()
                .cloned()
                .ok_or_else(|| std::io::Error::other("The application icon is missing"))?;
            // If creating the tray fails, startup fails visibly instead of
            // allowing the window to hide somewhere the user cannot restore it.
            TrayIconBuilder::with_id("relay")
                .icon(icon)
                .tooltip("praxis-relay")
                .menu(&menu)
                .show_menu_on_left_click(false)
                .on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show(app),
                    "quit" => app.exit(0),
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if matches!(
                        event,
                        TrayIconEvent::Click {
                            button: MouseButton::Left,
                            button_state: MouseButtonState::Up,
                            ..
                        }
                    ) {
                        show(tray.app_handle());
                    }
                })
                .build(app)?;
            let startup_ok = if preferences.auto_start_api || start_api {
                match tauri::async_runtime::block_on(relay.start()) {
                    Ok(()) => true,
                    Err(error) => {
                        tauri::async_runtime::block_on(relay.startup_error(format!("{error:#}")));
                        false
                    }
                }
            } else {
                true
            };
            if !(preferences.start_in_tray || minimized) || !startup_ok {
                show(app.handle());
            }
            Ok(())
        })
        .on_window_event(|window, event| match event {
            tauri::WindowEvent::CloseRequested { api, .. } => {
                api.prevent_close();
                let _ = window.set_skip_taskbar(true);
                let _ = window.hide();
            }
            tauri::WindowEvent::Resized(_) => {
                if window.is_minimized().unwrap_or(false) {
                    let _ = window.set_skip_taskbar(true);
                    let _ = window.hide();
                }
            }
            _ => {}
        })
        .invoke_handler(tauri::generate_handler![relay_command, window_command])
        .build(context);
    let application = match application {
        Ok(application) => application,
        Err(error) => {
            let message = format!("Unable to open Praxis Relay:\n\n{error}");
            #[cfg(windows)]
            {
                #[link(name = "user32")]
                unsafe extern "system" {
                    fn MessageBoxW(
                        window: *mut std::ffi::c_void,
                        text: *const u16,
                        title: *const u16,
                        kind: u32,
                    ) -> i32;
                }
                let text: Vec<u16> = message.encode_utf16().chain(Some(0)).collect();
                let title: Vec<u16> = "Praxis Relay".encode_utf16().chain(Some(0)).collect();
                unsafe {
                    MessageBoxW(std::ptr::null_mut(), text.as_ptr(), title.as_ptr(), 0x10);
                }
            }
            eprintln!("{message}");
            std::process::exit(1);
        }
    };
    application.run(|app, event| {
        if let tauri::RunEvent::Exit = event {
            let relay = app.state::<Arc<Relay>>();
            tauri::async_runtime::block_on(async {
                relay.cancel_login().await;
                let _ = relay.stop().await;
                relay.shutdown_management().await;
            });
        }
    });
}
