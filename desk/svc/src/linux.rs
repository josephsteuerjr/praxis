//! `helene-svc` на Linux (порт 28.09): служба systemd и дом владельца.
//!
//! Устройство — в шапке `common/linux_service.rs`. Коротко: пакет ставит программу в
//! /opt/helene и шаблон `helene@.service`; экземпляр шаблона — владелец, агент идёт от его
//! имени без входа в систему. Дом владельца — `~/.local/share/helene` (конфиг, данные и
//! ссылки на части программы).
//!
//! CLI:
//!   helene-svc daemon --owner <имя>            (зовёт systemd из шаблона юнита)
//!   helene-svc daemon --config <путь>          (то же руками — отладка)
//!   helene-svc home                            (завести/проверить дом владельца; JSON)
//!   helene-svc unit [--root /opt/helene]       (печать шаблона юнита — его кладёт сборка)
//!   helene-svc service install|remove|state    (владелец; пароль спросит polkit)
//!   helene-svc service-root enable|disable <имя>  (под root через pkexec — руками не звать)

use std::path::{Path, PathBuf};

use super::daemon;

include!("../../common/linux_service.rs");

fn arg_after(flag: &str) -> Option<String> {
    super::arg_after(flag)
}

/// (имя, uid, дом) пользователя по записи passwd — `getpwuid_r`/`getpwnam_r`: переменные
/// окружения врут под sudo и отсутствуют под systemd до `User=`.
fn passwd_entry(by_name: Option<&str>, by_uid: Option<u32>) -> Option<(String, u32, PathBuf)> {
    use std::ffi::{CStr, CString};
    let mut buffer = vec![0 as libc::c_char; 16 * 1024];
    let mut entry: libc::passwd = unsafe { std::mem::zeroed() };
    let mut found: *mut libc::passwd = std::ptr::null_mut();
    let code = match (by_name, by_uid) {
        (Some(name), _) => {
            let name = CString::new(name).ok()?;
            unsafe {
                libc::getpwnam_r(name.as_ptr(), &mut entry, buffer.as_mut_ptr(), buffer.len(), &mut found)
            }
        }
        (None, Some(uid)) => unsafe {
            libc::getpwuid_r(uid, &mut entry, buffer.as_mut_ptr(), buffer.len(), &mut found)
        },
        (None, None) => return None,
    };
    if code != 0 || found.is_null() {
        return None;
    }
    let name = unsafe { CStr::from_ptr(entry.pw_name) }.to_string_lossy().into_owned();
    let home = unsafe { CStr::from_ptr(entry.pw_dir) }.to_string_lossy().into_owned();
    Some((name, entry.pw_uid, PathBuf::from(home)))
}

fn me() -> Result<(String, u32, PathBuf), String> {
    let uid = unsafe { libc::geteuid() };
    passwd_entry(None, Some(uid)).ok_or_else(|| format!("не нашёл себя (uid {uid}) в списке пользователей"))
}

/// Корень программы: папка, где лежит этот бинарь, со снятыми ссылками (из дома владельца
/// `helene-svc` — ссылка в /opt/helene).
fn program_root() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|exe| exe.canonicalize().ok())
        .and_then(|exe| exe.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from(LINUX_SVC_PROGRAM_ROOT))
}

fn helene_home(home: &Path) -> PathBuf {
    home.join(LINUX_SVC_HOME_REL)
}

/// Что сделала подготовка дома — для JSON-ответа и журнала.
#[derive(Default)]
struct HomeReport {
    created: Vec<String>,
    relinked: Vec<String>,
    conflicts: Vec<String>,
    missing_in_program: Vec<String>,
}

/// Завести дом владельца: папка 0700, `data/`, `helene.json` из шаблона программы (если
/// его ещё нет — свой конфиг владельца пакет не трогает никогда) и ссылки на части
/// программы. Чужой файл на месте ссылки не сносится — это конфликт, и он называется.
fn init_home(root: &Path, program: &Path) -> Result<HomeReport, String> {
    use std::os::unix::fs::PermissionsExt;
    let mut report = HomeReport::default();
    if !root.is_dir() {
        std::fs::create_dir_all(root).map_err(|e| format!("не завёлся дом {}: {e}", root.display()))?;
        report.created.push(root.display().to_string());
    }
    let _ = std::fs::set_permissions(root, std::fs::Permissions::from_mode(0o700));
    let data = root.join("data");
    if !data.is_dir() {
        std::fs::create_dir_all(&data).map_err(|e| format!("не завелась {}: {e}", data.display()))?;
        report.created.push("data".into());
    }
    let config = root.join("helene.json");
    if !config.exists() {
        let template = program.join("helene.json");
        std::fs::copy(&template, &config).map_err(|e| {
            format!("не положился helene.json из шаблона {}: {e}", template.display())
        })?;
        let _ = std::fs::set_permissions(&config, std::fs::Permissions::from_mode(0o600));
        report.created.push("helene.json".into());
    }
    for name in LINUX_SVC_LINKED {
        let target = program.join(name);
        if !target.exists() {
            report.missing_in_program.push((*name).to_string());
            continue;
        }
        let link = root.join(name);
        match std::fs::symlink_metadata(&link) {
            Ok(meta) if meta.file_type().is_symlink() => {
                if std::fs::read_link(&link).ok().as_deref() != Some(target.as_path()) {
                    let _ = std::fs::remove_file(&link);
                    std::os::unix::fs::symlink(&target, &link)
                        .map_err(|e| format!("не переставилась ссылка {}: {e}", link.display()))?;
                    report.relinked.push((*name).to_string());
                }
            }
            Ok(_) => report.conflicts.push((*name).to_string()),
            Err(_) => {
                std::os::unix::fs::symlink(&target, &link)
                    .map_err(|e| format!("не завелась ссылка {}: {e}", link.display()))?;
                report.created.push((*name).to_string());
            }
        }
    }
    Ok(report)
}

fn fail(words: impl AsRef<str>) -> ! {
    eprintln!("{}", words.as_ref());
    std::process::exit(1);
}

fn json_line(value: serde_json::Value) {
    println!("{value}");
}

/// Жив ли движок на порту конфига — тот же опознавательный маршрут, что у окна (`/api/who`).
fn engine_alive(config: &Path) -> Option<bool> {
    let port = super::load_plan(config).ok()?.port;
    Some(super::harness_alive(port))
}

pub fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    match mode.as_str() {
        "daemon" => {
            let config = match (arg_after("--config"), arg_after("--owner")) {
                (Some(raw), _) => PathBuf::from(raw),
                (None, Some(owner)) => {
                    let (name, uid, home) = me().unwrap_or_else(|why| fail(why));
                    if let Err(why) = linux_svc_owner_ok(&name, Some(uid)) {
                        fail(why);
                    }
                    if name != owner.trim() {
                        fail(format!(
                            "служба поднята от имени {name}, а владелец в юните — {owner}: \
                             чужого агента не поднимаю"
                        ));
                    }
                    let root = helene_home(&home);
                    // Дом заводится и здесь: служба обязана работать, даже если окно ни разу
                    // не открывали (владелец поставил пакет и включил службу из терминала).
                    if let Err(why) = init_home(&root, &program_root()) {
                        fail(why);
                    }
                    root.join("helene.json")
                }
                (None, None) => fail("daemon --owner <имя> | --config <путь к helene.json>"),
            };
            std::process::exit(daemon::run(&config));
        }
        "home" => {
            let (_, _, home) = me().unwrap_or_else(|why| fail(why));
            let root = helene_home(&home);
            let program = program_root();
            match init_home(&root, &program) {
                Ok(report) => json_line(serde_json::json!({
                    "ok": true,
                    "home": root,
                    "program": program,
                    "config": root.join("helene.json"),
                    "created": report.created,
                    "relinked": report.relinked,
                    "conflicts": report.conflicts,
                    "missing_in_program": report.missing_in_program,
                })),
                Err(why) => fail(why),
            }
        }
        "unit" => {
            let root = arg_after("--root").unwrap_or_else(|| LINUX_SVC_PROGRAM_ROOT.to_string());
            print!("{}", linux_svc_unit_text(&root));
        }
        "service" => {
            let op = std::env::args().nth(2).unwrap_or_default();
            let (name, uid, home) = me().unwrap_or_else(|why| fail(why));
            let owner = arg_after("--owner").unwrap_or(name);
            if let Err(why) = linux_svc_owner_ok(&owner, Some(uid)) {
                fail(why);
            }
            let config = helene_home(&home).join("helene.json");
            let root_op = match op.as_str() {
                "install" => Some("enable"),
                "remove" => Some("disable"),
                "state" => None,
                _ => fail("service install|remove|state [--owner <имя>]"),
            };
            let mut said = String::new();
            if let Some(root_op) = root_op {
                if op == "install" {
                    if let Err(why) = init_home(&helene_home(&home), &program_root()) {
                        fail(why);
                    }
                }
                let exe = program_root().join("helene-svc");
                let argv = vec![
                    exe.display().to_string(),
                    "service-root".into(),
                    root_op.into(),
                    owner.clone(),
                ];
                match linux_svc_run_admin(&argv) {
                    Ok(out) => said = out,
                    Err(why) => fail(why),
                }
            }
            let state = linux_svc_state(&owner);
            let engine = if state == "running" {
                // Служба только что поднята: движку нужны секунды, чтобы занять порт.
                std::thread::sleep(std::time::Duration::from_secs(if root_op.is_some() { 3 } else { 0 }));
                engine_alive(&config)
            } else {
                engine_alive(&config)
            };
            json_line(serde_json::json!({
                "ok": true,
                "op": op,
                "owner": owner,
                "unit": linux_svc_instance(&owner),
                "state": state,
                "engine_alive": engine,
                "said": said,
            }));
        }
        "service-root" => {
            // Корневая половина: сюда приходит pkexec (или sudo на раннере). Имя владельца
            // сверяется с тем, КТО просил (PKEXEC_UID / SUDO_UID): включить службу чужому
            // пользователю через свой пароль нельзя.
            if unsafe { libc::geteuid() } != 0 {
                fail("service-root работает только под администратором (его зовёт pkexec)");
            }
            let op = std::env::args().nth(2).unwrap_or_default();
            let owner = std::env::args().nth(3).unwrap_or_default();
            if let Err(why) = linux_svc_owner_ok(&owner, None) {
                fail(why);
            }
            let asker = std::env::var("PKEXEC_UID")
                .or_else(|_| std::env::var("SUDO_UID"))
                .ok()
                .and_then(|raw| raw.trim().parse::<u32>().ok());
            let Some(asker) = asker else {
                fail("не понял, кто просит: нет PKEXEC_UID/SUDO_UID — service-root зовут только через pkexec");
            };
            let Some((asker_name, _, _)) = passwd_entry(None, Some(asker)) else {
                fail(format!("просящий uid {asker} не найден в списке пользователей"));
            };
            if asker_name != owner {
                fail(format!("{asker_name} просит службу для {owner}: только для себя"));
            }
            let args: Vec<&str> = match op.as_str() {
                "enable" => vec!["enable", "--now"],
                "disable" => vec!["disable", "--now"],
                _ => fail("service-root enable|disable <имя>"),
            };
            let out = std::process::Command::new("/bin/systemctl")
                .args(&args)
                .arg(linux_svc_instance(&owner))
                .output()
                .unwrap_or_else(|e| fail(format!("systemctl не запустился: {e}")));
            print!("{}", String::from_utf8_lossy(&out.stdout));
            eprint!("{}", String::from_utf8_lossy(&out.stderr));
            std::process::exit(out.status.code().unwrap_or(1));
        }
        _ => {
            eprintln!(
                "helene-svc daemon|home|unit|service — служба Hélène на Linux\n\
                 \n\
                 daemon --owner <имя>        супервизор канала, движка и реле (зовёт systemd)\n\
                 home                        завести дом владельца {LINUX_SVC_HOME_REL}\n\
                 unit                        шаблон юнита {LINUX_SVC_TEMPLATE}\n\
                 service install|remove|state  поставить/снять службу (пароль спросит polkit)"
            );
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod linux_tests {
    use super::*;

    #[test]
    fn home_is_made_with_links_and_a_template_config_and_keeps_owner_files() {
        let base = std::env::temp_dir().join(format!("helene-home-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&base);
        let program = base.join("opt-helene");
        std::fs::create_dir_all(program.join("runtime/bin")).unwrap();
        std::fs::create_dir_all(program.join("app")).unwrap();
        std::fs::write(program.join("helene.json"), "{\"mode\": \"local\"}").unwrap();
        std::fs::write(program.join("helene-relay"), "").unwrap();
        let root = base.join("home/.local/share/helene");
        let first = init_home(&root, &program).unwrap();
        assert!(first.created.contains(&"helene.json".to_string()));
        assert!(first.created.contains(&"runtime".to_string()));
        assert_eq!(std::fs::read_link(root.join("runtime")).unwrap(), program.join("runtime"));
        assert!(first.missing_in_program.contains(&"tree".to_string()));
        // Конфиг владельца пакет не трогает никогда.
        std::fs::write(root.join("helene.json"), "{\"mine\": true}").unwrap();
        // Чужой настоящий файл на месте ссылки — конфликт, а не снос.
        std::fs::remove_file(root.join("helene-relay")).unwrap();
        std::fs::write(root.join("helene-relay"), "own").unwrap();
        let second = init_home(&root, &program).unwrap();
        assert!(second.created.is_empty(), "{:?}", second.created);
        assert_eq!(second.conflicts, vec!["helene-relay".to_string()]);
        assert_eq!(std::fs::read_to_string(root.join("helene.json")).unwrap(), "{\"mine\": true}");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&root).unwrap().permissions().mode() & 0o777, 0o700);
        let _ = std::fs::remove_dir_all(&base);
    }

    #[test]
    fn passwd_knows_root_by_uid() {
        let (name, uid, home) = passwd_entry(None, Some(0)).expect("root есть в любом passwd");
        assert_eq!((name.as_str(), uid), ("root", 0));
        assert!(home.is_absolute());
    }
}
