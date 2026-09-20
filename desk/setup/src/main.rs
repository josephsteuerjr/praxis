// Установщик Hélène — отдельная нативная оболочка (Rust, Tauri: окно + WebView2).
//
// Три P0 из видения владельца (installer/ui/OWNER_VISION.md) живут здесь:
//   1. один экземпляр: повторный запуск поднимает и фокусирует живое окно
//      (плагин single-instance регистрируется ПЕРВЫМ — до любого окна);
//   2. закрыть окно = завершить процесс: трея у установщика нет по построению,
//      поведение основной программы «закрыть в трей» сюда не наследуется;
//   3. открывается развёрнутым окном; кадр сцены — Full HD, масштаб считает UI.
//
// Окно рождается невидимым и показывается из UI после первого кадра: иначе
// на тёмной системной теме мигнул бы белый прямоугольник WebView2.
//
// `helene-setup.exe --uninstall [--purge]` — удаление без окна: программа,
// ярлыки, запись в «Приложениях», служба; данные остаются, если не --purge.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Без фичи custom-protocol Tauri считает сборку «dev» и грузит devUrl
// (http://localhost:5173) вместо вшитой страницы: у покупателя окно установщика
// осталось бы пустым навсегда, и ни одна проверка дальше по конвейеру этого не
// ловит. Пусть такая сборка просто не соберётся.
#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!(
    "релизная сборка установщика без --features custom-protocol грузит http://localhost:5173; \
     собирай `cargo build --release --features custom-protocol` или `tauri build`"
);

mod install;

use std::process::Command;
use std::time::Duration;

use tauri::{Emitter, Manager};

fn focus_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
    }
}

#[tauri::command]
fn defaults() -> install::Defaults {
    install::defaults()
}

#[tauri::command]
async fn probe_model(base_url: String, key: String, framework: String) -> serde_json::Value {
    let (ok, note, models) = tauri::async_runtime::spawn_blocking(move || install::probe_model(&base_url, &key, &framework))
        .await
        .unwrap_or((false, "проверка не выполнилась".into(), Vec::new()));
    serde_json::json!({ "ok": ok, "note": note, "models": models })
}

#[tauri::command]
async fn relay_models() -> Result<Vec<String>, String> {
    tauri::async_runtime::spawn_blocking(install::relay_models)
        .await
        .map_err(|e| e.to_string())?
}

#[tauri::command]
fn relay_login() -> Result<String, String> {
    install::relay_login()
}

#[tauri::command]
fn relay_status() -> String {
    install::relay_status()
}

/// Службы прежних поколений продукта (Vera, Frame, Praxis) и своя, если она
/// живёт не в той папке, куда сейчас ставят. Опрос SCM — WMI, без прав.
/// `home` — папка установки или снятия: своя служба из списка выпадает.
#[tauri::command]
async fn legacy_services(home: Option<String>) -> Vec<install::LegacyService> {
    tauri::async_runtime::spawn_blocking(move || {
        let path = home.map(std::path::PathBuf::from);
        install::legacy_services(path.as_deref())
    })
    .await
    .unwrap_or_default()
}

/// Есть ли у этой учётной записи права администратора: экран режима по этому
/// ответу либо оставляет карточку «Служба» живой, либо делает недоступной и
/// пишет ПОЧЕМУ. Ничего не меняет и прав не просит.
#[tauri::command]
async fn admin_rights() -> install::AdminRights {
    tauri::async_runtime::spawn_blocking(install::admin_rights)
        .await
        .unwrap_or(install::AdminRights { can: true, certain: false, elevated: false })
}

/// Снять названную службу. Зовётся ТОЛЬКО кнопкой в сцене согласия: молча
/// трогать службу прежнего поколения нельзя, даже свою.
#[tauri::command]
async fn remove_service(name: String) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || install::remove_service(&name))
        .await
        .map_err(|e| e.to_string())?
}

/// Установка целиком; ход — событиями `install-progress`, итог — распиской.
///
/// Журнал пишем ВСЕГДА, а не только в безоконном `--install`: раньше при отказе
/// владелец видел одну красную строку в окне, и она исчезала навсегда вместе с окном.
#[tauri::command]
async fn install(app: tauri::AppHandle, setup: install::Setup) -> Result<install::Receipt, String> {
    let result = tauri::async_runtime::spawn_blocking(move || {
        let mut log = String::new();
        let r = install::install(&setup, |p| {
            log.push_str(&format!("[{}/{}] {}\n", p.step, p.total, p.label));
            let _ = app.emit("install-progress", p);
        });
        match &r {
            Ok(rec) => log.push_str(&format!("OK {}\n", serde_json::to_string(rec).unwrap_or_default())),
            Err(e) => log.push_str(&format!("FAIL {e}\n")),
        }
        let _ = std::fs::write(install::exe_dir().join("install.log"), &log);
        r
    })
    .await
    .map_err(|e| e.to_string())?;
    result
}

/// Открыть установленный Hélène и закрыть установщик.
#[cfg(windows)]
#[tauri::command]
fn open_frame(app: tauri::AppHandle, exe: String) -> Result<(), String> {
    let path = std::path::PathBuf::from(&exe);
    let mut cmd = Command::new(&path);
    if let Some(dir) = path.parent() {
        cmd.current_dir(dir);
    }
    cmd.spawn().map_err(|e| format!("Hélène не запустилась: {e}"))?;
    // Окно установщика прячем сразу, а процесс держим ещё несколько секунд:
    // Windows отдаёт передний план новому окну, только пока запустивший его
    // процесс жив. Иначе Hélène открывалась позади других окон, и казалось,
    // что не открылся вовсе.
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_secs(10));
        install::relay_abort();
        app.exit(0);
    });
    Ok(())
}

/// macOS: `exe` из расписки — это бандл `Helene.app`; его открывает `open`, он
/// же выводит окно на передний план, держать установщик живым незачем.
#[cfg(not(windows))]
#[tauri::command]
fn open_frame(app: tauri::AppHandle, exe: String) -> Result<(), String> {
    install::launch_installed(std::path::Path::new(&exe)).map_err(|e| format!("Hélène не запустилась: {e}"))?;
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    std::thread::spawn(move || {
        install::relay_abort();
        app.exit(0);
    });
    Ok(())
}

/// Окно открыто как визард снятия (helene-setup.exe --uninstall без --quiet).
static UNINSTALL_MODE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
static UNINSTALL_DONE: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Снятие из визарда: результат — текст расписки; хвост (самоудаление) — на выходе.
#[tauri::command]
async fn uninstall_run(purge: bool) -> Result<String, String> {
    let text = tauri::async_runtime::spawn_blocking(move || install::uninstall(purge))
        .await
        .map_err(|e| e.to_string())??;
    let _ = std::fs::write(std::env::temp_dir().join("helene-uninstall.log"), &text);
    UNINSTALL_DONE.store(true, std::sync::atomic::Ordering::Relaxed);
    Ok(text)
}

/// Где стоит Hélène для экспорта: рядом с установщиком (портативный запуск) или
/// по записи установки. -> путь к архиву переноса.
fn export_agent() -> Result<String, String> {
    let here = install::exe_dir();
    // Питон рантайма — по системе (`install::PYTHON_REL`): `runtime/python.exe`
    // на Windows, `runtime/bin/python3` на macOS.
    let dir = if here.join("helene.json").is_file() && install::python_exe(&here).is_file() {
        here
    } else {
        std::path::PathBuf::from(
            install::installed_info().ok_or("Hélène не установлена — экспортировать нечего")?.dir,
        )
    };
    let python = install::python_exe(&dir);
    let script = dir.join("app").join("localharness").join("carry.py");
    if !python.is_file() || !script.is_file() {
        return Err(format!("в {} нет помощника переноса ({}, app/localharness/carry.py)", dir.display(), install::PYTHON_REL));
    }
    let mut cmd = std::process::Command::new(python);
    cmd.arg("-u")
        .arg(script)
        .arg("export")
        .arg("--config")
        .arg(dir.join("helene.json"))
        .current_dir(&dir)
        .env("PYTHONUTF8", "1");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(0x08000000);
    }
    let out = cmd.output().map_err(|e| e.to_string())?;
    let text = String::from_utf8_lossy(&out.stdout).to_string();
    // ⚠ Путь к архиву берём из МАШИННОЙ строки `carry-export {json}`, а не из
    // человеческой фразы: та переводится вместе с интерфейсом, и разбор по
    // префиксу «архив: » объявил бы удачный экспорт провалом. Разбор фразы
    // остаётся запасным — на случай, когда рядом лежит app/ прошлой поставки.
    let archive = text
        .lines()
        .find_map(|l| l.trim().strip_prefix("carry-export "))
        .and_then(|j| serde_json::from_str::<serde_json::Value>(j).ok())
        .and_then(|v| v.get("archive").and_then(|a| a.as_str()).map(|s| s.to_string()))
        .or_else(|| {
            text.lines()
                .find_map(|l| l.trim().strip_prefix("архив: "))
                .map(|s| s.rsplit_once(" (").map(|(p, _)| p).unwrap_or(s).trim().to_string())
        })
        .filter(|p| !p.is_empty());
    match archive {
        Some(path) if out.status.success() => Ok(path),
        _ => Err(String::from_utf8_lossy(&out.stderr).trim().chars().rev().take(300).collect::<Vec<_>>().into_iter().rev().collect()),
    }
}

#[cfg(windows)]
fn message_box(text: &str) {
    let script = format!(
        "Add-Type -AssemblyName PresentationFramework; [System.Windows.MessageBox]::Show('{}', '{}') | Out-Null",
        text.replace('\'', "''"),
        install::PRODUCT
    );
    let _ = Command::new(install::powershell_exe())
        .args(["-NoProfile", "-Command", &script])
        .status();
}

/// macOS: тот же диалог через osascript (`install::message_box`).
#[cfg(not(windows))]
fn message_box(text: &str) {
    install::message_box(text);
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    // Безоконная установка по готовому JSON решений: для проверок и тихой
    // установки. Ход и итог — в install.log рядом с установщиком.
    if let Some(i) = args.iter().position(|a| a == "--install") {
        let Some(path) = args.get(i + 1) else {
            message_box("--install требует путь к JSON с решениями");
            return;
        };
        let log_path = install::exe_dir().join("install.log");
        let mut log = String::new();
        let result = std::fs::read_to_string(path)
            .map_err(|e| format!("{path}: {e}"))
            .and_then(|raw| serde_json::from_str::<install::Setup>(&raw).map_err(|e| e.to_string()))
            .and_then(|setup| {
                install::install(&setup, |p| {
                    log.push_str(&format!("[{}/{}] {}
", p.step, p.total, p.label));
                })
            });
        match &result {
            Ok(r) => log.push_str(&format!("OK {}
", serde_json::to_string(r).unwrap_or_default())),
            Err(e) => log.push_str(&format!("FAIL {e}
")),
        }
        let _ = std::fs::write(&log_path, &log);
        let failed = result.is_err();
        if !args.iter().any(|a| a == "--quiet") {
            message_box(&match result {
                Ok(r) => format!("Hélène установлена в {}", r.dir),
                Err(e) => format!("Установка не удалась: {e}"),
            });
        }
        if failed {
            std::process::exit(1);
        }
        return;
    }
    // Обновление поверх установленного, БЕЗ вопросов: решения берутся из самой
    // установки (`install::setup_from_dir`), и визард не открывается вовсе.
    //
    //     helene-setup.exe --update [--dir <папка>]
    //
    // ⚠ Живой случай 20.09.2026: «блин, он мне ставить собрался, а не
    // обновлять». Окно на Windows запускало мастер без аргументов — человек,
    // нажавший «Скачать и установить», получал полный визард и вводил заново
    // имя агента, конституцию и модель. На macOS то же место давно делает
    // `install.sh` тихо. Теперь платформы сошлись.
    //
    // Не вышло прочитать решения (нет имён, нет конституции, папка не найдена)
    // — это НЕ отказ: дальше открывается обычный визард, и человек отвечает
    // сам. Единственное, чего здесь нельзя, — записать вместо его выбора
    // умолчания. Ход и итог — в install.log рядом с установщиком.
    if args.iter().any(|a| a == "--update") {
        let asked = args
            .iter()
            .position(|a| a == "--dir")
            .and_then(|i| args.get(i + 1))
            .map(std::path::PathBuf::from);
        let dir = asked.or_else(|| install::installed_info().map(|i| std::path::PathBuf::from(i.dir)));
        let log_path = install::exe_dir().join("install.log");
        match dir.as_deref().and_then(install::setup_from_dir) {
            Some(setup) => {
                let mut log = format!("обновление поверх {}\n", setup.dir);
                let result = install::install(&setup, |p| {
                    log.push_str(&format!("[{}/{}] {}\n", p.step, p.total, p.label));
                });
                match &result {
                    Ok(r) => log.push_str(&format!("OK {}\n", serde_json::to_string(r).unwrap_or_default())),
                    Err(e) => log.push_str(&format!("FAIL {e}\n")),
                }
                let _ = std::fs::write(&log_path, &log);
                if let Err(e) = result {
                    if !args.iter().any(|a| a == "--quiet") {
                        message_box(&format!("Обновление не удалось: {e}"));
                    }
                    std::process::exit(1);
                }
                return;
            }
            None => {
                let where_ = dir.map(|d| d.display().to_string()).unwrap_or_else(|| "не найдена".into());
                let _ = std::fs::write(
                    &log_path,
                    format!("решений в установке нет ({where_}) — открываю визард\n"),
                );
                // Дальше — обычный поток с окном.
            }
        }
    }
    // Экспорт агента без окна: `helene-setup.exe --export [--quiet]` — тот же
    // помощник, что и за кнопкой «Экспорт агента» в Настройках
    // (app/localharness/carry.py). Берётся установка из реестра, либо папка
    // самого установщика, если helene.json лежит рядом (портативный запуск).
    if args.iter().any(|a| a == "--export") {
        let quiet = args.iter().any(|a| a == "--quiet");
        let result = export_agent();
        let text = match &result {
            Ok(path) => format!("Архив переноса: {path}"),
            Err(err) => format!("Экспорт не удался: {err}"),
        };
        let _ = std::fs::write(install::exe_dir().join("export.log"), &text);
        if !quiet {
            message_box(&text);
        }
        if result.is_err() {
            std::process::exit(1);
        }
        return;
    }
    let uninstall_mode = args.iter().any(|a| a == "--uninstall");
    if uninstall_mode && args.iter().any(|a| a == "--quiet") {
        // Тихое снятие из командной строки; с окном — та же сцена, что у установки.
        let purge = args.iter().any(|a| a == "--purge");
        let result = install::uninstall(purge);
        let ok = result.is_ok();
        let text = match result {
            Ok(text) => text,
            Err(err) => format!("Удаление не удалось: {err}"),
        };
        let _ = std::fs::write(std::env::temp_dir().join("helene-uninstall.log"), &text);
        // Хвост самоудаления — ТОЛЬКО когда снятие состоялось. Раньше он бежал
        // всегда и уносил helene-setup.exe даже из папки, которую снимать отказались.
        if ok {
            install::uninstall_finish();
        }
        if !ok {
            std::process::exit(1);
        }
        return;
    }
    UNINSTALL_MODE.store(uninstall_mode, std::sync::atomic::Ordering::Relaxed);

    tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| {
            focus_main(app);
            // «Удалить» в «Приложениях» при открытом установщике запускает
            // helene-setup.exe --uninstall. Молча сфокусировать окно установки —
            // значит показать человеку не то, о чём он просил.
            if argv.iter().any(|a| a == "--uninstall")
                && !UNINSTALL_MODE.load(std::sync::atomic::Ordering::Relaxed)
            {
                std::thread::spawn(|| {
                    message_box(
                        "Сейчас открыт установщик. Закрой его окно и запусти удаление ещё раз.",
                    )
                });
            }
        }))
        .invoke_handler(tauri::generate_handler![defaults, install, open_frame, probe_model, relay_login, relay_status, relay_models, uninstall_run, legacy_services, remove_service, admin_rights])
        .setup(|app| {
            // Учётные данные ChatGPT прошлого запуска установщика: пока они
            // лежали в %TEMP%, протухшая учётка от другого аккаунта показывалась
            // как зелёное «Вход выполнен» и переезжала в новую установку.
            // Только здесь: второй экземпляр до setup не доходит (его снимает
            // плагин single-instance), иначе он стёр бы вход первого.
            install::relay_cleanup();
            tauri::WebviewWindowBuilder::new(
                app,
                "main",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title(if UNINSTALL_MODE.load(std::sync::atomic::Ordering::Relaxed) { "Снятие Hélène" } else { "Установка Hélène" })
            .initialization_script(if UNINSTALL_MODE.load(std::sync::atomic::Ordering::Relaxed) {
                "window.SETUP_MODE = 'uninstall';"
            } else {
                "window.SETUP_MODE = 'install';"
            })
            .inner_size(1600.0, 900.0)
            .min_inner_size(960.0, 600.0)
            .center()
            .maximized(true)
            .decorations(false)
            .shadow(true)
            .visible(false)
            .build()?;
            // Страховка: окно рождается невидимым, а показывает его UI. Любое
            // исключение в модуле UI до win.show() оставляло живой процесс вообще
            // без окна — владелец видел пустоту, а single-instance был занят.
            // Показать уже показанное окно безвредно (так же сделано в оболочке).
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_secs(4));
                if let Some(window) = handle.get_webview_window("main") {
                    if !window.is_visible().unwrap_or(false) {
                        let _ = window.show();
                        let _ = window.set_focus();
                    }
                }
            });
            Ok(())
        })
        // Закрыли установщик посреди входа в ChatGPT — помощник входа не
        // должен остаться держать порт.
        .on_window_event(|_window, event| {
            if let tauri::WindowEvent::Destroyed = event {
                install::relay_abort();
            }
        })
        .build(tauri::generate_context!())
        .expect("окно установщика не поднялось")
        // Любой выход установщика — и отменённый вход в ChatGPT не остаётся
        // висеть с занятым портом.
        .run(|_app, event| {
            if let tauri::RunEvent::Exit = event {
                install::relay_abort();
                // Временный дом реле с живым refresh_token к аккаунту ChatGPT не
                // должен пережить установщик: раньше он оставался в %TEMP% навсегда.
                install::relay_cleanup();
                if UNINSTALL_DONE.load(std::sync::atomic::Ordering::Relaxed) {
                    install::uninstall_finish();
                }
            }
        });
}
