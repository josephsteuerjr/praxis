//! helene-svc — ОПЦИОНАЛЬНАЯ Windows-служба харнесса (решение владельца 31.08).
//!
//! По умолчанию продукт живёт без службы: харнесс — дочерний процесс окна/трея.
//! Служба добавляет три вещи: защиту папки установки (права выравниваются при
//! каждом старте), жизнь без открытого окна и — дальше по плану — брокера прав.
//! Паттерн — тот же, что у виндоус-тела: `praxis-system-router` (служба)
//! дотягивается до сессии владельца через задачу планировщика.
//!
//! ⚠ ГЛАВНОЕ ПРО ПРАВА (04.09). Служба работает под LocalSystem в нулевой
//! сессии. Харнесс она под собой НЕ поднимает: он живёт в интерактивной сессии
//! владельца, с правами владельца, без повышения. Два повода:
//!   1. раньше `Command::spawn()` отдавал детям права СИСТЕМЫ — этого никто не
//!      выбирал: владелец включал службу ради жизни без окна, а получал
//!      всевластие молча;
//!   2. из Session 0 не виден рабочий стол — UIA и всё интерактивное там мертвы
//!      по построению Windows.
//! Выход из нулевой сессии — задача планировщика `Helene\session-host`, см.
//! раздел «сессия».
//!
//! Служба — ВТОРОЙ супервизор той же пары детей (в режиме `session-host`). Всё,
//! чему окно научилось после отладки, здесь повторено намеренно: журнал детей в
//! файл, job-объект, проверка занятого порта, честный код завершения ребёнка.
//! Расхождение этих двух веток уже стоило продукту двух агентов на одном
//! дереве — см. блок «замок порта» в supervise().
//!
//! CLI:
//!   helene-svc install --config C:\...\helene.json   (требует админа, один UAC)
//!   helene-svc uninstall                           (снимает службу И возвращает
//!                                                   права на папку установки)
//!   helene-svc run --config <path>                 (вход SCM; руками не звать)
//!   helene-svc session-host --config <path>        (харнесс в сессии владельца;
//!                                                   зовёт планировщик, не человек)
//!   helene-svc session-task install|remove --config <path> [--user DOMAIN\User]
//!   helene-svc acl-align --config <path>           (закрыть папку установки)
//!   helene-svc acl-relax --config <path>           (открыть её обратно — обновление)
//!   helene-svc broker --config <path>              (только труба брокера — отладка)
//!   helene-svc foreground --config <path>          (та же логика в консоли — отладка)

use std::ffi::OsString;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use windows_service::service::{
    ServiceAccess, ServiceAction, ServiceActionType, ServiceControl, ServiceControlAccept,
    ServiceErrorControl, ServiceExitCode, ServiceFailureActions, ServiceFailureResetPeriod,
    ServiceInfo, ServiceStartType, ServiceState, ServiceStatus, ServiceType,
};
use windows_service::service_control_handler::{self, ServiceControlHandlerResult, ServiceStatusHandle};
use windows_service::service_manager::{ServiceManager, ServiceManagerAccess};
use windows_service::{define_windows_service, service_dispatcher};

const SERVICE_NAME: &str = "Helene";  // идентификатор в SCM — латиницей
/// Порт встроенного реле по умолчанию — как в `ui-kit/contract.json`.
const RELAY_PORT: u16 = 5011;
const SERVICE_DISPLAY: &str = "Hélène · агент";
// Прежний текст обещал «живым до входа пользователя». Это перестало быть
// правдой в тот день, когда харнесс переехал в сессию владельца: до входа
// поднимать его теперь некуда. Описание видно в оснастке служб, и врать там
// нельзя.
const SERVICE_DESCRIPTION: &str =
    "Держит агента Hélène живым в сессии владельца без открытого окна и стережёт права на папку установки. Снять: uninstall-service.ps1.";
const CONFIG_NAME: &str = "helene.json";
const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// Журнал службы и журналы детей режутся на этом размере — служба может
/// прожить месяцы, а перезапуск упавшего ребёнка пишет строку каждую минуту.
const LOG_MAX: u64 = 5 * 1024 * 1024;

/// Консоль отпущена (режим session-host) — на экран больше не пишем.
///
/// ⚠ МИНА, из-за которой этот флаг существует. `eprintln!` при ошибке записи не
/// возвращает ошибку, а ПАНИКУЕТ («failed printing to stderr»). После
/// FreeConsole дескриптор stderr указывает в никуда, и первая же строка журнала
/// уронила бы харнесс в сессии владельца — с виду «задача не запускается».
static CONSOLE_GONE: AtomicBool = AtomicBool::new(false);

fn to_console(text: &str) {
    if !CONSOLE_GONE.load(Ordering::Relaxed) {
        eprintln!("{text}");
    }
}

struct Log(Option<std::fs::File>);

impl Log {
    fn open(dir: &Path) -> Self {
        let _ = std::fs::create_dir_all(dir);
        Log(open_rolling(&dir.join("service.log")))
    }
    fn line(&mut self, text: &str) {
        to_console(text);
        if let Some(f) = &mut self.0 {
            let _ = writeln!(f, "{} {}", now_stamp(), text);
        }
    }
}

/// Файл журнала с усечением: больше LOG_MAX — уезжает в `.1`.
fn open_rolling(path: &Path) -> Option<std::fs::File> {
    if let Ok(meta) = std::fs::metadata(path) {
        if meta.len() > LOG_MAX {
            let mut old = path.as_os_str().to_os_string();
            old.push(".1");
            let _ = std::fs::rename(path, PathBuf::from(old));
        }
    }
    std::fs::OpenOptions::new().create(true).append(true).open(path).ok()
}

/// Куда писать, когда план ещё не прочитан (дерева мы не знаем): рядом с
/// конфигом, в `data\service.log` — там же, где его ищет сборщик логов окна и
/// куда указывает install-service.ps1. Без этого причина раннего выхода
/// пропадала совсем: SCM показывал «служба не ответила своевременно (1053)»,
/// а в продукте не оставалось ни строки.
fn early_line(config: &Path, text: &str) {
    let dir = config
        .parent()
        .map(|d| d.join("data"))
        .unwrap_or_else(|| PathBuf::from("data"));
    let _ = std::fs::create_dir_all(&dir);
    if let Some(mut f) = open_rolling(&dir.join("service.log")) {
        let _ = writeln!(f, "{} {}", now_stamp(), text);
    }
    // Через эту же функцию идёт хук паники: `eprintln!` без консоли паникует
    // сам, и паника внутри паники — это тихий abort без единого следа.
    to_console(text);
}

fn arg_after(flag: &str) -> Option<String> {
    let args: Vec<String> = std::env::args().collect();
    args.iter()
        .position(|a| a == flag)
        .and_then(|i| args.get(i + 1))
        .cloned()
}

fn resolve(base: &Path, raw: &str) -> PathBuf {
    let p = PathBuf::from(raw);
    if p.is_absolute() { p } else { base.join(p) }
}

/// `canonicalize()` на Windows даёт верватим-путь `\\?\C:\…`. Он живёт в
/// командной строке службы, приезжает детям в HELENE_TREE и вылезает владельцу
/// на экранах «Файлы»/«Система», а брандмауэр по такому пути программу не
/// узнаёт. Снимаем префикс сразу.
fn plain_path(p: &Path) -> PathBuf {
    let s = p.to_string_lossy();
    match s.strip_prefix(r"\\?\") {
        Some(rest) if rest.starts_with("UNC\\") => PathBuf::from(format!(r"\\{}", &rest[4..])),
        Some(rest) => PathBuf::from(rest),
        None => p.to_path_buf(),
    }
}

/// Системные программы — только полным путём из `%SystemRoot%`.
/// `Command::new("netsh")` ищет exe СНАЧАЛА в папке СВОЕГО процесса, а папка
/// процесса службы — это папка установки: туда пишет обычный пользователь и
/// туда же `fence.py` пускает самого агента. Подложенный `netsh.exe` исполнился
/// бы СИСТЕМОЙ при первом же старте службы. Двенадцать строк — те же, что у
/// оболочки (`shell/src/main.rs::sys_exe`).
fn system_root() -> PathBuf {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:\\Windows"))
}

fn sys_exe(name: &str) -> PathBuf {
    let full = system_root().join("System32").join(name);
    if full.exists() {
        full
    } else {
        PathBuf::from(name)
    }
}

/// Где конфиг: `--config`, иначе рядом с exe. Без `unwrap()`: паника здесь
/// уходила через FFI-границу define_windows_service! и служба исчезала без
/// единого следа (владелец видел только 1053).
fn config_path() -> PathBuf {
    if let Some(raw) = arg_after("--config") {
        return PathBuf::from(raw);
    }
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(|d| d.join(CONFIG_NAME)))
        .unwrap_or_else(|| PathBuf::from(CONFIG_NAME))
}

/// Дети живут ровно столько, сколько служба: job-объект с KILL_ON_JOB_CLOSE.
/// Без него после `sc stop` внуки (процесс агента, питон реле, процессы
/// песочницы) оставались жить под SYSTEM и держать порт трубы. Тот же модуль,
/// что у оболочки (shell/main.rs::job) плюс явное гашение при остановке.
#[cfg(windows)]
mod job {
    use windows_sys::Win32::Foundation::HANDLE;
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, TerminateJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct Job(HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> Option<Job> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    return None;
                }
                let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
                let ok = SetInformationJobObject(
                    handle,
                    JobObjectExtendedLimitInformation,
                    &info as *const _ as *const std::ffi::c_void,
                    std::mem::size_of_val(&info) as u32,
                );
                if ok == 0 {
                    return None;
                }
                Some(Job(handle))
            }
        }

        pub fn adopt(&self, child: &std::process::Child) {
            use std::os::windows::io::AsRawHandle;
            unsafe {
                AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE);
            }
        }

        /// Погасить всё дерево разом — вместе с внуками, о которых служба
        /// ничего не знает.
        pub fn terminate(&self) {
            unsafe {
                TerminateJobObject(self.0, 1);
            }
        }
    }
}

#[cfg(windows)]
static JOB: std::sync::OnceLock<Option<job::Job>> = std::sync::OnceLock::new();

fn adopt(child: &Child) {
    #[cfg(windows)]
    if let Some(job) = JOB.get_or_init(job::Job::new).as_ref() {
        job.adopt(child);
    }
    #[cfg(not(windows))]
    let _ = child;
}

fn kill_all_descendants() {
    #[cfg(windows)]
    if let Some(job) = JOB.get_or_init(job::Job::new).as_ref() {
        job.terminate();
    }
}

/// Вывод ребёнка — в файл рядом с данными (deskapp.log, runner.log), теми же
/// именами, что у окна. Без этого у службы нет консоли: sys.stdout ребёнка
/// нулевой, и traceback падения испарялся целиком.
fn child_log(tree: &Path, script: &Path) -> Option<std::fs::File> {
    let stem = script.file_stem()?.to_string_lossy().into_owned();
    open_rolling(&tree.join(format!("{stem}.log")))
}

/// Секрет трубы: тот же механизм, что у оболочки (shell/main.rs), и намеренно
/// тот же файл в дереве. Труба (deskapp.py) без секрета отдаёт роль ВЛАДЕЛЬЦА
/// каждому запросу по петле — а служба поднимает харнесс на машине, где рядом
/// работают чужие процессы и другие учётные записи Windows. Секрет один на
/// дерево: свой у каждого супервизора означал бы 403 в собственном окне.
fn desk_token_path(tree: &Path) -> PathBuf {
    tree.join("memory").join(".state").join("desk-token")
}

/// Прочитать секрет из файла. Проверка формы — не педантизм: обрезанный файл
/// («» или один байт) в роли токена означал бы «замок, который открывается
/// пустотой».
fn read_token_file(path: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(path).ok()?;
    let t = raw.trim();
    (t.len() >= 16 && t.len() <= 128 && t.bytes().all(|b| b.is_ascii_alphanumeric()))
        .then(|| t.to_string())
}

/// Секрет в файле: читаем, а нет — заводим. Пустая строка = не смогли, и это
/// честное «замка нет», а не тихий отказ.
///
/// Функция общая для двух замков — трубы харнесса (`desk-token`) и брокера
/// (`broker-token`). Разъедься они, второй замок оказался бы слабее первого
/// молча.
fn ensure_token_file(path: &Path) -> String {
    if let Some(t) = read_token_file(path) {
        return t;
    }
    let Some(token) = random_hex(24) else { return String::new() };
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    match std::fs::write(path, &token) {
        Ok(()) => token,
        Err(_) => String::new(),
    }
}

fn ensure_desk_token(tree: &Path) -> String {
    ensure_token_file(&desk_token_path(tree))
}

/// Те же правила запуска ребёнка, что у оболочки (shell/main.rs::spawn_child):
/// одна семантика запуска, два супервизора — окно и служба.
///
/// ⚠ КТО СЮДА ДОХОДИТ. Дочерний процесс наследует права родителя — и это ровно
/// то, из-за чего служба перестала звать эту функцию сама: под LocalSystem
/// наследование давало агенту права СИСТЕМЫ. Теперь сюда попадают только
/// процессы, УЖЕ живущие в сессии владельца с его правами: `session-host`
/// (его запускает планировщик по просьбе службы) и `foreground` (отладка).
/// Служба в нулевой сессии идёт другим путём — supervise_session().
fn spawn_child(
    python: &Path,
    script: &Path,
    args: &[String],
    tree: &Path,
    host: &str,
    token: &str,
    config: &Path,
) -> Result<Child, String> {
    #[cfg(windows)]
    use std::os::windows::process::CommandExt;
    let mut cmd = Command::new(python);
    cmd.arg("-u")
        .arg(script)
        .args(args)
        .env("HELENE_TREE", tree)
        .env("HELENE_HOST", host)
        .env("PYTHONUTF8", "1")
        // Замок трубы; PRAXIS_DESK_TOKEN снимаем, чтобы секрет решал тот, кто
        // поднял харнесс, а не переменная окружения службы.
        .env("HELENE_TOKEN", token)
        .env("HELENE_CONFIG", config)
        .env_remove("PRAXIS_DESK_TOKEN");
    if let Some(dir) = script.parent() {
        cmd.current_dir(dir);
    }
    if let Some(log) = child_log(tree, script) {
        if let Ok(err) = log.try_clone() {
            cmd.stdout(Stdio::from(log)).stderr(Stdio::from(err));
        }
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    match cmd.spawn() {
        Ok(child) => {
            adopt(&child);
            Ok(child)
        }
        // Причину раньше глотал `.ok()`, и в журнале оставалось только
        // «не поднялся» без единого слова о том, почему.
        Err(err) => Err(err.to_string()),
    }
}

struct Plan {
    mode: String,
    python: PathBuf,
    app: PathBuf,
    runner: Option<PathBuf>,
    config: PathBuf,
    tree: PathBuf,
    port: u16,
    relay_enabled: bool,
    relay_port: u16,
    relay_key: String,
    /// `relay.instructions` — какой системный промпт реле кладёт перед
    /// конституцией: `minimal` (~60 слов) или `full` (23 КБ чужого промпта
    /// Codex CLI). Умолчание то же, что у оболочки (shell::spawn_relay).
    /// ⚠ До 06.09 служба переменную не передавала вовсе, и под session-host
    /// агент получал полный чужой промпт на каждом ходу. Найдено ревью 06.09.
    relay_instructions: String,
    phone: bool,
    /// Отдельная галочка «разрешить агенту нулевую сессию» (`service.session0`
    /// в helene.json), по умолчанию выключена. Включённая возвращает прежний
    /// порядок: харнесс поднимается ПОД СЛУЖБОЙ, то есть правами СИСТЕМЫ и без
    /// рабочего стола. Это осознанный выбор владельца, а не умолчание.
    session0: bool,
    /// Выключатель брокера (`service.broker` в helene.json), по умолчанию
    /// включён. Читается ЗАНОВО на каждом обращении к трубе — «действует
    /// немедленно» из спеки означает именно это, а не «после перезапуска
    /// службы».
    broker: bool,
    /// `service.firewall` — ставит ли служба правило брандмауэра для телефона.
    /// Умолчание — true.
    ///
    /// ⚠ Ключ был заведён и ЗАБЫТ: его не читал ни один потребитель, а
    /// `supervise` получал константу `true`. Из-за этого обе позиции галочки на
    /// экране владельца ничего не значили, а кнопка «Телефон» под службой
    /// уходила в `exec`, где ей отвечали «нулевая сессия выключена» — то есть
    /// ради одного правила владельцу предлагалось выдать агенту права системы
    /// целиком.
    firewall_rule: bool,
}

/// helene.json так, как его сохранил редактор ВЛАДЕЛЬЦА: UTF-8, UTF-8 с меткой
/// BOM, UTF-16 с меткой. Ровно та же функция, что в оболочке
/// (shell/src/main.rs::decode_config) — трое читателей одного файла не имеют
/// права понимать его по-разному. ПЕРВЫЙ-ЗАПУСК.md зовёт править этот файл
/// руками, а Блокнот, VS Code и `Set-Content` из PowerShell 5.1 пишут именно так;
/// со строгим UTF-8 служба не стартовала вовсе, называя причиной «не разобрался».
fn decode_config(bytes: &[u8]) -> Result<String, String> {
    if bytes.starts_with(&[0xFF, 0xFE]) || bytes.starts_with(&[0xFE, 0xFF]) {
        let big = bytes[0] == 0xFE;
        let body = &bytes[2..];
        if body.len() % 2 != 0 {
            return Err("оборванный UTF-16".into());
        }
        let units: Vec<u16> = body
            .chunks_exact(2)
            .map(|p| if big { u16::from_be_bytes([p[0], p[1]]) } else { u16::from_le_bytes([p[0], p[1]]) })
            .collect();
        return String::from_utf16(&units).map_err(|_| "не читается как UTF-16".to_string());
    }
    let body = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(bytes);
    String::from_utf8(body.to_vec()).map_err(|_| "не читается как UTF-8".to_string())
}

fn load_plan(config_path: &Path) -> Result<Plan, String> {
    let bytes = std::fs::read(config_path)
        .map_err(|e| format!("helene.json не читается: {e}"))?;
    let raw = decode_config(&bytes).map_err(|why| format!("helene.json не читается: {why}"))?;
    let cfg: serde_json::Value =
        serde_json::from_str(&raw).map_err(|e| format!("helene.json не разобрался: {e}"))?;
    let base = config_path.parent().unwrap_or(Path::new(".")).to_path_buf();
    let python = match cfg.get("python").and_then(|v| v.as_str()) {
        Some(raw) if raw != "python" => resolve(&base, raw),
        _ => {
            let embedded = base.join("runtime").join("python.exe");
            if embedded.exists() { embedded } else { PathBuf::from("python") }
        }
    };
    let tree = resolve(
        &base,
        cfg.get("tree").and_then(|v| v.as_str()).unwrap_or("data"),
    );
    let _ = std::fs::create_dir_all(&tree);
    Ok(Plan {
        // Ключ `mode` служба не читала вовсе: владелец переводил окно на
        // удалённый харнесс, окно детей не поднимало — а служба поднимала свою
        // пару всегда, и на дереве оказывался второй агент на его деньги.
        mode: cfg
            .get("mode")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .trim()
            .to_string(),
        python,
        // Дефолт тот же, что у окна (shell/main.rs::spawn_local): отсутствие
        // ключа `app` там давало «deskapp.py», а здесь — вообще никаких детей
        // при зелёном `sc query`.
        app: resolve(
            &base,
            cfg.get("app").and_then(|v| v.as_str()).unwrap_or("deskapp.py"),
        ),
        runner: cfg
            .get("runner")
            .and_then(|v| v.as_str())
            .map(|r| resolve(&base, r)),
        config: config_path.to_path_buf(),
        tree,
        port: cfg.get("port").and_then(|v| v.as_u64()).unwrap_or(8094) as u16,
        relay_enabled: cfg
            .get("relay")
            .and_then(|r| r.get("enabled"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        relay_port: cfg
            .get("relay")
            .and_then(|r| r.get("port"))
            .and_then(|v| v.as_u64())
            .unwrap_or(RELAY_PORT as u64) as u16,
        relay_key: cfg
            .get("model")
            .and_then(|m| m.get("key"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        relay_instructions: cfg
            .get("relay")
            .and_then(|r| r.get("instructions"))
            .and_then(|v| v.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .unwrap_or("minimal")
            .to_string(),
        phone: cfg
            .get("phone")
            .and_then(|p| p.get("enabled"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        // Умолчание — false, и оно должно оставаться false даже если ключа нет
        // вовсе: «не сказано» здесь означает «не разрешено».
        session0: cfg
            .get("service")
            .and_then(|s| s.get("session0"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false),
        // Умолчание — true: брокер сам по себе ничего лишнего не даёт (exec
        // под СИСТЕМОЙ заперт галочкой нулевой сессии), а выключенный по
        // умолчанию он был бы просто мёртвым кодом. Выключатель нужен для
        // экрана Настроек — чтобы владелец мог закрыть дверь целиком.
        broker: cfg
            .get("service")
            .and_then(|s| s.get("broker"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true),
        firewall_rule: cfg
            .get("service")
            .and_then(|s| s.get("firewall"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true),
    })
}

/// Может ли служба вообще что-то поднять по этому плану. Проверка одна на два
/// входа: `install` (владелец у экрана, ошибку видно сразу) и старт службы
/// (иначе то же самое всплывало бы вечным циклом «не поднялся» при зелёном
/// `sc query`).
fn plan_usable(plan: &Plan) -> Result<(), String> {
    if plan.mode != "local" {
        return Err(format!(
            "helene.json просит mode=\"{}\" — харнесс живёт не здесь, служба не нужна",
            plan.mode
        ));
    }
    if plan.python != Path::new("python") && !plan.python.exists() {
        return Err(format!("нет питона: {}", plan.python.display()));
    }
    if !plan.app.exists() {
        return Err(format!("нет канала: {} (ключ \"app\" в helene.json)", plan.app.display()));
    }
    if let Some(runner) = &plan.runner {
        if !runner.exists() {
            return Err(format!("нет руннера: {} (ключ \"runner\")", runner.display()));
        }
    }
    Ok(())
}

/// Харнесс уже жив на этом порту? Ровно та же проба, что у окна
/// (shell/main.rs::harness_alive): держатель порта и есть харнесс.
fn harness_alive(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// Кто держит порт. Четыре случая, а не три: «харнесс под ключом» (401/403) —
/// это НЕ «здесь не харнесс». Под замком трубы собственный же харнесс отвечает
/// 403 всякому, кто не предъявил секрет, и служба называла его чужой
/// программой — ровно та ошибка, которую окно уже научилось не делать
/// (shell/main.rs::Verdict::Guarded).
enum Holder {
    Tree(PathBuf),
    Guarded,
    Silent,
}

/// Один запрос `/api/home`. `token` пустой — спрашиваем анонимно.
/// ureq ради этого в службу не тянем: сокет и три строки протокола.
fn home_probe(port: u16, token: &str) -> Holder {
    use std::io::Read;
    use std::net::{SocketAddr, TcpStream};
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    let Ok(mut sock) = TcpStream::connect_timeout(&addr, Duration::from_millis(400)) else {
        return Holder::Silent;
    };
    if sock.set_read_timeout(Some(Duration::from_millis(1500))).is_err()
        || sock.set_write_timeout(Some(Duration::from_millis(1500))).is_err()
    {
        return Holder::Silent;
    }
    let query = if token.is_empty() { String::new() } else { format!("?key={token}") };
    let req = format!(
        "GET /api/home{query} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nConnection: close\r\n\r\n"
    );
    if sock.write_all(req.as_bytes()).is_err() {
        return Holder::Silent;
    }
    let mut buf = Vec::new();
    if sock.take(64 * 1024).read_to_end(&mut buf).is_err() {
        return Holder::Silent;
    }
    let text = String::from_utf8_lossy(&buf);
    let status = text.lines().next().unwrap_or("");
    if status.contains(" 401") || status.contains(" 403") {
        return Holder::Guarded;
    }
    let tree = text
        .split("\r\n\r\n")
        .nth(1)
        .and_then(|body| serde_json::from_str::<serde_json::Value>(body.trim()).ok())
        .and_then(|v| v.get("tree").and_then(|t| t.as_str()).map(PathBuf::from));
    match tree {
        Some(t) => Holder::Tree(t),
        None => Holder::Silent,
    }
}

/// Кто держит порт. Сначала спрашиваем БЕЗ ключа: держатель — не обязательно
/// наш, и секрет дерева нельзя отдавать первому, кто занял порт. Ключ
/// предъявляем только тому, кто ответил «нужен ключ».
fn harness_holder(port: u16, token: &str) -> Holder {
    match home_probe(port, "") {
        Holder::Guarded if !token.is_empty() => match home_probe(port, token) {
            Holder::Tree(t) => Holder::Tree(t),
            _ => Holder::Guarded,
        },
        other => other,
    }
}

fn same_tree(a: &Path, b: &Path) -> bool {
    let norm = |p: &Path| plain_path(&p.canonicalize().unwrap_or_else(|_| p.to_path_buf()))
        .to_string_lossy()
        .to_lowercase();
    norm(a) == norm(b)
}

// Само правило (имя, сужение, слова расписки) — общее с оболочкой, см.
// common/firewall_rule.rs.
include!("../../common/firewall_rule.rs");
// Штамп журналов и случайные байты — общие с оболочкой и установщиком.
include!("../../common/stamp.rs");
include!("../../common/random_hex.rs");
// Список агентов установки — общий с оболочкой: служба поднимает ВСЕХ, кого
// оболочка показывает в трее, и берёт оттуда же порт по умолчанию.
include!("../../common/agents.rs");

/// Правило брандмауэра для трубы, когда разрешён телефон.
///
/// У окна это делает кнопка «Показать QR» (shell/main.rs::firewall_allow), но
/// служба живёт в Session 0: штатный запрос Windows «Разрешить доступ?» там
/// показать некому, и Windows по умолчанию заводит БЛОКИРУЮЩЕЕ правило. Блок
/// сильнее разрешения, поэтому сначала снимаем всё, что уже есть на эту
/// программу и порт, и только потом ставим своё.
fn firewall_ensure(python: &Path, port: u16, log: &mut Log) {
    let exe = plain_path(python);
    // Правило именуем программой только если знаем её путь: при "python" из
    // PATH сузить правило не по чему, и оно ставится на один порт.
    let program: Option<String> = if exe.is_absolute() && exe.exists() {
        Some(exe.display().to_string())
    } else {
        None
    };
    // Имя — то же, что у окна, и правило то же: см. common/firewall_rule.rs.
    // Совпадение имён не случайно (netsh требует уникальности), но раньше
    // означало, что служба меняет суженное правило окна на открытое.
    let name = firewall_rule_title(SERVICE_NAME, port);
    let netsh = |args: &[String]| -> Option<std::process::Output> {
        // Полный путь, а не голое имя: процесс службы — LocalSystem, и его
        // папка приложения есть папка установки (см. sys_exe).
        let mut cmd = Command::new(sys_exe("netsh.exe"));
        cmd.args(args);
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        cmd.output().ok()
    };
    let s = |v: &str| v.to_string();
    // Удаление несуществующего правила возвращает ненулевой код — это норма.
    netsh(&[s("advfirewall"), s("firewall"), s("delete"), s("rule"), format!("name={name}")]);
    let mut wipe = vec![
        s("advfirewall"), s("firewall"), s("delete"), s("rule"), s("name=all"), s("dir=in"),
        s("protocol=TCP"), format!("localport={port}"),
    ];
    let add = firewall_add_args(&name, port, program.as_deref());
    if let Some(program) = &program {
        wipe.push(format!("program={program}"));
        // Снос по программе и порту — только когда программу знаем: иначе
        // снесли бы на этом порту и чужие правила.
        netsh(&wipe);
    }
    let added = netsh(&add);
    match added {
        Some(out) if out.status.success() => log.line(&format!(
            "брандмауэр: правило «{name}» ({}) — {FIREWALL_SCOPE_HUMAN}",
            program.as_deref().unwrap_or("любая программа")
        )),
        // netsh отвечает в OEM-кодировке: без console_text причина отказа
        // приезжала в журнал сплошными «□».
        Some(out) => log.line(&format!(
            "брандмауэр: правило не поставилось — {}",
            console_text(&out.stdout)
        )),
        None => log.line("брандмауэр: netsh не запустился"),
    }
}

/// Роль ребёнка выражена типом, а не подписью из журнала: раньше труба
/// узнавалась сравнением с русской строкой «труба», и переименование подписи
/// молча оставило бы телефон без сети.
#[derive(PartialEq, Clone, Copy)]
enum Role {
    Trube,
    Runner,
}

struct Kid {
    role: Role,
    label: &'static str,
    script: PathBuf,
    args: Vec<String>,
    child: Option<Child>,
    started: Option<Instant>,
    not_before: Instant,
    backoff: u64,
    /// Чей это ребёнок. С 11.09 в установке может быть несколько агентов, и
    /// служба поднимает пару КАЖДОМУ: дерево, порт и секрет у них свои.
    agent: String,
    /// Имя агента словами владельца — для строк журнала службы.
    whose: String,
    python: PathBuf,
    tree: PathBuf,
    token: String,
    port: u16,
    phone: bool,
    /// Чей конфиг читать ребёнку. ⚠ Раньше канал не получал его вовсе, а
    /// `readers.config_path()` ищет файл от `__file__` — код же у всех агентов
    /// установки общий. Значит канал второго агента читал и правил конфиг ПЕРВОГО.
    /// Шов (`HELENE_CONFIG`) был и стоял первым в `config_path()`; его просто никто
    /// не ставил. Оболочка (`shell`) закрыта той же правкой — головы две, дефект был
    /// один.
    config: PathBuf,
}

impl Kid {
    fn host(&self) -> &'static str {
        // Труба слушает Wi-Fi только если разрешён телефон; руннер — всегда петля.
        match self.role {
            Role::Trube if self.phone => "0.0.0.0",
            _ => "127.0.0.1",
        }
    }

    /// Как назвать ребёнка в журнале: у единственного агента — как раньше
    /// («канал», «руннер»), у соседей — с именем, иначе две пары строк в одном
    /// файле неразличимы.
    fn said(&self) -> String {
        if self.agent == BASE_AGENT_ID {
            self.label.to_string()
        } else {
            format!("{} · {}", self.whose, self.label)
        }
    }
    /// Следующая пауза: 5 → 10 → 20 → 40 → 60 с. Раньше это получалось
    /// побочным эффектом `clamp` от нуля.
    fn back_off(&mut self, now: Instant) {
        self.backoff = if self.backoff == 0 { 5 } else { (self.backoff * 2).min(60) };
        self.not_before = now + Duration::from_secs(self.backoff);
    }
}

/// Супервизор пары детей. Работает В ТОЙ ЖЕ учётной записи, что и вызвавший его
/// процесс: `session-host` (сессия владельца), `foreground` (консоль) и —
/// только по явной галочке `service.session0` — сама служба.
///
/// `firewall` — можно ли трогать брандмауэр. Правило требует прав
/// администратора: под службой оно ставится (это её работа), а из сессии
/// владельца netsh ответил бы отказом, и в журнал каждую загрузку ехала бы
/// строка про «правило не поставилось», которую владельцу нечем починить.
fn supervise(
    plan: &Plan,
    stop: Arc<AtomicBool>,
    log: &mut Log,
    firewall: bool,
    stopping: &mut dyn FnMut(),
) {
    let now = Instant::now();
    // Секрет трубы заводится до подъёма детей: он уходит им в окружение, и его
    // же читает окно, чтобы говорить с харнессом службы. У каждого агента он
    // свой и лежит в его дереве.
    let token = ensure_desk_token(&plan.tree);
    if token.is_empty() {
        log.line(
            "секрет канала не завёлся — канал останется открытым любому процессу этой машины",
        );
    }
    let mut kids: Vec<Kid> = vec![Kid {
        role: Role::Trube,
        label: "канал",
        script: plan.app.clone(),
        args: vec![plan.port.to_string()],
        child: None,
        started: None,
        not_before: now,
        backoff: 0,
        agent: BASE_AGENT_ID.to_string(),
        whose: String::new(),
        python: plan.python.clone(),
        tree: plan.tree.clone(),
        token: token.clone(),
        port: plan.port,
        phone: plan.phone,
        config: plan.config.clone(),
    }];
    if let Some(runner) = &plan.runner {
        kids.push(Kid {
            role: Role::Runner,
            label: "руннер",
            script: runner.clone(),
            args: vec!["--config".into(), plan.config.to_string_lossy().into_owned()],
            child: None,
            started: None,
            not_before: now,
            backoff: 0,
            agent: BASE_AGENT_ID.to_string(),
            whose: String::new(),
            python: plan.python.clone(),
            tree: plan.tree.clone(),
            token: token.clone(),
            port: plan.port,
            phone: false,
            config: plan.config.clone(),
        });
    }
    // Соседи по установке (`agents/<id>/helene.json`). Служба поднимает их так
    // же, как окно: канал — всегда, руннер — когда мозг настроен. Иначе
    // владелец, живущий без окна, получал бы второго агента только по
    // нажатию — то есть не получал бы вовсе.
    let install = plan.config.parent().unwrap_or(Path::new(".")).to_path_buf();
    for a in raisable(&install).into_iter().filter(|a| !a.base) {
        let cfg = agent_config(&a.config);
        if cfg.get("mode").and_then(|v| v.as_str()).unwrap_or("local") != "local" {
            continue;
        }
        let their_token = ensure_desk_token(&a.tree);
        let python = match cfg.get("python").and_then(|v| v.as_str()) {
            Some(raw) if raw != "python" => resolve(&a.dir, raw),
            _ => plan.python.clone(),
        };
        let app = resolve(&a.dir, cfg.get("app").and_then(|v| v.as_str()).unwrap_or("deskapp.py"));
        let phone = cfg
            .get("phone")
            .and_then(|p| p.get("enabled"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        kids.push(Kid {
            role: Role::Trube,
            label: "канал",
            script: app,
            args: vec![a.port.to_string()],
            child: None,
            started: None,
            not_before: now,
            backoff: 0,
            agent: a.id.clone(),
            whose: a.name.clone(),
            python: python.clone(),
            tree: a.tree.clone(),
            token: their_token.clone(),
            port: a.port,
            phone,
            config: a.config.clone(),
        });
        let ready = !cfg
            .get("model")
            .and_then(|m| m.get("key"))
            .and_then(|v| v.as_str())
            .map(|k| k.trim().is_empty())
            .unwrap_or(true)
            || cfg.get("setup_complete").and_then(|v| v.as_bool()).unwrap_or(false);
        match cfg.get("runner").and_then(|v| v.as_str()) {
            Some(runner) if ready => kids.push(Kid {
                role: Role::Runner,
                label: "руннер",
                script: resolve(&a.dir, runner),
                args: vec!["--config".into(), a.config.to_string_lossy().into_owned()],
                child: None,
                started: None,
                not_before: now,
                backoff: 0,
                agent: a.id.clone(),
                whose: a.name.clone(),
                python,
                tree: a.tree.clone(),
                token: their_token,
                port: a.port,
                phone: false,
                config: a.config.clone(),
            }),
            _ => log.line(&format!(
                "{}: мозг не настроен — поднимаю только канал, впиши ключ в окне этого агента",
                a.name
            )),
        }
    }
    log.line(&format!(
        "служба: python={} · дерево={} · детей={} · порт={} · телефон={} · агентов={}",
        plan.python.display(),
        plan.tree.display(),
        kids.len(),
        plan.port,
        if plan.phone { "да" } else { "нет" },
        kids.iter().map(|k| k.agent.clone()).collect::<std::collections::BTreeSet<_>>().len()
    ));
    if let Some(warn) = user_writable_warning() {
        log.line(&warn);
    }
    if plan.phone && firewall {
        firewall_ensure(&plan.python, plan.port, log);
    }

    let mut relay: Option<Child> = None;
    let mut relay_not_before = Instant::now();
    let mut relay_backoff: u64 = 0;
    // Реле, которого нет в поставке, раньше писало «не поднялось» вечно.
    let mut relay_enabled = plan.relay_enabled;
    let mut port_checked: Option<Instant> = None;
    // Агенты, чей порт держит не наша труба, — по прошлой проверке.
    let mut port_busy_ids: Vec<String> = Vec::new();
    let mut port_noted: Option<Instant> = None;

    while !stop.load(Ordering::Relaxed) {
        let now = Instant::now();
        for kid in kids.iter_mut() {
            let Some(child) = kid.child.as_mut() else { continue };
            match child.try_wait() {
                Ok(Some(status)) => {
                    // Ребёнок, проживший больше двух минут, «по кругу» не падает:
                    // счётчик паузы сбрасывается. Иначе после пяти падений за всю
                    // жизнь службы пауза навсегда оставалась 60 с.
                    if kid.started.is_some_and(|t| now.duration_since(t) > Duration::from_secs(120)) {
                        kid.backoff = 0;
                    }
                    let _ = child.wait();
                    kid.child = None;
                    kid.started = None;
                    kid.back_off(now);
                    log.line(&format!(
                        "{}: завершился ({status}) — подниму через {} c",
                        kid.said(),
                        kid.backoff
                    ));
                }
                Ok(None) => {}
                // Ошибка чтения статуса — НЕ смерть. Раньше `.unwrap_or(true)`
                // считал её смертью, служба поднимала второго ребёнка поверх
                // живого, а старый Child дропался без wait() и становился
                // неуправляемым навсегда. У окна эта строка написана безопасно.
                Err(_) => {}
            }
        }

        // Замок один на пару детей — порт трубы. Пока его держит не наш ребёнок
        // (открытое окно владельца, прежняя копия, посторонняя программа),
        // служба не поднимает эту пару: иначе её deskapp вечно падал бы на
        // WinError 10048, а руннер стал бы ВТОРЫМ агентом на том же дереве —
        // два хода, две записи в window.jsonl, два рождения. Окно эту проверку
        // делает с самого начала (shell/main.rs::spawn_local), служба — нет.
        //
        // ⚠ Считается ПО АГЕНТУ: у каждого свой порт и своё дерево, и занятый
        // порт одного не имеет права держать взаперти остальных.
        if port_checked.is_none_or(|t| now.duration_since(t) >= Duration::from_secs(3)) {
            port_checked = Some(now);
            let ids: Vec<String> = kids
                .iter()
                .map(|k| k.agent.clone())
                .collect::<std::collections::BTreeSet<_>>()
                .into_iter()
                .collect();
            let mut busy = Vec::new();
            let mut said_now = false;
            for id in &ids {
                let hold = kids
                    .iter()
                    .any(|k| &k.agent == id && k.role == Role::Trube && k.child.is_some());
                if hold {
                    continue;
                }
                let Some(any) = kids.iter().find(|k| &k.agent == id) else { continue };
                if !harness_alive(any.port) {
                    continue;
                }
                busy.push(id.clone());
                if port_noted.is_none_or(|t| now.duration_since(t) >= Duration::from_secs(60)) {
                    let whose = match harness_holder(any.port, &any.token) {
                        Holder::Tree(t) if same_tree(&t, &any.tree) => {
                            "то же дерево — похоже, открыто окно; детей не поднимаю".to_string()
                        }
                        Holder::Tree(t) => format!("ЧУЖОЕ дерево {} — детей не поднимаю", t.display()),
                        // Четвёртый случай: харнесс под ключом, которого у нас
                        // нет. Раньше это писалось как «это не харнесс» —
                        // ложное обвинение своей же трубе под замком.
                        Holder::Guarded => {
                            "харнесс под ключом, которого у меня нет — детей не поднимаю"
                                .to_string()
                        }
                        Holder::Silent => "это не харнесс — детей не поднимаю".to_string(),
                    };
                    log.line(&format!("{}: порт {} занят: {whose}", any.said(), any.port));
                    said_now = true;
                }
            }
            if said_now {
                port_noted = Some(now);
            }
            port_busy_ids = busy;
        }
        // Порт держит не наша труба — значит харнесс на этом дереве сейчас не
        // мы. Руннер ЭТОГО агента гасим: два руннера на одном дереве — это два
        // агента, чинящих один и тот же прерванный ход.
        for kid in kids
            .iter_mut()
            .filter(|k| k.role != Role::Trube && port_busy_ids.contains(&k.agent))
        {
            if let Some(child) = &mut kid.child {
                let _ = child.kill();
                let _ = child.wait();
                kid.child = None;
                kid.started = None;
                let (said, port) = (kid.said(), kid.port);
                log.line(&format!("{said}: остановлен — порт {port} держит другой харнесс"));
            }
        }

        for kid in kids.iter_mut() {
            if kid.child.is_some() || now < kid.not_before || port_busy_ids.contains(&kid.agent) {
                continue;
            }
            let host = kid.host();
            let python = kid.python.clone();
            let tree = kid.tree.clone();
            let token = kid.token.clone();
            match spawn_child(&python, &kid.script, &kid.args, &tree, host, &token, &kid.config) {
                Ok(child) => {
                    kid.child = Some(child);
                    kid.started = Some(now);
                    // Паузу здесь НЕ сбрасываем: она обнуляется только тем, что
                    // ребёнок прожил больше двух минут (см. выше). Сброс на
                    // подъёме держал бы паузу вечно на пяти секундах.
                    log.line(&format!("{}: поднят ({})", kid.said(), kid.script.display()));
                }
                Err(err) => {
                    kid.back_off(now);
                    log.line(&format!(
                        "{}: не поднялся ({}): {err} — ещё попытка через {} c",
                        kid.said(),
                        kid.script.display(),
                        kid.backoff
                    ));
                }
            }
        }

        if relay_enabled {
            let dead = match &mut relay {
                Some(child) => match child.try_wait() {
                    Ok(Some(_)) => true,
                    Ok(None) => false,
                    Err(_) => false,
                },
                None => true,
            };
            if dead && Instant::now() >= relay_not_before {
                if let Some(child) = &mut relay {
                    let _ = child.wait();
                    log.line(&format!("реле: умерло — перезапускаю (пауза {relay_backoff} c)"));
                }
                relay = None;
                match spawn_relay(plan) {
                    Ok(child) => relay = Some(child),
                    Err(err) => {
                        // Нет exe — это навсегда: говорим один раз и больше не
                        // возвращаемся, вместо строки в журнал каждую минуту.
                        relay_enabled = false;
                        log.line(&format!("реле: {err} — больше не пробую до перезапуска службы"));
                    }
                }
                relay_backoff = if relay_backoff == 0 { 5 } else { (relay_backoff * 2).min(60) };
                relay_not_before = Instant::now() + Duration::from_secs(relay_backoff);
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }

    // SCM должен услышать StopPending ДО того, как мы начнём гасить детей:
    // всё это время статус для него оставался Running с wait_hint=0, и он был
    // вправе отстрелить службу по таймауту.
    stopping();
    log.line("служба: остановка — гашу детей");
    // Сначала job: он снимает и внуков (процесс агента, питон реле, процессы
    // песочницы), которые раньше переживали `sc stop` и оставались под SYSTEM.
    kill_all_descendants();
    for kid in kids.iter_mut() {
        if let Some(child) = &mut kid.child {
            let _ = child.kill();
            let _ = child.wait();
        }
    }
    if let Some(child) = &mut relay {
        let _ = child.kill();
        let _ = child.wait();
    }
    log.line("служба: остановлена");
}

/// Встроенное реле подписки. Та же семантика, что у оболочки
/// (shell/main.rs::spawn_relay): реле живёт в <дерево>/relay, конфигурируется
/// окружением, консоли не имеет.
fn spawn_relay(plan: &Plan) -> Result<Child, String> {
    let base = plan
        .config
        .parent()
        .ok_or_else(|| "не понял, где лежит конфиг".to_string())?
        .to_path_buf();
    let exe = base.join("helene-relay.exe");
    if !exe.exists() {
        return Err(format!("в этой поставке нет {}", exe.display()));
    }
    let home = plan.tree.join("relay");
    let _ = std::fs::create_dir_all(&home);
    let mut cmd = Command::new(&exe);
    cmd.arg("serve")
        .current_dir(&home)
        .env("RELAY_PORT", plan.relay_port.to_string())
        .env("RELAY_LOCAL", "1")
        .env("RELAY_INSTRUCTIONS", &plan.relay_instructions)
        .env("RELAY_LOG_DIR", home.join("logs"));
    if !plan.relay_key.trim().is_empty() {
        // Тот же контракт, что у оболочки: ключ мозга обязателен Bearer-ом.
        cmd.env("RELAY_API_KEY", &plan.relay_key);
    }
    let python = base.join("runtime").join("python.exe");
    if python.exists() {
        cmd.env("RELAY_PYTHON", &python);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    match cmd.spawn() {
        Ok(child) => {
            adopt(&child);
            Ok(child)
        }
        Err(err) => Err(format!("не поднялось: {err}")),
    }
}

/// Родные утилиты Windows (schtasks, icacls, netsh) отвечают в OEM-кодировке —
/// cp866 на русской системе, — а не в UTF-8. Без перевода причина отказа
/// приезжала в `service.log` сплошными «□», и владелец, для которого этот файл
/// и написан, не мог прочитать ни слова. Близнец живёт в установщике
/// (`setup/src/install.rs::console_text`); свести их в один файл нельзя —
/// `common/` включается ещё и в оболочку, у которой своя таблица.
fn console_text(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.trim().to_string(),
        Err(_) => bytes
            .iter()
            .map(|b| decode_cp866(*b))
            .collect::<String>()
            .trim()
            .to_string(),
    }
}

fn decode_cp866(b: u8) -> char {
    if b < 0x80 {
        return b as char;
    }
    const HIGH: &str = concat!(
        "АБВГДЕЖЗИЙКЛМНОП",
        "РСТУФХЦЧШЩЪЫЬЭЮЯ",
        "абвгдежзийклмноп",
        "░▒▓│┤╡╢╖╕╣║╗╝╜╛┐",
        "└┴┬├─┼╞╟╚╔╩╦╠═╬╧",
        "╨╤╥╙╘╒╓╫╪┘┌█▄▌▐▀",
        "рстуфхцчшщъыьэюя",
        "ЁёЄєЇїЎў°∙·√№¤■\u{00A0}",
    );
    HIGH.chars().nth((b - 0x80) as usize).unwrap_or('\u{fffd}')
}

// ============================================================ сессия (A1)
//
// ЗАЧЕМ ЭТО ЕСТЬ. Служба живёт под LocalSystem в нулевой сессии. Пока она
// поднимала питона прямым `Command::spawn()`, ребёнок наследовал её права:
// агент де-факто работал СИСТЕМОЙ, хотя владелец включал службу ради жизни без
// окна. Плюс из Session 0 не виден рабочий стол — UIA и всё интерактивное там
// мертвы по построению Windows (у Праксис тело в нулевой сессии прямо
// отказывается работать).
//
// Поэтому служба ничего не поднимает сама. Она просит планировщик запустить
// задачу, заведённую ПОД ВЛАДЕЛЬЦЕМ и БЕЗ повышения; задача поднимает этот же
// exe в режиме `session-host`, и уже он супервизит пару детей — теми же
// правилами, что окно. Образец — `praxis-system-router::launch_session_task`.

/// Имя задачи планировщика. Папку `Helene\` планировщик заводит сам.
/// Меняется вместе с установщиком: старая задача от прежней установки после
/// переименования осталась бы висеть и поднимать старый exe.
const SESSION_TASK: &str = "Helene\\session-host";

/// Аргументы `schtasks /Create`. Чистая функция — её накрывает тест, потому что
/// цена ошибки здесь не «не запустилось», а «запустилось не с теми правами».
///
/// * `/RU <владелец> /IT` — задача идёт ИНТЕРАКТИВНЫМ токеном владельца. Пароль
///   не нужен, а без входа в систему она просто не стартует — это и есть
///   честная привязка к сессии.
/// * `/RL LIMITED` — без повышения. Ровно это отличает новый порядок от
///   старого: права администратора агент теперь просит отдельно (UAC или
///   брокер), а не получает молча вместе со службой.
/// * `/SC ONLOGON` — второй, независимый повод подняться: при входе владельца
///   харнесс стартует сам, даже если служба не разглядела сессию (так бывает
///   при входе по RDP — консольной сессии там нет).
/// * `/F` — перезапись. Переустановка и обновление не плодят задач.
///
/// `/TR` собирается ОДНОЙ строкой с кавычками внутри: schtasks разбирает её
/// сам, и путь с пробелом (`C:\Users\Иван Петров\…`) без кавычек разъехался бы
/// на два аргумента.
fn session_task_create_args(exe: &Path, config: &Path, user: &str) -> Vec<String> {
    vec![
        "/Create".into(),
        "/F".into(),
        "/TN".into(),
        SESSION_TASK.into(),
        "/SC".into(),
        "ONLOGON".into(),
        "/RU".into(),
        user.into(),
        "/IT".into(),
        "/RL".into(),
        "LIMITED".into(),
        "/TR".into(),
        format!(
            "\"{}\" session-host --config \"{}\"",
            plain_path(exe).display(),
            plain_path(config).display()
        ),
    ]
}

fn session_task_run_args() -> Vec<String> {
    vec!["/Run".into(), "/TN".into(), SESSION_TASK.into()]
}

/// Остановка задачи гасит ТОЛЬКО то, что запустили мы. Харнесс, поднятый окном
/// владельца, планировщику не принадлежит и остаётся жить — иначе `sc stop`
/// убивал бы агента прямо у человека под руками.
fn session_task_end_args() -> Vec<String> {
    vec!["/End".into(), "/TN".into(), SESSION_TASK.into()]
}

fn session_task_delete_args() -> Vec<String> {
    vec!["/Delete".into(), "/F".into(), "/TN".into(), SESSION_TASK.into()]
}

fn session_task_query_args() -> Vec<String> {
    vec!["/Query".into(), "/TN".into(), SESSION_TASK.into()]
}

/// Один вызов schtasks. Полным путём из `%SystemRoot%` — по той же причине, что
/// и netsh (см. sys_exe): папка процесса службы есть папка установки.
fn schtasks(args: &[String]) -> Result<(bool, String), String> {
    let mut cmd = Command::new(sys_exe("schtasks.exe"));
    cmd.args(args);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    match cmd.output() {
        Ok(out) => {
            let mut said = console_text(&out.stderr);
            if said.is_empty() {
                said = console_text(&out.stdout);
            }
            Ok((out.status.success(), said))
        }
        Err(err) => Err(format!("schtasks не запустился: {err}")),
    }
}

/// Кто сейчас за компьютером. `None` — НИКТО НЕ ВОШЁЛ, и это состояние, а не
/// ошибка: харнесс поднимать некуда, ждём входа.
///
/// Возвращается `DOMAIN\User` — в таком виде его ждёт `schtasks /RU`.
///
/// ⚠ Граница честности: спрашивается КОНСОЛЬНАЯ сессия. Вход по RDP она не
/// видит, и служба в этом случае считает, что никто не вошёл. Харнесс всё равно
/// поднимется — задачу триггерит ещё и сам вход (`/SC ONLOGON`).
#[cfg(windows)]
fn active_session_user() -> Option<String> {
    use windows_sys::Win32::System::RemoteDesktop::{
        WTSDomainName, WTSGetActiveConsoleSessionId, WTSUserName,
    };
    let id = unsafe { WTSGetActiveConsoleSessionId() };
    // 0xFFFFFFFF — консоли сейчас нет вовсе (например, идёт переключение
    // сессий). Не «ошибка», а «спроси позже».
    if id == u32::MAX {
        return None;
    }
    let user = wts_text(id, WTSUserName)?;
    let user = user.trim().to_string();
    // Пустое имя — экран входа: сессия есть, человека в ней нет.
    if user.is_empty() {
        return None;
    }
    let domain = wts_text(id, WTSDomainName).unwrap_or_default();
    let domain = domain.trim().to_string();
    Some(if domain.is_empty() { user } else { format!("{domain}\\{user}") })
}

#[cfg(windows)]
fn wts_text(session: u32, class: i32) -> Option<String> {
    use windows_sys::Win32::System::RemoteDesktop::{WTSFreeMemory, WTSQuerySessionInformationW};
    let mut buf: windows_sys::core::PWSTR = std::ptr::null_mut();
    let mut len: u32 = 0;
    // Первый аргумент — WTS_CURRENT_SERVER_HANDLE, то есть эта же машина.
    let ok =
        unsafe { WTSQuerySessionInformationW(std::ptr::null_mut(), session, class, &mut buf, &mut len) };
    if ok == 0 || buf.is_null() {
        return None;
    }
    let mut n = 0usize;
    while unsafe { *buf.add(n) } != 0 {
        n += 1;
    }
    let text = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(buf, n) });
    unsafe { WTSFreeMemory(buf as *mut std::ffi::c_void) };
    Some(text)
}

#[cfg(not(windows))]
fn active_session_user() -> Option<String> {
    None
}

/// Отпустить консоль. Планировщик поднимает консольное приложение с новым
/// окном, и оно осталось бы висеть на рабочем столе владельца всю жизнь
/// харнесса. FreeConsole закрывает его, потому что мы — единственный, кто к
/// этой консоли подключён; вывод и так идёт в service.log.
///
/// Флаг ставим ПЕРВЫМ: после FreeConsole печать на экран паникует (см.
/// CONSOLE_GONE), а гонка здесь стоила бы харнесса.
fn drop_console() {
    CONSOLE_GONE.store(true, Ordering::Relaxed);
    #[cfg(windows)]
    unsafe {
        windows_sys::Win32::System::Console::FreeConsole()
    };
}

/// Завести задачу планировщика. Зовётся из `helene-svc install` (то есть уже
/// из-под администратора) и отдельной командой `session-task install` — её
/// может позвать установщик или обновление, ничего не переустанавливая.
///
/// `user`: `--user DOMAIN\User`, иначе — тот, кто сейчас за консолью. Аргумент
/// нужен потому, что установку зовут ПОВЫШЕННЫМ процессом, а у пользователя с
/// ограниченной учёткой повышение идёт от ДРУГОГО аккаунта (администратора):
/// имя того процесса — не имя владельца.
fn session_task_install(config: &Path) -> Result<String, String> {
    let exe = std::env::current_exe().map_err(|e| format!("не понял, где я сам: {e}"))?;
    let user = match arg_after("--user") {
        Some(u) if !u.trim().is_empty() => u.trim().to_string(),
        _ => active_session_user().ok_or_else(|| {
            "не понял, под кем заводить задачу: за консолью никого нет. \
             Позови с --user DOMAIN\\User"
                .to_string()
        })?,
    };
    let args = session_task_create_args(&exe, config, &user);
    match schtasks(&args)? {
        (true, _) => Ok(user),
        (false, said) => Err(format!("задача {SESSION_TASK} не завелась: {said}")),
    }
}

fn session_task_remove() -> Result<(), String> {
    match schtasks(&session_task_delete_args())? {
        (true, _) => Ok(()),
        (false, said) => {
            // Отказ может значить всего лишь «задачи и не было», а это не провал
            // снятия. Спрашиваем планировщик напрямую вместо разбора текста
            // ошибки: текст локализован, и на русской системе английского
            // «cannot find» не встретится вовсе.
            match schtasks(&session_task_query_args()) {
                Ok((false, _)) => Ok(()),
                _ => Err(format!("задача {SESSION_TASK} не снялась: {said}")),
            }
        }
    }
}

// ================================================= права на папку (A4)
//
// ЗАЧЕМ. Служба под LocalSystem запускает exe из папки установки. Пока в эту
// папку пишет обычный пользователь, любой код от его имени получает СИСТЕМУ на
// следующей загрузке — подменил файл, дождался старта службы. Это не выдумка:
// дыра нашлась живой в остатке от прежней установки продукта (`Get-Acl` на
// `%LocalAppData%\Programs\Helene` показывал у пользователя Modify).
//
// ЧТО ЗАКРЫВАЕМ: `*.exe` и `*.dll` в корне, `runtime\`, `app\`. Пользователю —
// читать и выполнять, запись — SYSTEM и администраторам.
// ЧТО НЕ ТРОГАЕМ: `data\` (дом агента) и `tree\` (его собственный код, который
// он правит по замыслу), а также сам корень — там лежит `helene.json`, который
// продукт переписывает при каждой правке настроек. Запереть дом агента значило
// бы сломать саму идею песочницы как свободы.

const SID_SYSTEM: &str = "S-1-5-18";
const SID_ADMINS: &str = "S-1-5-32-544";
const SID_USERS: &str = "S-1-5-32-545";
/// Прошедшие проверку подлинности. Выдаётся ВМЕСТЕ с «Пользователями» и только
/// на чтение: доменная учётка не всегда состоит в локальной группе
/// «Пользователи», а запереть владельца от его же агента — отказ хуже дыры.
const SID_AUTHENTICATED: &str = "S-1-5-11";

/// Что именно закрываем в этой папке установки. Порядок устойчивый (папки, потом
/// файлы по алфавиту) — чтобы журнал не плясал от загрузки к загрузке.
fn hardened_paths(root: &Path) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = ["runtime", "app"]
        .iter()
        .map(|n| root.join(n))
        .filter(|p| p.is_dir())
        .collect();
    let mut files: Vec<PathBuf> = std::fs::read_dir(root)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            // .dll рядом с .exe закрываем по той же причине: подложенная
            // библиотека грузится в процесс службы и получает те же права.
            p.is_file()
                && p.extension().is_some_and(|e| {
                    e.eq_ignore_ascii_case("exe") || e.eq_ignore_ascii_case("dll")
                })
        })
        .collect();
    files.sort();
    out.extend(files);
    out
}

/// Аргументы `icacls` на закрытие одного пути. Чистая функция — тест на неё
/// дешевле любого прогона, а ошибка в SID закрывает владельцу его же продукт.
///
/// SID, а не имена: на русской Windows группа зовётся «Пользователи», и
/// `icacls /grant Users:RX` там просто не находит адресата.
/// ⚠ ЗАМЕРЕНО ЖИВЬЁМ (04.09, русская Windows 11). `/inheritance:r` снимает
/// только УНАСЛЕДОВАННЫЕ разрешения. Явные — переживают его, и это здесь
/// несущее свойство: песочница (`localharness/fence.py::_grant`) выдаёт SID
/// своего AppContainer явный RX на `runtime\` и `app\` и ставит маркер
/// «уже выдано». Снеси мы её ACE (например, через `/reset` или `/setowner`),
/// песочница молча осталась бы без рантайма — маркер не дал бы ей выдать
/// заново. Проверено: чужой явный грант на месте и после выравнивания.
fn harden_args(path: &Path, dir: bool) -> Vec<String> {
    // (OI)(CI) — наследование на файлы и папки внутри. Для файла наследовать
    // нечему, и icacls на такой флаг ругается.
    let inh = if dir { "(OI)(CI)" } else { "" };
    vec![
        plain_path(path).display().to_string(),
        // Снимаем наследование от корня — иначе Modify пользователя приезжает
        // сверху и всё, что мы выдали, теряет смысл. Рекурсии (/T) нет:
        // наследуемые ACE система разносит по детям сама, а /T на runtime с
        // тысячами файлов — это минуты на каждой загрузке.
        "/inheritance:r".into(),
        "/grant:r".into(),
        format!("*{SID_SYSTEM}:{inh}F"),
        "/grant:r".into(),
        format!("*{SID_ADMINS}:{inh}F"),
        "/grant:r".into(),
        format!("*{SID_USERS}:{inh}RX"),
        "/grant:r".into(),
        format!("*{SID_AUTHENTICATED}:{inh}RX"),
    ]
}

/// Обратное действие: вернуть путь к наследованию от корня. Нужно обновлению —
/// установщик копирует файлы ОТ ИМЕНИ ВЛАДЕЛЬЦА, а закрытые `runtime\` и `app\`
/// он перезаписать не сможет. Без этой команды первое же обновление после
/// включения службы упёрлось бы в «отказано в доступе» без объяснений.
///
/// `/inheritance:e`, а НЕ `/reset`. Разница не косметическая:
///   * `/inheritance:e` возвращает наследование от корня (оттуда и приезжает
///     Modify владельца), не трогая явные разрешения — включая выданные
///     песочницей контейнеру;
///   * `/reset` заменил бы весь список наследуемым и снёс бы их вместе с нашими.
/// Проверено живьём: после `/inheritance:e` явный чужой грант на месте, а
/// Modify владельца вернулся унаследованным. Следующее выравнивание закрывает
/// всё обратно — пара строго обратима.
fn relax_args(path: &Path) -> Vec<String> {
    vec![plain_path(path).display().to_string(), "/inheritance:e".into()]
}

fn icacls(args: &[String]) -> Result<(bool, String), String> {
    let mut cmd = Command::new(sys_exe("icacls.exe"));
    cmd.args(args).arg("/Q");
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    match cmd.output() {
        Ok(out) => {
            let mut said = console_text(&out.stderr);
            if said.is_empty() {
                said = console_text(&out.stdout);
            }
            Ok((out.status.success(), said))
        }
        Err(err) => Err(format!("icacls не запустился: {err}")),
    }
}

/// Выключен ли UAC на этой машине (`EnableLUA=0`). `None` — не смогли узнать.
///
/// ⚠ ЗАМЕРЕНО ЖИВЬЁМ на машине владельца 04.09: `EnableLUA=0`, учётная запись
/// в администраторах. В таком сочетании выравнивание прав НЕ мешает переписать
/// exe: обычный, неповышенный процесс владельца уже несёт полный
/// административный токен, а администраторам мы даём запись намеренно — без
/// этого не встанет обновление и не снимется продукт. Молчать об этом нельзя:
/// «права выровнены» звучало бы как защита, которой на этой машине нет.
fn uac_disabled() -> Option<bool> {
    let mut cmd = Command::new(sys_exe("reg.exe"));
    cmd.args([
        "query",
        r"HKLM\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System",
        "/v",
        "EnableLUA",
    ]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let out = cmd.output().ok()?;
    if !out.status.success() {
        return None;
    }
    // «EnableLUA    REG_DWORD    0x0» — цифра одна и та же на любом языке.
    let text = console_text(&out.stdout);
    let line = text.lines().find(|l| l.contains("EnableLUA"))?;
    let value = line.split_whitespace().last()?;
    Some(value == "0x0")
}

/// Папка установки — та, где лежит helene.json. Проверка на `helene-svc.exe`
/// рядом не формальность: без неё `acl-align` на конфиге из дерева разработки
/// закрыл бы права на чужие папки.
fn install_root(config: &Path) -> Option<PathBuf> {
    let root = config.parent()?.to_path_buf();
    root.join("helene-svc.exe").is_file().then_some(root)
}

/// Выровнять права. Зовётся при КАЖДОМ старте службы, и это осознанно:
/// обновление продукта приносит новые файлы со свежими правами, и маркер
/// «уже сделано» тихо оставил бы их открытыми. Само действие идемпотентно —
/// `/inheritance:r /grant:r` задаёт итоговое состояние, а не добавляет к нему.
///
/// ⚠ ЧЕГО ЭТО НЕ ЗАКРЫВАЕТ, и врать здесь нельзя. Папка установки лежит в
/// профиле владельца, значит владелец — её хозяин, а хозяин объекта всегда
/// может переписать права обратно (WRITE_DAC у владельца неотнимаем) и удалить
/// файл правами на РОДИТЕЛЬСКУЮ папку. Выравнивание закрывает обычную подмену
/// файла чужой программой; полностью дыру закрывает только установка в папку,
/// куда обычный пользователь не пишет вовсе — об этом продукт говорит отдельно
/// (user_writable_warning).
fn align_acl(config: &Path, log: &mut Log) {
    let Some(root) = install_root(config) else {
        log.line(&format!(
            "права на папку: рядом с {} нет helene-svc.exe — это не папка установки, ничего не трогаю",
            config.display()
        ));
        return;
    };
    let started = Instant::now();
    let paths = hardened_paths(&root);
    if paths.is_empty() {
        log.line(&format!("права на папку: в {} нечего закрывать", root.display()));
        return;
    }
    let mut failed: Vec<String> = Vec::new();
    for path in &paths {
        let dir = path.is_dir();
        match icacls(&harden_args(path, dir)) {
            Ok((true, _)) => {}
            Ok((false, said)) => failed.push(format!("{}: {said}", path.display())),
            Err(err) => failed.push(format!("{}: {err}", path.display())),
        }
    }
    if failed.is_empty() {
        log.line(&format!(
            "права на папку выровнены ({} мест за {} мс): пользователю — читать и выполнять, запись — системе и администраторам; data\\ и tree\\ остались его",
            paths.len(),
            started.elapsed().as_millis()
        ));
        // См. uac_disabled(): при выключенном UAC у администратора и обычный
        // процесс несёт полный токен, а администраторам мы даём запись
        // намеренно. Значит выравнивание на такой машине почти ничего не
        // закрывает — и сказать об этом обязаны мы, а не случай.
        if uac_disabled() == Some(true) {
            log.line(
                "…но на этой машине выключен UAC (EnableLUA=0). Если твоя учётная запись — \
                 администраторская, любая программа от твоего имени всё равно перепишет файлы \
                 продукта: администраторам запись нужна для обновления, и отнять её нельзя. \
                 Выравнивание закрывает папку от ОБЫЧНЫХ учёток, не от твоей.",
            );
        }
    } else {
        // Молчать нельзя: невыровненные права — это открытая дорога к правам
        // СИСТЕМЫ, и владелец должен узнать об этом из журнала, а не потом.
        log.line(&format!(
            "права на папку выровнены НЕ ВЕЗДЕ ({} из {}): {}",
            paths.len() - failed.len(),
            paths.len(),
            failed.join(" · ")
        ));
    }
}

/// Куда писать журнал руке, которая трогает права. План читаем, если он
/// читается: журнал прав должен лежать в одной истории с журналом службы.
/// Не прочитался (снятие идёт по сломанному конфигу — обычное дело) — рядом с
/// конфигом, там же, где `early_line` держит ранние отказы.
fn acl_log_dir(config: &Path) -> PathBuf {
    load_plan(config)
        .map(|p| p.tree)
        .unwrap_or_else(|_| config.parent().unwrap_or(Path::new(".")).join("data"))
}

/// Открыть обратно. Две руки зовут это место:
///   * команда `acl-relax` из-под администратора — перед обновлением вручную;
///   * `uninstall()` — сразу после удаления службы.
///
/// Второе — не удобство, а обязанность. Права закрывает служба (`align_acl` на
/// каждом старте), и открыть их обратно может только тот, у кого есть админ.
/// Служба снята — закрывать больше некому, а `runtime\` и `app\` остались
/// пользователю на чтение: он не поставит обновление, не перепишет ни файла и
/// не увидит ни слова о причине. Раньше это жило только строкой в
/// `ОБНОВЛЕНИЕ.md` — то есть у того, кто её прочитал.
fn relax_acl(config: &Path, log: &mut Log) {
    let Some(root) = install_root(config) else {
        log.line("права на папку: это не папка установки, ничего не трогаю");
        return;
    };
    let paths = hardened_paths(&root);
    let mut failed: Vec<String> = Vec::new();
    for path in &paths {
        match icacls(&relax_args(path)) {
            Ok((true, _)) => {}
            Ok((false, said)) => failed.push(format!("{}: {said}", path.display())),
            Err(err) => failed.push(format!("{}: {err}", path.display())),
        }
    }
    if failed.is_empty() {
        log.line(&format!(
            "права на папку открыты обратно ({} мест) — обновляй; следующий старт службы закроет снова",
            paths.len()
        ));
    } else {
        log.line(&format!("права на папку открыты не везде: {}", failed.join(" · ")));
    }
}

// ==================================================== брокер прав (A2, A3)
//
// ЗАЧЕМ ОН ЕСТЬ. Служба живёт под LocalSystem, а харнесс — в сессии владельца
// и без повышения (см. раздел «сессия»). Значит привилегированное действие —
// правило брандмауэра, служба, ветка реестра машины — агенту недоступно, и это
// правильно. Брокер даёт третий путь: агент ПРОСИТ выполнить одну задачу,
// служба выполняет её разово и отдаёт квитанцию, а владелец видит в журнале,
// что и зачем просили. Агент при этом никогда не «становится системой».
//
// ДВЕ ДВЕРИ, а не одна:
//   * вверх — `exec`: процесс правами СИСТЕМЫ. Заперт галочкой нулевой сессии
//     (`service.session0`), выключенной по умолчанию;
//   * вбок — `spawn_interactive`: процесс в сессии владельца, ЕГО правами. Это
//     не повышение, а наоборот: служба сидит в Session 0 и рабочего стола не
//     видит, а через эту дверь процесс появляется на настоящем рабочем столе.
//
// ТРИ ЗАМКА:
//   1. дескриптор трубы (SDDL): СИСТЕМА и владелец установки, больше никто;
//   2. токен из файла `memory\.state\broker-token` (права «СИСТЕМА,
//      администраторы, владелец»);
//   3. клиент обязан сидеть в АКТИВНОЙ пользовательской сессии — процесс из
//      Session 0 или из чужой сессии брокеру не клиент.
//
// ЧЕГО ЭТО НЕ ЗАКРЫВАЕТ, и врать нельзя: от самого агента брокер не закрыт и
// закрыт быть не может — он законный клиент, а его код исполняется внутри
// процесса-клиента. Поэтому галочка нулевой сессии выключена по умолчанию, а
// каждое обращение ложится в `broker.log` рядом с `service.log`.

// Протокол, имена, SDDL и клиентская сторона — общие с оболочкой, один текст
// на обе стороны трубы: см. common/broker.rs.
include!("../../common/broker.rs");

/// Первый экземпляр трубы заводится с `FILE_FLAG_FIRST_PIPE_INSTANCE`: если имя
/// уже занято, значит его занял НЕ мы, и служить на чужом канале нельзя ни
/// секунды. Флаг гасится ТОЛЬКО после удачного создания — иначе неудача первой
/// попытки молча разрешила бы подсесть вторым экземпляром к чужой трубе.
static BROKER_FIRST: AtomicBool = AtomicBool::new(true);

/// Привилегированные команды идут по одной. Не ради скорости: журнал владельца
/// должен читаться как последовательность, а две элевации разом он потом не
/// разберёт. Заодно это снимает гонку с наследованием дескрипторов — см.
/// broker_spawn_session().
static BROKER_GATE: std::sync::Mutex<()> = std::sync::Mutex::new(());

/// Счётчик для имён временных файлов вывода.
static BROKER_SEQ: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);

/// Журнал обращений к брокеру. Отдельный файл, а не `service.log`: владельцу
/// нужен список «кто, зачем и чем кончилось», не перемешанный с надзором за
/// детьми. Усечение — то же (LOG_MAX).
fn broker_note(tree: &Path, line: &str) {
    let path = broker_log_path(tree);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Some(mut f) = open_rolling(&path) {
        let _ = writeln!(f, "{} {}", now_stamp(), line);
    }
}

/// Права на файл токена: СИСТЕМА, администраторы и владелец установки. Файл
/// лежит в дереве данных, а дерево по построению открыто (это дом агента) —
/// значит замок здесь держит не наследование, а явный список.
///
/// Чистая функция: ошибка в SID тут означает либо «секрет читают все», либо
/// «владелец не может прочитать свой же токен», и то и другое дешевле поймать
/// тестом.
fn broker_token_acl_args(path: &Path, owner_sid: &str) -> Vec<String> {
    vec![
        plain_path(path).display().to_string(),
        "/inheritance:r".into(),
        "/grant:r".into(),
        format!("*{SID_SYSTEM}:F"),
        "/grant:r".into(),
        format!("*{SID_ADMINS}:F"),
        "/grant:r".into(),
        format!("*{}:F", owner_sid.trim()),
    ]
}

/// Завести (или прочитать) секрет брокера и сузить права на файл. Сужаем
/// только при заведении: `icacls` на каждом обращении — это лишний процесс на
/// каждую просьбу, а список прав от этого не меняется.
fn broker_ensure_token(tree: &Path, owner_sid: Option<&str>, log: &mut Log) -> String {
    let path = broker_token_path(tree);
    let fresh = read_token_file(&path).is_none();
    let token = ensure_token_file(&path);
    if token.is_empty() {
        log.line(&format!(
            "брокер: не смог завести секрет {} — канала не будет: без замка он открыт",
            path.display()
        ));
        return token;
    }
    if !fresh {
        return token;
    }
    match owner_sid.filter(|s| broker_sid_ok(s)) {
        Some(sid) => match icacls(&broker_token_acl_args(&path, sid)) {
            Ok((true, _)) => log.line(
                "брокер: секрет заведён; читать его могут СИСТЕМА, администраторы и владелец",
            ),
            // Молчать нельзя: незакрытый секрет — это открытая дверь брокера
            // для всякого, кто пишет в дерево.
            Ok((false, said)) => log.line(&format!(
                "брокер: секрет заведён, но права на файл НЕ сузились ({said}). \
                 Его прочитает любой, кто пишет в дерево"
            )),
            Err(err) => log.line(&format!("брокер: секрет заведён, но icacls не запустился: {err}")),
        },
        None => log.line(
            "брокер: секрет заведён, но владелец неизвестен — права на файл остались \
             унаследованными от дерева",
        ),
    }
    token
}

/// Сравнение секретов без ранней остановки. Разница с `==` здесь не
/// теоретическая: `==` на строках возвращается на первом же несовпавшем байте,
/// и по времени ответа секрет подбирается побайтно.
fn secret_eq(a: &str, b: &str) -> bool {
    let (a, b) = (a.as_bytes(), b.as_bytes());
    if a.len() != b.len() {
        return false;
    }
    let mut diff = 0u8;
    for i in 0..a.len() {
        diff |= a[i] ^ b[i];
    }
    diff == 0
}

/// Можно ли выполнять `exec` — то есть поднимать процесс правами СИСТЕМЫ.
///
/// Чистая функция, потому что это и есть главная развилка брокера: ошибка
/// здесь означает «агент молча получил права системы», а не «не сработало».
/// Можно ли ставить правило брандмауэра — то есть открыта ли УЗКАЯ дверь.
///
/// Отдельно от `broker_exec_allowed` нарочно: правило брандмауэра не требует
/// права поднимать любой процесс правами СИСТЕМЫ, и связывать их одной галочкой
/// значит предлагать владельцу выдать агенту всё ради телефона. Ровно это и
/// было, пока `service.firewall` не читал никто.
fn broker_firewall_allowed(firewall: bool) -> Result<(), String> {
    if firewall {
        return Ok(());
    }
    Err(
        "правило брандмауэра службе запрещено: галочка «служба ставит правило          брандмауэра» выключена в Настройках. Включи её — или включи телефон в          режиме без службы, там правило ставит окно с запросом прав у Windows."
            .into(),
    )
}

fn broker_exec_allowed(session0: bool) -> Result<(), String> {
    if session0 {
        return Ok(());
    }
    Err(
        "нулевая сессия выключена — брокер не поднимает процессы правами СИСТЕМЫ. \
         Это отдельная галочка в Настройках («разрешить агенту нулевую сессию»), и она \
         выключена по умолчанию: с ней агент работает правами системы, и слабая модель \
         может не понять, что делает. Для процесса в твоей сессии есть spawn_interactive."
            .into(),
    )
}

/// Из какой сессии пришёл клиент. `active` — активная консольная сессия
/// (`u32::MAX`, если её сейчас нет).
///
/// Третий замок брокера: труба видна СИСТЕМЕ, а СИСТЕМА — это ещё и всякая
/// служба на машине. Клиент обязан быть процессом ЖИВОГО ЧЕЛОВЕКА за этим
/// компьютером, а не соседней службой в нулевой сессии.
fn broker_client_ok(session: u32, active: u32) -> Result<(), String> {
    if session == 0 {
        return Err(
            "клиент сидит в нулевой сессии — это служба, а не человек за компьютером. \
             Брокер отвечает только процессам из сессии владельца"
                .into(),
        );
    }
    if active != u32::MAX && session != active {
        return Err(format!(
            "клиент в сессии {session}, а за компьютером сейчас сессия {active} — \
             отвечаю только активной"
        ));
    }
    Ok(())
}

/// Строка журнала об одном обращении. Одна строка — одно обращение: журнал
/// читает человек, и разъехавшийся по строкам отказ он не свяжет с просьбой.
fn broker_journal_line(ask: &BrokerAsk, receipt: &BrokerReceipt, pid: u32, session: u32) -> String {
    let outcome = if !receipt.ok {
        "ОТКАЗ".to_string()
    } else {
        match receipt.code {
            Some(code) => format!("код {code}"),
            None => "не дождался".to_string(),
        }
    };
    let note = if receipt.note.is_empty() {
        String::new()
    } else {
        format!(" · {}", receipt.note.replace('\n', " "))
    };
    format!(
        "{} · {outcome} за {} мс · зачем: {} · команда: {} · клиент: pid {pid}, сессия {session}{note}",
        ask.op.as_str(),
        receipt.ms,
        ask.why,
        broker_shown_command(&ask.cmd, &ask.args),
    )
}

/// Что вышло из одной команды. Не «успех», а всё, что видел брокер.
struct Ran {
    ok: bool,
    code: Option<i32>,
    pid: Option<u32>,
    out: String,
    err: String,
    ms: u64,
    note: String,
}

impl Ran {
    fn failed(note: &str, started: Instant) -> Ran {
        Ran {
            ok: false,
            code: None,
            pid: None,
            out: String::new(),
            err: String::new(),
            ms: started.elapsed().as_millis() as u64,
            note: note.to_string(),
        }
    }
}

/// Файл под вывод команды.
///
/// ⚠ ПОЧЕМУ ФАЙЛ, А НЕ КАНАЛ. Канал между родителем и ребёнком — это буфер на
/// несколько килобайт. Ребёнок, напечатавший больше, встаёт на записи и ждёт,
/// пока мы прочитаем, а мы ждём его завершения: взаимная блокировка, из
/// которой не выходит и таймаут (процесс жив и «работает»). Файл этой развилки
/// не имеет вовсе.
fn broker_temp(tag: &str) -> Option<(PathBuf, std::fs::File)> {
    let seq = BROKER_SEQ.fetch_add(1, Ordering::Relaxed);
    let path = std::env::temp_dir().join(format!(
        "helene-broker-{}-{seq}-{tag}.txt",
        std::process::id()
    ));
    let file = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(true)
        .open(&path)
        .ok()?;
    Some((path, file))
}

/// Прочитать и убрать за собой. Родные утилиты Windows отвечают в OEM-кодировке
/// — без console_text причина отказа приезжала бы в квитанцию сплошными «□».
fn broker_take(path: &Path) -> String {
    let text = std::fs::read(path).map(|b| console_text(&b)).unwrap_or_default();
    let _ = std::fs::remove_file(path);
    broker_cut(&text)
}

/// Дождаться ребёнка. `None` в коде — процесс НЕ завершился: либо его убил
/// таймаут, либо мы и не ждали. Ноль в этом месте был бы враньём.
fn broker_wait_child(child: &mut Child, timeout: Duration) -> (Option<i32>, bool) {
    if timeout.is_zero() {
        return (None, false);
    }
    let deadline = Instant::now() + timeout;
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return (status.code(), false),
            Ok(None) => {}
            Err(_) => return (None, false),
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return (None, true);
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

/// `exec` — процесс правами СИСТЕМЫ. Служба уже LocalSystem, поднимать никого
/// не надо; вся работа здесь — честно собрать квитанцию.
#[cfg(windows)]
fn broker_run_as_system(ask: &BrokerAsk) -> Ran {
    let started = Instant::now();
    let Some((out_path, out_file)) = broker_temp("out") else {
        return Ran::failed("не смог завести файл под вывод", started);
    };
    let Some((err_path, err_file)) = broker_temp("err") else {
        let _ = std::fs::remove_file(&out_path);
        return Ran::failed("не смог завести файл под ошибки", started);
    };
    // Полным путём — и он уже проверен на границе (broker_cmd_ok): голое имя
    // Windows искала бы СНАЧАЛА в папке процесса службы, то есть в папке
    // установки, куда пишет обычный пользователь.
    let mut cmd = Command::new(&ask.cmd);
    cmd.args(&ask.args)
        .stdin(Stdio::null())
        .stdout(Stdio::from(out_file))
        .stderr(Stdio::from(err_file));
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(err) => {
            let _ = std::fs::remove_file(&out_path);
            let _ = std::fs::remove_file(&err_path);
            return Ran::failed(&format!("не поднялось: {err}"), started);
        }
    };
    let pid = child.id();
    adopt(&child);
    let (code, killed) = broker_wait_child(&mut child, Duration::from_secs(ask.timeout_sec));
    let note = if killed {
        format!("убит по таймауту ({} с)", ask.timeout_sec)
    } else if code.is_none() {
        "не жду завершения (timeout_sec=0)".to_string()
    } else {
        String::new()
    };
    Ran {
        ok: !killed,
        code,
        pid: Some(pid),
        out: broker_take(&out_path),
        err: broker_take(&err_path),
        ms: started.elapsed().as_millis() as u64,
        note,
    }
}

/// Пометить дескриптор наследуемым — иначе ребёнок, поднятый
/// `CreateProcessAsUserW`, не получит ни вывода, ни ошибок.
///
/// ⚠ `bInheritHandles = TRUE` наследует ВСЕ наследуемые дескрипторы процесса.
/// Наши трубы заводятся с `bInheritHandle: 0`, а файлы Rust по умолчанию
/// ненаследуемы — наследуется ровно то, что помечено здесь. Гонки между двумя
/// одновременными командами нет: привилегированные команды идут по одной
/// (BROKER_GATE).
#[cfg(windows)]
fn broker_inheritable(file: &std::fs::File) -> windows_sys::Win32::Foundation::HANDLE {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::Foundation::{SetHandleInformation, HANDLE_FLAG_INHERIT};
    let handle = file.as_raw_handle() as windows_sys::Win32::Foundation::HANDLE;
    unsafe { SetHandleInformation(handle, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT) };
    handle
}

/// `spawn_interactive` — процесс в сессии владельца, ЕГО правами.
///
/// Вторая дверь брокера. Служба сидит в нулевой сессии, рабочего стола не
/// видит и показать там ничего не может; `WTSQueryUserToken` даёт токен
/// человека за компьютером, `CreateProcessAsUserW` поднимает процесс уже в его
/// сессии и на его рабочем столе (`lpDesktop = winsta0\default` — без этого
/// окно появилось бы на невидимом рабочем столе службы).
///
/// Это НЕ повышение: права здесь — владельца, а не системы. Поэтому дверь
/// открыта и без галочки нулевой сессии.
#[cfg(windows)]
/// Поставить правило брандмауэра по просьбе окна. Порт — единственное, что
/// приходит от клиента; всё остальное собирает `firewall_ensure` из общего
/// `common/firewall_rule.rs`, то есть тем же текстом, что и окно.
///
/// Порт берём из `args[0]`, а не из `cmd`: `cmd` в этой двери не значит ничего
/// и не исполняется. Негодный порт — отказ с человеческой строкой, а не
/// молчаливый netsh с мусором.
fn broker_firewall(ask: &BrokerAsk, tree: &Path) -> Ran {
    let started = Instant::now();
    let raw = ask.args.first().map(|s| s.trim()).unwrap_or("");
    let Ok(port) = raw.parse::<u16>() else {
        return Ran::failed(
            &format!("порт правила не разобран: {raw:?} — нужен номер от 1 до 65535"),
            started,
        );
    };
    if port == 0 {
        return Ran::failed("порт правила не может быть нулевым", started);
    }
    let plan = match load_plan(&config_path()) {
        Ok(plan) => plan,
        Err(why) => {
            return Ran::failed(
                &format!("не прочитан helene.json — не знаю, на какую программу вешать правило: {why}"),
                started,
            )
        }
    };
    // Журнал службы: правило ставится её руками, и след должен лежать там же,
    // где остальные её действия. В журнал брокера это попадёт квитанцией.
    let mut log = Log::open(tree);
    firewall_ensure(&plan.python, port, &mut log);
    Ran {
        ok: true,
        code: Some(0),
        pid: None,
        out: String::new(),
        err: String::new(),
        ms: started.elapsed().as_millis() as u64,
        note: format!("правило брандмауэра для порта {port} поставлено службой"),
    }
}

fn broker_spawn_session(ask: &BrokerAsk, cwd: &Path) -> Ran {
    use windows_sys::Win32::Foundation::{
        CloseHandle, GetLastError, HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT,
    };
    use windows_sys::Win32::System::Environment::{CreateEnvironmentBlock, DestroyEnvironmentBlock};
    use windows_sys::Win32::System::RemoteDesktop::{
        WTSGetActiveConsoleSessionId, WTSQueryUserToken,
    };
    use windows_sys::Win32::System::Threading::{
        CreateProcessAsUserW, GetExitCodeProcess, WaitForSingleObject, CREATE_UNICODE_ENVIRONMENT,
        PROCESS_INFORMATION, STARTF_USESTDHANDLES, STARTUPINFOW,
    };

    let started = Instant::now();
    let wide = |s: &str| -> Vec<u16> { s.encode_utf16().chain(std::iter::once(0)).collect() };

    let session = unsafe { WTSGetActiveConsoleSessionId() };
    if session == u32::MAX {
        return Ran::failed(
            "в систему никто не вошёл — поднимать процесс некуда: у сессии владельца нет \
             рабочего стола, пока владелец не вошёл",
            started,
        );
    }
    let mut token: HANDLE = std::ptr::null_mut();
    if unsafe { WTSQueryUserToken(session, &mut token) } == 0 {
        return Ran::failed(
            &format!(
                "не получил токен сессии {session}: код {}. Эта дверь работает только у службы \
                 под СИСТЕМОЙ",
                unsafe { GetLastError() }
            ),
            started,
        );
    }

    let Some((out_path, out_file)) = broker_temp("out") else {
        unsafe { CloseHandle(token) };
        return Ran::failed("не смог завести файл под вывод", started);
    };
    let Some((err_path, err_file)) = broker_temp("err") else {
        unsafe { CloseHandle(token) };
        let _ = std::fs::remove_file(&out_path);
        return Ran::failed("не смог завести файл под ошибки", started);
    };
    let out_handle = broker_inheritable(&out_file);
    let err_handle = broker_inheritable(&err_file);
    // Пустой ввод: без него ребёнок с STARTF_USESTDHANDLES получит негодный
    // stdin и часть программ на этом падает.
    let nul = std::fs::OpenOptions::new().read(true).open("NUL").ok();
    let nul_handle = nul.as_ref().map(broker_inheritable).unwrap_or(std::ptr::null_mut());

    // Окружение — ВЛАДЕЛЬЦА, а не службы. Без этого у процесса были бы TEMP,
    // APPDATA и PATH системного профиля, и он писал бы в C:\Windows.
    let mut env: *mut std::ffi::c_void = std::ptr::null_mut();
    let env_ok = unsafe { CreateEnvironmentBlock(&mut env, token, 0) } != 0;

    let mut desktop = wide(r"winsta0\default");
    let mut info: STARTUPINFOW = unsafe { std::mem::zeroed() };
    info.cb = std::mem::size_of::<STARTUPINFOW>() as u32;
    info.lpDesktop = desktop.as_mut_ptr();
    info.dwFlags = STARTF_USESTDHANDLES;
    info.hStdInput = nul_handle;
    info.hStdOutput = out_handle;
    info.hStdError = err_handle;

    // Программа передаётся ОТДЕЛЬНО (lpApplicationName) — поиск по путям не
    // выполняется вовсе. Командная строка нужна лишь как argv, и собирается
    // она по правилам самой Windows (broker_command_line).
    let app = wide(&ask.cmd);
    let mut line = wide(&broker_command_line(&ask.cmd, &ask.args));
    let dir = wide(&plain_path(cwd).to_string_lossy());
    let mut pi: PROCESS_INFORMATION = unsafe { std::mem::zeroed() };
    let ok = unsafe {
        CreateProcessAsUserW(
            token,
            app.as_ptr(),
            line.as_mut_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            1, // наследовать дескрипторы — ради вывода, см. broker_inheritable
            CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW,
            env,
            dir.as_ptr(),
            &info,
            &mut pi,
        )
    };
    let code = unsafe { GetLastError() };
    if env_ok {
        unsafe { DestroyEnvironmentBlock(env) };
    }
    unsafe { CloseHandle(token) };
    if ok == 0 {
        let _ = std::fs::remove_file(&out_path);
        let _ = std::fs::remove_file(&err_path);
        return Ran::failed(&format!("процесс в сессии {session} не поднялся: код {code}"), started);
    }
    unsafe { CloseHandle(pi.hThread) };

    let mut exit: Option<i32> = None;
    let mut note = String::new();
    if ask.timeout_sec == 0 {
        note = "не жду завершения (timeout_sec=0)".into();
    } else {
        match unsafe { WaitForSingleObject(pi.hProcess, (ask.timeout_sec * 1000) as u32) } {
            WAIT_OBJECT_0 => {
                let mut raw: u32 = 0;
                if unsafe { GetExitCodeProcess(pi.hProcess, &mut raw) } != 0 {
                    exit = Some(raw as i32);
                }
            }
            // ⚠ НЕ УБИВАЕМ. Это окно на рабочем столе владельца, а не
            // служебная команда: закрыть его посреди работы — хуже, чем
            // дождаться. Честно говорим, что процесс ещё жив.
            WAIT_TIMEOUT => {
                note = format!(
                    "процесс ещё работает (pid {}) — не трогаю его: это окно в твоей сессии",
                    pi.dwProcessId
                )
            }
            _ => note = format!("не дождался процесса: код {}", unsafe { GetLastError() }),
        }
    }
    let pid = pi.dwProcessId;
    unsafe { CloseHandle(pi.hProcess) };
    // Файлы вывода читаем в любом случае: даже у незавершённого процесса в них
    // уже что-то есть, и это лучше пустоты.
    Ran {
        ok: true,
        code: exit,
        pid: Some(pid),
        out: broker_take(&out_path),
        err: broker_take(&err_path),
        ms: started.elapsed().as_millis() as u64,
        note,
    }
}

// ──────────────────────────────────────────────────── труба: сервер (A2)

/// Дескриптор трубы. Своё владение — потому что `HANDLE` это сырой указатель:
/// забытый `CloseHandle` держал бы экземпляр трубы вечно, а их число не
/// бесконечно.
#[cfg(windows)]
struct Pipe(windows_sys::Win32::Foundation::HANDLE);

#[cfg(windows)]
unsafe impl Send for Pipe {}

#[cfg(windows)]
impl Drop for Pipe {
    fn drop(&mut self) {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(self.0) };
    }
}

#[cfg(windows)]
impl std::io::Read for Pipe {
    fn read(&mut self, buf: &mut [u8]) -> std::io::Result<usize> {
        use windows_sys::Win32::Foundation::{GetLastError, ERROR_BROKEN_PIPE};
        use windows_sys::Win32::Storage::FileSystem::ReadFile;
        let mut got: u32 = 0;
        let cap = buf.len().min(u32::MAX as usize) as u32;
        let ok = unsafe { ReadFile(self.0, buf.as_mut_ptr(), cap, &mut got, std::ptr::null_mut()) };
        if ok == 0 {
            let code = unsafe { GetLastError() };
            // Клиент ушёл — это конец потока, а не сбой. Именно так выглядит
            // будильник (см. broker_wake) и клиент, передумавший на полпути.
            if code == ERROR_BROKEN_PIPE {
                return Ok(0);
            }
            return Err(std::io::Error::from_raw_os_error(code as i32));
        }
        Ok(got as usize)
    }
}

#[cfg(windows)]
impl std::io::Write for Pipe {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        use windows_sys::Win32::Foundation::GetLastError;
        use windows_sys::Win32::Storage::FileSystem::WriteFile;
        let mut put: u32 = 0;
        let cap = buf.len().min(u32::MAX as usize) as u32;
        let ok = unsafe { WriteFile(self.0, buf.as_ptr(), cap, &mut put, std::ptr::null_mut()) };
        if ok == 0 {
            return Err(std::io::Error::from_raw_os_error(unsafe { GetLastError() } as i32));
        }
        Ok(put as usize)
    }
    fn flush(&mut self) -> std::io::Result<()> {
        use windows_sys::Win32::Storage::FileSystem::FlushFileBuffers;
        unsafe { FlushFileBuffers(self.0) };
        Ok(())
    }
}

/// Новый экземпляр трубы с дескриптором из SDDL.
///
/// Труба БАЙТОВАЯ (`PIPE_TYPE_BYTE`) намеренно: границы сообщений держит длина
/// в кадре, и клиенту тогда хватает обычного `std::fs::File` — ни строчки
/// Win32. Message-mode потребовал бы `SetNamedPipeHandleState` на той стороне.
///
/// `PIPE_REJECT_REMOTE_CLIENTS` — труба только для этой машины: без него имя
/// видно по сети через IPC$.
#[cfg(windows)]
fn broker_pipe_instance(name: &str, sddl: &str, first: bool) -> Result<Pipe, String> {
    use windows_sys::Win32::Foundation::{GetLastError, LocalFree, ERROR_ACCESS_DENIED, HLOCAL, INVALID_HANDLE_VALUE};
    use windows_sys::Win32::Security::Authorization::{
        ConvertStringSecurityDescriptorToSecurityDescriptorW, SDDL_REVISION_1,
    };
    use windows_sys::Win32::Security::{PSECURITY_DESCRIPTOR, SECURITY_ATTRIBUTES};
    use windows_sys::Win32::Storage::FileSystem::{FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_DUPLEX};
    use windows_sys::Win32::System::Pipes::{
        CreateNamedPipeW, PIPE_READMODE_BYTE, PIPE_REJECT_REMOTE_CLIENTS, PIPE_TYPE_BYTE,
        PIPE_UNLIMITED_INSTANCES, PIPE_WAIT,
    };
    let wide = |s: &str| -> Vec<u16> { s.encode_utf16().chain(std::iter::once(0)).collect() };
    let name_w = wide(name);
    let sddl_w = wide(sddl);
    let mut sd: PSECURITY_DESCRIPTOR = std::ptr::null_mut();
    let built = unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl_w.as_ptr(),
            SDDL_REVISION_1,
            &mut sd,
            std::ptr::null_mut(),
        )
    };
    if built == 0 {
        return Err(format!(
            "дескриптор канала не собрался (SDDL «{sddl}»), код {}",
            unsafe { GetLastError() }
        ));
    }
    let attrs = SECURITY_ATTRIBUTES {
        nLength: std::mem::size_of::<SECURITY_ATTRIBUTES>() as u32,
        lpSecurityDescriptor: sd,
        // Дескриптор трубы не должен уезжать детям: ребёнок с ним отвечал бы
        // на просьбы вместо брокера.
        bInheritHandle: 0,
    };
    let open = PIPE_ACCESS_DUPLEX | if first { FILE_FLAG_FIRST_PIPE_INSTANCE } else { 0 };
    let handle = unsafe {
        CreateNamedPipeW(
            name_w.as_ptr(),
            open,
            PIPE_TYPE_BYTE | PIPE_READMODE_BYTE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            PIPE_UNLIMITED_INSTANCES,
            64 * 1024,
            64 * 1024,
            0,
            &attrs,
        )
    };
    let code = unsafe { GetLastError() };
    unsafe { LocalFree(sd as HLOCAL) };
    if handle == INVALID_HANDLE_VALUE {
        let squatted = if first && code == ERROR_ACCESS_DENIED {
            " — имя уже занято другой программой; служить на чужом канале нельзя"
        } else {
            ""
        };
        return Err(format!("канал {name} не завёлся: код {code}{squatted}"));
    }
    Ok(Pipe(handle))
}

#[cfg(windows)]
fn broker_pipe_connect(pipe: &Pipe) -> Result<(), String> {
    use windows_sys::Win32::Foundation::{GetLastError, ERROR_PIPE_CONNECTED};
    use windows_sys::Win32::System::Pipes::ConnectNamedPipe;
    if unsafe { ConnectNamedPipe(pipe.0, std::ptr::null_mut()) } != 0 {
        return Ok(());
    }
    match unsafe { GetLastError() } {
        // Клиент успел подключиться между созданием и ConnectNamedPipe. Не
        // ошибка, а штатная гонка: Windows сообщает о ней вот так.
        ERROR_PIPE_CONNECTED => Ok(()),
        code => Err(format!("канал не принял клиента: код {code}")),
    }
}

/// Кто на том конце: pid клиента и его сессия. Это третий замок брокера.
#[cfg(windows)]
fn broker_client_who(pipe: &Pipe) -> Result<(u32, u32), String> {
    use windows_sys::Win32::Foundation::GetLastError;
    use windows_sys::Win32::System::Pipes::GetNamedPipeClientProcessId;
    use windows_sys::Win32::System::RemoteDesktop::ProcessIdToSessionId;
    let mut pid: u32 = 0;
    if unsafe { GetNamedPipeClientProcessId(pipe.0, &mut pid) } == 0 {
        return Err(format!("не узнал, кто на том конце канала: код {}", unsafe {
            GetLastError()
        }));
    }
    let mut session: u32 = 0;
    if unsafe { ProcessIdToSessionId(pid, &mut session) } == 0 {
        return Err(format!("не узнал сессию процесса {pid}: код {}", unsafe {
            GetLastError()
        }));
    }
    Ok((pid, session))
}

/// SID строкой из указателя на SID.
#[cfg(windows)]
fn sid_text(sid: windows_sys::Win32::Security::PSID) -> Option<String> {
    use windows_sys::Win32::Foundation::{LocalFree, HLOCAL};
    use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
    let mut out: windows_sys::core::PWSTR = std::ptr::null_mut();
    if unsafe { ConvertSidToStringSidW(sid, &mut out) } == 0 || out.is_null() {
        return None;
    }
    let mut n = 0usize;
    while unsafe { *out.add(n) } != 0 {
        n += 1;
    }
    let text = String::from_utf16_lossy(unsafe { std::slice::from_raw_parts(out, n) });
    unsafe { LocalFree(out as HLOCAL) };
    Some(text)
}

/// Чей это токен.
#[cfg(windows)]
fn token_user_sid(token: windows_sys::Win32::Foundation::HANDLE) -> Option<String> {
    use windows_sys::Win32::Security::{GetTokenInformation, TokenUser, TOKEN_USER};
    let mut need: u32 = 0;
    unsafe { GetTokenInformation(token, TokenUser, std::ptr::null_mut(), 0, &mut need) };
    if need == 0 {
        return None;
    }
    // Буфер словами, а не байтами: TOKEN_USER содержит указатель, и байтовый
    // Vec не обещает нужного выравнивания.
    let words = (need as usize).div_ceil(8).max(1);
    let mut buf = vec![0u64; words];
    let ok = unsafe {
        GetTokenInformation(
            token,
            TokenUser,
            buf.as_mut_ptr().cast(),
            (words * 8) as u32,
            &mut need,
        )
    };
    if ok == 0 {
        return None;
    }
    let user = unsafe { &*(buf.as_ptr() as *const TOKEN_USER) };
    sid_text(user.User.Sid)
}

/// SID владельца установки — того, кому открывается труба.
///
/// Спрашивается токен человека за консолью. `None` — никто не вошёл, и это
/// СОСТОЯНИЕ, а не ошибка: открывать трубу некому.
///
/// ⚠ Запасной путь (собственный токен) верен только в отладочном режиме
/// `broker`, где процесс уже принадлежит владельцу. Под LocalSystem он дал бы
/// `S-1-5-18` — то есть трубу, видную ОДНОЙ СИСТЕМЕ: тихо закрытый брокер
/// вместо честного «никто не вошёл». Поэтому SYSTEM здесь отбрасывается.
#[cfg(windows)]
fn broker_owner_sid() -> Option<String> {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::Security::TOKEN_QUERY;
    use windows_sys::Win32::System::RemoteDesktop::{
        WTSGetActiveConsoleSessionId, WTSQueryUserToken,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    let session = unsafe { WTSGetActiveConsoleSessionId() };
    if session != u32::MAX {
        let mut token: HANDLE = std::ptr::null_mut();
        if unsafe { WTSQueryUserToken(session, &mut token) } != 0 {
            let sid = token_user_sid(token);
            unsafe { CloseHandle(token) };
            if let Some(sid) = sid {
                if sid != SID_SYSTEM {
                    return Some(sid);
                }
            }
        }
    }
    let mut token: HANDLE = std::ptr::null_mut();
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) } != 0 {
        let sid = token_user_sid(token);
        unsafe { CloseHandle(token) };
        return sid.filter(|s| s != SID_SYSTEM);
    }
    None
}

#[cfg(windows)]
fn broker_reply(pipe: &mut Pipe, receipt: &BrokerReceipt) {
    use std::io::Write as _;
    let body = receipt.to_json();
    let _ = pipe.write_all(&broker_frame(body.as_bytes()));
    let _ = pipe.flush();
}

/// Одно обращение целиком: прочитать, проверить три замка, выполнить, ответить
/// квитанцией, записать в журнал.
#[cfg(windows)]
fn broker_handle(mut pipe: Pipe, config: PathBuf) {
    use windows_sys::Win32::System::RemoteDesktop::WTSGetActiveConsoleSessionId;
    let at = now_stamp();
    let raw = match broker_read_frame(&mut pipe as &mut dyn std::io::Read, BROKER_MAX_ASK) {
        Ok(body) => body,
        // Пустое соединение — это будильник (broker_wake) или клиент,
        // передумавший на полпути. Молчим: строка в журнале каждые пять секунд
        // съела бы его целиком.
        Err(_) => return,
    };
    let plan = load_plan(&config).ok();
    let tree = plan
        .as_ref()
        .map(|p| p.tree.clone())
        .unwrap_or_else(|| config.parent().unwrap_or(Path::new(".")).join("data"));

    // Кто на том конце — узнаём ПЕРВЫМ делом, ещё до разбора запроса: чтобы
    // каждая строка журнала, включая отказ на разборе, называла клиента.
    // Владельцу мало знать, что «кто-то просил» — ему нужно знать, кто.
    let (pid, session) = broker_client_who(&pipe).unwrap_or((0, u32::MAX));
    let whom = format!("клиент: pid {pid}, сессия {session}");
    let refuse = |pipe: &mut Pipe, ask: Option<&BrokerAsk>, why: &str| {
        broker_reply(pipe, &BrokerReceipt::refused(ask, &at, why));
    };

    // Замок третий: клиент обязан сидеть в активной пользовательской сессии.
    // Раньше всего остального — процессу из чужой сессии незачем даже
    // разбирать его запрос.
    let active = unsafe { WTSGetActiveConsoleSessionId() };
    if let Err(why) = broker_client_ok(session, active) {
        broker_note(&tree, &format!("ОТКАЗ: {why} · {whom}"));
        refuse(&mut pipe, None, &why);
        return;
    }

    let text = match String::from_utf8(raw) {
        Ok(text) => text,
        Err(_) => {
            broker_note(&tree, &format!("ОТКАЗ: запрос не в UTF-8 · {whom}"));
            refuse(&mut pipe, None, "запрос не в UTF-8");
            return;
        }
    };
    let ask = match BrokerAsk::parse(&text) {
        Ok(ask) => ask,
        Err(why) => {
            // Пишем в журнал и разбор, который не состоялся: «отказ без why» —
            // это тоже событие, о котором владелец должен узнать.
            broker_note(&tree, &format!("ОТКАЗ на разборе: {why} · {whom}"));
            refuse(&mut pipe, None, &why);
            return;
        }
    };

    // Замок второй: секрет.
    match read_token_file(&broker_token_path(&tree)) {
        None => {
            let why = "секрета брокера нет — служба ещё не завела его. Брокер живёт только \
                       при включённой службе";
            broker_note(&tree, &format!("ОТКАЗ: {why} · {whom}"));
            refuse(&mut pipe, Some(&ask), why);
            return;
        }
        Some(mine) if !secret_eq(&mine, &ask.token) => {
            let why = "не тот токен. Он лежит в memory\\.state\\broker-token и читается \
                       только владельцем";
            broker_note(&tree, &format!("ОТКАЗ: {why} · зачем: {} · {whom}", ask.why));
            refuse(&mut pipe, Some(&ask), why);
            return;
        }
        Some(_) => {}
    }

    let session0 = plan.as_ref().map(|p| p.session0).unwrap_or(false);
    // Узкая дверь брокера читается из того же перечитанного конфига: галочка
    // «служба ставит правило брандмауэра» действует немедленно, как и выключатель.
    let firewall_rule = plan.as_ref().map(|p| p.firewall_rule).unwrap_or(true);
    // Выключатель из Настроек: конфиг перечитан только что, значит он
    // действует немедленно, а не «после перезапуска службы».
    if !plan.as_ref().map(|p| p.broker).unwrap_or(true) {
        let why = "брокер выключен в Настройках";
        broker_note(&tree, &format!("ОТКАЗ: {why} · зачем: {} · {whom}", ask.why));
        refuse(&mut pipe, Some(&ask), why);
        return;
    }

    let ran = match ask.op {
        BrokerOp::Ping => Ran {
            ok: true,
            code: Some(0),
            pid: None,
            out: String::new(),
            err: String::new(),
            ms: 0,
            note: format!(
                "брокер жив · нулевая сессия: {} · процессы правами СИСТЕМЫ {}",
                if session0 { "включена" } else { "выключена" },
                if session0 { "разрешены" } else { "запрещены" }
            ),
        },
        BrokerOp::Exec => match broker_exec_allowed(session0) {
            Err(why) => Ran::failed(&why, Instant::now()),
            Ok(()) => {
                let _lock = BROKER_GATE.lock();
                broker_run_as_system(&ask)
            }
        },
        BrokerOp::SpawnInteractive => {
            let _lock = BROKER_GATE.lock();
            broker_spawn_session(&ask, &tree)
        }
        // Узкая дверь: клиент называет ТОЛЬКО порт, аргументы netsh собирает
        // служба сама (firewall_ensure → common/firewall_rule.rs). Ни `cmd`, ни
        // `args` просьбы здесь не читаются вовсе — их нечем подменить.
        BrokerOp::Firewall => match broker_firewall_allowed(firewall_rule) {
            Err(why) => Ran::failed(&why, Instant::now()),
            Ok(()) => {
                let _lock = BROKER_GATE.lock();
                broker_firewall(&ask, &tree)
            }
        },
    };

    let receipt = BrokerReceipt {
        id: ask.id.clone(),
        ok: ran.ok,
        op: ask.op.as_str().to_string(),
        code: ran.code,
        pid: ran.pid,
        out: ran.out,
        err: ran.err,
        ms: ran.ms,
        at: at.clone(),
        why: ask.why.clone(),
        note: ran.note,
    };
    // `ping` в журнал действий не пишется: он ничего не делает, а строка на
    // каждую проверку связи вытеснила бы из журнала настоящие обращения.
    if ask.op.runs() || !receipt.ok {
        broker_note(&tree, &broker_journal_line(&ask, &receipt, pid, session));
    }
    broker_reply(&mut pipe, &receipt);
}

/// Будильник.
///
/// ⚠ ЗАЧЕМ ОН НУЖЕН. Приём соединения блокирующий: пока никто не подключился,
/// поток стоит в `ConnectNamedPipe` и не видит ни остановки службы, ни смены
/// человека за компьютером — а дескриптор трубы у нас остался от ПРЕЖНЕГО
/// владельца, и новый в неё не войдёт вовсе. Раз в пять секунд стучимся в
/// собственную трубу и тут же уходим: цикл просыпается, перечитывает конфиг и
/// владельца и заводит следующий экземпляр уже с верным дескриптором.
///
/// Перекрытый ввод-вывод (`FILE_FLAG_OVERLAPPED`) решил бы это честнее, но
/// потянул бы `OVERLAPPED` в каждое чтение и запись трубы — цена больше выигрыша.
#[cfg(windows)]
fn broker_wake(name: String, stop: Arc<AtomicBool>) {
    std::thread::spawn(move || {
        while !stop.load(Ordering::Relaxed) {
            std::thread::sleep(Duration::from_secs(5));
            let _ = std::fs::OpenOptions::new().read(true).write(true).open(&name);
        }
    });
}

/// Цикл брокера. Живёт в отдельном потоке службы.
#[cfg(windows)]
fn broker_serve(config: PathBuf, stop: Arc<AtomicBool>) {
    let mut said_off = false;
    let mut said_nobody = false;
    let mut announced = String::new();
    let mut last_err = String::new();
    let mut waking = false;
    // Журнал открываем ОДИН раз, а не на каждом круге: будильник крутит цикл
    // каждые пять секунд, и открытие файла (с проверкой размера и, возможно,
    // переименованием) на каждом круге дралось бы за него с главным потоком
    // службы, который пишет в тот же service.log.
    let mut log = Log::open(
        &load_plan(&config)
            .map(|p| p.tree)
            .unwrap_or_else(|_| config.parent().unwrap_or(Path::new(".")).join("data")),
    );
    while !stop.load(Ordering::Relaxed) {
        let Ok(plan) = load_plan(&config) else {
            std::thread::sleep(Duration::from_secs(10));
            continue;
        };
        if !plan.broker {
            if !said_off {
                log.line("брокер выключен (service.broker=false) — канала нет");
                said_off = true;
            }
            std::thread::sleep(Duration::from_secs(3));
            continue;
        }
        said_off = false;
        let Some(root) = install_root(&config) else {
            log.line("брокер: рядом с конфигом нет helene-svc.exe — это не папка установки, канал не открываю");
            return;
        };
        // Владельца спрашиваем на КАЖДОМ круге: вошёл другой человек — труба
        // должна открыться ему, а не прежнему.
        let Some(owner) = broker_owner_sid() else {
            if !said_nobody {
                log.line(
                    "брокер: в систему никто не вошёл — канал открывать некому. Это состояние, \
                     а не ошибка; открою при входе",
                );
                said_nobody = true;
            }
            std::thread::sleep(Duration::from_secs(5));
            continue;
        };
        said_nobody = false;
        let Some(sddl) = broker_sddl(&owner) else {
            log.line(&format!("брокер: «{owner}» не похоже на SID — канал не открываю"));
            std::thread::sleep(Duration::from_secs(30));
            continue;
        };
        let name = broker_pipe_name(&root);
        let token = broker_ensure_token(&plan.tree, Some(&owner), &mut log);
        if token.is_empty() {
            std::thread::sleep(Duration::from_secs(30));
            continue;
        }
        if !waking {
            broker_wake(name.clone(), stop.clone());
            waking = true;
        }
        // Флаг «первый экземпляр» гасится ТОЛЬКО после удачи: иначе неудачная
        // первая попытка разрешила бы подсесть вторым экземпляром к чужой трубе.
        let first = BROKER_FIRST.load(Ordering::Relaxed);
        let pipe = match broker_pipe_instance(&name, &sddl, first) {
            Ok(pipe) => {
                BROKER_FIRST.store(false, Ordering::Relaxed);
                last_err.clear();
                pipe
            }
            Err(err) => {
                // Одна и та же беда каждые пять секунд забила бы журнал:
                // говорим только когда она сменилась.
                if last_err != err {
                    log.line(&format!("брокер: {err}"));
                    last_err = err;
                }
                std::thread::sleep(Duration::from_secs(5));
                continue;
            }
        };
        let announce = format!("{name} · {owner}");
        if announced != announce {
            log.line(&format!(
                "брокер слушает {name} · доступ: СИСТЕМА и {owner} · процессы правами СИСТЕМЫ {} · журнал: {}",
                if plan.session0 { "РАЗРЕШЕНЫ (нулевая сессия включена)" } else { "запрещены" },
                broker_log_path(&plan.tree).display()
            ));
            announced = announce;
        }
        if let Err(err) = broker_pipe_connect(&pipe) {
            log.line(&format!("брокер: {err}"));
            std::thread::sleep(Duration::from_secs(1));
            continue;
        }
        if stop.load(Ordering::Relaxed) {
            return;
        }
        // Каждое обращение — свой поток: зависший клиент не должен запирать
        // брокер целиком. Сами привилегированные команды всё равно идут по
        // одной (BROKER_GATE).
        let next = config.clone();
        std::thread::spawn(move || broker_handle(pipe, next));
    }
}

#[cfg(not(windows))]
fn broker_serve(_config: PathBuf, _stop: Arc<AtomicBool>) {}

// ======================================== служба: харнесс в сессии владельца

/// Что служба сказала о состоянии сессии в прошлый раз. Нужно, чтобы одна и та
/// же строка не писалась в журнал каждые три секунды: владелец жаловался на
/// дребезг, и журнал на 5 МБ вытеснял бы всё остальное за сутки.
#[derive(PartialEq, Clone, Copy)]
enum Said {
    Nothing,
    Alive,
    NoOne,
    Trying,
}

/// Главный цикл службы. Служба НЕ поднимает детей сама — она следит за тем,
/// живёт ли харнесс в сессии владельца, и просит планировщик поднять его, когда
/// не живёт.
fn supervise_session(plan: &Plan, stop: Arc<AtomicBool>, log: &mut Log, stopping: &mut dyn FnMut()) {
    log.line(&format!(
        "служба: харнесс живёт в сессии владельца (задача {SESSION_TASK}) · дерево={} · порт={} · телефон={}",
        plan.tree.display(),
        plan.port,
        if plan.phone { "да" } else { "нет" }
    ));
    if let Some(warn) = user_writable_warning() {
        log.line(&warn);
    }
    align_acl(&plan.config, log);
    // Правило брандмауэра ставит служба: телефону оно нужно, а из сессии
    // владельца netsh требует повышения. Это одна из немногих вещей, которые
    // служба делает СВОИМИ правами намеренно.
    if plan.phone {
        firewall_ensure(&plan.python, plan.port, log);
    }

    let mut said = Said::Nothing;
    let mut next_try = Instant::now();
    let mut backoff: u64 = 0;
    let mut probed: Option<Instant> = None;
    let mut alive = false;

    while !stop.load(Ordering::Relaxed) {
        let now = Instant::now();
        // Проба порта — раз в три секунды, как и у супервизора детей.
        if probed.is_none_or(|t| now.duration_since(t) >= Duration::from_secs(3)) {
            probed = Some(now);
            alive = harness_alive(plan.port);
        }
        if alive {
            // Порт держит харнесс — наш из задачи или тот, что открыл владелец
            // своим окном. В обоих случаях делать нечего: второй харнесс на
            // одном дереве — это два агента (см. «замок порта» в supervise).
            if said != Said::Alive {
                log.line("харнесс отвечает на порту — не вмешиваюсь");
                said = Said::Alive;
            }
            backoff = 0;
            next_try = now;
        } else {
            match active_session_user() {
                // ЧЕСТНО: активной сессии нет — никто не вошёл в систему.
                // Харнесс не поднимается, и это СОСТОЯНИЕ, а не ошибка: агенту
                // некуда подниматься. Из нулевой сессии он не видит рабочего
                // стола, а работать правами СИСТЕМЫ он не должен. Ждём входа —
                // задача поднимется и сама, по триггеру ONLOGON.
                None => {
                    if said != Said::NoOne {
                        log.line(
                            "в систему никто не вошёл — харнесс не поднимаю. Это состояние, \
                             а не ошибка: агент живёт в сессии владельца, а её сейчас нет. \
                             Поднимется сам при входе.",
                        );
                        said = Said::NoOne;
                    }
                    backoff = 0;
                    next_try = now;
                }
                Some(user) => {
                    if now >= next_try {
                        match schtasks(&session_task_run_args()) {
                            Ok((true, _)) => {
                                log.line(&format!("прошу планировщик поднять харнесс в сессии {user}"));
                                said = Said::Trying;
                            }
                            Ok((false, why)) => log.line(&format!(
                                "задача {SESSION_TASK} не запустилась ({why}) — заведи её: helene-svc session-task install --config …"
                            )),
                            Err(err) => log.line(&format!("задача {SESSION_TASK}: {err}")),
                        }
                        // 5 → 10 → 20 → 40 → 80 → 160 → 300 с. Та же лестница,
                        // что у детей: харнессу нужно время подняться, а
                        // безнадёжный случай не должен долбить планировщик.
                        backoff = if backoff == 0 { 5 } else { (backoff * 2).min(300) };
                        next_try = now + Duration::from_secs(backoff);
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(500));
    }

    stopping();
    // Гасим ТОЛЬКО свою задачу. Если харнесс подняло окно владельца, он не наш
    // и остаётся жить. Задача не запущена — schtasks ответит отказом, и это
    // норма, поэтому пишем в журнал только неожиданное.
    log.line("служба: остановка — прошу планировщик остановить задачу сессии");
    if let Ok((false, why)) = schtasks(&session_task_end_args()) {
        if !why.is_empty() {
            // Чаще всего это значит «задача и не была запущена» (харнесс поднят
            // окном владельца) — не тревога, просто запись.
            log.line(&format!("задача {SESSION_TASK} не остановлена (возможно, и не была запущена): {why}"));
        }
    }
    log.line("служба: остановлена");
}

/// Папка, куда по умолчанию пишет только администратор.
fn admin_only_dir(p: &Path) -> bool {
    let path = plain_path(p).to_string_lossy().to_lowercase();
    ["ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "SystemRoot"]
        .iter()
        .filter_map(std::env::var_os)
        .any(|root| {
            let root = PathBuf::from(root).to_string_lossy().to_lowercase();
            !root.is_empty() && path.starts_with(&root)
        })
}

/// Служба работает как LocalSystem и запускает код (python.exe, deskapp.py,
/// runner.py) из своей папки. Если в эту папку пишет обычный пользователь —
/// любой код от его имени получает SYSTEM на следующей загрузке. Это не
/// чинится внутри службы: место установки выбирает установщик, а агенту его
/// папка нужна на запись по построению. Поэтому — говорим прямо, по трём
/// каналам: файлом `service-warning.txt` рядом с конфигом (его читает
/// установщик и печатает в расписку — единственный канал, который владелец
/// действительно видит), в `service.log` при каждом старте и в stdout, который
/// виден только при ручном запуске `helene-svc install` из консоли.
fn user_writable_warning() -> Option<String> {
    let dir = std::env::current_exe().ok()?.parent()?.to_path_buf();
    if admin_only_dir(&dir) {
        return None;
    }
    Some(format!(
        "ВНИМАНИЕ: служба работает как СИСТЕМА и запускает код из {} — в эту папку \
         пишет обычный пользователь, значит любой код от его имени получит права \
         СИСТЕМЫ при следующей загрузке. Безопаснее держать продукт в папке, куда \
         пишет только администратор, или не ставить службу вовсе.",
        dir.display()
    ))
}

/// Файл-предупреждение рядом с конфигом — единственный канал, по которому
/// установщик может УВИДЕТЬ то, что печатает `install`.
///
/// Установку службы зовут только скрытым поднятым процессом
/// (`setup/src/install.rs::service_op` → `install-service.ps1` → `helene-svc
/// install`, и так же из окна): окно скрыто, stdout никуда не перенаправлен,
/// наверх едет один код возврата. Значит `println!` в `install()` не видит
/// никто, и обещание «предупреждение при установке» держалось бы только на
/// `service.log`, который владелец-непрограммист не откроет. Поэтому текст
/// ложится файлом, а установщик читает его после успешной установки и печатает
/// в расписку (`setup/src/install.rs::service_warning`).
const WARNING_NAME: &str = "service-warning.txt";

fn warning_file(config: &Path) -> PathBuf {
    config
        .parent()
        .unwrap_or_else(|| Path::new("."))
        .join(WARNING_NAME)
}

define_windows_service!(ffi_service_main, service_main);

fn set_state(
    status: &ServiceStatusHandle,
    state: ServiceState,
    exit: ServiceExitCode,
    accept: ServiceControlAccept,
    checkpoint: u32,
    hint: Duration,
) {
    let _ = status.set_service_status(ServiceStatus {
        service_type: ServiceType::OWN_PROCESS,
        current_state: state,
        controls_accepted: accept,
        exit_code: exit,
        checkpoint,
        wait_hint: hint,
        process_id: None,
    });
}

fn service_main(_arguments: Vec<OsString>) {
    let config = config_path();
    // Обработчик и первая квитанция SCM — ДО чтения конфига. Раньше оба ранних
    // выхода случались раньше первого set_service_status: SCM тридцать секунд
    // ждал SERVICE_RUNNING и показывал владельцу «служба не ответила
    // своевременно (1053)», а причина — уже сформулированная текстом — молча
    // выбрасывалась.
    let stop = Arc::new(AtomicBool::new(false));
    let stop_handler = stop.clone();
    let handler = move |control| match control {
        ServiceControl::Stop | ServiceControl::Shutdown | ServiceControl::Preshutdown => {
            stop_handler.store(true, Ordering::Relaxed);
            ServiceControlHandlerResult::NoError
        }
        ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
        _ => ServiceControlHandlerResult::NotImplemented,
    };
    let status = match service_control_handler::register(SERVICE_NAME, handler) {
        Ok(s) => s,
        Err(err) => {
            early_line(&config, &format!("служба: SCM не принял обработчик: {err}"));
            return;
        }
    };
    let accept = ServiceControlAccept::STOP
        | ServiceControlAccept::SHUTDOWN
        | ServiceControlAccept::PRESHUTDOWN;
    set_state(
        &status,
        ServiceState::StartPending,
        ServiceExitCode::Win32(0),
        ServiceControlAccept::empty(),
        1,
        Duration::from_secs(20),
    );

    // Останов с ненулевым кодом: он виден в SCM и не запускает failure actions
    // (они реагируют на падение процесса, а не на честный стоп с кодом).
    let refuse = |status: &ServiceStatusHandle| {
        set_state(
            status,
            ServiceState::Stopped,
            ServiceExitCode::ServiceSpecific(1),
            ServiceControlAccept::empty(),
            0,
            Duration::default(),
        );
    };
    let plan = match load_plan(&config) {
        // Конфиг прочитан — дерево известно, пишем причину туда, где её ищут.
        Ok(plan) => match plan_usable(&plan) {
            Ok(()) => plan,
            Err(err) => {
                Log::open(&plan.tree).line(&format!(
                    "служба не стартовала: {err} · конфиг {}",
                    config.display()
                ));
                refuse(&status);
                return;
            }
        },
        Err(err) => {
            early_line(&config, &format!("служба не стартовала: {err} · конфиг {}", config.display()));
            refuse(&status);
            return;
        }
    };
    let mut log = Log::open(&plan.tree);
    // Брокер живёт В СЛУЖБЕ — и только в ней: он и есть та привилегия, которой
    // у харнесса в сессии владельца нет. Отдельным потоком, потому что дальше
    // главный поток уходит в бесконечный надзор, а труба должна отвечать
    // независимо от него.
    {
        let cfg = plan.config.clone();
        let stop_broker = stop.clone();
        std::thread::spawn(move || broker_serve(cfg, stop_broker));
    }
    set_state(&status, ServiceState::Running, ServiceExitCode::Win32(0), accept, 0, Duration::default());
    // Пока гасим детей, статус для SCM — StopPending с честным ожиданием, иначе
    // он вправе отстрелить службу по таймауту и сказать владельцу «служба не
    // смогла остановиться».
    let mut stopping = || {
        set_state(
            &status,
            ServiceState::StopPending,
            ServiceExitCode::Win32(0),
            ServiceControlAccept::empty(),
            1,
            Duration::from_secs(20),
        );
    };
    if plan.session0 {
        // Владелец включил галочку нулевой сессии осознанно. Говорим вслух, чем
        // он за это платит: агент работает правами СИСТЕМЫ и рабочего стола не
        // видит — руки, которым нужен экран, в этом режиме мертвы.
        log.line(
            "ВНИМАНИЕ: включена нулевая сессия (service.session0) — харнесс поднимается ПОД СЛУЖБОЙ, \
             то есть правами СИСТЕМЫ и без рабочего стола. Руки, которым нужен экран, работать не будут.",
        );
        align_acl(&plan.config, &mut log);
        supervise(&plan, stop, &mut log, plan.firewall_rule, &mut stopping);
    } else {
        supervise_session(&plan, stop, &mut log, &mut stopping);
    }
    set_state(
        &status,
        ServiceState::Stopped,
        ServiceExitCode::Win32(0),
        ServiceControlAccept::empty(),
        0,
        Duration::default(),
    );
}

fn install(config: &Path) -> Result<(), String> {
    // Конфиг проверяем ДО регистрации: сломанный helene.json иначе всплывал бы
    // как «служба не ответила своевременно (1053)» на следующей загрузке, без
    // единого слова о причине.
    let plan = load_plan(config)?;
    plan_usable(&plan)?;
    let manager = ServiceManager::local_computer(
        None::<&str>,
        ServiceManagerAccess::CONNECT | ServiceManagerAccess::CREATE_SERVICE,
    )
    .map_err(|e| format!("SCM: {e} (запусти от администратора)"))?;
    let exe = plain_path(&std::env::current_exe().map_err(|e| e.to_string())?);
    let info = ServiceInfo {
        name: OsString::from(SERVICE_NAME),
        display_name: OsString::from(SERVICE_DISPLAY),
        service_type: ServiceType::OWN_PROCESS,
        start_type: ServiceStartType::AutoStart,
        error_control: ServiceErrorControl::Normal,
        executable_path: exe,
        launch_arguments: vec![
            OsString::from("run"),
            OsString::from("--config"),
            config.as_os_str().to_os_string(),
        ],
        dependencies: vec![],
        account_name: None, // LocalSystem: детям нужен доступ к дереву в любой учётке
        account_password: None,
    };
    let service = manager
        .create_service(&info, ServiceAccess::START | ServiceAccess::CHANGE_CONFIG)
        .map_err(|e| {
            format!("создание службы: {e} (если она уже стоит — сначала сними: helene-svc uninstall)")
        })?;
    let _ = service.set_description(SERVICE_DESCRIPTION);
    // Погашение дерева процессов при выключении машины требует времени больше,
    // чем обычные пять секунд Shutdown.
    let _ = service.set_preshutdown_timeout(Duration::from_secs(30));
    // Самовосстановление службы — то же, что ставил install-service.ps1
    // (`sc failure`): прямой вызов `helene-svc install` давал раньше ДРУГУЮ
    // службу, без него.
    let _ = service.update_failure_actions(ServiceFailureActions {
        reset_period: ServiceFailureResetPeriod::After(Duration::from_secs(86400)),
        reboot_msg: None,
        command: None,
        actions: Some(vec![
            ServiceAction { action_type: ServiceActionType::Restart, delay: Duration::from_secs(5) },
            ServiceAction { action_type: ServiceActionType::Restart, delay: Duration::from_secs(15) },
            ServiceAction { action_type: ServiceActionType::Restart, delay: Duration::from_secs(60) },
        ]),
    });
    // Задача планировщика — ПОСЛЕ регистрации службы и ДО её запуска.
    // После — чтобы неудачная установка (нет прав администратора) не оставляла
    // за собой задачу, поднимающую агента при каждом входе: владелец просил
    // службу, а получил бы автозапуск, которого не заказывал.
    // До запуска — чтобы служба на первом же круге нашла, кого просить.
    //
    // Провал задачи установку НЕ роняет: служба и без неё делает вторую свою
    // работу — стережёт права на папку. Но молчать нельзя, поэтому текст едет в
    // расписку тем же каналом, что и предупреждение о правах.
    let mut warnings: Vec<String> = Vec::new();
    match session_task_install(config) {
        Ok(user) => println!("задача планировщика {SESSION_TASK} заведена под {user} (без повышения)"),
        Err(err) => {
            eprintln!("{err}");
            warnings.push(format!(
                "Задача планировщика не завелась: {err}. Пока её нет, служба не сможет поднять \
                 агента в твоей сессии. Заведи её командой: helene-svc session-task install --config <helene.json>"
            ));
        }
    }
    // Предупреждение — и на экран (когда install зовут руками из консоли), и
    // файлом рядом с конфигом: из-под скрытого поднятого процесса, которым его
    // зовут установщик и окно, stdout не виден никому. Нет предупреждений —
    // снимаем старый файл, чтобы он не пугал после переезда в защищённую папку.
    if let Some(warn) = user_writable_warning() {
        println!("{warn}");
        warnings.push(warn);
    }
    if warnings.is_empty() {
        let _ = std::fs::remove_file(warning_file(config));
    } else if let Err(e) = std::fs::write(warning_file(config), warnings.join("\n\n")) {
        eprintln!("предупреждение не записалось в {}: {e}", warning_file(config).display());
    }
    // Результат запуска раньше выбрасывался, и `install` печатал «установлена и
    // запущена» независимо от того, что произошло.
    service
        .start::<&str>(&[])
        .map_err(|e| format!("служба создана, но не запустилась: {e} · причина в {}\\service.log", plan.tree.display()))?;
    println!("служба {SERVICE_NAME} установлена (autostart) и запущена");
    println!("конфиг: {}", config.display());
    println!("журнал: {}\\service.log", plan.tree.display());
    Ok(())
}

fn uninstall() -> Result<(), String> {
    let manager =
        ServiceManager::local_computer(None::<&str>, ServiceManagerAccess::CONNECT)
            .map_err(|e| format!("SCM: {e} (запусти от администратора)"))?;
    let service = manager
        .open_service(
            SERVICE_NAME,
            ServiceAccess::STOP | ServiceAccess::DELETE | ServiceAccess::QUERY_STATUS,
        )
        .map_err(|e| format!("службы нет? {e}"))?;
    // Ждём остановку ПО ФАКТУ, а не фиксированные 1200 мс: не остановленная
    // служба после delete() лишь помечается на удаление, живёт до перезагрузки,
    // а следующая установка падает с 1072 без всякой подсказки.
    let stopped = match service.query_status() {
        Ok(st) if st.current_state == ServiceState::Stopped => true,
        _ => {
            let _ = service.stop();
            let deadline = Instant::now() + Duration::from_secs(20);
            loop {
                if matches!(service.query_status(), Ok(st) if st.current_state == ServiceState::Stopped) {
                    break true;
                }
                if Instant::now() >= deadline {
                    break false;
                }
                std::thread::sleep(Duration::from_millis(300));
            }
        }
    };
    service.delete().map_err(|e| format!("удаление: {e}"))?;
    let config = config_path();
    // ⚠ Права возвращаем ЗДЕСЬ, пока у нас ещё есть админ. Закрыла их служба
    // (`align_acl` при каждом старте), и после её снятия открыть обратно
    // некому: обычная учётка осталась бы с `runtime\` и `app\` на чтение —
    // ни обновления, ни правки, ни объяснения. Отказ icacls установку не
    // роняет: он ложится в журнал строкой, а служба уже удалена.
    let mut log = Log::open(&acl_log_dir(&config));
    relax_acl(&config, &mut log);
    // Службы больше нет — и предупреждение о ней больше не про что.
    let _ = std::fs::remove_file(warning_file(&config));
    // Задача сессии без службы бессмысленна, а забытая — поднимала бы харнесс
    // при каждом входе от имени снятого продукта. Ровно так на машине владельца
    // осталась жить служба прошлого поколения; повторять эту историю задачей
    // планировщика не будем.
    if let Err(err) = session_task_remove() {
        eprintln!("{err}");
    }
    if !stopped {
        return Err(format!(
            "служба {SERVICE_NAME} не остановилась за 20 с; запись помечена на удаление и исчезнет после перезагрузки"
        ));
    }
    println!("служба {SERVICE_NAME} удалена");
    Ok(())
}

fn main() {
    // Паника внутри service_main уходила через FFI-границу define_windows_service!
    // и служба исчезала без следа (strip=true, хука не было) — владелец видел
    // только 1053. Теперь причина попадает в журнал рядом с конфигом.
    std::panic::set_hook(Box::new(|info| {
        early_line(&config_path(), &format!("паника: {info}"));
    }));
    let mode = std::env::args().nth(1).unwrap_or_default();
    match mode.as_str() {
        "install" => {
            let raw = config_path();
            // Раньше при провале canonicalize подставлялся ОТНОСИТЕЛЬНЫЙ
            // "helene.json", и служба со стартовой папкой C:\Windows\System32
            // не находила конфиг никогда. Честный отказ лучше.
            let config = match raw.canonicalize() {
                Ok(p) => plain_path(&p),
                Err(e) => {
                    eprintln!("не установилась: нет конфига {} ({e})", raw.display());
                    std::process::exit(1);
                }
            };
            if let Err(e) = install(&config) {
                eprintln!("не установилась: {e}");
                std::process::exit(1);
            }
        }
        "uninstall" => {
            if let Err(e) = uninstall() {
                eprintln!("{e}");
                std::process::exit(1);
            }
        }
        "run" => {
            // Вход из SCM. Прямой запуск руками даст честную ошибку диспетчера.
            if service_dispatcher::start(SERVICE_NAME, ffi_service_main).is_err() {
                eprintln!("`run` зовёт SCM (это вход службы). Для отладки: foreground");
                std::process::exit(1);
            }
        }
        // Харнесс в сессии владельца. Сюда приходит планировщик по просьбе
        // службы (или по входу владельца). Права здесь — владельца, не системы:
        // задача заведена с /RL LIMITED.
        "session-host" | "foreground" => {
            let session = mode == "session-host";
            // `expect` здесь ронял программу паникой вместо внятной строки.
            let Some(raw) = arg_after("--config") else {
                eprintln!("{mode} --config <путь к helene.json>");
                std::process::exit(2);
            };
            let config = PathBuf::from(raw);
            let plan = match load_plan(&config).and_then(|p| plan_usable(&p).map(|_| p)) {
                Ok(p) => p,
                Err(e) => {
                    eprintln!("{e}");
                    std::process::exit(1);
                }
            };
            let mut log = Log::open(&plan.tree);
            if session {
                // Консоль отпускаем ПОСЛЕ разбора конфига: ранние отказы должны
                // успеть напечататься, а дальше журнал — единственный канал.
                // Планировщик запускает консольное приложение с новым окном, и
                // оно висело бы на рабочем столе всю жизнь харнесса.
                drop_console();
                log.line("харнесс поднят в сессии владельца (задача планировщика, без повышения)");
            }
            let stop = Arc::new(AtomicBool::new(false));
            let stop_ctrlc = stop.clone();
            let _ = ctrlc_handler(stop_ctrlc);
            // Второй экземпляр не страшен: планировщик по умолчанию не заводит
            // вторую копию задачи, а замок порта внутри supervise не даст
            // поднять вторую пару детей на одном дереве.
            // Брандмауэр из сессии владельца не трогаем: правило требует
            // повышения, его ставит служба.
            supervise(&plan, stop, &mut log, !session && plan.firewall_rule, &mut || {});
        }
        // Отдельные команды для установщика и обновления — чтобы не
        // переустанавливать службу ради одной задачи или одной правки прав.
        "session-task" => {
            let Some(raw) = arg_after("--config") else {
                eprintln!("session-task install|remove --config <путь к helene.json> [--user DOMAIN\\User]");
                std::process::exit(2);
            };
            let config = PathBuf::from(raw);
            let op = std::env::args().nth(2).unwrap_or_default();
            let done = match op.as_str() {
                "install" => session_task_install(&config).map(|user| {
                    println!("задача {SESSION_TASK} заведена под {user} (без повышения)");
                }),
                "remove" => session_task_remove().map(|()| println!("задача {SESSION_TASK} снята")),
                _ => {
                    eprintln!("session-task install|remove --config <путь> [--user DOMAIN\\User]");
                    std::process::exit(2);
                }
            };
            if let Err(e) = done {
                eprintln!("{e}");
                std::process::exit(1);
            }
        }
        "acl-align" | "acl-relax" => {
            let Some(raw) = arg_after("--config") else {
                eprintln!("{mode} --config <путь к helene.json>");
                std::process::exit(2);
            };
            let config = PathBuf::from(raw);
            // Журнал — туда же, куда пишет служба: одна история на все руки,
            // которые трогают права.
            let mut log = Log::open(&acl_log_dir(&config));
            if mode == "acl-align" {
                align_acl(&config, &mut log);
            } else {
                relax_acl(&config, &mut log);
            }
        }
        // Только труба брокера, без надзора за детьми — отладка.
        //
        // ⚠ ПОД ОБЫЧНЫМ ПОЛЬЗОВАТЕЛЕМ ЭТО НЕ ТО ЖЕ САМОЕ, что брокер службы:
        // процесс идёт правами владельца, а не СИСТЕМЫ. Значит `exec` поднимет
        // процесс правами владельца (никакого повышения), а
        // `spawn_interactive` не сможет взять токен сессии и честно откажет.
        // Годится проверить разбор, замки и журнал — не права.
        "broker" => {
            let Some(raw) = arg_after("--config") else {
                eprintln!("broker --config <путь к helene.json>");
                std::process::exit(2);
            };
            let config = PathBuf::from(raw);
            if let Err(e) = load_plan(&config) {
                eprintln!("{e}");
                std::process::exit(1);
            }
            let stop = Arc::new(AtomicBool::new(false));
            let _ = ctrlc_handler(stop.clone());
            broker_serve(config, stop);
        }
        _ => {
            eprintln!(
                "helene-svc install|uninstall|run|session-host|foreground|session-task|acl-align|acl-relax|broker [--config helene.json]"
            );
            std::process::exit(2);
        }
    }
}

/// Ctrl+C в foreground-режиме — без крейта ctrlc: SetConsoleCtrlHandler руками.
fn ctrlc_handler(stop: Arc<AtomicBool>) -> Result<(), ()> {
    use std::sync::OnceLock;
    static STOP: OnceLock<Arc<AtomicBool>> = OnceLock::new();
    STOP.set(stop).map_err(|_| ())?;
    #[cfg(windows)]
    unsafe {
        unsafe extern "system" fn handler(_ctrl: u32) -> i32 {
            if let Some(s) = STOP.get() {
                s.store(true, Ordering::Relaxed);
            }
            1
        }
        #[link(name = "kernel32")]
        unsafe extern "system" {
            fn SetConsoleCtrlHandler(
                handler: Option<unsafe extern "system" fn(u32) -> i32>,
                add: i32,
            ) -> i32;
        }
        SetConsoleCtrlHandler(Some(handler), 1);
    }
    Ok(())
}

#[cfg(test)]
mod session_tests {
    use super::*;

    fn joined(args: &[String]) -> String {
        args.join(" ")
    }

    /// Главное в задаче — НЕ «запускается», а «запускается под владельцем и без
    /// повышения». Ошибка ровно здесь и была: харнесс наследовал права службы.
    #[test]
    fn task_runs_as_owner_without_elevation() {
        let args = session_task_create_args(
            Path::new(r"C:\Users\Иван Петров\AppData\Local\Programs\Helene\helene-svc.exe"),
            Path::new(r"C:\Users\Иван Петров\AppData\Local\Programs\Helene\helene.json"),
            "DESKTOP\\Иван",
        );
        let line = joined(&args);
        // Под владельцем, его интерактивным токеном.
        assert!(args.windows(2).any(|w| w[0] == "/RU" && w[1] == "DESKTOP\\Иван"));
        assert!(args.iter().any(|a| a == "/IT"));
        // Без повышения: HIGHEST здесь означал бы ровно ту дыру, которую чиним.
        assert!(args.windows(2).any(|w| w[0] == "/RL" && w[1] == "LIMITED"));
        assert!(!line.contains("HIGHEST"));
        // Пароля не спрашиваем и не храним.
        assert!(!args.iter().any(|a| a == "/RP"));
        // Второй повод подняться — вход владельца.
        assert!(args.windows(2).any(|w| w[0] == "/SC" && w[1] == "ONLOGON"));
        // Перезапись: переустановка не плодит задач.
        assert!(args.iter().any(|a| a == "/F"));
        assert!(args.windows(2).any(|w| w[0] == "/TN" && w[1] == SESSION_TASK));
    }

    /// Пробелы в пути — не редкость, а обычное «C:\Users\Иван Петров». Без
    /// кавычек schtasks разобрал бы это как две команды.
    #[test]
    fn task_command_is_quoted() {
        let args = session_task_create_args(
            Path::new(r"C:\Program Files\Helene\helene-svc.exe"),
            Path::new(r"C:\Program Files\Helene\helene.json"),
            "USER",
        );
        let at = args.iter().position(|a| a == "/TR").expect("нет /TR");
        let tr = &args[at + 1];
        assert_eq!(
            tr,
            "\"C:\\Program Files\\Helene\\helene-svc.exe\" session-host --config \"C:\\Program Files\\Helene\\helene.json\""
        );
        // Режим тот самый: не `run` (вход SCM) и не `foreground`.
        assert!(tr.contains("session-host"));
    }

    /// Верватим-путь `\\?\C:\…` планировщик не понимает — снимаем префикс.
    #[test]
    fn task_command_has_no_verbatim_prefix() {
        let args = session_task_create_args(
            Path::new(r"\\?\C:\Helene\helene-svc.exe"),
            Path::new(r"\\?\C:\Helene\helene.json"),
            "USER",
        );
        let tr = &args[args.iter().position(|a| a == "/TR").unwrap() + 1];
        assert!(!tr.contains(r"\\?\"), "верватим-префикс уехал в задачу: {tr}");
        assert!(tr.starts_with("\"C:\\Helene\\helene-svc.exe\""));
    }

    #[test]
    fn task_run_end_delete_name_the_same_task() {
        for args in [
            session_task_run_args(),
            session_task_end_args(),
            session_task_delete_args(),
            session_task_query_args(),
        ] {
            assert!(
                args.windows(2).any(|w| w[0] == "/TN" && w[1] == SESSION_TASK),
                "команда не про нашу задачу: {args:?}"
            );
        }
        assert!(session_task_delete_args().iter().any(|a| a == "/F"));
    }
}

#[cfg(test)]
mod acl_tests {
    use super::*;

    /// Что закрываем и что оставляем открытым. Дом агента (`data`) и его
    /// собственный код (`tree`) обязаны остаться писуемыми — иначе ломается
    /// сама идея песочницы как свободы, а не клетки.
    #[test]
    fn hardened_paths_close_code_and_leave_home_open() {
        let root = std::env::temp_dir().join(format!("helene-acl-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        for dir in ["runtime", "app", "data", "tree", "data/memory"] {
            std::fs::create_dir_all(root.join(dir)).unwrap();
        }
        for file in ["helene.exe", "helene-svc.exe", "WebView2Loader.dll", "helene.json", "ЛИЦЕНЗИЯ.md"] {
            std::fs::write(root.join(file), b"x").unwrap();
        }
        let closed = hardened_paths(&root);
        let names: Vec<String> = closed
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect();
        for must in ["runtime", "app", "helene.exe", "helene-svc.exe", "WebView2Loader.dll"] {
            assert!(names.iter().any(|n| n == must), "не закрыли {must}: {names:?}");
        }
        for must_not in ["data", "tree", "helene.json", "ЛИЦЕНЗИЯ.md"] {
            assert!(!names.iter().any(|n| n == must_not), "закрыли лишнее: {must_not}");
        }
        // Сам корень не трогаем: в нём helene.json, который продукт переписывает
        // при каждой правке настроек.
        assert!(!closed.iter().any(|p| p == &root));
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn harden_gives_user_read_and_execute_only() {
        let args = harden_args(Path::new(r"C:\Helene\runtime"), true);
        let line = args.join(" ");
        // Наследование снимаем — иначе Modify пользователя приезжает от корня.
        assert!(args.iter().any(|a| a == "/inheritance:r"), "{line}");
        // Пишут только система и администраторы.
        assert!(line.contains("*S-1-5-18:(OI)(CI)F"), "{line}");
        assert!(line.contains("*S-1-5-32-544:(OI)(CI)F"), "{line}");
        // Пользователь — только читать и выполнять. Ни M, ни F, ни W.
        assert!(line.contains("*S-1-5-32-545:(OI)(CI)RX"), "{line}");
        assert!(line.contains("*S-1-5-11:(OI)(CI)RX"), "{line}");
        assert!(!line.contains("545:(OI)(CI)M"), "{line}");
        assert!(!line.contains("545:(OI)(CI)F"), "{line}");
        // Имена групп локализованы («Пользователи») — только SID.
        assert!(!line.contains("Users"), "{line}");
        assert!(!line.contains("SYSTEM"), "{line}");
        // /grant:r, а не /grant: замена, а не добавка — иначе идемпотентности нет.
        assert_eq!(args.iter().filter(|a| *a == "/grant:r").count(), 4, "{line}");
        assert!(!args.iter().any(|a| a == "/grant"), "{line}");
        // Рекурсии нет: наследование разносит права само, а /T на runtime с
        // тысячами файлов — это минуты на каждой загрузке.
        assert!(!args.iter().any(|a| a == "/T"), "{line}");
        // /reset снёс бы ЯВНЫЕ разрешения, в том числе выданные песочницей
        // контейнеру на runtime\ и app\ — и она осталась бы без рантайма.
        assert!(!args.iter().any(|a| a == "/reset"), "{line}");
        assert!(!args.iter().any(|a| a == "/setowner"), "{line}");
    }

    /// У файла наследовать нечему: (OI)(CI) на файле icacls не примет.
    #[test]
    fn harden_file_has_no_inheritance_flags() {
        let args = harden_args(Path::new(r"C:\Helene\helene-svc.exe"), false);
        let line = args.join(" ");
        assert!(!line.contains("(OI)"), "{line}");
        assert!(!line.contains("(CI)"), "{line}");
        assert!(line.contains("*S-1-5-32-545:RX"), "{line}");
        assert!(line.contains("*S-1-5-18:F"), "{line}");
    }

    /// Обратное действие — ровно обратное закрытию: вернуть наследование, и
    /// ничего больше. Явные разрешения (в том числе выданные песочницей) при
    /// этом остаются на месте — потому и не /reset.
    #[test]
    fn relax_only_restores_inheritance() {
        let args = relax_args(Path::new(r"C:\Helene\app"));
        assert_eq!(
            args,
            vec![r"C:\Helene\app".to_string(), "/inheritance:e".to_string()]
        );
    }

    /// Снятие службы возвращает права, и делает это ЖУРНАЛЬНО — в ту же
    /// историю, что писала сама служба. Конфиг при снятии сплошь и рядом
    /// сломан или его нет вовсе: тогда журнал ложится рядом с ним, а не
    /// пропадает.
    #[test]
    fn acl_log_dir_prefers_the_tree_and_falls_back_to_the_config() {
        let dir = std::env::temp_dir().join("helene-test-acl-log-dir");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let config = dir.join("helene.json");

        // Конфига нет — журнал рядом с ним, в data\ (там же его ищет early_line).
        assert_eq!(acl_log_dir(&config), dir.join("data"));

        // Конфиг есть — журнал в дереве, как у службы.
        std::fs::write(&config, br#"{"mode":"local","tree":"memory"}"#).unwrap();
        assert_eq!(acl_log_dir(&config), dir.join("memory"));

        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Закрытие и открытие ходят по ОДНОМУ И ТОМУ ЖЕ списку мест: разойдись
    /// они — обновление уткнулось бы в папку, которую никто не открыл.
    #[test]
    fn harden_and_relax_walk_the_same_places() {
        let paths = [Path::new(r"C:\Helene\runtime"), Path::new(r"C:\Helene\helene.exe")];
        for p in paths {
            assert_eq!(harden_args(p, true)[0], relax_args(p)[0]);
        }
    }
}

#[cfg(test)]
mod broker_tests {
    use super::*;

    /// Токен — второй замок брокера. Прочитать его должны только СИСТЕМА,
    /// администраторы и владелец: файл лежит в дереве данных, а дерево по
    /// построению открыто самому агенту и всему, что от него запускается.
    #[test]
    fn token_file_is_closed_to_everyone_else() {
        let args = broker_token_acl_args(
            Path::new(r"C:\Helene\data\memory\.state\broker-token"),
            "S-1-5-21-1-2-3-1001",
        );
        let line = args.join(" ");
        assert!(args.iter().any(|a| a == "/inheritance:r"), "{line}");
        assert!(line.contains("*S-1-5-18:F"), "{line}");
        assert!(line.contains("*S-1-5-32-544:F"), "{line}");
        assert!(line.contains("*S-1-5-21-1-2-3-1001:F"), "{line}");
        // «Пользователи» и «Прошедшие проверку» — никого: на папку установки
        // им читать можно, на секрет брокера нельзя.
        assert!(!line.contains("S-1-5-32-545"), "{line}");
        assert!(!line.contains("S-1-5-11"), "{line}");
        // Замена, а не добавка — иначе повторный вызов копил бы права.
        assert!(!args.iter().any(|a| a == "/grant"), "{line}");
        // Наследование не возвращаем и владельца не меняем.
        assert!(!args.iter().any(|a| a == "/reset" || a == "/setowner"), "{line}");
    }

    /// Главная развилка брокера: без галочки нулевой сессии `exec` не работает.
    /// Ошибка здесь означает не «не сработало», а «агент молча получил права
    /// СИСТЕМЫ».
    #[test]
    fn firewall_door_is_separate_from_system_rights() {
        // Две двери — две галочки. Пока они стояли на одной, кнопка «Телефон»
        // под службой была мертва по умолчанию, и оживить её можно было только
        // выдав агенту право поднимать ЛЮБОЙ процесс правами системы.
        assert!(broker_firewall_allowed(true).is_ok());
        let err = broker_firewall_allowed(false)
            .expect_err("поставил правило при выключенной галочке service.firewall");
        assert!(err.contains("брандмауэра"), "отказ обязан называть галочку: {err}");
        // И обратно: разрешённый брандмауэр НЕ открывает нулевую сессию.
        assert!(broker_exec_allowed(false).is_err());
    }

    #[test]
    fn exec_is_refused_while_session0_is_off() {
        let err = broker_exec_allowed(false).expect_err("пустил СИСТЕМУ при выключенной галочке");
        // Отказ обязан объяснять СЕБЯ владельцу: он читает это в квитанции.
        assert!(err.contains("нулевая сессия"), "{err}");
        assert!(err.contains("Настройках"), "{err}");
        assert!(err.contains("spawn_interactive"), "{err}");
        assert!(broker_exec_allowed(true).is_ok());
    }

    /// Третий замок: труба видна СИСТЕМЕ, а СИСТЕМА — это ещё и всякая служба
    /// на машине. Клиентом брокера может быть только процесс живого человека.
    #[test]
    fn only_the_active_session_is_a_client() {
        assert!(broker_client_ok(0, 1).is_err(), "пустил процесс из нулевой сессии");
        assert!(broker_client_ok(2, 1).is_err(), "пустил чужую сессию");
        assert!(broker_client_ok(1, 1).is_ok());
        // Консольной сессии сейчас нет (переключение пользователей): чужую
        // сессию отсеять нечем, но нулевую — по-прежнему есть.
        assert!(broker_client_ok(3, u32::MAX).is_ok());
        assert!(broker_client_ok(0, u32::MAX).is_err());
    }

    /// Сравнение секретов не должно возвращаться на первом несовпавшем байте.
    #[test]
    fn secret_compare_is_length_and_content() {
        assert!(secret_eq("abc123", "abc123"));
        assert!(!secret_eq("abc123", "abc124"));
        assert!(!secret_eq("abc123", "abc1234"));
        assert!(!secret_eq("", "x"));
    }

    /// Журнал читает человек. Одно обращение — одна строка, и в ней обязаны
    /// быть «зачем», команда и чем кончилось.
    #[test]
    fn journal_line_says_why_and_how_it_ended() {
        let ask = BrokerAsk::new(
            "deadbeefdeadbeef",
            BrokerOp::Exec,
            r"C:\Windows\System32\netsh.exe",
            &["advfirewall".into(), "name=Helene (8094)".into()],
            "правило брандмауэра для телефона",
        );
        let receipt = BrokerReceipt {
            id: String::new(),
            ok: true,
            op: "exec".into(),
            code: Some(0),
            pid: Some(4242),
            out: String::new(),
            err: String::new(),
            ms: 145,
            at: String::new(),
            why: ask.why.clone(),
            note: String::new(),
        };
        let line = broker_journal_line(&ask, &receipt, 1234, 1);
        assert!(!line.contains('\n'), "строка разъехалась: {line}");
        assert!(line.contains("правило брандмауэра для телефона"), "{line}");
        assert!(line.contains("netsh.exe"), "{line}");
        assert!(line.contains("код 0"), "{line}");
        assert!(line.contains("145 мс"), "{line}");
        assert!(line.contains("pid 1234"), "{line}");

        // Отказ виден словом, а не отсутствием кода.
        let refused = BrokerReceipt::refused(Some(&ask), "[04.09.2026 13:00:00]", "нулевая сессия выключена");
        let line = broker_journal_line(&ask, &refused, 1234, 1);
        assert!(line.contains("ОТКАЗ"), "{line}");
        assert!(line.contains("нулевая сессия выключена"), "{line}");
        assert!(!line.contains("код 0"), "отказ выглядит как удача: {line}");
    }

    /// Незавершённый процесс не должен выглядеть как удачно вернувший ноль.
    #[test]
    fn unfinished_process_is_not_a_zero() {
        let ask = BrokerAsk::new(
            "deadbeefdeadbeef",
            BrokerOp::SpawnInteractive,
            r"C:\Windows\System32\notepad.exe",
            &[],
            "владелец попросил открыть блокнот",
        );
        let receipt = BrokerReceipt {
            id: String::new(),
            ok: true,
            op: "spawn_interactive".into(),
            code: None,
            pid: Some(7),
            out: String::new(),
            err: String::new(),
            ms: 60000,
            at: String::new(),
            why: ask.why.clone(),
            note: "процесс ещё работает (pid 7)".into(),
        };
        let line = broker_journal_line(&ask, &receipt, 1234, 1);
        assert!(line.contains("не дождался"), "{line}");
        assert!(line.contains("ещё работает"), "{line}");
    }

    /// Имя трубы и путь к секрету считаются из папки установки и дерева — тех
    /// же, что видит оболочка. Разъедься они, клиент стучался бы не в ту трубу.
    #[test]
    fn pipe_and_token_are_derived_from_the_install() {
        let root = Path::new(r"C:\Users\Иван\AppData\Local\Programs\Helene");
        let name = broker_pipe_name(root);
        assert!(name.starts_with(r"\\.\pipe\helene-broker-"));
        // В имени только то, что годится для имени трубы: ни слэшей, ни
        // пробелов из пути установки.
        let tail = name.trim_start_matches(r"\\.\pipe\");
        assert!(tail.chars().all(|c| c.is_ascii_alphanumeric() || c == '-'), "{name}");
        assert_eq!(
            broker_token_path(Path::new(r"C:\Helene\data")),
            Path::new(r"C:\Helene\data\memory\.state\broker-token")
        );
    }
}

#[cfg(test)]
mod tests {
    use super::decode_config;

    /// Три способа, которыми владелец сохранит helene.json руками, — и все три
    /// служба обязана читать так же, как оболочка.
    #[test]
    fn config_encodings() {
        let plain = b"{\"a\": 1}".to_vec();
        assert_eq!(decode_config(&plain).unwrap(), "{\"a\": 1}");

        let mut with_bom = vec![0xEF, 0xBB, 0xBF];
        with_bom.extend_from_slice(b"{\"a\": 1}");
        assert_eq!(decode_config(&with_bom).unwrap(), "{\"a\": 1}");

        let mut utf16 = vec![0xFF, 0xFE];
        for unit in "{\"a\": 1}".encode_utf16() {
            utf16.extend_from_slice(&unit.to_le_bytes());
        }
        assert_eq!(decode_config(&utf16).unwrap(), "{\"a\": 1}");

        let mut be = vec![0xFE, 0xFF];
        for unit in "{\"a\": 1}".encode_utf16() {
            be.extend_from_slice(&unit.to_be_bytes());
        }
        assert_eq!(decode_config(&be).unwrap(), "{\"a\": 1}");
    }
}

#[cfg(test)]
mod contract_tests {
    /// Константы службы — те же, что в `ui-kit/contract.json` (одно место
    /// для трёх языков; задача A п. 1.12).
    #[test]
    fn contract_json_matches_constants() {
        let c: serde_json::Value = serde_json::from_str(include_str!("../../ui-kit/contract.json")).unwrap();
        assert_eq!(c["ports"]["relay"], super::RELAY_PORT);
        assert_eq!(c["config_name"], super::CONFIG_NAME);
    }
}
