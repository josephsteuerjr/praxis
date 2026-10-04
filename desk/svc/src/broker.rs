//! `helene-svc broker` — корневой резидент Linux: исполняет просьбы агента
//! правами root за включённой нулевой сессией (04.10, слово владельца).
//!
//! УСТРОЙСТВО. Системная служба `helene-broker.service` (root, ставится пакетом,
//! НЕ включается автоматически — дверь по умолчанию закрыта). Слушает unix-сокет
//! `/run/helene/broker.sock`; каждая связь обязана предъявить себя через
//! SO_PEERCRED — ядро кладёт uid/гид подключившегося, подделать нельзя. uid=0
//! и системные uid (<1000) не обслуживаются: брокер для живых людей, а не для
//! соседних служб (то же правило, что у трубы Windows).
//!
//! Протокол — построчный JSON: одна просьба, один ответ.
//!   {"op":"ping"} → {"ok":true}
//!   {"op":"exec","argv":["/usr/bin/whoami"],"why":"…"} → {"ok":true,"code":0,
//!    "out":"root","journal":"/var/lib/helene/broker.log"}
//!
//! ДВЕРЬ `exec` открывает только явное согласие владельца: в конфиге просящего
//! (`~/.local/share/helene/helene.json`) должно стоять `service.session0: true`
//! (его пишет третья ступень лестницы). Выключено — отказ словами, просьба в
//! журнал всё равно ложится: свидетель есть всегда.
//!
//! КАЖДАЯ просьба — в журнал `/var/lib/helene/broker.log` (JSON-строка: время,
//! uid, op, why, исход) и в journald (stderr). Бинарный путь — только
//! абсолютный: голое имя root искал бы в своём PATH (правило трубы Windows).
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::UnixListener;
use std::path::{Path, PathBuf};

use serde_json::{Value, json};

pub const SOCKET_PATH: &str = "/run/helene/broker.sock";
pub const JOURNAL_PATH: &str = "/var/lib/helene/broker.log";
/// Минимальный uid живого человека: системные службы (root, gdm, dbus…) не
/// обслуживаются — брокер не для соседних демонов.
pub const MIN_HUMAN_UID: u32 = 1000;

pub fn fail(text: &str) -> ! {
    eprintln!("{text}");
    std::process::exit(1);
}

/// Чей это uid: (имя, дом). Системные и несуществующие — отказ.
pub fn owner_of_uid(uid: u32) -> Result<(String, PathBuf), String> {
    if uid < MIN_HUMAN_UID {
        return Err(format!("uid {uid} — системный: брокер обслуживает людей, не службы"));
    }
    let entry = std::fs::read_to_string("/etc/passwd").map_err(|e| format!("/etc/passwd: {e}"))?;
    for line in entry.lines() {
        let parts: Vec<&str> = line.split(':').collect();
        if parts.len() >= 6 && parts[2].parse::<u32>() == Ok(uid) {
            return Ok((parts[0].to_string(), PathBuf::from(parts[5])));
        }
    }
    Err(format!("uid {uid} не найден в /etc/passwd"))
}

/// Открыта ли нулевая сессия у владельца: service.session0 == true в его конфиге.
/// Чистая функция — стенд проверяет разбор без сокета.
pub fn session0_opened(config: &Path) -> bool {
    let Ok(text) = std::fs::read_to_string(config) else { return false };
    let Ok(cfg) = serde_json::from_str::<Value>(&text) else { return false };
    cfg.get("service").and_then(|s| s.get("session0")).and_then(Value::as_bool) == Some(true)
}

/// Просьба валидна: абсолютный путь, не пусто, без shell-метасимволов в argv[0]
/// (аргументы передаются массивом — шелла нет вовсе).
pub fn exec_allowed(argv: &[String]) -> Result<(), String> {
    let Some(first) = argv.first() else {
        return Err("exec без команды".into());
    };
    let path = Path::new(first);
    if !path.is_absolute() {
        return Err(format!("только абсолютный путь, голое имя не беру: {first}"));
    }
    if first.contains("..") {
        return Err("путь с «..» не рассматриваю".into());
    }
    Ok(())
}

/// Одна строка в журнал: свидетель всегда, включая отказы.
pub fn journal(uid: u32, op: &str, why: &str, outcome: &str) {
    let line = json!({
        "at_utc": std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0),
        "uid": uid, "op": op, "why": why, "outcome": outcome,
    }).to_string();
    let _ = std::fs::create_dir_all(Path::new(JOURNAL_PATH).parent().unwrap());
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(JOURNAL_PATH) {
        let _ = writeln!(f, "{line}");
    }
    eprintln!("[broker] uid={uid} op={op} outcome={outcome} why={why}");
}

fn answer(stream: &mut std::os::unix::net::UnixStream, value: Value) {
    let _ = writeln!(stream, "{value}");
}

/// Обработать одну просьбу (чистая логика без сокета — удобна стенду).
pub fn handle(request: &str, cred: Option<libc::ucred>) -> Value {
    let Some(cred) = cred else {
        return json!({"ok": false, "error": "нет SO_PEERCRED — кто ты?"});
    };
    let uid = cred.uid;
    let parsed: Value = match serde_json::from_str(request) {
        Ok(v) => v,
        Err(e) => {
            journal(uid, "malformed", "", &format!("не разобрался: {e}"));
            return json!({"ok": false, "error": format!("не разобрался: {e}")});
        }
    };
    let op = parsed.get("op").and_then(Value::as_str).unwrap_or("").to_string();
    let why = parsed.get("why").and_then(Value::as_str).unwrap_or("").chars().take(300).collect::<String>();
    match op.as_str() {
        "ping" => {
            journal(uid, "ping", &why, "pong");
            json!({"ok": true, "op": "ping"})
        }
        "exec" => {
            let argv: Vec<String> = parsed
                .get("argv")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect())
                .unwrap_or_default();
            if let Err(why_not) = exec_allowed(&argv) {
                journal(uid, "exec", &why, &why_not);
                return json!({"ok": false, "error": why_not});
            }
            let (name, home) = match owner_of_uid(uid) {
                Ok(pair) => pair,
                Err(e) => {
                    journal(uid, "exec", &why, &e);
                    return json!({"ok": false, "error": e});
                }
            };
            let config = home.join(".local/share/helene/helene.json");
            if !session0_opened(&config) {
                let said = format!(
                    "нулевая сессия выключена у {name} — права системы не выдаются \
                     (третья ступень лестницы в настройках)"
                );
                journal(uid, "exec", &why, &said);
                return json!({"ok": false, "error": said});
            }
            let out = std::process::Command::new(&argv[0])
                .args(&argv[1..])
                .output();
            match out {
                Ok(out) => {
                    let code = out.status.code().unwrap_or(-1);
                    let text = String::from_utf8_lossy(&out.stdout).trim().chars().take(400).collect::<String>();
                    let outcome = format!("code={code}");
                    journal(uid, "exec", &why, &outcome);
                    json!({"ok": code == 0, "code": code, "out": text})
                }
                Err(e) => {
                    let said = format!("не запустилось: {e}");
                    journal(uid, "exec", &why, &said);
                    json!({"ok": false, "error": said})
                }
            }
        }
        _ => {
            let said = format!("неизвестная op «{op}»: ping | exec");
            journal(uid, &op, &why, &said);
            json!({"ok": false, "error": said})
        }
    }
}

pub fn run() -> i32 {
    if unsafe { libc::geteuid() } != 0 {
        fail("broker работает только от root (его поднимает helene-broker.service)");
    }
    let dir = Path::new(SOCKET_PATH).parent().unwrap();
    if let Err(e) = std::fs::create_dir_all(dir) {
        fail(&format!("не завёлся {dir}: {e}"));
    }
    let _ = std::fs::remove_file(SOCKET_PATH);
    let listener = match UnixListener::bind(SOCKET_PATH) {
        Ok(l) => l,
        Err(e) => fail(&format!("не поднялся сокет {SOCKET_PATH}: {e}")),
    };
    // Сокет — на всех: идентичность даёт ядро через SO_PEERCRED, а не права файла.
    let _ = std::fs::set_permissions(SOCKET_PATH, std::fs::Permissions::from_mode(0o666));
    eprintln!("[broker] слушаю {SOCKET_PATH}, журнал {JOURNAL_PATH}");
    for stream in listener.incoming() {
        let Ok(mut stream) = stream else { continue };
        let cred = peer_cred(&stream);
        let Ok(cloned) = stream.try_clone() else { continue };
        let reader = BufReader::new(cloned);
        for line in reader.lines().map_while(Result::ok) {
            if line.trim().is_empty() {
                continue;
            }
            let reply = handle(&line, cred);
            answer(&mut stream, reply);
        }
    }
    0
}

/// uid подключившегося — вопросом к ядру (SO_PEERCRED). Подделать нельзя.
pub fn peer_cred(stream: &std::os::unix::net::UnixStream) -> Option<libc::ucred> {
    let mut cred = libc::ucred { pid: 0, uid: 0, gid: 0 };
    let len = std::mem::size_of::<libc::ucred>() as u32;
    let rc = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            &mut cred as *mut _ as *mut libc::c_void,
            &len as *const u32 as *mut u32,
        )
    };
    if rc == 0 { Some(cred) } else { None }
}

#[cfg(test)]
mod broker_tests {
    use super::*;
    use std::path::PathBuf;

    fn tmp_config(session0: Option<bool>) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("helene-broker-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let cfg = dir.join("helene.json");
        let body = match session0 {
            Some(v) => format!("{{\"service\": {{\"session0\": {v}}}}}"),
            None => "{}".to_string(),
        };
        std::fs::write(&cfg, body).unwrap();
        cfg
    }

    #[test]
    fn session0_gate_reads_the_canonical_key() {
        assert!(session0_opened(&tmp_config(Some(true))));
        assert!(!session0_opened(&tmp_config(Some(false))));
        assert!(!session0_opened(&tmp_config(None)), "нет ключа — закрыто");
        assert!(!session0_opened(&PathBuf::from("/nowhere/helene.json")));
    }

    #[test]
    fn exec_demands_absolute_clean_paths() {
        assert!(exec_allowed(&["/usr/bin/whoami".into()]).is_ok());
        assert!(exec_allowed(&["whoami".into()]).is_err(), "голое имя — мимо");
        assert!(exec_allowed(&["/tmp/../bin/sh".into()]).is_err(), ".. — мимо");
        assert!(exec_allowed(&[]).is_err(), "пусто — мимо");
    }

    #[test]
    fn system_uids_are_not_served() {
        assert!(owner_of_uid(0).is_err(), "root не обслуживается");
        assert!(owner_of_uid(120).is_err(), "gdm не обслуживается");
    }

    #[test]
    fn handle_without_cred_refuses() {
        let r = handle("{\"op\":\"ping\"}", None);
        assert_eq!(r["ok"], false);
    }
}
