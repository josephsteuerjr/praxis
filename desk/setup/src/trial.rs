//! Испытание новой версии на ПК после обновления (1.2.5, слово Егора 28.09).
//!
//! «Нужно ужесточить приёмку обновлений агентом — и на сервере, и на десктопе: Йоно может
//! вернуться…» До 1.2.5 установщик ПК ставил новую версию и тут же стирал прежнюю: сломайся
//! новая — откатывать было не на что, а агента никто не спрашивал, жив ли он в ней. На
//! сервере так уже было (`server/updater`); теперь и здесь:
//!
//!   * `install_tx` (обновление поверх) переносит правки агента в его коде
//!     (`server/updater/codecarry.py desk`), прежнюю программу кладёт в
//!     `backups/program-<версия>` (`Tx::commit_keep`), пишет состояние испытания
//!     (`backups/update-trial.json`, `begin`) и зовёт сторожа — `helene-setup --trial --dir`;
//!   * сторож (`watch`) ждёт, что новая версия ожила: квитанция читателя
//!     (`data/memory/.control/desk_inbox/.reader.json`) свежее установки. Мозга нет —
//!     испытывать некому: обновление принимается со словами «проверь сам». Иначе — расписка
//!     «испытание» в `data/memory/.control/update-plan.receipt.json` (`desktop: true`), тот же
//!     протокол, что у исполнителя на сервере: движок кладёт агенту записку, агент проверяет
//!     себя ДЕЛОМ и говорит `update_request accept|reject` (принять — только с доказательством,
//!     `localharness/trial_proof.py`), владелец может сказать своё кнопкой в окне;
//!   * «принято» — прежняя программа удаляется; «сломано» или молчание до срока — откат:
//!     правки агента за испытание — ему в workspace, программа останавливается, прежняя
//!     встаёт на место той же транзакцией (`Tx::begin_from`), с данными, нажитыми за испытание.
//!
//! Часы испытания идут, только пока программа запущена: на сервере контейнер живёт всегда,
//! а на ПК владелец может закрыть окно — молчание агента тогда не его молчание. Программа не
//! запущена долго — сторож выходит, а движок, поднявшись, зовёт его снова (`runner`).
//!
//! Сторож живёт в копии установщика во временной папке: папку установки, из которой запущен
//! exe, Windows не даст переименовать, а откат переименовывает именно её.
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::install;

/// Состояние испытания — в папке установки, рядом со снимками памяти.
pub const STATE_REL: [&str; 2] = ["backups", "update-trial.json"];
pub const KEPT_PREFIX: &str = "program-";
pub const TRIAL_ARG: &str = "--trial";

const SCHEMA: &str = "helene.update.v1";
/// Минуты испытания (как у исполнителя на сервере по умолчанию).
pub const TRIAL_MIN: u32 = 30;
/// Канал новой версии ответил, а движок за это время так и не ожил — не поднялась.
const START_WAIT: f64 = 600.0;
/// Программа не запущена вовсе столько — сторож выходит (позовёт движок при старте).
const IDLE_EXIT: f64 = 900.0;
/// Агент занят ходом к сроку — продлить на столько (всего не больше самого срока).
const EXTEND: f64 = 600.0;
/// Квитанция читателя моложе — движок жив (`deskd/readers.py::READER_FRESH_S`).
const READER_FRESH: f64 = 45.0;
const TICK: Duration = Duration::from_secs(3);
/// Агент договаривает ход (сказал «сломано» и объясняет владельцу) — ждать его конца
/// до стольких секунд, прежде чем гасить движок (как исполнитель на сервере).
const IDLE_WAIT: f64 = 180.0;
/// Откат не удался (папку держит Проводник, антивирус) — столько попыток с паузой; между
/// ними новая версия снова поднята, агент не лежит.
const ROLLBACK_TRIES: u32 = 3;
const ROLLBACK_PAUSE: Duration = Duration::from_secs(90);
/// Замок сторожа старше — его хозяин умер.
const LOCK_STALE: f64 = 30.0;

#[derive(Serialize, Deserialize, Clone, Debug, Default)]
#[serde(default)]
pub struct Trial {
    pub schema: String,
    /// Номер обновления — `id` расписки (к нему привязано слово агента и окна).
    pub id: String,
    /// Ключ испытания: слово относится ровно к этому испытанию.
    pub key: String,
    pub from_version: String,
    pub to_version: String,
    /// Где лежит прежняя программа (пусто — сохранить не вышло, откатывать не на что).
    pub kept: String,
    /// Рантайм не менялся и переехал в новую — при откате вернуть его прежней.
    pub runtime_moved: bool,
    /// Как обошлись с интерфейсом окна: keep | to_prev | fresh.
    pub static_plan: String,
    /// Имена верхнего уровня новой поставки: при откате они остаются в отвергнутой версии.
    pub new_top: Vec<String>,
    pub code_sha256: std::collections::BTreeMap<String, String>,
    pub service: bool,
    pub scope: String,
    /// starting | trial | accepting | rollback | done | rolled_back | failed
    pub phase: String,
    pub installed_epoch: f64,
    /// Когда канал новой версии ответил впервые.
    pub channel_since: f64,
    pub since_epoch: f64,
    pub minutes: u32,
    /// Сколько секунд испытания осталось (идут, пока программа запущена).
    pub left: f64,
    pub last_tick: f64,
    pub extended: f64,
    pub agent_code: Value,
    pub extensions: String,
    pub checks: Vec<Value>,
    pub verdict: Value,
    pub rollback_why: String,
    pub rollback_plain: String,
    pub rollback_tries: u32,
    pub notes: Vec<String>,
}

impl Trial {
    fn is_open(&self) -> bool {
        matches!(self.phase.as_str(), "starting" | "trial" | "accepting" | "rollback")
    }
}

pub fn epoch() -> f64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs_f64())
        .unwrap_or(0.0)
}

/// Секунды эпохи -> `2026-09-29T12:34:56Z` (UTC; как у исполнителя на сервере).
pub fn utc(secs: f64) -> String {
    let s = secs.max(0.0) as i64;
    let (days, rem) = (s.div_euclid(86_400), s.rem_euclid(86_400));
    // Гражданская дата из дней от 1970-01-01 (алгоритм Хиннанта).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1_460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}

pub fn state_path(dir: &Path) -> PathBuf {
    dir.join(STATE_REL[0]).join(STATE_REL[1])
}

pub fn kept_path(dir: &Path, version: &str) -> PathBuf {
    dir.join("backups").join(format!("{KEPT_PREFIX}{version}"))
}

pub fn load(dir: &Path) -> Option<Trial> {
    let raw = std::fs::read(state_path(dir)).ok()?;
    serde_json::from_slice(&raw).ok()
}

fn save(dir: &Path, t: &Trial) {
    if let Ok(text) = serde_json::to_string_pretty(t) {
        let _ = install::write_atomic_pub(&state_path(dir), &text);
    }
}

/// Папка данных установки (ключ `tree` в helene.json, по умолчанию `data`).
pub fn data_dir(dir: &Path) -> PathBuf {
    install::read_json_pub(&dir.join("helene.json"))
        .and_then(|c| c.get("tree").and_then(|v| v.as_str()).map(str::to_string))
        .map(|t| {
            let p = PathBuf::from(&t);
            if p.is_absolute() { p } else { dir.join(p) }
        })
        .unwrap_or_else(|| dir.join("data"))
}

fn control(dir: &Path, name: &str) -> PathBuf {
    data_dir(dir).join("memory").join(".control").join(name)
}

// --------------------------------------------------------------------------- начало

/// Что знает установщик в миг подмены — для испытания.
pub struct Begin<'a> {
    pub from_version: &'a str,
    pub to_version: &'a str,
    pub kept: Option<&'a Path>,
    pub runtime_moved: bool,
    pub static_plan: &'a str,
    pub new_top: Vec<String>,
    pub code_sha256: std::collections::BTreeMap<String, String>,
    pub service: bool,
    pub scope: &'a str,
    pub agent_code: Value,
    pub extensions: String,
}

/// Завести испытание (после подмены, до запуска новой версии). -> состояние.
pub fn begin(dir: &Path, b: Begin) -> Trial {
    let now = epoch();
    let t = Trial {
        schema: SCHEMA.into(),
        id: install::random_hex_pub(8).unwrap_or_else(|| format!("{:x}", now as u64)),
        from_version: b.from_version.into(),
        to_version: b.to_version.into(),
        kept: b.kept.map(|p| p.display().to_string()).unwrap_or_default(),
        runtime_moved: b.runtime_moved,
        static_plan: b.static_plan.into(),
        new_top: b.new_top,
        code_sha256: b.code_sha256,
        service: b.service,
        scope: b.scope.into(),
        phase: "starting".into(),
        installed_epoch: now,
        minutes: TRIAL_MIN,
        agent_code: b.agent_code,
        extensions: b.extensions,
        ..Default::default()
    };
    save(dir, &t);
    receipt(dir, &t, "running", "новая версия поднимается — жду, что её движок оживёт", "", "");
    t
}

// --------------------------------------------------------------------------- расписка

/// Расписка в протоколе исполнителя (`deskd/control.py`): её читают движок (записка
/// агенту), рука `update_request` и окно.
fn receipt(dir: &Path, t: &Trial, state: &str, step: &str, note: &str, summary: &str) {
    let mut trial = json!({
        "key": t.key,
        "since_epoch": t.since_epoch,
        "since_utc": if t.since_epoch > 0.0 { utc(t.since_epoch) } else { String::new() },
        "until_epoch": if t.since_epoch > 0.0 { epoch() + t.left.max(0.0) } else { 0.0 },
        "until_utc": if t.since_epoch > 0.0 { utc(epoch() + t.left.max(0.0)) } else { String::new() },
        "minutes": t.minutes,
        "extended": t.extended,
    });
    if !t.verdict.is_null() {
        trial["verdict"] = t.verdict.clone();
    }
    let now = epoch();
    let finished = matches!(state, "done" | "rolled_back" | "failed");
    let mut row = json!({
        "schema": SCHEMA,
        "id": t.id,
        "desktop": true,
        "state": state,
        "phase": if state == "trial" { "trial" } else { t.phase.as_str() },
        "from_version": t.from_version,
        "to_version": t.to_version,
        "step": step,
        "note": if note.is_empty() { step } else { note },
        "summary": summary,
        "checks": t.checks,
        "agent_code": t.agent_code,
        "extensions": {"summary": t.extensions},
        "trial": trial,
        "updated_utc": utc(now),
    });
    if finished {
        row["finished_epoch"] = json!(now);
        row["finished_utc"] = json!(utc(now));
    }
    if state == "rolled_back" || state == "failed" {
        row["rollback"] = json!({"ok": state == "rolled_back", "notes": t.notes});
    }
    if let Some(by) = t.verdict.get("by").and_then(|v| v.as_str()).filter(|b| *b == "window") {
        row["confirmed"] = json!({"by": by});
    }
    let path = control(dir, "update-plan.receipt.json");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let _ = install::write_atomic_pub(&path, &(row.to_string() + "\n"));
    if finished {
        let hist = control(dir, "update-history.jsonl");
        let line = json!({"id": t.id, "desktop": true, "state": state, "from_version": t.from_version,
                          "to_version": t.to_version, "summary": summary, "note": note,
                          "finished_utc": utc(now)});
        use std::io::Write;
        if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&hist) {
            let _ = writeln!(f, "{line}");
        }
    }
}

/// Биение сторожа (`updater.json`): рука агента и движок видят, что он на месте.
fn beat(dir: &Path, t: &Trial, busy: &str) {
    let row = json!({
        "schema": "helene.updater.v1",
        "desktop": true,
        "ok": true,
        "pid": std::process::id(),
        "beat_epoch": epoch(),
        "current_version": t.to_version,
        "busy": busy,
    });
    let _ = install::write_atomic_pub(&control(dir, "updater.json"), &row.to_string());
}

/// Замок сторожа: второй (движок позвал, пока первый ещё поднимался) выходит сразу —
/// иначе двое откатили бы одну установку. Файл не держится открытым (папку откат
/// переименовывает) — живость по свежести, сторож обновляет его каждый тик.
struct Lock(PathBuf);

impl Lock {
    fn path(dir: &Path) -> PathBuf {
        dir.join("backups").join("update-trial.lock")
    }

    fn take(dir: &Path) -> Option<Lock> {
        let path = Lock::path(dir);
        for _ in 0..2 {
            match std::fs::OpenOptions::new().write(true).create_new(true).open(&path) {
                Ok(_) => {
                    let lock = Lock(path);
                    lock.touch(dir);
                    return Some(lock);
                }
                Err(_) => {
                    let age = std::fs::metadata(&path)
                        .and_then(|m| m.modified())
                        .ok()
                        .and_then(|m| m.elapsed().ok())
                        .map(|d| d.as_secs_f64())
                        .unwrap_or(f64::MAX);
                    if age < LOCK_STALE {
                        return None;
                    }
                    let _ = std::fs::remove_file(&path);
                }
            }
        }
        None
    }

    fn touch(&self, dir: &Path) {
        let _ = std::fs::write(Lock::path(dir), std::process::id().to_string());
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// Сторож ушёл насовсем — биение прочь: иначе окно и рука видели бы «исполнителя, который
/// молчит» (у сервера этим же файлом бьётся исполнитель обновлений).
fn beat_gone(dir: &Path) {
    let _ = std::fs::remove_file(control(dir, "updater.json"));
}

// --------------------------------------------------------------------------- проба

#[derive(Default, Debug)]
struct Probe {
    /// Канал ответил на /api/state.
    channel: bool,
    /// Квитанция читателя свежая.
    runner: bool,
    runner_at: f64,
    busy: bool,
    /// Мозг настроен (None — канал не ответил, не знаю).
    brain: Option<bool>,
}

fn probe(dir: &Path) -> Probe {
    let data = data_dir(dir);
    let mut p = Probe::default();
    if let Some(r) = install::read_json_pub(&data.join("memory").join(".control").join("desk_inbox").join(".reader.json")) {
        p.runner_at = r.get("at").and_then(|v| v.as_f64()).unwrap_or(0.0);
        p.runner = p.runner_at > 0.0 && (epoch() - p.runner_at).abs() < READER_FRESH;
        p.busy = p.runner && r.get("busy").and_then(|v| v.as_bool()).unwrap_or(false);
    }
    let port = install::installed_port_pub(dir);
    let key = std::fs::read_to_string(data.join("memory").join(".state").join("desk-token")).unwrap_or_default();
    let url = format!("http://127.0.0.1:{port}/api/state?key={}", key.trim());
    let agent = ureq::AgentBuilder::new().timeout(Duration::from_secs(5)).build();
    if let Ok(resp) = agent.get(&url).call() {
        p.channel = true;
        if let Ok(body) = resp.into_string().map(|s| serde_json::from_str::<Value>(&s).unwrap_or(Value::Null)) {
            p.brain = body.get("brain").and_then(|b| b.get("configured")).and_then(|v| v.as_bool());
        }
    }
    p
}

/// Слово на испытании (агент рукой или владелец кнопкой) — к этому ли испытанию.
fn verdict(dir: &Path, t: &Trial) -> Option<Value> {
    let v = install::read_json_pub(&control(dir, "update-plan.verdict.json"))?;
    let ours = v.get("id").and_then(|x| x.as_str()) == Some(t.id.as_str())
        && !t.key.is_empty()
        && v.get("key").and_then(|x| x.as_str()) == Some(t.key.as_str());
    let word = v.get("verdict").and_then(|x| x.as_str()).unwrap_or("");
    (ours && (word == "accept" || word == "reject")).then_some(v)
}

// --------------------------------------------------------------------------- сторож

/// Позвать сторожа — отдельным процессом, который переживёт установщик.
pub fn spawn_watcher(dir: &Path) -> Result<(), String> {
    let installed = dir.join(install::setup_rel());
    let exe = if installed.is_file() {
        installed
    } else {
        std::env::current_exe().map_err(|e| e.to_string())?
    };
    let mut cmd = std::process::Command::new(&exe);
    cmd.arg(TRIAL_ARG).arg("--dir").arg(dir).current_dir(std::env::temp_dir());
    #[cfg(windows)]
    {
        crate::win::spawn_outside_hidden(&mut cmd).map_err(|e| format!("сторож испытания не запустился: {e}"))?;
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .process_group(0);
        cmd.spawn().map_err(|e| format!("сторож испытания не запустился: {e}"))?;
    }
    Ok(())
}

/// На Windows сторож не может жить в папке установки — переехать во временную и
/// перезапуститься оттуда. -> true, если перезапустился (этому процессу — выйти).
#[cfg(windows)]
fn relocate(dir: &Path, args: &[String]) -> bool {
    let Ok(me) = std::env::current_exe() else { return false };
    // Канонически: `\\?\`, короткие имена 8.3 и регистр иначе прятали бы, что exe — внутри.
    let canon = |p: &Path| std::fs::canonicalize(p).unwrap_or_else(|_| p.to_path_buf());
    let norm = |p: &Path| canon(p).display().to_string().to_lowercase().replace('/', "\\");
    if !norm(&me).starts_with(&(norm(dir).trim_end_matches('\\').to_string() + "\\")) {
        return false;
    }
    let home = std::env::temp_dir().join("helene-trial");
    let _ = std::fs::create_dir_all(&home);
    // Копии прошлых сторожей — прочь (живую Windows удалить не даст: exe занят).
    if let Ok(rd) = std::fs::read_dir(&home) {
        for e in rd.flatten() {
            if e.file_name().to_string_lossy().starts_with("helene-setup-") {
                let _ = std::fs::remove_file(e.path());
            }
        }
    }
    let copy = home.join(format!("helene-setup-{}.exe", std::process::id()));
    if std::fs::copy(&me, &copy).is_err() {
        return false;
    }
    let mut cmd = std::process::Command::new(&copy);
    cmd.args(args.iter().skip(1)).current_dir(&home);
    crate::win::spawn_outside_hidden(&mut cmd).is_ok()
}

#[cfg(not(windows))]
fn relocate(_dir: &Path, _args: &[String]) -> bool {
    false
}

/// Журнал сторожа — рядом с состоянием; человеку, когда что-то пошло не так.
fn log(dir: &Path, line: &str) {
    use std::io::Write;
    let path = dir.join("backups").join("update-trial.log");
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{} {line}", utc(epoch()));
    }
}

/// Вход `helene-setup --trial --dir <папка>`. -> код выхода.
pub fn watch(dir: &Path, args: &[String]) -> i32 {
    if relocate(dir, args) {
        return 0;
    }
    let Some(first) = load(dir) else { return 0 };
    if !first.is_open() {
        return 0;
    }
    let Some(lock) = Lock::take(dir) else { return 0 };
    log(dir, &format!("сторож {} поднят: {} → {}, фаза {}", std::process::id(), first.from_version, first.to_version, first.phase));
    let started = epoch();
    let mut last_up = started;
    loop {
        let Some(mut t) = load(dir) else { return 0 };
        // Закрыто — или заменено следующим обновлением: у нового испытания свой сторож.
        if !t.is_open() || t.id != first.id {
            return 0;
        }
        lock.touch(dir);
        if install::owner_stopped_pub() {
            // An intentional stop is neither failed health nor elapsed trial time.
            let now = epoch();
            if t.channel_since > 0.0 { t.channel_since = now; }
            t.last_tick = now;
            last_up = now;
            save(dir, &t);
            std::thread::sleep(TICK);
            continue;
        }
        let p = probe(dir);
        let now = epoch();
        if p.channel || p.runner {
            last_up = now;
        }
        match t.phase.as_str() {
            "starting" => {
                beat(dir, &t, "жду, что новая версия оживёт");
                starting(dir, &mut t, &p, now);
            }
            "trial" => {
                beat(dir, &t, "испытание: агент проверяет себя в новой версии");
                trial_tick(dir, &mut t, &p, now);
            }
            "accepting" => {
                finish_accept(dir, &mut t);
                return 0;
            }
            "rollback" => {
                beat(dir, &t, "возвращаю прежнюю версию");
                let code = do_rollback(dir, &mut t);
                return code;
            }
            _ => return 0,
        }
        if matches!(t.phase.as_str(), "accepting" | "rollback") {
            continue;
        }
        if now - last_up > IDLE_EXIT {
            log(dir, "программа не запущена — сторож выходит; движок позовёт снова при старте");
            save(dir, &t);
            return 0;
        }
        std::thread::sleep(TICK);
    }
}

fn check(name: &str, title: &str, ok: bool, note: &str) -> Value {
    json!({"name": name, "title": title, "ok": ok, "note": note})
}

fn starting(dir: &Path, t: &mut Trial, p: &Probe, now: f64) {
    if p.channel && t.channel_since <= 0.0 {
        t.channel_since = now;
        save(dir, t);
    }
    let fresh = p.runner && p.runner_at >= t.installed_epoch - 1.0;
    if fresh {
        t.checks = vec![
            check("runner", "движок новой версии поднялся", true, "квитанция читателя свежая"),
            check("channel", "канал окна отвечает", p.channel, if p.channel { "отвечает" } else { "не ответил" }),
        ];
        if !t.extensions.is_empty() {
            t.checks.push(check("extensions", "расширения владельца", true, &t.extensions));
        }
        if p.brain == Some(false) {
            t.verdict = json!({"verdict": "accept", "by": "installer", "words": "мозг не настроен — испытывать некому"});
            t.phase = "accepting".into();
            save(dir, t);
            return;
        }
        let rnd = install::random_hex_pub(12).unwrap_or_else(|| format!("{:x}", (now * 1000.0) as u64));
        t.key = rnd;
        t.phase = "trial".into();
        t.since_epoch = now;
        t.left = f64::from(t.minutes) * 60.0;
        t.last_tick = now;
        save(dir, t);
        receipt(dir, t, "trial", "агент проверяет себя в новой версии",
                &format!("{} поднята; теперь агент проверяет себя делом (до {}). «Сломано» или молчание до срока — откат",
                         t.to_version, utc(now + t.left)), "");
        log(dir, &format!("испытание открыто, {} мин", t.minutes));
        return;
    }
    if t.channel_since > 0.0 && now - t.channel_since > START_WAIT {
        t.checks = vec![check("runner", "движок новой версии поднялся", false,
                              &format!("не ожил за {} мин, хотя окно запущено", (START_WAIT / 60.0) as u32))];
        t.rollback_why = format!("движок новой версии не ожил за {} мин", (START_WAIT / 60.0) as u32);
        t.rollback_plain = "новая версия не запустилась".into();
        t.phase = "rollback".into();
        save(dir, t);
    }
}

fn trial_tick(dir: &Path, t: &mut Trial, p: &Probe, now: f64) {
    if let Some(v) = verdict(dir, t) {
        let word = v.get("verdict").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let by = v.get("by").and_then(|x| x.as_str()).unwrap_or("").to_string();
        let words = v.get("words").and_then(|x| x.as_str()).unwrap_or("").to_string();
        t.verdict = json!({"verdict": word, "by": by, "words": words,
                           "at_utc": v.get("at_utc").cloned().unwrap_or(Value::Null),
                           "proof": v.get("proof").cloned().unwrap_or(Value::Null)});
        let _ = std::fs::remove_file(control(dir, "update-plan.verdict.json"));
        let agent = by == "agent";
        if word == "accept" {
            t.phase = "accepting".into();
        } else {
            t.rollback_why = format!("{} на испытании сказал «сломано»{}", if agent { "агент" } else { "владелец" },
                                     if words.is_empty() { String::new() } else { format!(": «{}»", clip(&words, 300)) });
            t.rollback_plain = if agent {
                format!("агент сказал, что в новой версии не всё работает{}",
                        if words.is_empty() { String::new() } else { format!(": «{}»", clip(&words, 200)) })
            } else {
                "ты попросил вернуть прежнюю версию".into()
            };
            t.phase = "rollback".into();
        }
        save(dir, t);
        return;
    }
    // Часы идут, только пока программа запущена.
    let up = p.runner || p.channel;
    if up && t.last_tick > 0.0 {
        t.left -= (now - t.last_tick).clamp(0.0, 30.0);
    }
    t.last_tick = now;
    if t.left > 0.0 {
        save(dir, t);
        return;
    }
    let allowance = f64::from(t.minutes) * 60.0;
    if p.busy && t.extended + EXTEND <= allowance {
        t.extended += EXTEND;
        t.left += EXTEND;
        save(dir, t);
        receipt(dir, t, "trial", "агент ещё отвечает — даю на проверку ещё 10 минут", "", "");
        return;
    }
    t.verdict = json!({"verdict": "timeout", "by": "", "words": "", "at_utc": utc(now)});
    t.rollback_why = format!("агент не ответил на испытании за {} мин{}", t.minutes, if t.extended > 0.0 { " (с продлением)" } else { "" });
    t.rollback_plain = format!("агент не подтвердил за {} мин, что в новой версии всё работает", t.minutes);
    t.phase = "rollback".into();
    save(dir, t);
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

include!("../../common/code_manifest.rs");

fn finish_accept(dir: &Path, t: &mut Trial) {
    if install::owner_stopped_pub() { save(dir, t); return; }
    if let Err(why) = verify_code_manifest(dir, &t.code_sha256) {
        t.phase = "failed".into();
        t.checks.push(check("installed-code", "код соответствует выпуску", false, &why));
        t.notes.push(why.clone());
        save(dir, t);
        receipt(dir, t, "failed", "приёмка кода не подтверждена", &why, "Сохранена прежняя версия; изменённый код не выдан за выпуск.");
        return;
    }
    let by = t.verdict.get("by").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let words = t.verdict.get("words").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let (note, summary) = match by.as_str() {
        "agent" => (
            format!("агент проверил себя делом и принял {}{}", t.to_version,
                    if words.is_empty() { String::new() } else { format!(": «{}»", clip(&words, 300)) }),
            format!("обновление до {} прошло: агент проверил себя в новой версии", t.to_version),
        ),
        "window" => (
            format!("владелец принял {} кнопкой в окне", t.to_version),
            format!("обновление до {} прошло — ты его принял", t.to_version),
        ),
        _ => (
            format!("испытание агентом пропущено ({}) — проверь его сам", if words.is_empty() { "мозг не настроен" } else { &words }),
            format!("обновление до {} стоит; агент себя не проверял — мозг не настроен", t.to_version),
        ),
    };
    // Прежняя программа и чистая копия прежней версии больше не нужны.
    if !t.kept.is_empty() {
        let _ = crate::tx::remove_tree(Path::new(&t.kept));
    }
    let pristine = dir.join("pristine");
    let _ = std::fs::remove_file(pristine.join(format!("{}.zip", t.from_version)));
    let _ = std::fs::remove_file(pristine.join(format!("after-carry-{}.json", t.to_version)));
    t.phase = "done".into();
    save(dir, t);
    receipt(dir, t, "done", "обновление принято", &note, &summary);
    beat_gone(dir);
    log(dir, &format!("принято ({by})"));
}

/// Вернуть прежнюю версию. -> код выхода (0 — вернулась, 1 — нет).
pub fn do_rollback(dir: &Path, t: &mut Trial) -> i32 {
    loop {
        match rollback_once(dir, t) {
            Ok(()) => return 0,
            Err(retry) if retry && t.rollback_tries < ROLLBACK_TRIES => {
                log(dir, &format!("откат не удался (попытка {} из {ROLLBACK_TRIES}) — новая версия снова поднята, повторю", t.rollback_tries));
                std::thread::sleep(ROLLBACK_PAUSE);
            }
            Err(_) => {
                let why = if t.rollback_why.is_empty() { "откат".to_string() } else { t.rollback_why.clone() };
                return fail(dir, t, &why);
            }
        }
    }
}

/// Одна попытка отката. Err(true) — не вышло, но стоит повторить (новая версия снова
/// поднята); Err(false) — повторять нечего.
fn rollback_once(dir: &Path, t: &mut Trial) -> Result<(), bool> {
    t.rollback_tries += 1;
    save(dir, t);
    let why = if t.rollback_why.is_empty() { "откат".to_string() } else { t.rollback_why.clone() };
    receipt(dir, t, "running", "возвращаю прежнюю версию", &why, "");
    log(dir, &format!("откат: {why}"));
    let kept = PathBuf::from(&t.kept);
    if t.kept.is_empty() || !kept.is_dir() {
        t.notes.push("прежней программы нет — вернуть нечего; стоит новая версия".into());
        return Err(false);
    }
    // 0. Агент мог сказать «сломано» посреди хода и ещё объясняет владельцу — не рвать.
    let waited_from = epoch();
    while epoch() - waited_from < IDLE_WAIT && probe(dir).busy {
        std::thread::sleep(TICK);
    }
    // 1. Правки агента за испытание — ему, пока новая версия на месте.
    let python = install::python_exe(dir);
    let script = dir.join("server").join("updater").join("codecarry.py");
    if python.is_file() && script.is_file() {
        let mut cmd = std::process::Command::new(&python);
        cmd.arg("-X").arg("utf8").arg(&script).arg("trial-edits").arg("--install").arg(dir)
            .arg("--to").arg(&t.to_version).env("PYTHONIOENCODING", "utf-8");
        if let Ok(out) = install::run_hidden_for_pub(&mut cmd, Duration::from_secs(120)) {
            let text = String::from_utf8_lossy(&out.stdout);
            if let Some(v) = text.lines().rev().find_map(|l| serde_json::from_str::<Value>(l).ok()) {
                let n = v.get("edited").and_then(|e| e.as_array()).map(|a| a.len()).unwrap_or(0);
                if n > 0 {
                    t.notes.push(format!("правки агента за испытание ({n}) — в {}",
                                         v.get("folder").and_then(|f| f.as_str()).unwrap_or("workspace")));
                }
            }
        }
    }
    // 2. Остановить новую версию: служба и всё из папки.
    let had_service = install::service_is_ours_pub(dir) || (t.service && t.rollback_tries > 1);
    if had_service && install::service_is_ours_pub(dir) {
        if let Err(e) = install::service_uninstall_pub(dir) {
            t.notes.push(format!("служба не снялась: {e}"));
        }
    }
    // Не вышло после остановки — поднять новую версию обратно: агент не должен лежать.
    let back_up = |t: &mut Trial| {
        if install::owner_stopped_pub() { save(dir, t); return; }
        if had_service {
            let state = install::install_service_pub(dir);
            t.notes.push(format!("новая версия снова поднята, служба: {state}"));
        }
        let _ = install::launch_pub(dir);
        save(dir, t);
    };
    let mut stopped = install::stop_running(dir);
    for _ in 0..3 {
        if stopped {
            break;
        }
        std::thread::sleep(Duration::from_secs(2));
        stopped = install::stop_running(dir);
    }
    if !stopped {
        let busy = install::locked_files_pub(dir);
        if !busy.is_empty() {
            t.notes.push(format!("новая версия не останавливается — держит файлы: {}", busy.join(", ")));
            back_up(t);
            return Err(true);
        }
    }
    // 3. Прежняя программа — на место той же транзакцией.
    let top = t.new_top.clone();
    let extra: Vec<(String, String)> = if t.static_plan == "to_prev" {
        vec![("app/static.prev".into(), "app/static".into())]
    } else {
        Vec::new()
    };
    let static_carry = if t.static_plan == "keep" { crate::tx::StaticCarry::Keep } else { crate::tx::StaticCarry::None };
    let swapped = (|| -> Result<(), String> {
        let mut tx = crate::tx::Tx::begin_from(dir, &t.from_version, &kept)?;
        let carry = crate::tx::Carry {
            drop: &[install::INSTALL_MARKER, "install.log", "stop.log", "extensions-check.json"],
            old_payload_top: &top,
            static_carry,
            keep_runtime: t.runtime_moved,
            extra: &extra,
        };
        tx.swap(&carry)?;
        let _ = tx.commit().join();
        Ok(())
    })();
    if let Err(e) = swapped {
        t.notes.push(format!("прежняя версия не встала: {e}"));
        back_up(t);
        return Err(true);
    }
    t.notes.push(format!("{} возвращена на место", t.from_version));
    // 4. Настройки и запись в «Приложениях» — прежней версии.
    if let Err(e) = install::mark_version_pub(dir, &t.scope, &t.from_version) {
        t.notes.push(format!("версия в настройках не записалась: {e}"));
    }
    // 5. Old service binaries may not know the stop latch: do not restart them.
    if had_service && !install::owner_stopped_pub() {
        let state = install::install_service_pub(dir);
        t.notes.push(format!("служба: {state}"));
    }
    let _ = install::launch_pub(dir);
    t.phase = "rolled_back".into();
    save(dir, t);
    let plain = if t.rollback_plain.is_empty() { why.clone() } else { t.rollback_plain.clone() };
    receipt(dir, t, "rolled_back", "прежняя версия возвращена", &why,
            &format!("обновление до {} откачено: {plain}. Снова стоит {}; память агента не тронута",
                     t.to_version, t.from_version));
    beat_gone(dir);
    log(dir, "откат закончен");
    Ok(())
}

fn fail(dir: &Path, t: &mut Trial, why: &str) -> i32 {
    t.phase = "failed".into();
    save(dir, t);
    receipt(dir, t, "failed", "откат не удался", why,
            &format!("вернуть {} не вышло: {}. Стоит {}", t.from_version,
                     t.notes.last().cloned().unwrap_or_default(), t.to_version));
    beat_gone(dir);
    log(dir, &format!("откат не удался: {}", t.notes.join("; ")));
    1
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn utc_matches_known_moments() {
        assert_eq!(utc(0.0), "1970-01-01T00:00:00Z");
        assert_eq!(utc(1_790_000_000.0), "2026-09-21T14:13:20Z");
        assert_eq!(utc(951_782_400.0), "2000-02-29T00:00:00Z");
    }

    fn tmp(tag: &str) -> PathBuf {
        let r = std::env::temp_dir().join(format!("helene-trial-{tag}-{}", std::process::id()));
        let _ = crate::tx::remove_tree(&r);
        std::fs::create_dir_all(r.join("data").join("memory").join(".control")).unwrap();
        std::fs::create_dir_all(r.join("backups")).unwrap();
        r
    }

    fn trial(dir: &Path) -> Trial {
        use sha2::{Digest, Sha256};
        let bytes = b"pass\n";
        let mut code_sha256 = std::collections::BTreeMap::new();
        for rel in ["tree/agent.py", "app/deskapp.py"] {
            let path = dir.join(rel);
            std::fs::create_dir_all(path.parent().unwrap()).unwrap();
            std::fs::write(&path, bytes).unwrap();
            code_sha256.insert(rel.into(), format!("{:x}", Sha256::digest(bytes)));
        }
        begin(dir, Begin {
            from_version: "1.2.4",
            to_version: "1.2.5",
            kept: None,
            runtime_moved: true,
            static_plan: "keep",
            new_top: vec!["app".into()],
            code_sha256,
            service: false,
            scope: "user",
            agent_code: json!({"summary": "правок нет"}),
            extensions: String::new(),
        })
    }

    fn read_receipt(dir: &Path) -> Value {
        install::read_json_pub(&control(dir, "update-plan.receipt.json")).unwrap()
    }

    #[test]
    fn a_fresh_runner_opens_the_trial_and_an_old_one_does_not() {
        let dir = tmp("open");
        let mut t = trial(&dir);
        assert_eq!(read_receipt(&dir)["state"], "running");
        let now = epoch();
        // квитанция прежней версии (до установки) — не в счёт
        let old = Probe { channel: true, runner: true, runner_at: t.installed_epoch - 30.0, brain: Some(true), ..Default::default() };
        starting(&dir, &mut t, &old, now);
        assert_eq!(t.phase, "starting");
        let fresh = Probe { runner_at: t.installed_epoch + 2.0, ..old };
        starting(&dir, &mut t, &fresh, now);
        assert_eq!(t.phase, "trial");
        let r = read_receipt(&dir);
        assert_eq!((r["state"].as_str(), r["desktop"].as_bool()), (Some("trial"), Some(true)));
        assert_eq!(r["trial"]["key"], t.key.as_str());
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn no_brain_means_nobody_to_test_and_it_is_said() {
        let dir = tmp("nobrain");
        let mut t = trial(&dir);
        let p = Probe { channel: true, runner: true, runner_at: t.installed_epoch + 1.0, brain: Some(false), ..Default::default() };
        starting(&dir, &mut t, &p, epoch());
        assert_eq!(t.phase, "accepting");
        finish_accept(&dir, &mut t);
        let r = read_receipt(&dir);
        assert_eq!(r["state"], "done");
        assert!(r["note"].as_str().unwrap().contains("проверь его сам"));
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn acceptance_without_code_proof_keeps_previous_program() {
        let dir = tmp("no-code-proof");
        let kept = kept_path(&dir, "1.2.4");
        std::fs::create_dir_all(kept.join("app")).unwrap();
        let mut t = trial(&dir);
        t.kept = kept.display().to_string();
        t.code_sha256.clear();
        t.verdict = json!({"verdict": "accept", "by": "agent"});
        finish_accept(&dir, &mut t);
        assert_eq!(read_receipt(&dir)["state"], "failed");
        assert!(kept.exists(), "без подтверждения кода прежняя программа остаётся");
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn a_window_that_never_wakes_its_engine_is_rolled_back() {
        let dir = tmp("dead");
        let mut t = trial(&dir);
        let p = Probe { channel: true, ..Default::default() };
        let now = epoch();
        starting(&dir, &mut t, &p, now);
        assert_eq!(t.phase, "starting");
        starting(&dir, &mut t, &p, now + START_WAIT + 1.0);
        assert_eq!(t.phase, "rollback");
        assert!(t.rollback_why.contains("не ожил"));
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn the_clock_stops_while_the_program_is_closed_and_silence_rolls_back() {
        let dir = tmp("clock");
        let mut t = trial(&dir);
        let now = epoch();
        let alive = Probe { channel: true, runner: true, runner_at: t.installed_epoch + 1.0, brain: Some(true), ..Default::default() };
        starting(&dir, &mut t, &alive, now);
        let full = t.left;
        let closed = Probe::default();
        trial_tick(&dir, &mut t, &closed, now + 20.0);
        assert_eq!(t.left, full, "программа закрыта — часы стоят");
        let up = Probe { runner: true, ..Default::default() };
        let mut at = now + 20.0;
        while t.phase == "trial" {
            at += 25.0;
            trial_tick(&dir, &mut t, &up, at);
        }
        assert_eq!(t.phase, "rollback");
        assert_eq!(t.verdict["verdict"], "timeout");
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn the_word_is_taken_only_for_this_trial() {
        let dir = tmp("word");
        let mut t = trial(&dir);
        let alive = Probe { channel: true, runner: true, runner_at: t.installed_epoch + 1.0, brain: Some(true), ..Default::default() };
        starting(&dir, &mut t, &alive, epoch());
        let vpath = control(&dir, "update-plan.verdict.json");
        std::fs::write(&vpath, json!({"id": t.id, "key": "чужой", "verdict": "accept", "by": "agent"}).to_string()).unwrap();
        trial_tick(&dir, &mut t, &Probe::default(), epoch());
        assert_eq!(t.phase, "trial");
        std::fs::write(&vpath, json!({"id": t.id, "key": t.key, "verdict": "reject", "by": "window", "words": ""}).to_string()).unwrap();
        trial_tick(&dir, &mut t, &Probe::default(), epoch());
        assert_eq!(t.phase, "rollback");
        assert_eq!(t.rollback_plain, "ты попросил вернуть прежнюю версию");
        assert!(!vpath.exists());
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn only_one_watcher_holds_the_lock_and_a_dead_ones_lock_is_taken_over() {
        let dir = tmp("lock");
        let first = Lock::take(&dir).expect("первый берёт замок");
        assert!(Lock::take(&dir).is_none(), "второй сторож выходит");
        drop(first);
        assert!(!Lock::path(&dir).exists(), "ушёл — замок снят");
        // замок умершего сторожа (давний) — берётся
        std::fs::write(Lock::path(&dir), "1").unwrap();
        let old = std::time::SystemTime::now() - Duration::from_secs(120);
        std::fs::File::options().write(true).open(Lock::path(&dir)).unwrap().set_modified(old).unwrap();
        assert!(Lock::take(&dir).is_some());
        let _ = crate::tx::remove_tree(&dir);
    }

    #[test]
    fn accept_by_the_agent_removes_the_kept_program() {
        let dir = tmp("accept");
        let kept = kept_path(&dir, "1.2.4");
        std::fs::create_dir_all(kept.join("app")).unwrap();
        let mut t = trial(&dir);
        t.kept = kept.display().to_string();
        std::fs::create_dir_all(dir.join("pristine")).unwrap();
        std::fs::write(dir.join("pristine").join("1.2.4.zip"), b"x").unwrap();
        std::fs::write(dir.join("pristine").join("1.2.5.zip"), b"x").unwrap();
        t.verdict = json!({"verdict": "accept", "by": "agent", "words": "shell и память живы"});
        finish_accept(&dir, &mut t);
        assert!(!kept.exists());
        assert!(!dir.join("pristine").join("1.2.4.zip").exists());
        assert!(dir.join("pristine").join("1.2.5.zip").exists(), "база следующего обновления остаётся");
        let r = read_receipt(&dir);
        assert_eq!(r["state"], "done");
        let hist = std::fs::read_to_string(control(&dir, "update-history.jsonl")).unwrap();
        assert!(hist.contains("\"done\""));
        let _ = crate::tx::remove_tree(&dir);
    }
}
