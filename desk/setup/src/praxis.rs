//! Мастер Praxis (1.2, 27.09): тот же exe, собранный с фичей `praxis`.
//!
//! Praxis — окно к агенту на своём сервере: ни ядра, ни рантайма, ни данных, только
//! `praxis.exe`, статика окна и `helene.json` с адресом сервера и ключом канала
//! (их спрашивает само окно при первом запуске). До 1.2 его ставил NSIS серыми
//! страницами; теперь — этот мастер: «для кого», раскладка транзакцией рядом (та же,
//! что у Hélène: отмена — откат), ярлыки, запись в «Приложениях», «Открыть».
//! Снятие спрашивает только одно: оставить ли настройки подключения.
#![cfg(feature = "praxis")]

use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};

use crate::install::{Progress, Receipt, Setup, Step};

/// Установка или обновление Praxis. Из решений нужны только режим и папка.
#[cfg(windows)]
pub fn install_praxis(s: &Setup, cancel: &AtomicBool, progress: &mut dyn FnMut(Progress)) -> Result<Receipt, String> {
    use crate::payload::{Skip, Stop};
    use crate::tx::{Carry, StaticCarry, Tx};
    let source = crate::install::source().ok_or_else(|| {
        "в установщике нет поставки — файл скачался не целиком? Скачай Praxis-<версия>-setup.exe заново".to_string()
    })?;
    let dir = crate::install::target_dir(s)?;
    let scope = crate::install::effective_scope(s, &dir);
    let had_install = dir.join("praxis.exe").exists() || dir.join("helene.json").exists();
    let total = if had_install { 6 } else { 5 };
    let mut n = 0usize;
    let mut say = |phase: &str, label: &str, frac: Option<f64>, detail: Option<String>, cancellable: bool, bump: bool, progress: &mut dyn FnMut(Progress)| {
        if bump {
            n += 1;
        }
        progress(Progress { step: n, total, label: label.to_string(), phase: phase.to_string(), frac, detail, cancellable });
    };
    let mut steps: Vec<Step> = Vec::new();
    if let Some(note) = crate::tx::recover(&dir) {
        steps.push(Step { label: "Прерванная установка".into(), ok: true, note: Some(note) });
    }
    say("check", "Проверяю установщик", None, None, true, true, progress);
    source.verify()?;
    let manifest = source.manifest();
    let version = if manifest.version.is_empty() { crate::install::source_version() } else { manifest.version.clone() };
    if cancel.load(Ordering::Relaxed) {
        return Err(crate::install::CANCELLED.into());
    }

    say("lay", "Раскладываю новую версию рядом", Some(0.0), None, true, true, progress);
    let mut tx = Tx::begin(&dir, &version)?;
    if scope == "machine" {
        let _ = crate::install::icacls_users_modify_pub(&tx.new);
    }
    let skip = Skip { top: &["helene.json", "helene.log"], rel: &[] };
    let laid = source.extract(&tx.new, &skip, cancel, &mut |done, all, files| {
        let frac = if all > 0 { (done as f64 / all as f64).min(1.0) } else { 0.0 };
        say("lay", "Раскладываю новую версию рядом", Some(frac), Some(format!("{files} файлов")), true, false, progress);
    });
    match laid {
        Ok(stats) => steps.push(Step {
            label: "Файлы программы".into(),
            ok: true,
            note: Some(format!("{} файлов разложено рядом и поставлено одной подменой", stats.files)),
        }),
        Err(Stop::Cancelled) => {
            let _ = tx.rollback();
            return Err(crate::install::CANCELLED.into());
        }
        Err(Stop::Failed(e)) => {
            let _ = tx.rollback();
            return Err(format!("новая версия не разложилась: {e} — прежняя версия цела"));
        }
    }

    if had_install {
        say("stop", "Закрываю открытое окно Praxis", None, None, true, true, progress);
        // Окно Praxis из этой папки — гасим; не погасилось — подмена скажет, что папка занята.
        let _ = crate::install::stop_running(&dir);
        if cancel.load(Ordering::Relaxed) {
            let _ = tx.rollback();
            return Err(crate::install::CANCELLED.into());
        }
    }

    say("swap", "Меняю версии местами", None, None, false, true, progress);
    let old_top: Vec<String> = crate::install::read_json_pub(&dir.join(crate::payload::MANIFEST))
        .and_then(|m| m.get("top").and_then(|t| t.as_array()).cloned())
        .map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_string)).collect::<Vec<String>>())
        .unwrap_or_default()
        .into_iter()
        .filter(|n| n != "helene.json")
        .collect();
    let carry = Carry {
        drop: &["uninstall.exe", "install.log", crate::install::INSTALL_MARKER, "КАК-ВЕРНУТЬСЯ.md"],
        old_payload_top: &old_top,
        static_carry: StaticCarry::None,
        keep_runtime: false,
        extra: &[],
    };
    tx.swap(&carry)?;

    say("configure", "Настройки подключения", None, None, false, true, progress);
    // Адрес сервера и ключ канала — владельца: поверх не переписываются никогда. Нет
    // своих — заготовка из поставки (окно спросит адрес и ключ при первом запуске).
    let cfg = dir.join("helene.json");
    let configured = (|| -> Result<Step, String> {
        let note = if cfg.is_file() {
            "адрес сервера и ключ канала оставлены как были".to_string()
        } else {
            let template = source.read("helene.json").ok_or("в поставке нет заготовки helene.json")?;
            std::fs::write(&cfg, template).map_err(|e| format!("{}: {e}", cfg.display()))?;
            "адрес сервера и ключ канала окно спросит при первом запуске".to_string()
        };
        crate::install::write_marker_pub(&dir, &scope, &version)?;
        Ok(Step { label: "Настройки".into(), ok: true, note: Some(note) })
    })();
    match configured {
        Ok(step) => steps.push(step),
        Err(e) => {
            let back = tx.rollback();
            return Err(format!(
                "настройки не записались: {e}. {}",
                if back.is_empty() { "Прежняя версия возвращена на место".to_string() } else { format!("Вернулось не всё: {}", back.join("; ")) }
            ));
        }
    }

    say("register", "Ярлыки и запись в «Приложениях»", None, None, false, true, progress);
    let exe = crate::install::shell_exe(&dir);
    let ico = dir.join(crate::install::ICON_FILE);
    match crate::install::shortcuts_for_pub(&exe, crate::install::PRODUCT, Some(ico.as_path()).filter(|p| p.is_file()), scope == "machine") {
        Ok(note) => steps.push(Step { label: "Ярлыки".into(), ok: true, note: Some(note) }),
        Err(err) => steps.push(Step { label: "Ярлыки".into(), ok: false, note: Some(err) }),
    }
    let size_kb = (manifest.bytes / 1024).min(u32::MAX as u64) as u32;
    match crate::install::register_uninstall_for_pub(&dir, size_kb, &version, scope == "machine") {
        Ok(note) => steps.push(Step { label: "Запись об удалении".into(), ok: true, note }),
        Err(err) => steps.push(Step { label: "Запись об удалении".into(), ok: false, note: Some(err) }),
    }

    let cleanup = tx.commit();
    crate::install::push_cleanup(cleanup);
    say("done", "Готово", Some(1.0), None, false, true, progress);
    Ok(Receipt {
        dir: dir.display().to_string(),
        exe: exe.display().to_string(),
        service: "skipped".into(),
        scope,
        backup: None,
        warning: None,
        steps,
    })
}

/// Снятие Praxis: окно, ярлыки, запись, файлы. Настройки подключения (`helene.json`) —
/// только по слову «стереть всё».
#[cfg(windows)]
pub fn uninstall_praxis(dir: &Path, purge: bool, progress: &mut dyn FnMut(Progress)) -> Result<String, String> {
    let mut n = 0usize;
    let mut say = |phase: &str, label: &str, progress: &mut dyn FnMut(Progress)| {
        n += 1;
        progress(Progress { step: n, total: 3, label: label.to_string(), phase: phase.to_string(), ..Default::default() });
    };
    let scope = crate::probe::scope_of_dir(dir);
    say("stop", "Закрываю окно Praxis", progress);
    let _ = crate::install::stop_running(dir);
    say("files", "Убираю программу, ярлыки и запись", progress);
    let mut problems: Vec<String> = Vec::new();
    crate::install::unregister_pub(scope == "machine", &mut problems);
    let mut left: Vec<String> = Vec::new();
    for entry in std::fs::read_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))?.flatten() {
        let name = entry.file_name().to_string_lossy().to_string();
        if !purge && (name == "helene.json" || name == "helene.json.bak") {
            continue;
        }
        if name.eq_ignore_ascii_case(crate::install::SETUP_ENTRY) {
            continue;
        }
        let path = entry.path();
        let res = if path.is_dir() { crate::tx::remove_tree(&path) } else { std::fs::remove_file(&path) };
        if res.is_err() {
            left.push(name);
        }
    }
    if !left.is_empty() {
        problems.push(format!("не удалось удалить: {}", left.join(", ")));
    }
    let exe_here = std::env::current_exe()
        .ok()
        .and_then(|e| e.parent().map(|p| p == dir))
        .unwrap_or(false);
    crate::install::keep_setup_exe(!exe_here);
    say("done", "Готово", progress);
    let mut text = if purge {
        "Praxis удалён вместе с настройками подключения.".to_string()
    } else {
        format!(
            "Praxis удалён. Настройки подключения (адрес сервера и ключ канала) оставлены в {} — \
             следующая установка Praxis подхватит их сама.",
            dir.display()
        )
    };
    if !problems.is_empty() {
        text.push_str(&format!(" Не всё получилось: {}.", problems.join("; ")));
    }
    Ok(text)
}

