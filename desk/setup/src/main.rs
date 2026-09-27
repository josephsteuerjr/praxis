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

mod backup;
mod install;
mod payload;
#[cfg(feature = "praxis")]
mod praxis;
mod probe;
mod tx;
#[cfg(windows)]
mod win;

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

// ------------------------------------------------- 1.2: отмена и поднятый исполнитель

/// Отмена установки, начатой с экрана: кнопка «Отмена» ставит флаг, фазы проверяют
/// его между файлами; поднятому исполнителю флаг уходит файлом `cancel`.
static CANCEL: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Задание поднятому исполнителю (`--worker <папка обмена>`): что сделать и с чем.
#[derive(serde::Serialize, serde::Deserialize)]
struct WorkerPlan {
    /// `install` | `uninstall`
    op: String,
    #[serde(default)]
    setup: Option<install::Setup>,
    #[serde(default)]
    dir: Option<String>,
    #[serde(default)]
    purge: bool,
}

#[derive(serde::Serialize, serde::Deserialize, Default)]
struct WorkerResult {
    ok: bool,
    #[serde(default)]
    receipt: Option<install::Receipt>,
    #[serde(default)]
    text: Option<String>,
    #[serde(default)]
    error: Option<String>,
}

/// Куда поднятому исполнителю можно: только папка продукта (`…\Helene`) — в Program
/// Files, по записи «для всех» в реестре или уже установка Hélène. Задание лежит во
/// временной папке пользователя, и поднятый процесс не должен по нему переименовывать
/// или удалять чужие папки (`C:\Windows` → `.old`).
fn worker_dir_allowed(dir: &std::path::Path) -> bool {
    let name_ok = dir
        .file_name()
        .map(|n| n.to_string_lossy().eq_ignore_ascii_case(install::PRODUCT))
        .unwrap_or(false);
    if !name_ok || !dir.is_absolute() {
        return false;
    }
    #[cfg(windows)]
    {
        let norm = |p: &std::path::Path| {
            let s = std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf()).display().to_string();
            s.strip_prefix(r"\\?\").unwrap_or(&s).to_lowercase()
        };
        let d = norm(dir);
        let under_pf = dir.parent().map(|p| norm(p) == norm(&win::program_files())).unwrap_or(false);
        let registered = probe::registered().iter().any(|(p, _)| norm(p) == d);
        let ours = dir.join("helene-build.json").is_file() || dir.join(install::INSTALL_MARKER).is_file();
        under_pf || registered || ours
    }
    #[cfg(not(windows))]
    {
        false
    }
}

/// Тело поднятого исполнителя. Ход — строками JSON в `progress.jsonl`, итог — в
/// `result.json`; отмена — появление файла `cancel`. -> код выхода.
fn run_worker(xdir: &std::path::Path) -> i32 {
    use std::io::Write;
    let write_result = |r: &WorkerResult| {
        let _ = std::fs::write(xdir.join("result.json"), serde_json::to_string(r).unwrap_or_default());
    };
    let plan: WorkerPlan = match std::fs::read(xdir.join("plan.json"))
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
    {
        Some(p) => p,
        None => {
            write_result(&WorkerResult { error: Some("задание исполнителю не читается".into()), ..Default::default() });
            return 2;
        }
    };
    let cancel = std::sync::Arc::new(std::sync::atomic::AtomicBool::new(false));
    {
        let cancel = cancel.clone();
        let flag = xdir.join("cancel");
        std::thread::spawn(move || loop {
            if flag.exists() {
                cancel.store(true, std::sync::atomic::Ordering::Relaxed);
                return;
            }
            std::thread::sleep(Duration::from_millis(150));
        });
    }
    let mut out = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(xdir.join("progress.jsonl"))
        .ok();
    let mut emit = |p: install::Progress| {
        if let Some(f) = out.as_mut() {
            let _ = writeln!(f, "{}", serde_json::to_string(&p).unwrap_or_default());
            let _ = f.flush();
        }
    };
    let result = match plan.op.as_str() {
        "install" => match plan.setup {
            Some(setup) => {
                let dir = match install::target_dir(&setup) {
                    Ok(d) => d,
                    Err(e) => {
                        write_result(&WorkerResult { error: Some(e), ..Default::default() });
                        return 1;
                    }
                };
                if !worker_dir_allowed(&dir) {
                    write_result(&WorkerResult {
                        error: Some(format!("поднятый исполнитель не ставит в {} — это не папка {}", dir.display(), install::PRODUCT)),
                        ..Default::default()
                    });
                    return 1;
                }
                match install::install_run(&setup, &cancel, &mut emit) {
                    Ok(r) => WorkerResult { ok: true, receipt: Some(r), ..Default::default() },
                    Err(e) => WorkerResult { error: Some(e), ..Default::default() },
                }
            }
            None => WorkerResult { error: Some("в задании нет решений установки".into()), ..Default::default() },
        },
        "uninstall" => {
            let dir = plan.dir.map(std::path::PathBuf::from).unwrap_or_else(install::exe_dir);
            if !worker_dir_allowed(&dir) {
                write_result(&WorkerResult {
                    error: Some(format!("поднятый исполнитель не снимает {} — это не папка {}", dir.display(), install::PRODUCT)),
                    ..Default::default()
                });
                return 1;
            }
            match install::uninstall_dir(&dir, plan.purge, &mut emit) {
                Ok(text) => {
                    let timing = install::UNINSTALL_TIMING.lock().map(|t| t.clone()).unwrap_or_default();
                    let _ = std::fs::write(xdir.join("..").join("helene-uninstall-worker.log"), format!("{text}\n{timing}\n"));
                    // Поднятый исполнитель может убрать мастер из Program Files — окно
                    // без прав не может.
                    install::uninstall_finish();
                    WorkerResult { ok: true, text: Some(text), ..Default::default() }
                }
                Err(e) => WorkerResult { error: Some(e), ..Default::default() },
            }
        }
        other => WorkerResult { error: Some(format!("незнакомое задание: {other}")), ..Default::default() },
    };
    write_result(&result);
    install::join_cleanup(30);
    if result.ok { 0 } else { 1 }
}

/// Сделать `plan` поднятым исполнителем (один запрос прав) и вести его ход сюда.
#[cfg(windows)]
fn run_elevated(plan: &WorkerPlan, on_progress: &mut dyn FnMut(install::Progress)) -> Result<WorkerResult, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let xdir = std::env::temp_dir().join(format!(
        "helene-setup-x-{}-{}",
        std::process::id(),
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_millis()).unwrap_or(0)
    ));
    std::fs::create_dir_all(&xdir).map_err(|e| format!("{}: {e}", xdir.display()))?;
    std::fs::write(xdir.join("plan.json"), serde_json::to_string(plan).map_err(|e| e.to_string())?)
        .map_err(|e| format!("задание не записалось: {e}"))?;
    let child = match win::Elevated::start(&exe, &format!("--worker \"{}\"", xdir.display())) {
        Ok(c) => c,
        Err(e) => {
            let _ = std::fs::remove_dir_all(&xdir);
            return Err(e);
        }
    };
    let mut offset = 0usize;
    let mut cancel_sent = false;
    let mut pump = |offset: &mut usize| {
        if let Ok(bytes) = std::fs::read(xdir.join("progress.jsonl")) {
            if bytes.len() > *offset {
                let fresh = &bytes[*offset..];
                // Только целые строки: хвост без перевода строки дочитаем в следующий раз.
                if let Some(last_nl) = fresh.iter().rposition(|b| *b == b'\n') {
                    for line in fresh[..=last_nl].split(|b| *b == b'\n') {
                        if let Ok(p) = serde_json::from_slice::<install::Progress>(line) {
                            on_progress(p);
                        }
                    }
                    *offset += last_nl + 1;
                }
            }
        }
    };
    let code = loop {
        if CANCEL.load(std::sync::atomic::Ordering::Relaxed) && !cancel_sent {
            let _ = std::fs::write(xdir.join("cancel"), b"1");
            cancel_sent = true;
        }
        pump(&mut offset);
        if let Some(code) = child.wait(150) {
            break code;
        }
    };
    pump(&mut offset);
    let result = std::fs::read(xdir.join("result.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<WorkerResult>(&b).ok());
    let _ = std::fs::remove_dir_all(&xdir);
    result.ok_or_else(|| format!("поднятый исполнитель вышел без итога (код {code}) — прежняя версия на месте, если журнал подмены не говорит иного"))
}

#[cfg(not(windows))]
fn run_elevated(_plan: &WorkerPlan, _on_progress: &mut dyn FnMut(install::Progress)) -> Result<WorkerResult, String> {
    Err("поднятый исполнитель есть только на Windows".into())
}

/// Установка по решениям — сама или поднятым исполнителем, если режим «для всех».
fn install_any(setup: &install::Setup, on_progress: &mut dyn FnMut(install::Progress)) -> Result<install::Receipt, String> {
    let dir = install::target_dir(setup)?;
    let scope = install::effective_scope(setup, &dir);
    if !install::in_place() && install::needs_elevation(&scope) {
        let plan = WorkerPlan { op: "install".into(), setup: Some(setup.clone()), dir: None, purge: false };
        let r = run_elevated(&plan, on_progress)?;
        return match (r.ok, r.receipt) {
            (true, Some(rec)) => Ok(rec),
            _ => Err(r.error.unwrap_or_else(|| "установка не удалась".into())),
        };
    }
    install::install_run(setup, &CANCEL, on_progress)
}

/// Снятие установки `dir` — само или поднятым исполнителем («для всех»).
fn uninstall_any(dir: &std::path::Path, purge: bool, on_progress: &mut dyn FnMut(install::Progress)) -> Result<String, String> {
    let scope = probe::scope_of_dir(dir);
    if install::needs_elevation(&scope) {
        let plan = WorkerPlan { op: "uninstall".into(), setup: None, dir: Some(dir.display().to_string()), purge };
        let r = run_elevated(&plan, on_progress)?;
        return if r.ok { Ok(r.text.unwrap_or_default()) } else { Err(r.error.unwrap_or_else(|| "снятие не удалось".into())) };
    }
    install::uninstall_dir(dir, purge, on_progress)
}

/// Журнал установки: при удаче — в папке установки, при отказе — во временной папке
/// (папки установки после отката может не быть) и путь уходит в текст отказа.
fn write_install_log(result: &Result<install::Receipt, String>, log: &str) -> Option<std::path::PathBuf> {
    let path = match result {
        Ok(r) => std::path::PathBuf::from(&r.dir).join("install.log"),
        Err(_) => std::env::temp_dir().join("Helene-install.log"),
    };
    std::fs::write(&path, log).ok().map(|_| path)
}

#[tauri::command]
fn defaults() -> install::Defaults {
    install::defaults()
}

/// Решения уже стоящей установки (26.09, сцена «уже установлена»): те же, что
/// читает `--update`. Нет решений (имена, конституция) — None, и мастер идёт
/// обычным маршрутом, а не подставляет умолчания поверх выбора владельца.
/// «Удалить» из мастера на месте — это `uninstall.exe` установщика NSIS: он снимает
/// службу и файлы через `helene-setup.exe --uninstall --quiet`, потом свою запись и ярлыки.
#[tauri::command]
fn uninstall_launch(app: tauri::AppHandle) -> Result<(), String> {
    let exe = install::exe_dir().join("uninstall.exe");
    if !exe.is_file() {
        return Err("рядом нет uninstall.exe установщика".into());
    }
    let mut cmd = Command::new(&exe);
    cmd.current_dir(std::env::temp_dir());
    #[cfg(windows)]
    win::spawn_outside(&mut cmd).map_err(|e| format!("uninstall.exe не запустился: {e}"))?;
    #[cfg(not(windows))]
    cmd.spawn().map_err(|e| format!("uninstall.exe не запустился: {e}"))?;
    app.exit(0);
    Ok(())
}

#[tauri::command]
fn installed_setup(dir: String) -> Option<install::Setup> {
    install::setup_from_dir(std::path::Path::new(&dir))
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
/// 1.2: «для всех» — поднятым исполнителем (один запрос прав), ход от него идёт
/// сюда же; «Отмена» до подмены — откат (`cancel_install`). Журнал пишется ВСЕГДА:
/// при удаче — в папке установки, при отказе — во временной папке.
#[tauri::command]
async fn install(app: tauri::AppHandle, setup: install::Setup) -> Result<install::Receipt, String> {
    CANCEL.store(false, std::sync::atomic::Ordering::Relaxed);
    let result = tauri::async_runtime::spawn_blocking(move || {
        let mut log = String::new();
        let r = install_any(&setup, &mut |p| {
            if p.frac.is_none() || p.phase == "done" {
                log.push_str(&format!("[{}/{}] {}\n", p.step, p.total, p.label));
            }
            let _ = app.emit("install-progress", p);
        });
        match &r {
            Ok(rec) => log.push_str(&format!("OK {}\n", serde_json::to_string(rec).unwrap_or_default())),
            Err(e) => log.push_str(&format!("FAIL {e}\n")),
        }
        let where_ = write_install_log(&r, &log);
        r.map_err(|e| match where_ {
            Some(p) if !e.starts_with("Отменено") => format!("{e}\n\nЖурнал: {}", p.display()),
            _ => e,
        })
    })
    .await
    .map_err(|e| e.to_string())?;
    result
}

/// «Отмена» на экране установки: до подмены — откат, прежняя версия цела.
#[tauri::command]
fn cancel_install() {
    CANCEL.store(true, std::sync::atomic::Ordering::Relaxed);
}

/// Что лежит на машине: установки, остатки, копии (сцена «найдена память»).
#[tauri::command]
async fn found() -> Vec<probe::Found> {
    tauri::async_runtime::spawn_blocking(probe::probe).await.unwrap_or_default()
}

/// Открыть установленный Hélène и закрыть установщик.
#[cfg(windows)]
#[tauri::command]
fn open_frame(app: tauri::AppHandle, exe: String) -> Result<(), String> {
    let path = std::path::PathBuf::from(&exe);
    // Окно установщика прячем сразу, а процесс держим ещё несколько секунд:
    // Windows отдаёт передний план новому окну, только пока запустивший его
    // процесс жив. Иначе Hélène открывалась позади других окон, и казалось,
    // что не открылся вовсе.
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.hide();
    }
    std::thread::spawn(move || {
        // Под службой — сначала её канал (до 45 с): окно, открытое раньше службы,
        // поднимало своих детей, и два движка сходились на одном дереве (27.09).
        if let Some(dir) = path.parent() {
            install::wait_for_channel(dir, 45);
        }
        // Вне job мастера (иначе программа умерла бы вместе с ним) и без прав
        // администратора, даже если мастер поднят.
        if let Err(e) = win::launch_app(&path) {
            message_box(&format!("Hélène не запустилась: {e}. Открой её ярлыком."));
        }
        std::thread::sleep(Duration::from_secs(10));
        install::relay_abort();
        install::join_cleanup(20);
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
async fn uninstall_run(app: tauri::AppHandle, purge: bool, dir: Option<String>) -> Result<String, String> {
    let text = tauri::async_runtime::spawn_blocking(move || {
        let dir = dir.filter(|d| !d.trim().is_empty()).map(std::path::PathBuf::from).unwrap_or_else(install::exe_dir);
        uninstall_any(&dir, purge, &mut |p| {
            let _ = app.emit("uninstall-progress", p);
        })
    })
    .await
    .map_err(|e| e.to_string())??;
    let timing = install::UNINSTALL_TIMING.lock().map(|t| t.clone()).unwrap_or_default();
    let _ = std::fs::write(std::env::temp_dir().join("helene-uninstall.log"), format!("{text}\n{timing}\n"));
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
    // 26.09 (1.1.0): рядом с exe лежит uninstall.exe установщика NSIS или сказано
    // `--configure` — мастер работает «на месте»: файлы уже здесь, он их не копирует.
    // Что видит мастер (установки, найденная память, режимы, папки) — JSON в файл:
    // для разбора «почему он не нашёл мою копию» без окна.
    if let Some(i) = args.iter().position(|a| a == "--probe") {
        let d = install::defaults();
        let text = serde_json::to_string_pretty(&d).unwrap_or_default();
        match args.get(i + 1) {
            Some(path) => {
                let _ = std::fs::write(path, text);
            }
            None => println!("{text}"),
        }
        return;
    }
    // Поднятый исполнитель «для всех»: без окна, по заданию из папки обмена.
    if let Some(i) = args.iter().position(|a| a == "--worker") {
        #[cfg(windows)]
        let _ = win::adopt_self_into_job();
        let code = match args.get(i + 1) {
            Some(x) => run_worker(std::path::Path::new(x)),
            None => 2,
        };
        std::process::exit(code);
    }
    // Мастер и всё, что он породит, — в одном job: вышел или упал — дети умирают с ним.
    #[cfg(windows)]
    let _ = win::adopt_self_into_job();
    if args.iter().any(|a| a == "--configure")
        || install::nsis_root().is_some()
        || install::exe_dir().join(install::INSTALL_MARKER).is_file()
    {
        install::IN_PLACE.store(true, std::sync::atomic::Ordering::Relaxed);
    }
    // Перед обновлением поверх: установщик NSIS зовёт старую копию мастера, чтобы та
    // сняла службу и погасила окно. Ход — в stop.log рядом.
    if args.iter().any(|a| a == "--stop") {
        // `--dir` — папка СТАРОЙ установки: установщик NSIS зовёт новый мастер из своей
        // временной папки, потому что старая копия этого ключа может не знать.
        let dir = args
            .iter()
            .position(|a| a == "--dir")
            .and_then(|i| args.get(i + 1))
            .map(std::path::PathBuf::from)
            .unwrap_or_else(install::exe_dir);
        let result = install::stop_for_update(&dir);
        let _ = std::fs::write(
            install::exe_dir().join("stop.log"),
            match &result {
                Ok(note) => format!("OK {note}\n"),
                Err(e) => format!("FAIL {e}\n"),
            },
        );
        if let Err(e) = result {
            if !args.iter().any(|a| a == "--quiet") {
                message_box(&format!("Не удалось остановить Hélène перед обновлением: {e}"));
            }
            std::process::exit(1);
        }
        return;
    }
    // `--configure` поверх уже настроенной установки — обновление без вопросов:
    // решения из самой установки, конфиг сливается, потом открывается Hélène.
    // Настроенной ещё нет (первая установка) — дальше обычный мастер, на месте.
    if args.iter().any(|a| a == "--configure") {
        let dir = install::exe_dir();
        if let Some(mut setup) = install::setup_from_dir(&dir) {
            setup.force_extensions = true;
            let mut log = format!("настройка на месте поверх {}\n", dir.display());
            let result = install::install(&setup, |p| {
                log.push_str(&format!("[{}/{}] {}\n", p.step, p.total, p.label));
            });
            match &result {
                Ok(r) => log.push_str(&format!("OK {}\n", serde_json::to_string(r).unwrap_or_default())),
                Err(e) => log.push_str(&format!("FAIL {e}\n")),
            }
            // Под службой окно открываем, когда её канал уже отвечает: иначе окно
            // поднимает своих детей, и два движка сходятся на одном дереве (27.09).
            if result.is_ok() {
                let t0 = std::time::Instant::now();
                let up = install::wait_for_channel(&dir, 45);
                log.push_str(&format!(
                    "служба: канал {} ({} с)\n",
                    if up { "ответил" } else { "не ответил — открываю окно всё равно" },
                    t0.elapsed().as_secs()
                ));
            }
            let _ = std::fs::write(dir.join("install.log"), &log);
            match result {
                Ok(r) => {
                    let exe = std::path::PathBuf::from(&r.exe);
                    #[cfg(windows)]
                    let started = win::launch_app(&exe);
                    #[cfg(not(windows))]
                    let started = install::launch_installed(&exe);
                    if let Err(e) = started {
                        message_box(&format!("Hélène обновлена ({}), но не запустилась сама: {e}. Открой её ярлыком.", r.dir));
                    }
                }
                Err(e) => {
                    message_box(&format!("Обновление не удалось: {e}\n\nЖурнал: {}", dir.join("install.log").display()));
                    std::process::exit(1);
                }
            }
            return;
        }
    }
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
                install_any(&setup, &mut |p| {
                    if p.frac.is_some() && p.phase != "done" {
                        return;
                    }
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
        install::join_cleanup(30);
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
            Some(mut setup) => {
                // 25.09 (K): «обновить без расширений» — только явным словом; без него
                // несовместимое расширение владельца останавливает обновление до подмены.
                setup.force_extensions = args.iter().any(|a| a == "--force-extensions");
                let mut log = format!("обновление поверх {}\n", setup.dir);
                let result = install_any(&setup, &mut |p| {
                    if p.frac.is_none() {
                        log.push_str(&format!("[{}/{}] {}\n", p.step, p.total, p.label));
                    }
                });
                match &result {
                    Ok(r) => log.push_str(&format!("OK {}\n", serde_json::to_string(r).unwrap_or_default())),
                    Err(e) => log.push_str(&format!("FAIL {e}\n")),
                }
                let _ = std::fs::write(&log_path, &log);
                match result {
                    Err(e) => {
                        if !args.iter().any(|a| a == "--quiet") {
                            message_box(&format!("Обновление не удалось: {e}"));
                        }
                        std::process::exit(1);
                    }
                    Ok(r) => {
                        // Кнопка «Обновить» в окне: окно закрылось ради подмены — открыть
                        // обновлённое (под службой — когда её канал ответит).
                        if !args.iter().any(|a| a == "--no-launch") {
                            let exe = std::path::PathBuf::from(&r.exe);
                            install::wait_for_channel(std::path::Path::new(&r.dir), 45);
                            #[cfg(windows)]
                            let _ = win::launch_app(&exe);
                            #[cfg(not(windows))]
                            let _ = exe;
                        }
                        install::join_cleanup(30);
                    }
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
        let result = uninstall_any(&install::exe_dir(), purge, &mut |_| {});
        let ok = result.is_ok();
        let text = match result {
            Ok(text) => text,
            Err(err) => format!("Удаление не удалось: {err}"),
        };
        let timing = install::UNINSTALL_TIMING.lock().map(|t| t.clone()).unwrap_or_default();
        let _ = std::fs::write(std::env::temp_dir().join("helene-uninstall.log"), format!("{text}\n{timing}\n"));
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
        .invoke_handler(tauri::generate_handler![defaults, installed_setup, uninstall_launch, install, cancel_install, found, open_frame, probe_model, relay_login, relay_status, relay_models, uninstall_run, legacy_services, remove_service, admin_rights])
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
            .title(&if UNINSTALL_MODE.load(std::sync::atomic::Ordering::Relaxed) {
                format!("Снятие {}", install::PRODUCT_UI)
            } else {
                format!("Установка {}", install::PRODUCT_UI)
            })
            .initialization_script(&format!(
                "window.SETUP_MODE = '{}'; window.SETUP_VARIANT = '{}';",
                if UNINSTALL_MODE.load(std::sync::atomic::Ordering::Relaxed) { "uninstall" } else { "install" },
                if cfg!(feature = "praxis") { "praxis" } else { "helene" }
            ))
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
                // Прежняя версия (`.old`) удаляется в фоне после удачной установки —
                // окно закрыли раньше, чем она ушла: дождаться (не дольше 20 с).
                install::join_cleanup(20);
                // Временный дом реле с живым refresh_token к аккаунту ChatGPT не
                // должен пережить установщик: раньше он оставался в %TEMP% навсегда.
                install::relay_cleanup();
                if UNINSTALL_DONE.load(std::sync::atomic::Ordering::Relaxed) {
                    install::uninstall_finish();
                }
            }
        });
}
