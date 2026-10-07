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
use std::os::unix::io::AsRawFd;
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

pub fn broker_enabled(config: &Path) -> bool {
    std::fs::read_to_string(config).ok().and_then(|text| serde_json::from_str::<Value>(&text).ok())
        .map(|cfg| cfg["service"]["broker"].as_bool() != Some(false)).unwrap_or(false)
}

/// Shared execution for the opt-in socket and the separately password-confirmed CLI.
pub fn execute(parsed: &Value, uid: u32) -> Value {
    let reply = execute_checked(parsed);
    journal(uid, "exec", parsed["why"].as_str().unwrap_or(""),
            &format!("argv={} code={} ok={} error={}", parsed["argv"], reply["code"], reply["ok"], reply["error"]));
    reply
}

fn execute_checked(parsed: &Value) -> Value {
    use std::io::Read;
    use std::os::unix::fs::OpenOptionsExt;
    use std::os::unix::process::CommandExt;
    let started = std::time::Instant::now();
    let Some(raw) = parsed["argv"].as_array() else { return json!({"ok": false, "error": "argv — массив строк"}) };
    let Some(argv) = raw.iter().map(|v| v.as_str().map(str::to_string)).collect::<Option<Vec<_>>>() else {
        return json!({"ok": false, "error": "в argv есть не-строка"});
    };
    if let Err(e) = exec_allowed(&argv) { return json!({"ok": false, "error": e}); }
    let secs = parsed["timeout_sec"].as_u64().unwrap_or(60);
    if !(1..=600).contains(&secs) { return json!({"ok": false, "error": "timeout_sec: 1..600"}); }
    if let Some(expected) = parsed["digest"].as_str() {
        let actual = std::fs::read(&argv[0]).map(|bytes| super::daemon::command_digest(&bytes));
        if actual.ok().as_deref() != Some(expected) {
            return json!({"ok": false, "error": "файл команды изменился после просьбы — не исполняю"});
        }
    }
    let nonce = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default().as_nanos();
    let base = std::env::temp_dir().join(format!("helene-root-{}-{nonce}", std::process::id()));
    let out_path = base.with_extension("out");
    let err_path = base.with_extension("err");
    let result = (|| -> Result<Value, String> {
        let open = |path: &Path| std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).map_err(|e| e.to_string());
        let out_file = open(&out_path)?;
        let err_file = open(&err_path)?;
        let mut child = std::process::Command::new(&argv[0]).args(&argv[1..])
            .stdin(std::process::Stdio::null()).stdout(out_file).stderr(err_file).process_group(0)
            .spawn().map_err(|e| format!("не запустилось: {e}"))?;
        let deadline = started + std::time::Duration::from_secs(secs);
        let mut timed_out = false;
        let code = loop {
            if let Some(status) = child.try_wait().map_err(|e| e.to_string())? { break status.code(); }
            if std::time::Instant::now() >= deadline {
                unsafe { libc::killpg(child.id() as i32, libc::SIGKILL); }
                let _ = child.wait();
                timed_out = true;
                break None;
            }
            std::thread::sleep(std::time::Duration::from_millis(40));
        };
        let read = |path: &Path| -> String {
            let mut bytes = Vec::new();
            if let Ok(file) = std::fs::File::open(path) { let _ = file.take(65536).read_to_end(&mut bytes); }
            String::from_utf8_lossy(&bytes).into_owned()
        };
        Ok(json!({"ok": true, "code": code, "out": read(&out_path), "err": read(&err_path),
                  "ms": started.elapsed().as_millis() as u64,
                  "note": if timed_out { "срок команды вышел; группа процессов остановлена" } else { "" }}))
    })();
    let _ = std::fs::remove_file(&out_path);
    let _ = std::fs::remove_file(&err_path);
    let reply = result.unwrap_or_else(|e| json!({"ok": false, "error": e}));
    reply
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
    // Системные uid — мимо всегда, включая ping: присутствие брокера само
    // по себе не чужое дело (тот же закон, что у трубы Windows).
    if uid < MIN_HUMAN_UID {
        return json!({"ok": false, "error": format!("uid {uid} — системный: брокер обслуживает людей, не службы")});
    }
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
        "exec" | "service" => {
            let (name, home) = match owner_of_uid(uid) {
                Ok(pair) => pair,
                Err(e) => {
                    journal(uid, "exec", &why, &e);
                    return json!({"ok": false, "error": e});
                }
            };
            let config = home.join(".local/share/helene/helene.json");
            if !session0_opened(&config) || !broker_enabled(&config) {
                let said = format!(
                    "нулевая сессия выключена у {name} — права системы не выдаются \
                     (третья ступень лестницы в настройках)"
                );
                journal(uid, "exec", &why, &said);
                return json!({"ok": false, "error": said});
            }
            if op == "service" {
                let action = parsed["action"].as_str().unwrap_or("");
                if !matches!(action, "start" | "stop" | "restart") {
                    return json!({"ok": false, "error": "service: start | stop | restart"});
                }
                let unit = format!("helene@{name}.service");
                let request = json!({"argv": ["/bin/systemctl", action, unit], "timeout_sec": 60,
                    "why": format!("служба владельца: {action}")});
                let mut reply = execute(&request, uid);
                if reply["code"].as_i64() != Some(0) { reply["ok"] = json!(false); reply["error"] = reply["err"].clone(); }
                return reply;
            }
            execute(&parsed, uid)
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
        fail(&format!("не завёлся {dir:?}: {e}"));
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
        std::thread::spawn(move || {
            use std::io::Read;
            let _ = stream.set_read_timeout(Some(std::time::Duration::from_secs(10)));
            let _ = stream.set_write_timeout(Some(std::time::Duration::from_secs(10)));
            let cred = peer_cred(&stream);
            let Ok(cloned) = stream.try_clone() else { return };
            let mut line = String::new();
            if BufReader::new(cloned.take(65536)).read_line(&mut line).is_ok() && line.ends_with('\n') {
                answer(&mut stream, handle(&line, cred));
            }
        });
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
