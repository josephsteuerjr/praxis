// Hélène — нативная оболочка основной программы (Rust, Tauri: окно + WebView2,
// ни одного консольного окна по построению — windows_subsystem ниже).
//
// Архитектура продукта: харнесс — самопереписываемый Python-организм агента,
// его язык не меняется. Оболочка — то, что не самопереписывается: окно, рендер,
// связь, трей, уведомления. Где живёт харнесс, решает `helene.json` рядом с exe:
//
//   {"mode": "local", "python": "python", "app": "..\\deskapp.py",
//    "tree": "data", "port": 8094, "agent": {"name": "…"}}
//   {"mode": "remote", "base": "https://…", "key": "…"}
//
// local: оболочка поднимает харнесс дочерним тихим процессом (CREATE_NO_WINDOW)
// и втыкает связь в 127.0.0.1 — тот же протокол frame.desk.v1, что и к VPS.
// Файла нет — поведение прежнее: вшитый сборкой config.js (удалённый харнесс).
//
// Обязательное по решениям владельца 02.09:
//   * один экземпляр: повторный запуск поднимает и фокусирует живое окно;
//   * закрыть окно = в трей, и в первый раз об этом говорит уведомление;
//   * слово агента, когда окно не перед глазами, приходит уведомлением Windows;
//   * имя агента — из конфига; продукт зовётся Hélène.
//
// macOS (порт 0.7.1): та же оболочка, но бинарь живёт ВНУТРИ бандла
// (`Helene.app/Contents/MacOS/helene`), а установка — папкой выше
// (`~/Applications/Helene`, там `helene.json`, `runtime/`, `app/`, `data/`).
// Поэтому «рядом с exe» здесь означает не папку exe, а корень установки —
// `install_root()`, который ищет паспорт сборки `helene-build.json` вверх по
// родителям (на Windows паспорт лежит рядом с exe, и корень — та же папка).
// Службы, тела и брандмауэра на Mac нет: их ручки честно отвечают «нет», а окно
// прячет карточки по `app_info.platform`. Дети без job-объекта живут в своей
// группе процессов и получают `HELENE_PARENT_PID`, чтобы уйти вместе с окном.
#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

// Тот же гард, что у установщика (setup/src/main.rs), и по той же причине —
// асимметрия сборки. У оболочки нет devUrl, поэтому без фичи она собирается и
// даже работает; но tauri-codegen считает такую сборку dev'ом и дописывает в
// exe абсолютный путь папки сборки (with_config_parent) — в публичный релиз
// уезжает домашний путь владельца, а бинарь молча получает статус dev-сборки.
// Отличить его грепом нельзя, build_dist.py копирует exe вслепую, CI нет.
// Пусть такая сборка просто не соберётся.
#[cfg(all(not(debug_assertions), not(feature = "custom-protocol")))]
compile_error!(
    "релизная сборка оболочки без --features custom-protocol вшивает в exe путь папки сборки \
     и метит бинарь как dev; собирай `cargo build --release --features custom-protocol` или `tauri build`"
);

use std::io::{Read, Seek, SeekFrom};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::Manager;

#[cfg(windows)]
use std::os::windows::process::CommandExt;
// POSIX: `process_group(0)` — каждый ребёнок в своей группе процессов, чтобы
// остановить его вместе с внуками одним `killpg` (замена job-объекту Windows).
#[cfg(unix)]
use std::os::unix::process::CommandExt;

#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Имя в файловой системе (ярлык, папка) — латиницей; на экране — по-французски.
const PRODUCT: &str = "Helene";
const PRODUCT_UI: &str = "Hélène";
/// Порты по умолчанию — те же, что в `ui-kit/contract.json` (тест
/// `contract_json_matches_constants` сверяет): канал окна и встроенное реле.
/// ⚠ `DESK_PORT` живёт в `common/agents.rs`: список агентов считает от него
/// порты соседей, и служба берёт оттуда же — двух умолчаний быть не должно.
const RELAY_PORT: u16 = 5011;
const CONFIG_NAME: &str = "helene.json";
/// Паспорт сборки (`installer/build_dist.py`): лежит в корне установки, и по
/// нему корень и находится — см. `install_root`.
const BUILD_PASSPORT: &str = "helene-build.json";
/// Комната окна в памяти агента: memory/groups/<WINDOW_ROOM>.jsonl.
const WINDOW_ROOM: &str = "window";
/// AppUserModelID уведомлений = identifier из tauri.conf.json (см. register_toast_identity).
const TOAST_ID: &str = "app.helene.desk";

/// Имя продукта ЭТОЙ СБОРКИ. Константы выше — значения Hélène по умолчанию; та же
/// оболочка, собранная с другим `productName`/`identifier` (через TAURI_CONFIG при
/// сборке), зовётся своим именем: окно к серверу = «Praxis» / `app.praxis.desk`. До
/// 09.09 имя было вшито, и окно Праксис на компьютере владельца выглядело как второе
/// окно Hélène: тот же заголовок, значок, подпись, ярлык и уведомления (слово владельца).
/// Читается один раз в main() из generate_context!(); до этого — значения Hélène.
struct ProductIdentity {
    fs: String,
    ui: String,
    toast: String,
}

static PRODUCT_RT: std::sync::OnceLock<ProductIdentity> = std::sync::OnceLock::new();

fn init_product(config: &tauri::Config) {
    let name = config
        .product_name
        .clone()
        .map(|n| n.trim().to_string())
        .filter(|n| !n.is_empty())
        .unwrap_or_else(|| PRODUCT.to_string());
    let ui = if name == PRODUCT { PRODUCT_UI.to_string() } else { name.clone() };
    let _ = PRODUCT_RT.set(ProductIdentity { fs: name, ui, toast: config.identifier.clone() });
}

/// Имя в файловой системе (ярлыки, правило брандмауэра, служба) — латиницей.
fn product_fs() -> &'static str {
    PRODUCT_RT.get().map(|p| p.fs.as_str()).unwrap_or(PRODUCT)
}

/// Имя на экране: заголовок окна, трей, уведомления, подпись внизу окна.
fn product_ui() -> &'static str {
    PRODUCT_RT.get().map(|p| p.ui.as_str()).unwrap_or(PRODUCT_UI)
}

/// AppUserModelID уведомлений = identifier сборки.
fn toast_id() -> &'static str {
    PRODUCT_RT.get().map(|p| p.toast.as_str()).unwrap_or(TOAST_ID)
}

/// Владелец уже убирал окно в трей: отложенный показ окна не должен вытаскивать
/// его обратно поверх всего, чем человек занят.
static HIDDEN_BY_OWNER: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Окно открылось с экраном «порт держит чужая установка»: адрес харнесса в
/// вебвью не подставлен вовсе. Если чужая копия закроется и надзор поднимет
/// СВОЙ харнесс, окно об этом ещё не знает — и должно сказать владельцу, что
/// теперь его стоит перезапустить.
static BLOCKED_BY_FOREIGN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

/// Журнал оболочки рядом с exe: то, что иначе терялось бы без консоли.
/// Строка в helene.log. Штамп — местное время с секундами, тот же формат, что
/// у service.log и broker.log (`common/stamp.rs`); раньше здесь были секунды
/// эпохи, и три журнала продукта шли в трёх системах счисления.
fn log_line(text: &str) {
    use std::io::Write;
    if let Ok(mut f) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(install_root().join("helene.log"))
    {
        let _ = writeln!(f, "{} {text}", now_stamp());
    }
}

/// Уведомление Windows. Ошибка не глотается: она уходит в helene.log.
#[cfg(windows)]
fn toast(title: &str, body: &str) {
    use tauri_winrt_notification::{Duration as ToastDuration, Toast};
    let result = Toast::new(toast_id())
        .title(title)
        .text1(body)
        .duration(ToastDuration::Short)
        .show();
    if let Err(err) = result {
        log_line(&format!("уведомление не показалось: {err}"));
    }
}

/// Уведомление macOS — Центр уведомлений через `notify-rust` НАПРЯМУЮ и
/// синхронно. Не через tauri-plugin-notification: его `show()` отвечает
/// `Ok(())` всегда (внутри `let _ = spawn(…)`), и отказ Центра уведомлений
/// пропадал бы без строки в журнале — а это слово агента человеку.
/// Отправитель (`set_application`) ставится один раз на процесс: бандл с нашим
/// идентификатором должен быть известен LaunchServices; не известен (сборка не
/// в бандле, первый запуск из staging) — уведомление идёт от имени процесса
/// как есть, и об этом сказано в helene.log. Не показалось — тот же текст
/// через `osascript display notification`, и отказ обоих путей — в журнал.
#[cfg(target_os = "macos")]
fn toast(title: &str, body: &str) {
    static SENDER: std::sync::Once = std::sync::Once::new();
    SENDER.call_once(|| {
        if let Err(err) = notify_rust::set_application(toast_id()) {
            log_line(&format!(
                "уведомления: отправитель {} не принят ({err}) — уведомления пойдут от имени процесса как есть",
                toast_id()
            ));
        }
    });
    let sent = notify_rust::Notification::new().summary(title).body(body).show();
    if let Err(why) = sent {
        let script = format!(
            "display notification {} with title {}",
            applescript_quote(body),
            applescript_quote(title)
        );
        if let Err(err) = osascript(&script) {
            log_line(&format!("уведомление не показалось: {why}; и через osascript тоже: {err}"));
        }
    }
}

#[cfg(not(any(windows, target_os = "macos")))]
fn toast(_title: &str, _body: &str) {}

/// Строковый литерал AppleScript: обратный слэш и кавычка экранируются,
/// переводы строк — `\n`. Текст диалогов и уведомлений приходит и из ответов
/// сервера обновлений, и из слов агента — кавычка в нём не должна рвать скрипт.
/// Без cfg намеренно: зовётся только на macOS, а стенд на неё гоняется и на
/// Windows — чистая функция обязана собираться везде.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn applescript_quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => out.push(c),
        }
    }
    out.push('"');
    out
}

/// Выполнить AppleScript и вернуть его stdout. Без дедлайна намеренно: диалог
/// ждёт человека столько, сколько нужно, — как MessageBoxW на Windows.
/// Полный путь, а не голое имя: правило то же, что у `sys_exe`.
#[cfg(target_os = "macos")]
fn osascript(script: &str) -> Result<String, String> {
    let out = Command::new("/usr/bin/osascript")
        .arg("-e")
        .arg(script)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("osascript не запустился: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
    } else {
        let said = String::from_utf8_lossy(&out.stderr).trim().to_string();
        Err(if said.is_empty() {
            format!("osascript вернул код {}", out.status.code().unwrap_or(-1))
        } else {
            said
        })
    }
}

/// Окно с ошибкой — последний канал, когда журнала мало и окна ещё/уже нет.
/// Оболочка собрана как windows_subsystem="windows": консоли у неё нет, и без
/// этого владелец не получал вообще ничего — ни строки, ни звука.
#[cfg(windows)]
fn message_box_with(title: &str, text: &str, icon: u32) {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, MB_OK, MB_SETFOREGROUND, MB_TOPMOST,
    };
    let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
    let (caption, body) = (wide(title), wide(text));
    unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            body.as_ptr(),
            caption.as_ptr(),
            MB_OK | icon | MB_SETFOREGROUND | MB_TOPMOST,
        );
    }
}

#[cfg(windows)]
fn message_box(title: &str, text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::MB_ICONERROR;
    message_box_with(title, text, MB_ICONERROR);
}

/// То же окно, но со значком «просто сообщаю»: крест ошибки там, где ничего
/// не сломалось, — это неправда об экране.
#[cfg(windows)]
fn message_box_info(title: &str, text: &str) {
    use windows_sys::Win32::UI::WindowsAndMessaging::MB_ICONINFORMATION;
    message_box_with(title, text, MB_ICONINFORMATION);
}

/// macOS: родной диалог через `display dialog` (osascript). Не открылся —
/// текст целиком уходит в helene.log: раньше not(windows)-ветка молчала, и это
/// было «окно не открылось, и никто не сказал почему».
#[cfg(target_os = "macos")]
fn message_box_with(title: &str, text: &str, icon: &str) {
    let script = format!(
        "display dialog {} with title {} buttons {{\"OK\"}} default button 1 with icon {icon}",
        applescript_quote(text),
        applescript_quote(title)
    );
    if let Err(err) = osascript(&script) {
        log_line(&format!("окно с сообщением не открылось ({err}); текст был: {title} — {text}"));
    }
}

#[cfg(target_os = "macos")]
fn message_box(title: &str, text: &str) {
    message_box_with(title, text, "stop");
}

#[cfg(target_os = "macos")]
fn message_box_info(title: &str, text: &str) {
    message_box_with(title, text, "note");
}

/// Прочие POSIX: окна нет, но текст не пропадает — он в журнале.
#[cfg(not(any(windows, target_os = "macos")))]
fn message_box(title: &str, text: &str) {
    log_line(&format!("окно с сообщением показать нечем; текст был: {title} — {text}"));
}

#[cfg(not(any(windows, target_os = "macos")))]
fn message_box_info(title: &str, text: &str) {
    log_line(&format!("окно с сообщением показать нечем; текст был: {title} — {text}"));
}

/// То же окно, но не задерживая запуск: окно программы должно открыться, даже
/// если человек не подошёл нажать «ОК».
fn message_box_async(title: String, text: String) {
    std::thread::spawn(move || message_box(&title, &text));
}

/// Паника в оконном exe гасила процесс АБСОЛЮТНО молча: окно исчезало или не
/// появлялось, в helene.log оставалась одна строка «старт». Теперь любая
/// паника — в любом потоке — оставляет причину в журнале и показывает окно.
/// Второй раз окно не показываем: петля паник не должна засыпать экран.
fn install_panic_hook() {
    static SHOWN: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
    let previous = std::panic::take_hook();
    std::panic::set_hook(Box::new(move |info| {
        let place = info
            .location()
            .map(|l| format!("{}:{}", l.file(), l.line()))
            .unwrap_or_else(|| "неизвестно где".into());
        let what = info
            .payload()
            .downcast_ref::<&str>()
            .map(|s| (*s).to_string())
            .or_else(|| info.payload().downcast_ref::<String>().cloned())
            .unwrap_or_else(|| "без описания".into());
        let text = format!("ПАНИКА {place}: {what}");
        log_line(&text);
        if !SHOWN.swap(true, std::sync::atomic::Ordering::SeqCst) {
            message_box(
                &format!("{}: сбой", product_ui()),
                &format!("{text}\n\nПодробности — в helene.log рядом с программой."),
            );
        }
        previous(info);
    }));
}

/// Системные программы — только полным путём из %SystemRoot%.
/// Command::new("powershell") ищет exe СНАЧАЛА в папке своего процесса: файл
/// powershell.exe, положенный рядом с helene.exe (а туда пишет и сам агент —
/// fence.py разрешает ему папку установки), исполнялся бы вместо системного,
/// в том числе под UAC в install_service.
///
/// Вся четвёрка — только Windows: на macOS ни один путь не должен до них
/// дойти, и cfg делает такой вызов ошибкой сборки, а не сюрпризом в журнале.
#[cfg(windows)]
fn system_root() -> PathBuf {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:\\Windows"))
}

#[cfg(windows)]
fn sys_exe(name: &str) -> PathBuf {
    let full = system_root().join("System32").join(name);
    if full.exists() {
        full
    } else {
        PathBuf::from(name)
    }
}

#[cfg(windows)]
fn powershell_exe() -> PathBuf {
    let full = system_root()
        .join("System32")
        .join("WindowsPowerShell")
        .join("v1.0")
        .join("powershell.exe");
    if full.exists() {
        full
    } else {
        PathBuf::from("powershell.exe")
    }
}

#[cfg(windows)]
fn explorer_exe() -> PathBuf {
    let full = system_root().join("explorer.exe");
    if full.exists() {
        full
    } else {
        PathBuf::from("explorer.exe")
    }
}

/// Системная утилита POSIX — полным путём (`/usr/bin`, `/bin`), голое имя
/// только когда её там нет. Та же причина, что у `sys_exe`: текущая папка в
/// поиск программ на POSIX не входит, но PATH владельца — его, и подменять
/// `open` или `zip` через него незачем.
#[cfg(not(windows))]
fn posix_tool(name: &str) -> PathBuf {
    for dir in ["/usr/bin", "/bin", "/usr/sbin", "/sbin"] {
        let full = Path::new(dir).join(name);
        if full.is_file() {
            return full;
        }
    }
    PathBuf::from(name)
}

/// Как поднять ребёнка заново, если он упал: оболочка — надзиратель, а не
/// просто запускатель. Всё нужное для повторного spawn лежит здесь.
/// ⚠ `token` появился 11.09 вместе с несколькими агентами. Раньше секрет брался
/// из глобального `desk_token()` прямо в момент спавна — при одном агенте это
/// было одно и то же. При двух глобальный секрет принадлежит ТОМУ, кого окно
/// показывает сейчас, и дети второго получали бы чужой ключ: канал ответил бы
/// им 403 в собственном доме. Секрет — свойство дерева, поэтому едет со спекой.
#[derive(Clone)]
enum ChildSpec {
    /// `config` — ЧЕЙ это ребёнок по конфигу, а не по расположению кода.
    ///
    /// ⚠ Поймано приёмкой 11.09 пробой на поддельной установке с двумя агентами: канал
    /// соседа читал и ПЕРЕПИСЫВАЛ корневой `helene.json`. Причина не в ручках канала, а
    /// одна на всех: `readers.config_path()` ищет конфиг от `__file__`, а код у всех
    /// агентов установки общий (`localharness/agents.py` намеренно пишет соседу
    /// `app/runner/python/code` как `../../…`). Значит любой читатель конфига у второго
    /// агента читал первого. Раннер об этом знал (`--config`), канал — нет.
    Script { python: PathBuf, script: PathBuf, args: Vec<String>, tree: PathBuf, host: String, token: String, config: PathBuf },
    Relay { base: PathBuf, cfg: serde_json::Value, tree: PathBuf },
}

impl ChildSpec {
    fn label(&self) -> String {
        match self {
            ChildSpec::Script { script, .. } => script
                .file_name()
                .map(|s| s.to_string_lossy().into_owned())
                .unwrap_or_default(),
            ChildSpec::Relay { .. } => exe_name("helene-relay"),
        }
    }

    /// Как назвать это владельцу. Имён питоновских файлов он нигде в программе
    /// не видит, а уведомление о падении получал именно ими («deskapp.py
    /// падает раз за разом»).
    fn human(&self) -> &'static str {
        match self {
            ChildSpec::Script { script, .. } => {
                match script.file_name().map(|s| s.to_string_lossy().into_owned()).unwrap_or_default().as_str() {
                    "deskapp.py" => "Связь с агентом",
                    "runner.py" => "Агент",
                    _ => "Часть программы",
                }
            }
            ChildSpec::Relay { .. } => "Подписка ChatGPT",
        }
    }

    /// Куда пишется вывод этого ребёнка (реле ведёт свои логи само).
    fn log_path(&self) -> Option<PathBuf> {
        match self {
            ChildSpec::Script { script, tree, .. } => {
                let stem = script.file_stem()?.to_string_lossy().into_owned();
                Some(tree.join(format!("{stem}.log")))
            }
            ChildSpec::Relay { .. } => None,
        }
    }

    fn spawn(&self) -> SpawnOutcome {
        match self {
            ChildSpec::Script { python, script, args, tree, host, token, config } => {
                match spawn_child(python, script, args, tree, host, token, config) {
                    Some(child) => SpawnOutcome::Started(child),
                    None => SpawnOutcome::Failed,
                }
            }
            ChildSpec::Relay { base, cfg, tree } => match spawn_relay(base, cfg, tree) {
                RelaySpawn::Started(child) => SpawnOutcome::Started(child),
                RelaySpawn::WaitingPort(port) => SpawnOutcome::Waiting(format!(
                    "порт реле {port} держит другая копия (служба или прежнее окно) — своё реле \
                     не поднимаю и подниму, когда он освободится"
                )),
                RelaySpawn::Unavailable(why) => {
                    log_line(&why);
                    SpawnOutcome::Failed
                }
            },
        }
    }
}

/// Исход подъёма ребёнка — типизированный (25.09, E.1). «Порт занят» — это ОЖИДАНИЕ, а
/// не падение: раньше реле у окна при занятом порте 5011 писало в журнал каждые 30 с
/// «не поднялся, ещё попытка», хотя ждать было единственно верным.
enum SpawnOutcome {
    Started(Child),
    /// Ждём внешнего события (освободится порт). Слово — один раз на вход в состояние.
    Waiting(String),
    /// Не поднялся: причина уже в журнале, надзор попробует через 30 с.
    Failed,
}

/// Ребёнок под надзором. `child` — Option, потому что «не поднялся» не значит
/// «забыть навсегда»: раньше провал ПЕРВОГО спавна выбрасывал спеку из
/// надзора (filter_map), и агент не поднимался уже никогда, молча.
struct Managed {
    /// Чей это ребёнок (id агента) — надзор считает пропажу по каждому отдельно.
    agent: String,
    spec: ChildSpec,
    child: Option<Child>,
    falls: Vec<Instant>,
    retry_at: Option<Instant>,
    /// Ребёнок сказал кодом выхода, что виноват не случай, а настройки или
    /// раскладка (руннер: 3 — конфиг, 2 — нет папки с кодом агента). Такое
    /// перезапуском не лечится: крутить 1→2→4→…→32 с и дальше по десять минут
    /// значит жечь машину и врать владельцу «поднимаю снова».
    halted: bool,
    /// Ребёнок ждёт внешнего события (реле: порт держит другая копия). Пока ждём —
    /// не падение и не повод для строки в журнал каждые 30 с (25.09, E.1).
    waiting: bool,
}

/// Что оболочка должна поднять и где. Живёт после первой попытки: занятый
/// порт или придержавший запуск антивирус — не приговор на весь сеанс.
///
/// План теперь ПЕР-АГЕНТНЫЙ: `agent` — чей он, и по нему же надзор понимает,
/// чьих детей не хватает. Без этого поля «детей нет вовсе» считалось по всему
/// списку сразу, и упавший второй агент не поднимался, пока жив первый.
#[derive(Clone)]
struct SpawnPlan {
    agent: String,
    /// Имя агента словами владельца — для журнала и уведомлений: «порт занят»
    /// про безымянный `mira` владельцу не говорит ничего.
    name: String,
    /// Файл настроек этого агента: его и надо называть в отказах, а не общий
    /// `helene.json` рядом с программой.
    config: PathBuf,
    specs: Vec<ChildSpec>,
    port: u16,
    tree: PathBuf,
    /// Секрет канала ЭТОГО дерева: им же оболочка стучится в занятый порт.
    token: String,
}

impl SpawnPlan {
    /// Как назвать этого агента в строке журнала: у единственного — никак
    /// (он и есть «агент»), у соседа — по имени.
    fn whose(&self) -> String {
        if self.agent == BASE_AGENT_ID {
            String::new()
        } else {
            format!(" (агент «{}»)", self.name)
        }
    }
}

struct LocalHarness {
    children: Mutex<Vec<Managed>>,
    stopping: std::sync::atomic::AtomicBool,
    /// Локальный режим: планы подъёма для повторных попыток, по одному на
    /// агента. Пусто — удалённый режим либо ничего не настроено.
    plans: Mutex<Vec<SpawnPlan>>,
}

/// Папка САМОГО exe. Это не корень установки: на macOS бинарь лежит в
/// `Helene.app/Contents/MacOS`. Зовётся только там, где нужен именно exe
/// (перезапуск себя, поиск в PATH); всё остальное — `install_root()`.
fn exe_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Корень установки: от папки exe вверх по родителям (не больше пяти уровней)
/// до первой папки с паспортом сборки `helene-build.json`; не нашли — папка
/// exe. На Windows паспорт лежит рядом с exe, и ответ тот же, что раньше;
/// на macOS это `~/Applications/Helene` тремя уровнями выше бинаря в бандле.
/// Правило одно на оболочку и мастер установки (`setup/`).
fn install_root() -> PathBuf {
    static ROOT: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    ROOT.get_or_init(|| install_root_from(&exe_dir())).clone()
}

/// Чистая половина `install_root`: правило подъёма отдельно, чтобы его можно
/// было проверить на любой раскладке папок, а не только на живой установке.
fn install_root_from(exe_dir: &Path) -> PathBuf {
    let mut here = Some(exe_dir);
    for _ in 0..=5 {
        let Some(dir) = here else { break };
        if dir.join(BUILD_PASSPORT).is_file() {
            return dir.to_path_buf();
        }
        here = dir.parent();
    }
    exe_dir.to_path_buf()
}

/// Имя исполняемого файла по платформе: суффикс `.exe` только на Windows.
fn exe_name(stem: &str) -> String {
    if cfg!(windows) {
        format!("{stem}.exe")
    } else {
        stem.to_string()
    }
}

/// Питон из коробки: `runtime/python.exe` (embedded CPython) на Windows,
/// `runtime/bin/python3` (python-build-standalone) на остальных.
fn bundled_python(base: &Path) -> PathBuf {
    if cfg!(windows) {
        base.join("runtime").join("python.exe")
    } else {
        base.join("runtime").join("bin").join("python3")
    }
}

/// Мастер установки по платформе: `helene-setup.exe` рядом на Windows, бандл
/// `Helene Setup.app` в корне установки на macOS.
fn setup_binary(base: &Path) -> PathBuf {
    if cfg!(windows) {
        base.join("helene-setup.exe")
    } else if cfg!(target_os = "macos") {
        base.join("Helene Setup.app").join("Contents").join("MacOS").join("helene-setup")
    } else {
        base.join("helene-setup")
    }
}

/// Как назвать мастер установки в журнале и в окне.
fn setup_label() -> &'static str {
    if cfg!(windows) {
        "helene-setup.exe"
    } else {
        "Helene Setup.app"
    }
}

// --- статика окна: с диска, из exe только как запасной выход ------------------

/// Папка статики окна — `app/static` рядом с exe. Поставка кладёт туда сборку
/// Vite (`installer/build_dist.py::copy_static`), и окно читает её на каждый
/// запрос: правка файла видна по F5, оболочку пересобирать не нужно (слово
/// владельца 06.09: «статику сделать редактируемой»). До этого статика была
/// вшита в exe (`frontendDist`), и копия в `app/static` лежала мёртвым грузом:
/// пересобранный интерфейс не менял окно, пока не пересоберёшь оболочку.
/// Вшитая копия остаётся запасным выходом на случай снесённой папки.
fn static_root() -> PathBuf {
    install_root().join("app").join("static")
}

/// `%XX` в байты, байты в UTF-8; ломаные последовательности остаются как есть.
fn pct_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i] == b'%' && i + 2 < bytes.len() {
            let hex = std::str::from_utf8(&bytes[i + 1..i + 3]).unwrap_or("zz");
            if let Ok(v) = u8::from_str_radix(hex, 16) {
                out.push(v);
                i += 3;
                continue;
            }
        }
        out.push(bytes[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Относительный путь файла из пути URL; None — путь ведёт наружу папки.
/// Пусто и `/` — это index.html.
fn static_rel(uri_path: &str) -> Option<String> {
    let decoded = pct_decode(uri_path);
    let trimmed = decoded.trim_start_matches('/');
    let rel = if trimmed.is_empty() { "index.html" } else { trimmed };
    let bad = rel.split('/').any(|seg| seg.is_empty() || seg == "." || seg == "..")
        || rel.contains('\\')
        || rel.contains(':')
        || rel.contains('\0');
    if bad {
        None
    } else {
        Some(rel.to_string())
    }
}

fn static_mime(rel: &str) -> &'static str {
    let ext = rel
        .rsplit_once('.')
        .map(|(_, e)| e.to_ascii_lowercase())
        .unwrap_or_default();
    match ext.as_str() {
        "html" => "text/html; charset=utf-8",
        "js" | "mjs" => "text/javascript; charset=utf-8",
        "css" => "text/css; charset=utf-8",
        "json" | "webmanifest" | "map" => "application/json; charset=utf-8",
        "svg" => "image/svg+xml",
        "png" => "image/png",
        "ico" => "image/x-icon",
        "jpg" | "jpeg" => "image/jpeg",
        "webp" => "image/webp",
        "gif" => "image/gif",
        "woff2" => "font/woff2",
        "woff" => "font/woff",
        "ttf" => "font/ttf",
        "txt" | "md" => "text/plain; charset=utf-8",
        "wasm" => "application/wasm",
        _ => "application/octet-stream",
    }
}

/// Обработчик протокола окна (`http://helene.localhost/…` в WebView2): файл с
/// диска, иначе — вшитая копия, иначе 404. Ни одного залипшего бандла:
/// `no-cache` заставляет WebView2 переспрашивать файл на каждом F5, а поставка
/// кладёт свежий index.html поверх старого без пересборки оболочки.
/// Как окну позволено кэшировать этот файл.
///
/// ⚠ 17.09, ЖИВОЙ СЛУЧАЙ. Всё лежало под `no-cache` — это «кэшируй, но перепроверяй».
/// Перепроверять нечем: ни ETag, ни Last-Modified мы не отдаём, — и WebView2 держал у
/// себя СТАРЫЙ `index.html`. Имена бандлов хэшированы, поэтому старая страница тянула и
/// старый бандл: в обновлённой программе работал код прошлой версии. Снаружи это
/// выглядело как «окно не соединяется» — 403 на каждый запрос и вечное «не на связи»
/// при живом харнессе, который тем же ключом отвечал 200 из командной строки.
///
/// Страница — единственный файл, чьё имя не несёт отпечатка содержимого, поэтому она и
/// только она идёт с `no-store`: читается с диска всегда, и обновление больше не может
/// застрять в кэше окна. Остальному хватает `no-cache`: сменилось содержимое — сменилось
/// имя, а одинаковые шрифты и бандлы перечитывать с диска незачем.
fn cache_rule(mime: &str) -> &'static str {
    if mime.starts_with("text/html") { "no-store" } else { "no-cache" }
}

fn serve_static<R: tauri::Runtime>(
    ctx: tauri::UriSchemeContext<'_, R>,
    request: tauri::http::Request<Vec<u8>>,
) -> tauri::http::Response<std::borrow::Cow<'static, [u8]>> {
    use std::borrow::Cow;
    let (status, mime, body): (u16, String, Cow<'static, [u8]>) =
        match static_rel(request.uri().path()) {
            None => (403, "text/plain; charset=utf-8".to_string(), Cow::Borrowed(b"forbidden".as_slice())),
            Some(rel) => match std::fs::read(static_root().join(&rel)) {
                Ok(bytes) => (200, static_mime(&rel).to_string(), Cow::Owned(bytes)),
                Err(_) => match ctx.app_handle().asset_resolver().get(format!("/{rel}")) {
                    Some(asset) => (200, asset.mime_type().to_string(), Cow::Owned(asset.bytes().to_vec())),
                    None => (404, "text/plain; charset=utf-8".to_string(), Cow::Borrowed(b"not found".as_slice())),
                },
            },
        };
    let is_html = mime.starts_with("text/html");
    let mut resp = tauri::http::Response::builder()
        .status(status)
        .header("cache-control", cache_rule(&mime))
        .header("content-type", mime);
    // CSP из tauri.conf.json раньше клеил сам Tauri к вшитым файлам; файл с
    // диска идёт мимо него, поэтому заголовок ставим здесь и тот же самый.
    if is_html {
        if let Some(csp) = ctx.app_handle().config().app.security.csp.as_ref() {
            resp = resp.header("content-security-policy", csp.to_string());
        }
    }
    resp.body(body)
        .unwrap_or_else(|_| tauri::http::Response::new(Cow::Borrowed(b"".as_slice())))
}

fn resolve(base: &Path, raw: &str) -> PathBuf {
    let p = PathBuf::from(raw);
    if p.is_absolute() {
        p
    } else {
        base.join(p)
    }
}

/// Что вышло из чтения helene.json. Три случая, а не два: «файла нет» ведёт к
/// установщику, «файл есть, но не разобрался» — НИКОГДА не должно, иначе
/// установщик проходит по кругу и перезаписывает конституцию и настройки.
enum ConfigRead {
    Missing,
    Ok(serde_json::Value),
    Broken(String),
}

/// Текст конфига в UTF-8, чем бы его ни сохранили.
/// ПЕРВЫЙ-ЗАПУСК.md зовёт владельца править helene.json руками, а штатные
/// средства Windows пишут ровно то, чего serde_json не понимает: Блокнот и
/// VS Code — UTF-8 с BOM, `Set-Content` в PowerShell 5.1 — UTF-16LE.
/// Раньше и то и другое читалось как «конфига нет».
fn decode_config(bytes: &[u8]) -> Result<String, String> {
    if bytes.starts_with(&[0xFF, 0xFE]) || bytes.starts_with(&[0xFE, 0xFF]) {
        let big = bytes[0] == 0xFE;
        let body = &bytes[2..];
        if !body.len().is_multiple_of(2) {
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

fn read_config(path: &Path) -> ConfigRead {
    let bytes = match std::fs::read(path) {
        Ok(b) => b,
        Err(err) if err.kind() == std::io::ErrorKind::NotFound => return ConfigRead::Missing,
        Err(err) => return ConfigRead::Broken(format!("файл не читается: {err}")),
    };
    let text = match decode_config(&bytes) {
        Ok(t) => t,
        Err(why) => return ConfigRead::Broken(why),
    };
    if text.trim().is_empty() {
        return ConfigRead::Missing;
    }
    match serde_json::from_str(&text) {
        Ok(v) => ConfigRead::Ok(v),
        Err(err) => ConfigRead::Broken(format!("JSON не разобрался: {err}")),
    }
}

/// Конфиг для команд оболочки; None — нет или не разобрался.
fn config_value() -> Option<serde_json::Value> {
    match read_config(&install_root().join(CONFIG_NAME)) {
        ConfigRead::Ok(v) => Some(v),
        _ => None,
    }
}

/// Дерево данных: из конфига, но ОТ ПАПКИ ПРОГРАММЫ, а не от текущей папки
/// процесса. Относительный "data" при запуске helene.exe из другой папки
/// уводил сессию Telegram и сбор логов в чужое место.
fn base_tree() -> PathBuf {
    let base = install_root();
    let raw = config_value()
        .and_then(|c| c.get("tree").and_then(|v| v.as_str()).map(str::to_string))
        .unwrap_or_else(|| "data".into());
    resolve(&base, &raw)
}

/// Дерево ТОГО агента, которого показывает окно. С 11.09 их несколько, и почти
/// всё в оболочке (журналы, брокер, сессия Telegram, отметка обновления) —
/// про текущего, а не про корневого.
fn current_tree() -> PathBuf {
    with_current(base_tree(), |c| c.tree.clone().unwrap_or_else(base_tree))
}

/// Прежнее имя оставлено для читаемости мест, где смысл именно «дерево окна».
fn tree_dir() -> PathBuf {
    current_tree()
}

/// Имя агента даёт владелец при установке (`agent.name`); старые конфиги
/// держали его в `telegram.agent_name`. Пустое — честное «Агент».
fn agent_name(cfg: Option<&serde_json::Value>) -> String {
    let pick = |v: Option<&serde_json::Value>| {
        v.and_then(|s| s.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(String::from)
    };
    cfg.and_then(|c| {
        pick(c.get("agent").and_then(|a| a.get("name")))
            .or_else(|| pick(c.get("telegram").and_then(|t| t.get("agent_name"))))
    })
    .unwrap_or_else(|| "Агент".to_string())
}

/// Найти программу в PATH, НЕ заглядывая в папку самой программы: голое имя
/// в CreateProcess берётся сначала оттуда, и подложенный туда python.exe
/// исполнился бы вместо настоящего.
fn find_in_path(name: &str) -> Option<PathBuf> {
    // Папка exe и корень установки: на Windows это одна папка, на macOS —
    // две, и пишет агент во вторую.
    let here: Vec<PathBuf> = [exe_dir(), install_root()]
        .iter()
        .filter_map(|p| p.canonicalize().ok())
        .collect();
    let raw_ext = std::env::var("PATHEXT").unwrap_or_else(|_| ".EXE;.CMD;.BAT".into());
    let exts: Vec<&str> = raw_ext.split(';').filter(|e| !e.is_empty()).collect();
    for dir in std::env::split_paths(&std::env::var_os("PATH")?) {
        if dir.as_os_str().is_empty() {
            continue;
        }
        if let Ok(d) = dir.canonicalize() {
            if here.contains(&d) {
                continue;
            }
        }
        let direct = dir.join(name);
        if direct.is_file() {
            return Some(direct);
        }
        for ext in &exts {
            let cand = dir.join(format!("{name}{ext}"));
            if cand.is_file() {
                return Some(cand);
            }
        }
    }
    None
}

/// Питон для харнесса: явный из конфига → runtime/python.exe рядом с exe
/// (embedded CPython поставки) → найденный в PATH.
/// Голого имени "python" здесь больше нет: не нашли — возвращаем путь к
/// встроенному, и запуск честно падает с «нет такого файла» в helene.log,
/// а не запускает первое, что лежит рядом.
fn python_path(base: &Path, cfg: &serde_json::Value) -> PathBuf {
    let embedded = bundled_python(base);
    if let Some(raw) = cfg.get("python").and_then(|v| v.as_str()) {
        if raw != "python" {
            let explicit = resolve(base, raw);
            // На Windows — как было: явный путь берётся даже несуществующим,
            // и запуск честно падает с «нет такого файла».
            #[cfg(windows)]
            return explicit;
            // Вне Windows конфиг мог приехать с Windows (перенос агента
            // `carry.py` увозит helene.json целиком, а там `runtime/python.exe`).
            // Такой путь здесь не файл, а встроенный питон — есть; идём к нему
            // и говорим об этом в журнал, а не молчим мёртвыми детьми.
            #[cfg(not(windows))]
            {
                if explicit.is_file() || !embedded.is_file() {
                    return explicit;
                }
                log_line(&format!(
                    "python в конфиге ({}) здесь не существует — беру встроенный {}",
                    explicit.display(),
                    embedded.display()
                ));
                return embedded;
            }
        }
    }
    if embedded.exists() {
        return embedded;
    }
    find_in_path("python").unwrap_or(embedded)
}

/// Телефон подключается по Wi-Fi: труба слушает все адреса, а не только петлю.
/// Доступ не с петли deskapp даёт только по ключу устройства.
fn phone_enabled(cfg: &serde_json::Value) -> bool {
    cfg.get("phone")
        .and_then(|p| p.get("enabled"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

/// Дети живут ровно столько, сколько окно: job-объект с KILL_ON_JOB_CLOSE.
/// Убили окно из диспетчера — дети не остаются сиротами держать порт и файлы.
#[cfg(windows)]
mod job {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::System::JobObjects::{
        AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
        SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    pub struct Job(HANDLE);
    unsafe impl Send for Job {}
    unsafe impl Sync for Job {}

    impl Job {
        pub fn new() -> Option<Job> {
            unsafe {
                let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
                if handle.is_null() {
                    super::log_line("job-объект не создан — дети могут пережить окно");
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
                    // Хэндл ядра уже выдан: без CloseHandle он тёк до конца
                    // жизни процесса, и при этом защита от сирот молча
                    // выключалась на весь сеанс — ни строки об этом не было.
                    CloseHandle(handle);
                    super::log_line("job-объект не настроился — дети могут пережить окно");
                    return None;
                }
                Some(Job(handle))
            }
        }

        pub fn adopt(&self, child: &std::process::Child) {
            use std::os::windows::io::AsRawHandle;
            unsafe {
                if AssignProcessToJobObject(self.0, child.as_raw_handle() as HANDLE) == 0 {
                    super::log_line("ребёнок не приписан к job-объекту — может остаться сиротой");
                }
            }
        }
    }
}

#[cfg(windows)]
static JOB: std::sync::OnceLock<Option<job::Job>> = std::sync::OnceLock::new();

/// Приписать ребёнка к job-объекту окна (ничего не делает вне Windows).
fn adopt(child: &Child) {
    #[cfg(windows)]
    if let Some(job) = JOB.get_or_init(job::Job::new).as_ref() {
        job.adopt(child);
    }
    #[cfg(not(windows))]
    let _ = child;
}

/// Секрет трубы. Без него труба (deskapp.py) отдаёт роль ВЛАДЕЛЬЦА каждому
/// запросу по петле: любому процессу этой машины, коду другой учётной записи
/// Windows и рукам самого агента — то есть всю переписку, /api/anatomy с
/// ключами моделей и запись в soul/SOUL.md. Приём секрета в трубе был написан
/// давно (`HELENE_TOKEN`), но не ставил его никто.
///
/// Секрет один НА ДЕРЕВО, а не на процесс, и лежит в самом дереве: харнесс
/// поднимает либо окно, либо служба, а предъявлять трубе один и тот же ключ
/// должны оба плюс второе окно, которое подключается к уже живому харнессу.
/// Свой собственный ключ у каждого = 403 от собственного харнесса и пустое окно.
///
/// ⚠ 11.09: секретов стало столько же, сколько агентов, и `OnceLock` (один на
/// процесс) перестал быть правдой. Здесь живёт секрет ТОГО агента, которого
/// окно показывает сейчас; секреты остальных едут в их спеках детей.
static DESK_TOKEN: Mutex<String> = Mutex::new(String::new());

fn desk_token() -> String {
    DESK_TOKEN.lock().map(|g| g.clone()).unwrap_or_default()
}

fn set_desk_token(token: &str) {
    if let Ok(mut guard) = DESK_TOKEN.lock() {
        *guard = token.to_string();
    }
}

/// Кого окно показывает сейчас. Меняется переключателем агентов и трея; всё,
/// что раньше считалось «от единственного конфига рядом с exe» (дерево, файл
/// настроек, имя в заголовке), спрашивает ЭТО, а не раскладку.
struct CurrentAgent {
    id: String,
    name: String,
    /// Дом агента; None — удалённый режим, дерева на этой машине нет.
    tree: Option<PathBuf>,
    /// Файл настроек ИМЕННО этого агента (у корневого — рядом с программой).
    config: PathBuf,
}

static CURRENT: Mutex<Option<CurrentAgent>> = Mutex::new(None);

/// Идёт переключение агента: старое окно сносится, чтобы отдать метку `main`
/// новому.
///
/// ⚠⚠ ПОЙМАНО ЖИВОЙ ПРОБОЙ 11.09, и обе половины здесь несущие.
/// `close()` окно НЕ закрывает: обработчик `CloseRequested` прячет его в трей
/// (закрыть окно ≠ убить организм), метка `main` остаётся занятой, и новое
/// окно отвечает «a webview with label `main` already exists» — владелец
/// оставался БЕЗ окна вовсе. Значит нужен `destroy()`.
/// Но `destroy()` поднимает `Destroyed`, а тот гасит детей — то есть
/// переключение агента убивало бы всех агентов установки. Поэтому на время
/// переключения смерть окна не считается выходом.
static SWITCHING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);

fn set_current(who: CurrentAgent) {
    if let Ok(mut guard) = CURRENT.lock() {
        *guard = Some(who);
    }
}

/// Значение именованного аргумента командной строки: `--agent mira`.
/// Форму `--agent=mira` понимаем тоже — ярлыки Windows пишут и так.
fn arg_after(flag: &str) -> Option<String> {
    let mut args = std::env::args().skip(1);
    while let Some(arg) = args.next() {
        if arg == flag {
            return args.next().map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
        }
        if let Some(tail) = arg.strip_prefix(&format!("{flag}=")) {
            let tail = tail.trim().to_string();
            if !tail.is_empty() {
                return Some(tail);
            }
        }
    }
    None
}

fn with_current<T>(fallback: T, take: impl FnOnce(&CurrentAgent) -> T) -> T {
    match CURRENT.lock() {
        Ok(guard) => match guard.as_ref() {
            Some(cur) => take(cur),
            None => fallback,
        },
        Err(_) => fallback,
    }
}

fn current_id() -> String {
    with_current(BASE_AGENT_ID.to_string(), |c| c.id.clone())
}

/// Файл настроек текущего агента. У корневого это `helene.json` рядом с
/// программой — то же место, что и до 11.09.
fn current_config_path() -> PathBuf {
    with_current(install_root().join(CONFIG_NAME), |c| c.config.clone())
}

fn desk_token_path(tree: &Path) -> PathBuf {
    tree.join("memory").join(".state").join("desk-token")
}

/// Секрет из дерева, если он там уже есть и выглядит секретом.
fn read_desk_token(tree: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(desk_token_path(tree)).ok()?;
    let t = raw.trim();
    (t.len() >= 16 && t.len() <= 128 && t.bytes().all(|b| b.is_ascii_alphanumeric()))
        .then(|| t.to_string())
}

/// Прочитать секрет дерева, а если его нет — завести и положить туда же.
/// Не записался (дерево только для чтения) — возвращаем None и говорим об этом
/// в журнал: труба останется открытой, как была, но молчать об этом нельзя.
fn ensure_desk_token(tree: &Path) -> Option<String> {
    if let Some(t) = read_desk_token(tree) {
        return Some(t);
    }
    let token = random_hex(24)?;
    let path = desk_token_path(tree);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    match std::fs::write(&path, &token) {
        Ok(()) => Some(token),
        Err(err) => {
            log_line(&format!(
                "секрет канала не записался ({}): {err} — канал остаётся открытым любому процессу этой машины",
                path.display()
            ));
            None
        }
    }
}

fn spawn_child(python: &Path, script: &Path, args: &[String], tree: &Path, host: &str,
               token: &str, config: &Path) -> Option<Child> {
    let mut cmd = Command::new(python);
    // -u: без него вывод питона в файл буферизован блоками, и аварийное
    // завершение теряло ровно те килобайты, где причина. Служба (svc) делает
    // так же — это был единственный разошедшийся с ней ключ.
    cmd.arg("-u")
        .arg(script)
        .args(args)
        .env("HELENE_TREE", tree)
        .env("HELENE_HOST", host)
        .env("PYTHONUTF8", "1")
        // Замок трубы. Пустая строка тоже ставится осознанно: секрет решает
        // тот, кто поднял харнесс, а не переменная окружения владельца, —
        // иначе системная PRAXIS_DESK_TOKEN закрывала бы трубу ключом,
        // которого окно не знает (deskapp.py принимает обе переменные).
        // Секрет — этого дерева, а не «текущего агента окна»: см. ChildSpec.
        .env("HELENE_TOKEN", token)
        // Чей это агент — говорит оболочка, которая его и подняла, а не раскладка кода
        // на диске. Шов для этого уже был (`HELENE_CONFIG` стоит первым в
        // `readers.config_path()` и накрыт стендом `t_voice.py`), и его просто никто не
        // ставил: в канал уходил один порт. Одна переменная закрывает весь класс —
        // чтение чужого конфига и запись в него ручкой `agent-config`.
        .env("HELENE_CONFIG", config)
        // Кто родитель: на POSIX job-объекта нет, и движок с каналом сами
        // следят за этим pid и выходят, когда окно умерло (`localharness`).
        // Ставится на всех платформах — на Windows переменная просто лишняя.
        .env("HELENE_PARENT_PID", std::process::id().to_string())
        .env_remove("PRAXIS_DESK_TOKEN");
    if let Some(dir) = script.parent() {
        cmd.current_dir(dir);
    }
    // Вывод ребёнка — в файл рядом с данными (deskapp.log, runner.log): без
    // этого падение харнесса не оставляло следов.
    if let Some(log) = child_log(tree, script) {
        if let Ok(err) = log.try_clone() {
            cmd.stdout(Stdio::from(log)).stderr(Stdio::from(err));
        }
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    #[cfg(unix)]
    cmd.process_group(0);
    match cmd.spawn() {
        Ok(child) => {
            adopt(&child);
            Some(child)
        }
        Err(err) => {
            // Было eprintln! — в exe без консоли (windows_subsystem="windows")
            // это запись в никуда: провал не оставлял следа нигде.
            log_line(&format!(
                "дочерний процесс {} не поднялся: {err}",
                script.display()
            ));
            None
        }
    }
}

/// Предел файла вывода ребёнка.
const CHILD_LOG_MAX: u64 = 5 * 1024 * 1024;

/// Файл вывода ребёнка; больше предела — прежний уходит в .1.
/// Проверка здесь ловит только момент запуска; у живого агента файл режет
/// надзор (см. rotate_child_log), иначе deskapp.log рос без границы.
fn child_log(tree: &Path, script: &Path) -> Option<std::fs::File> {
    let stem = script.file_stem()?.to_string_lossy().into_owned();
    let path = tree.join(format!("{stem}.log"));
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() > CHILD_LOG_MAX {
            let _ = std::fs::rename(&path, tree.join(format!("{stem}.log.1")));
        }
    }
    std::fs::OpenOptions::new().create(true).append(true).open(path).ok()
}

/// Ротация лога живого ребёнка: переименовать нельзя (файл открыт им самим),
/// поэтому копия в .1 и обрезка на месте — дальше он допишет с начала.
fn rotate_child_log(path: &Path) -> bool {
    let Ok(meta) = std::fs::metadata(path) else { return false };
    if meta.len() <= CHILD_LOG_MAX {
        return false;
    }
    let backup = path.with_extension("log.1");
    if std::fs::copy(path, &backup).is_err() {
        return false;
    }
    match std::fs::OpenOptions::new().write(true).open(path) {
        Ok(f) => f.set_len(0).is_ok(),
        Err(_) => false,
    }
}

/// Харнесс уже жив на этом порту? (служба или другое окно.)
/// Проба — только коннект: держатель порта и есть харнесс по построению,
/// а поднимать второго на занятый порт бессмысленно в любом случае.
fn harness_alive(port: u16) -> bool {
    use std::net::{SocketAddr, TcpStream};
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(400)).is_ok()
}

/// Кто держит порт. Отдельный случай Guarded: харнесс под токеном отвечает
/// 403 без ключа, и это НЕ «чужая установка» — раньше владелец с системной
/// переменной PRAXIS_DESK_TOKEN получал ложное обвинение и мёртвое окно.
enum Holder {
    Tree(PathBuf),
    Guarded,
    Silent,
}

/// Один запрос `/api/home`. `key` пустой — спрашиваем анонимно.
fn home_probe(port: u16, key: &str) -> Holder {
    // 1500 мс не хватало холодному aiohttp: медленный ответ читался как «чужой».
    // redirects(0): держатель порта — не обязательно наш; переадресация увела
    // бы наш запрос (а с ним и ключ) на любой адрес, который назовёт он сам.
    // try_proxy_from_env(false): спрашиваем СВОЙ порт на 127.0.0.1, и прокси из
    // среды здесь не дорога, а стена — с ним оболочка решила бы, что канал
    // держит кто-то чужой, и не нашла бы собственного харнесса.
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_millis(2500))
        .redirects(0)
        .try_proxy_from_env(false)
        .build();
    let query = if key.is_empty() { String::new() } else { format!("?key={key}") };
    match agent.get(&format!("http://127.0.0.1:{port}/api/home{query}")).call() {
        Ok(resp) => {
            if (300..400).contains(&resp.status()) {
                return Holder::Silent;
            }
            let body = resp.into_string().unwrap_or_default();
            match serde_json::from_str::<serde_json::Value>(&body)
                .ok()
                .and_then(|v| v.get("tree").and_then(|t| t.as_str()).map(PathBuf::from))
            {
                Some(tree) => Holder::Tree(tree),
                None => Holder::Silent,
            }
        }
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => Holder::Guarded,
        Err(_) => Holder::Silent,
    }
}

/// Опознавательная карточка держателя порта: имя продукта и корень установки.
/// Ответ БЕЗ КЛЮЧА и без секретов (`deskd/control.py::who`).
struct WhoSaid {
    product: String,
    root: String,
}

/// Спросить порт, кто он такой, — анонимно.
fn who_probe(port: u16) -> Option<WhoSaid> {
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_millis(2500))
        .redirects(0)
        .try_proxy_from_env(false)
        .build();
    let body = agent
        .get(&format!("http://127.0.0.1:{port}/api/who"))
        .call()
        .ok()?
        .into_string()
        .ok()?;
    let v: serde_json::Value = serde_json::from_str(&body).ok()?;
    Some(WhoSaid {
        product: v.get("product").and_then(|x| x.as_str()).unwrap_or("").to_string(),
        root: v.get("root").and_then(|x| x.as_str()).unwrap_or("").to_string(),
    })
}

/// Наш ли это канал: то же имя продукта и тот же корень установки.
/// Чистая функция — стенд гоняется на любой ОС.
///
/// Пути сравниваются после `canonicalize` у вызывающего; здесь — построчно и
/// без учёта регистра на Windows, где `C:\Helene` и `c:\helene` это одна папка.
fn who_is_ours(said: &WhoSaid, product: &str, root: &Path) -> bool {
    if said.product.trim() != product.trim() || said.root.trim().is_empty() {
        return false;
    }
    let theirs = Path::new(said.root.trim());
    let theirs = theirs.canonicalize().unwrap_or_else(|_| theirs.to_path_buf());
    let ours = root.canonicalize().unwrap_or_else(|_| root.to_path_buf());
    if cfg!(windows) {
        theirs.to_string_lossy().to_lowercase() == ours.to_string_lossy().to_lowercase()
    } else {
        theirs == ours
    }
}

/// Кто держит порт. Три шага, и порядок здесь важен.
///
/// Сначала спрашиваем БЕЗ ключа: держатель порта — не обязательно наш, и
/// отдавать секрет дерева первому, кто занял порт, нельзя.
///
/// ⚠⚠ ЧТО БЫЛО ДО 19.09 (находка судей). На 401/403 окно СРАЗУ слало
/// `?key=<токен дерева>` — то есть вручало ключ владельца любой программе,
/// успевшей занять локальный порт. А занять его может кто угодно в этой
/// учётке, включая процесс, поднятый самим агентом; по этому ключу отдаются
/// ключ модели, токен бота, конституция и правка конфига. «Он ответил 403,
/// значит он наш под замком» — это не вывод, а предположение.
///
/// Теперь между «нужен ключ» и ключом стоит ОПОЗНАНИЕ: анонимная ручка
/// `/api/who` называет продукт и корень установки (ни то, ни другое не секрет).
/// Не сошлось — ключа он не получит, а владельцу скажут словами, что порт занят
/// чужим процессом. Не ответила вовсе (старая сборка без этой ручки) — тоже не
/// получит: молчание прибора это не «свой».
fn harness_holder(port: u16, key: &str) -> Holder {
    match home_probe(port, "") {
        Holder::Guarded => {
            if key.is_empty() {
                return Holder::Guarded;
            }
            let Some(said) = who_probe(port) else {
                log_line(&format!(
                    "порт {port} отвечает «нужен ключ», но не называет себя (/api/who) — \
                     ключ ему не предъявляю"
                ));
                return Holder::Silent;
            };
            if !who_is_ours(&said, product_ui(), &install_root()) {
                log_line(&format!(
                    "порт {port} держит чужой процесс: назвался «{}» из «{}» — ключ ему не предъявляю",
                    said.product, said.root
                ));
                return Holder::Silent;
            }
            match home_probe(port, key) {
                Holder::Tree(tree) => Holder::Tree(tree),
                // Наш ключ ему не подошёл — чей это харнесс, мы не знаем.
                _ => Holder::Guarded,
            }
        }
        other => other,
    }
}

/// Что делать с портом, который уже занят.
#[derive(Clone)]
enum Verdict {
    /// Наш харнесс (то же дерево) — окно становится клиентом.
    Ours,
    /// Харнесс под ключом, которого мы не знаем: чей — не знаем, но это не
    /// «чужая установка».
    Guarded,
    /// Порт держит кто-то другой: молча показывать чужого агента нельзя.
    /// Внутри — дерево чужой установки, если она его назвала: владельцу нужно
    /// сказать, ЧТО именно здесь работает, а не просто «занято».
    Foreign(Option<PathBuf>),
}

/// Ключ предъявляется ТОГО агента, чей это порт (`key`), а не «текущего в
/// окне»: у второго агента ключ свой, и чужим он получил бы «под замком» от
/// собственного харнесса.
fn harness_verdict(port: u16, tree: &Path, key: &str) -> Verdict {
    let ours = tree.canonicalize().unwrap_or_else(|_| tree.to_path_buf());
    match harness_holder(port, key) {
        Holder::Tree(theirs) => {
            let canon = theirs.canonicalize().unwrap_or_else(|_| theirs.clone());
            if canon == ours {
                Verdict::Ours
            } else {
                Verdict::Foreign(Some(theirs))
            }
        }
        Holder::Guarded => Verdict::Guarded,
        Holder::Silent => Verdict::Foreign(None),
    }
}

/// Встроенное реле подписки ChatGPT (отдельный exe в поставке). Поднимается
/// ребёнком, когда helene.json просит: relay.enabled. Дом реле — data/relay:
/// учётные данные живут в папке продукта и переезжают вместе с ней.
fn relay_enabled(cfg: &serde_json::Value) -> bool {
    cfg.get("relay")
        .and_then(|r| r.get("enabled"))
        .and_then(|v| v.as_bool())
        .unwrap_or(false)
}

fn relay_port(cfg: &serde_json::Value) -> u16 {
    cfg.get("relay")
        .and_then(|r| r.get("port"))
        .and_then(|v| v.as_u64())
        .unwrap_or(RELAY_PORT as u64)
        .min(u16::MAX as u64) as u16
}

/// Исход подъёма реле (25.09, E.1): «порт занят» — ожидание, не падение.
enum RelaySpawn {
    Started(Child),
    /// Порт держит кто-то другой (служба, прежняя копия): ждём, падением не считаем.
    WaitingPort(u16),
    /// Нет exe или spawn упал — причина словами.
    Unavailable(String),
}

/// Отпечаток настроек реле: по нему окно после «Сохранить» решает, поднимать ли своё
/// реле заново (25.09, C.1 — смена мозга и реле без перезапуска окна и службы).
fn relay_fingerprint(cfg: &serde_json::Value) -> String {
    let key = cfg
        .get("model")
        .and_then(|m| m.get("key"))
        .and_then(|v| v.as_str())
        .unwrap_or("");
    let instructions = cfg
        .get("relay")
        .and_then(|r| r.get("instructions"))
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("minimal");
    format!("{}|{}|{}|{}", relay_enabled(cfg), relay_port(cfg), key, instructions)
}

/// Маркер «новый вход в подписку»: `local_auth/login-generation` рядом с auth.json.
/// Пишет его ОКНО (`note_relay_login`), когда помощник входа завершился и auth.json на
/// месте. Реле читает вход только при старте, поэтому после нового входа своё реле
/// поднимается заново (окно — здесь, служба — по тому же маркеру у себя).
///
/// ⚠ Сторожить сам `auth.json` нельзя: реле ПЕРЕПИСЫВАЕТ его при каждом обновлении
/// токенов (`account_router.rs`: «CodexAuth may refresh and rewrite auth.json»), и
/// перезапуск «по mtime файла» шёл бы примерно раз в час, роняя ход посреди ответа.
/// Ровно так было собрано в 0.8.5 — поймано до выкладки Mac, починено в 0.8.6.
static RELAY_LOGIN_SEEN: Mutex<Option<std::time::SystemTime>> = Mutex::new(None);
/// Помощник входа запускался в этом сеансе и ещё не отмечен маркером.
static LOGIN_WAS_PENDING: std::sync::atomic::AtomicBool = std::sync::atomic::AtomicBool::new(false);
/// Подпись auth.json (mtime, длина) в момент запуска помощника входа: маркер ставится
/// только если помощник ВЫШЕЛ УСПЕШНО и файл после него другой. Отменённый или упавший
/// вход при старом auth.json перезапускал реле посреди хода (ревью 25.09, A4 F6).
static LOGIN_AUTH_BEFORE: Mutex<Option<(Option<std::time::SystemTime>, u64)>> = Mutex::new(None);

fn auth_signature(path: &Path) -> Option<(Option<std::time::SystemTime>, u64)> {
    let meta = std::fs::metadata(path).ok()?;
    Some((meta.modified().ok(), meta.len()))
}

/// Решение «ставить ли маркер нового входа» — чистая функция для стенда.
fn login_marks_new_generation(
    helper_ok: bool,
    before: Option<(Option<std::time::SystemTime>, u64)>,
    after: Option<(Option<std::time::SystemTime>, u64)>,
) -> bool {
    helper_ok && after.is_some() && before != after
}

fn relay_login_marker() -> PathBuf {
    relay_home().join("local_auth").join("login-generation")
}

fn relay_login_generation() -> Option<std::time::SystemTime> {
    std::fs::metadata(relay_login_marker())
        .ok()
        .and_then(|m| m.modified().ok())
}

/// Отметить новый вход: помощник входа завершился, auth.json лежит. Маркер читают
/// окно (`relay_status`) и служба (свой тик) — оба поднимают своё реле заново.
fn note_relay_login() {
    let path = relay_login_marker();
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos().to_string())
        .unwrap_or_default();
    if let Err(err) = std::fs::write(&path, stamp) {
        log_line(&format!("маркер входа в подписку не записался: {err}"));
    }
}

/// 25.09 (C.1). Реле применяет новые настройки без перезапуска окна: своё реле гасится
/// и поднимается заново по свежему конфигу (или не поднимается, если relay.enabled
/// снят). Чужое реле (порт держит служба или прежняя копия) не трогаем — им занимается
/// его хозяин: служба перечитывает helene.json и auth.json сама.
fn reconcile_relay(state: &tauri::State<LocalHarness>, cfg: &serde_json::Value, why: &str) -> String {
    let base_config = install_root().join(CONFIG_NAME);
    // Ревью 25.09 (A4 F3/F4). Записи детей НЕ удаляются и не переставляются: надзор
    // (`watch_children`) держит индексы записей между двумя захватами замка, и `remove`
    // здесь подсовывал ему чужую запись под старым индексом. Реле гасится НА МЕСТЕ, а
    // новая запись заводится только если у окна есть свои дети: окно-клиент под службой
    // своего реле не держит (реле службы перечитает настройки само), а фантомная запись
    // `child: None` прятала бы от надзора «харнесс пропал» до перезапуска окна.
    let mut had_own = false;
    let mut relay_idx: Option<usize> = None;
    let mut has_script_children = false;
    if let Ok(mut guard) = state.children.lock() {
        for (i, m) in guard.iter_mut().enumerate() {
            if matches!(m.spec, ChildSpec::Relay { .. }) {
                if let Some(child) = m.child.as_mut() {
                    stop_child(child);
                    had_own = true;
                }
                m.child = None;
                m.waiting = false;
                m.retry_at = None;
                relay_idx = Some(i);
            } else {
                has_script_children = true;
            }
        }
    }
    let mut spec: Option<ChildSpec> = None;
    let mut agent_id = String::new();
    if let Ok(mut plans) = state.plans.lock() {
        if let Some(plan) = plans.iter_mut().find(|p| p.config == base_config) {
            plan.specs.retain(|s| !matches!(s, ChildSpec::Relay { .. }));
            if relay_enabled(cfg) {
                let fresh = ChildSpec::Relay { base: install_root(), cfg: cfg.clone(), tree: plan.tree.clone() };
                plan.specs.insert(0, fresh.clone());
                spec = Some(fresh);
                agent_id = plan.agent.clone();
            }
        }
    }
    let Some(spec) = spec else {
        let line = format!(
            "реле: {why} — {}",
            if had_own { "своё реле погашено: по новым настройкам оно выключено" } else { "своего реле не было и не нужно" }
        );
        log_line(&line);
        // Выключенное реле: запись снимаем — это единственное место, где вектор
        // укорачивается, и делается это под замком одним `remove`, без подъёма между.
        if let (Some(i), Ok(mut guard)) = (relay_idx, state.children.lock()) {
            if i < guard.len() && matches!(guard[i].spec, ChildSpec::Relay { .. }) {
                guard.remove(i);
            }
        }
        return line;
    };
    if !had_own && relay_idx.is_none() && !has_script_children {
        let line = format!("реле: {why} — окно ходит к харнессу службы, реле держит она и перечитает настройки сама");
        log_line(&line);
        return line;
    }
    // Подъём — вне замка: CreateProcess под замком детей держал бы выход из трея.
    let outcome = spec.spawn();
    let (child, waiting, line) = match outcome {
        SpawnOutcome::Started(child) => (Some(child), false, format!("реле: {why} — поднято заново по новым настройкам")),
        SpawnOutcome::Waiting(said) => (None, true, format!("реле: {why} — {said}")),
        SpawnOutcome::Failed => (None, false, format!("реле: {why} — не поднялось сразу, надзор попробует через 30 с")),
    };
    if let Ok(mut guard) = state.children.lock() {
        let retry_at = child.is_none().then(|| Instant::now() + Duration::from_secs(30));
        let slot = relay_idx
            .filter(|&i| i < guard.len() && matches!(guard[i].spec, ChildSpec::Relay { .. }));
        match slot {
            Some(i) => {
                let m = &mut guard[i];
                m.spec = spec;
                m.child = child;
                m.waiting = waiting;
                m.retry_at = retry_at;
            }
            None => guard.push(Managed {
                agent: agent_id,
                spec,
                retry_at,
                child,
                falls: Vec::new(),
                halted: false,
                waiting,
            }),
        }
    }
    log_line(&line);
    line
}

/// Живое реле о себе: применённый вход (какой слот активен, сколько настроено). Это
/// ответ на «применится перезапуском», которое висело вечно: файл auth.json — не факт
/// применения, факт — слово самого реле (25.09, C.3).
#[tauri::command]
async fn relay_account() -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(relay_account_blocking)
        .await
        .map_err(|e| e.to_string())?
}

/// Отвечает ли держатель порта как реле: `GET /health` (без ключа) отдаёт JSON реле с
/// `"service":"relay"`. Раньше хватало 200/401/403 на `/v1/account` — и любая программа с
/// basic auth (nginx, чужой локальный API) сходила за реле (ревью 25.09, V4 F12); теперь
/// нужен именно его паспорт. Loopback без прокси и редиректов.
fn port_holder_is_relay(port: u16) -> bool {
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_millis(500))
        .timeout_read(Duration::from_secs(2))
        .redirects(0)
        .try_proxy_from_env(false)
        .build();
    match agent.get(&format!("http://127.0.0.1:{port}/health")).call() {
        Ok(resp) => resp
            .into_string()
            .map(|body| relay_health_marks_relay(&body))
            .unwrap_or(false),
        Err(_) => false,
    }
}

/// Паспорт реле в ответе `/health`: поле `service` равно `relay`.
fn relay_health_marks_relay(body: &str) -> bool {
    serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("service").and_then(|s| s.as_str()).map(|s| s == "relay"))
        .unwrap_or(false)
}

fn relay_account_blocking() -> Result<serde_json::Value, String> {
    let cfg = config_value().ok_or_else(|| "helene.json не прочитан".to_string())?;
    let port = relay_port(&cfg);
    let key = cfg
        .get("model")
        .and_then(|m| m.get("key"))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    if !harness_alive(port) {
        return Ok(serde_json::json!({ "reachable": false, "port": port }));
    }
    // Ревью 25.09 (A4 F5): loopback без прокси из среды и без редиректов — иначе за
    // HTTPS_PROXY запрос к своему реле уходит на прокси вместе с Bearer ключом мозга, а
    // расписка врёт «реле не отвечает» (тот же билдер, что у `home_probe`).
    let agent = ureq::AgentBuilder::new()
        .timeout_connect(Duration::from_millis(700))
        .timeout_read(Duration::from_secs(3))
        .redirects(0)
        .try_proxy_from_env(false)
        .build();
    let mut req = agent.get(&format!("http://127.0.0.1:{port}/v1/account"));
    if !key.trim().is_empty() {
        req = req.set("Authorization", &format!("Bearer {key}"));
    }
    match req.call() {
        Ok(resp) => {
            let text = resp.into_string().map_err(|e| format!("реле не дочиталось: {e}"))?;
            let body: serde_json::Value =
                serde_json::from_str(&text).map_err(|e| format!("реле ответило не JSON: {e}"))?;
            Ok(serde_json::json!({
                "reachable": true,
                "port": port,
                "active_slot": body.get("active_slot").cloned().unwrap_or(serde_json::Value::Null),
                "configured_slots": body.get("configured_slots").cloned().unwrap_or(serde_json::Value::Null),
            }))
        }
        Err(ureq::Error::Status(code, _)) => Ok(serde_json::json!({ "reachable": true, "port": port, "status": code })),
        Err(err) => Ok(serde_json::json!({ "reachable": false, "port": port, "error": err.to_string() })),
    }
}

/// Спека реле попадает в план только при relay.enabled — «сознательно не
/// поднимаю» и «не смог» больше не сходятся в одном молчаливом None.
fn spawn_relay(base: &Path, cfg: &serde_json::Value, tree: &Path) -> RelaySpawn {
    let relay = exe_name("helene-relay");
    let exe = base.join(&relay);
    if !exe.exists() {
        return RelaySpawn::Unavailable(format!("relay.enabled, но {relay} рядом нет — реле не поднимаю"));
    }
    let port = relay_port(cfg);
    // Порт реле занимает кто-то ещё (служба, осиротевшее реле прежней установки или
    // чужая программа): раньше своё реле уходило в петлю перезапусков, а весь
    // мозг молча шёл в ЧУЖОЕ реле — то есть в чужую подписку и чужую сессию.
    // Это ожидание, не падение: слово о нём говорит надзор один раз на состояние.
    if harness_alive(port) {
        // Ревью 25.09 (A4 F7): «другая копия» утверждалось без пробы держателя. Чужая
        // программа на порту — не ожидание, а отказ словами: подсказка «смени relay.port»
        // и тост, иначе мозг молча стучится в чужой процесс.
        if !port_holder_is_relay(port) {
            return RelaySpawn::Unavailable(format!(
                "порт реле {port} держит не реле (чужая программа): освободи порт или смени relay.port в настройках — своё реле не поднимаю"
            ));
        }
        return RelaySpawn::WaitingPort(port);
    }
    let home = tree.join("relay");
    let _ = std::fs::create_dir_all(&home);
    let python = bundled_python(base);
    let mut cmd = Command::new(&exe);
    // Инструкции реле: без этой переменной реле кладёт ПЕРЕД конституцией
    // агента 23 КБ чужого системного промпта («ты кодинг-агент Codex CLI») —
    // ~5-6 тыс. токенов подписки на каждый ход и прямой конфликт ролей.
    // minimal — ~60 слов; если апстрим их отвергнет, реле само повторит на
    // полном промпте. Вернуть прежнее: "relay": {"instructions": "full"}.
    let instructions = cfg
        .get("relay")
        .and_then(|r| r.get("instructions"))
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("minimal")
        .to_string();
    cmd.arg("serve")
        .current_dir(&home)
        .env("RELAY_PORT", port.to_string())
        .env("RELAY_LOCAL", "1")
        .env("RELAY_INSTRUCTIONS", instructions)
        .env("RELAY_LOG_DIR", home.join("logs"))
        // 25.09 (C.4): терминал реле — структурным полем `relay_terminal` рядом с чанком.
        // Движок читает его до content: лимит подписки становится типизированной
        // ошибкой с часом восстановления, а не английской репликой в чате.
        .env("RELAY_TYPED_TERMINAL", "field")
        .env("HELENE_PARENT_PID", std::process::id().to_string());
    // Ключ мозга = ключ реле: сгенерированный при установке ключ обязателен
    // Bearer-ом на /chat/completions — открытый локальный порт позволял бы
    // любому процессу на машине жечь подписку владельца.
    if let Some(key) = cfg
        .get("model")
        .and_then(|m| m.get("key"))
        .and_then(|v| v.as_str())
        .filter(|k| !k.trim().is_empty())
    {
        cmd.env("RELAY_API_KEY", key);
    }
    if python.exists() {
        cmd.env("RELAY_PYTHON", &python);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    #[cfg(unix)]
    cmd.process_group(0);
    match cmd.spawn() {
        Ok(child) => {
            adopt(&child);
            if let Ok(mut seen) = RELAY_LOGIN_SEEN.lock() {
                *seen = relay_login_generation();
            }
            RelaySpawn::Started(child)
        }
        Err(err) => RelaySpawn::Unavailable(format!("реле не поднялось: {err}")),
    }
}

/// Локальный режим: что и где поднимать для ОДНОГО агента. Чистый расчёт,
/// ничего не запускает — план живёт весь сеанс, чтобы повторная попытка была
/// возможна.
///
/// `base` — папка КОНФИГА этого агента (у корневого = папка программы, у
/// соседей = `agents/<id>/`): все относительные пути внутри файла считаются от
/// неё, и это единственная база (см. `localharness/agents.py`).
///
/// `relay_here` — поднимать ли реле подписки. Реле одно на установку: порт у
/// него один, вход в подписку один, и два реле дрались бы за него сами с
/// собой. Поэтому его просит только корневой агент.
fn build_plan(
    agent: &AgentEntry,
    base: &Path,
    cfg: &serde_json::Value,
    relay_here: bool,
    with_runner: bool,
) -> SpawnPlan {
    let port = agent.port;
    let tree = agent.tree.clone();
    let token = ensure_desk_token(&tree).unwrap_or_default();
    let python = python_path(base, cfg);
    let app = resolve(
        base,
        cfg.get("app").and_then(|v| v.as_str()).unwrap_or("deskapp.py"),
    );
    let host = if phone_enabled(cfg) { "0.0.0.0" } else { "127.0.0.1" };
    let mut specs = Vec::new();
    if relay_here && relay_enabled(cfg) {
        specs.push(ChildSpec::Relay {
            base: install_root(),
            cfg: cfg.clone(),
            tree: tree.clone(),
        });
    }
    specs.push(ChildSpec::Script {
        python: python.clone(),
        script: app,
        args: vec![port.to_string()],
        tree: tree.clone(),
        host: host.into(),
        token: token.clone(),
        config: agent.config.clone(),
    });
    if let Some(runner_raw) = cfg.get("runner").and_then(|v| v.as_str()).filter(|_| with_runner) {
        let config = agent.config.to_string_lossy().into_owned();
        specs.push(ChildSpec::Script {
            python,
            script: resolve(base, runner_raw),
            args: vec!["--config".into(), config],
            tree: tree.clone(),
            host: "127.0.0.1".into(),
            token: token.clone(),
            config: agent.config.clone(),
        });
    }
    SpawnPlan {
        agent: agent.id.clone(),
        name: agent.name.clone(),
        config: agent.config.clone(),
        specs,
        port,
        tree,
        token,
    }
}

/// Планы всех агентов установки, кого можно поднимать. Реле — только у
/// корневого; спорящие за порт и снятые сюда не попадают (`raisable`).
fn build_plans(base: &Path) -> Vec<SpawnPlan> {
    let mut out = Vec::new();
    for agent in raisable(base) {
        let cfg = agent_config(&agent.config);
        if cfg.get("mode").and_then(|v| v.as_str()).unwrap_or("local") != "local" {
            continue;               // удалённый агент живёт не здесь — поднимать нечего
        }
        // ⚠ Ненастроенному соседу канал поднимаем ВСЁ РАВНО, а раннер — нет.
        // Окно продукта — это страница, которую отдаёт канал агента: без него
        // владельцу негде вписать мозг новому агенту, и «добавить агента»
        // упиралось бы в пустое окно. А раннер без ключа модели не думает, а
        // ошибается на каждом ходе — поэтому его подъём ждёт настройки.
        // Установщик ради соседа не зовём: он настраивается в своём окне.
        let ready = !unconfigured(&Some(cfg.clone()));
        if !ready {
            log_line(&format!(
                "агент «{}» ({}) ещё не настроен — поднимаю только канал; впиши мозг в его окне",
                agent.name, agent.id
            ));
        }
        let dir = agent.dir.clone();
        out.push(build_plan(&agent, &dir, &cfg, agent.base, ready));
    }
    out
}

/// Поднять своих детей, если порт свободен. Пустой список — «не сейчас», а не
/// «никогда»: надзор попробует снова (порт освободился, антивирус отпустил).
/// `announce` — говорить ли владельцу; при повторных попытках молчим, чтобы
/// не превращать журнал и трей в дребезг.
///
/// Второе значение — вердикт по занятому порту, если порт занят. Он нужен
/// НАВЕРХУ: пока его знала только эта функция, окно всё равно строило адрес
/// вебвью тем же портом и становилось клиентом чужой установки.
fn start_children(plan: &SpawnPlan, announce: bool) -> (Vec<Managed>, Option<Verdict>) {
    // Дерево создаём ДО проверки: иначе canonicalize своего пути падает и
    // сравнение с чужим давало ложное «чужая установка».
    let _ = std::fs::create_dir_all(&plan.tree);
    let port = plan.port;
    if harness_alive(port) {
        let verdict = harness_verdict(port, &plan.tree, &plan.token);
        match &verdict {
            Verdict::Ours => {
                if announce {
                    log_line(&format!(
                        "код агента{} уже жив на 127.0.0.1:{port} — подключаюсь без своих детей",
                        plan.whose()
                    ));
                }
            }
            Verdict::Guarded => {
                if announce {
                    log_line(&format!(
                        "порт {port} держит код агента под ключом, которого у меня нет — подключаюсь без своих детей{}",
                        plan.whose()
                    ));
                }
            }
            Verdict::Foreign(theirs) => {
                if announce {
                    let whose = match theirs {
                        Some(t) => format!(" (её дерево: {})", t.display()),
                        None => String::new(),
                    };
                    let file = plan.config.display();
                    log_line(&format!(
                        "порт {port} занят ДРУГОЙ программой{whose} — свой код агента{} не поднимаю и в её дерево не хожу; закрой её или смени порт в {file}",
                        plan.whose()
                    ));
                    toast(product_ui(), &format!(
                        "Порт {port} занят другой программой. Пока она его держит, агент{} не поднимется. Закрой прежнюю копию или смени порт в {file}.",
                        plan.whose()
                    ));
                }
            }
        }
        return (Vec::new(), Some(verdict));
    }
    let mut children = Vec::new();
    let mut failed: Vec<&'static str> = Vec::new();
    for spec in &plan.specs {
        let (child, waiting) = match spec.spawn() {
            SpawnOutcome::Started(child) => (Some(child), false),
            SpawnOutcome::Waiting(why) => {
                // Ожидание (порт реле держит служба) — не «не поднялось»: в тост и в
                // счётчик падений не идёт, слово в журнал — одно, при входе в состояние.
                log_line(&why);
                (None, true)
            }
            SpawnOutcome::Failed => {
                failed.push(spec.human());
                (None, false)
            }
        };
        children.push(Managed {
            agent: plan.agent.clone(),
            spec: spec.clone(),
            // Не поднялся — спеку НЕ выбрасываем: надзор попробует через 30 с.
            retry_at: child.is_none().then(|| Instant::now() + Duration::from_secs(30)),
            child,
            falls: Vec::new(),
            halted: false,
            waiting,
        });
    }
    if announce && !failed.is_empty() {
        let names = failed.join(", ");
        log_line(&format!("не поднялось с первого раза: {names} — пробую снова через 30 с"));
        toast(
            product_ui(),
            &format!("Не удалось запустить: {names}. Пробую снова; причина — в helene.log рядом с программой."),
        );
    }
    (children, None)
}

/// Не настроено: явного receipt ещё нет и ключ модели тоже пуст.
/// `setup_complete` нужен не для красоты: у локальных Ollama/LM Studio ключа по
/// построению нет, поэтому проверка только `model.key` считала бы их ненастроенными.
fn unconfigured(cfg: &Option<serde_json::Value>) -> bool {
    match cfg {
        None => true,
        Some(cfg) => {
            if cfg
                .get("setup_complete")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
            {
                return false;
            }
            cfg.get("model")
                .and_then(|m| m.get("key"))
                .and_then(|k| k.as_str())
                .map(|k| k.trim().is_empty())
                .unwrap_or(true)
        }
    }
}

/// mtime файла в наносекундах — отпечаток свежести для окна. Строкой: в JS
/// число теряет точность за 2^53, а Python-сторона (`readers.safe_read_md`)
/// уже отдаёт `mtime_ns` строкой — одна форма на оба канала.
fn file_mtime_ns(path: &Path) -> Option<String> {
    let modified = std::fs::metadata(path).ok()?.modified().ok()?;
    let nanos = modified.duration_since(std::time::UNIX_EPOCH).ok()?.as_nanos();
    Some(nanos.to_string())
}

/// Записать конфиг целиком (экран настроек). Атомарно: tmp + rename.
///
/// `mtime_ns` — отпечаток, который окно получило от `config_load`. Не совпал с
/// файлом на диске — файл менял кто-то ещё (руннер чинил `agent_mode`,
/// установщик обновлял поверх, владелец правил в Блокноте), и черновик окна
/// устарел: отвечаем `{ok:false, code:"stale"}` с текущим отпечатком и не
/// пишем, как `safe_write_md` у маркдаунов (ревью 06.09, §3, решение 3).
/// Старое окно без отпечатка пишет как раньше.
#[tauri::command]
fn config_save(app: tauri::AppHandle, config: String, mtime_ns: Option<String>) -> Result<serde_json::Value, String> {
    // Файл ТОГО агента, которого показывает окно: у корневого — рядом с
    // программой, у соседа — его собственный. Иначе настройки второго агента
    // молча уезжали бы в конфиг первого.
    let target = current_config_path();
    let before = match read_config(&target) {
        ConfigRead::Ok(v) => Some(v),
        _ => None,
    };
    let mut out = config_save_at(&target, &config, mtime_ns.as_deref())?;
    // 25.09 (C.1): настройки реле применяются сразу, без перезапуска окна. Реле одно
    // на установку и живёт у корневого агента — сверяем только корневой конфиг.
    // Мозг (модель, ключ, адрес) движок перечитывает сам: он следит за helene.json.
    let saved = out.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
    if saved && target == install_root().join(CONFIG_NAME) {
        if let Ok(after) = serde_json::from_str::<serde_json::Value>(&config) {
            // Ревью 25.09 (A4 F8): отпечаток сравнивается только когда реле было или стало
            // включено — владелец без реле, сменивший ключ GLM, не получает в расписке
            // «настройки реле изменились — своего реле не было и не нужно».
            let relay_involved = relay_enabled(&after)
                || before.as_ref().map(relay_enabled).unwrap_or(false);
            let changed = relay_involved
                && before
                    .as_ref()
                    .map(|b| relay_fingerprint(b) != relay_fingerprint(&after))
                    .unwrap_or(true);
            if changed {
                let state = app.state::<LocalHarness>();
                let said = reconcile_relay(&state, &after, "настройки реле изменились");
                if let Some(obj) = out.as_object_mut() {
                    obj.insert("relay".into(), serde_json::Value::String(said));
                }
            }
        }
    }
    Ok(out)
}

fn config_save_at(target: &Path, config: &str, mtime_ns: Option<&str>) -> Result<serde_json::Value, String> {
    let parsed: serde_json::Value =
        serde_json::from_str(config).map_err(|e| format!("это не JSON: {e}"))?;
    let pretty = serde_json::to_string_pretty(&parsed).map_err(|e| e.to_string())?;
    let seen = mtime_ns.map(str::trim).filter(|s| !s.is_empty());
    if let (Some(seen), Some(now)) = (seen, file_mtime_ns(target)) {
        if seen != now {
            return Ok(serde_json::json!({
                "ok": false,
                "code": "stale",
                "mtime_ns": now,
                "error": "Настройки на диске изменились с тех пор, как окно их открыло — перечитай и перенеси правку заново.",
            }));
        }
    }
    let dir = target.parent().map(Path::to_path_buf).unwrap_or_else(install_root);
    let tmp = dir.join(format!(".tmp-{}", target.file_name().and_then(|n| n.to_str()).unwrap_or(CONFIG_NAME)));
    std::fs::write(&tmp, pretty).map_err(|e| format!("не записалось: {e}"))?;
    std::fs::rename(&tmp, target).map_err(|e| format!("не подменилось: {e}"))?;
    Ok(serde_json::json!({ "ok": true, "mtime_ns": file_mtime_ns(target) }))
}

/// Перезапуск начисто — единственный способ применить настройки, поэтому он
/// не имеет права быть гонкой. Себя из себя больше не спавним: новый процесс
/// заставал живого (single-instance) и выходил сам, либо успевал увидеть ещё
/// не убитых детей старого и оставался окном без харнесса. Теперь: гасим
/// детей, отдаём запуск отложенному хвосту cmd и только потом выходим.
#[tauri::command]
fn restart_self(app: tauri::AppHandle) {
    let Ok(exe) = std::env::current_exe() else {
        log_line("перезапуск: не узнал собственный путь");
        return;
    };
    let dir = exe.parent().map(Path::to_path_buf).unwrap_or_else(exe_dir);
    let state = app.state::<LocalHarness>();
    kill_children(&state);
    relay_abort();
    #[cfg(windows)]
    {
        let mut cmd = Command::new(sys_exe("cmd.exe"));
        cmd.current_dir(std::env::temp_dir());
        cmd.arg("/C");
        // Командную строку отдаём сырой: std экранирует кавычки как \", чего
        // cmd не понимает. Пауза — чтобы старый процесс успел умереть и
        // отпустить порт до того, как новый решит судьбу детей.
        cmd.raw_arg(format!(
            "ping 127.0.0.1 -n 3 >nul & start \"\" /D \"{}\" \"{}\"",
            dir.display(),
            exe.display()
        ));
        cmd.creation_flags(CREATE_NO_WINDOW);
        if let Err(err) = cmd.spawn() {
            // Не выходим: лучше живое окно без применённых настроек, чем
            // тишина после нажатия «Перезапустить сейчас».
            log_line(&format!("перезапуск не запустился: {err}"));
            toast(product_ui(), "Перезапуск не запустился — закрой и открой программу сам.");
            return;
        }
    }
    #[cfg(not(windows))]
    {
        // Та же пауза, что и на Windows, — иначе новый процесс застаёт живого
        // (single-instance) и выходит сам. Хвост отсоединён в свою группу:
        // выход старого процесса не должен утянуть его за собой.
        // macOS: бандл через `open -n` (три уровня выше бинаря), а не голый
        // exe — иначе Dock, меню и активация окна ведут себя как у скрипта.
        let target: PathBuf = match app_bundle_of(&exe) {
            Some(bundle) => bundle,
            None => exe.clone(),
        };
        let relaunch = if cfg!(target_os = "macos") && target.extension().is_some_and(|e| e == "app") {
            "sleep 2; exec /usr/bin/open -n \"$0\""
        } else {
            "sleep 2; exec \"$0\""
        };
        let mut cmd = Command::new(posix_tool("sh"));
        cmd.arg("-c")
            .arg(relaunch)
            .arg(&target)
            .current_dir(&dir)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0);
        if let Err(err) = cmd.spawn() {
            log_line(&format!("перезапуск не запустился: {err}"));
            toast(product_ui(), "Перезапуск не запустился — закрой и открой программу сам.");
            return;
        }
    }
    app.exit(0);
}

/// Бандл `.app`, внутри которого лежит этот бинарь: `X.app/Contents/MacOS/x`
/// → `X.app`. Не в бандле (сборка из cargo) — None.
#[cfg(not(windows))]
fn app_bundle_of(exe: &Path) -> Option<PathBuf> {
    let bundle = exe.parent()?.parent()?.parent()?;
    if bundle.extension().is_some_and(|e| e == "app") {
        Some(bundle.to_path_buf())
    } else {
        None
    }
}

/// Незавершённый вход в ChatGPT: один за раз. Повторное нажатие отменяет
/// прежнюю попытку — иначе помощник реле держит порт 1455, и новые попытки
/// падают с 400 в браузере.
static LOGIN: Mutex<Option<Child>> = Mutex::new(None);

/// Отравленный замок здесь не означает испорченных данных: внутри только
/// хэндл процесса. Раньше при отравлении Child терялся, и помощник входа
/// становился неубиваемым — держал порт 1455 до перезагрузки.
fn login_lock() -> std::sync::MutexGuard<'static, Option<Child>> {
    LOGIN.lock().unwrap_or_else(|e| e.into_inner())
}

fn abort_login_child(slot: &mut Option<Child>) {
    if let Some(mut child) = slot.take() {
        if child.try_wait().ok().flatten().is_none() {
            // Всё дерево (`/T`): помощник входа поднимает питон с колбэк-сервером,
            // и живым должен не остаться именно он.
            #[cfg(windows)]
            {
                let mut kill = Command::new(sys_exe("taskkill.exe"));
                kill.args(["/PID", &child.id().to_string(), "/T", "/F"]);
                let _ = run_hidden_for(&mut kill, Duration::from_secs(15));
                let _ = child.wait();
            }
            #[cfg(not(windows))]
            stop_child(&mut child);
        }
    }
}

/// Остановить ребёнка. Windows: kill + wait, как было (дерево добивает
/// job-объект). POSIX: SIGTERM всей группе процессов ребёнка (он её лидер —
/// см. `process_group(0)` при спавне), три секунды на выход, потом SIGKILL
/// группе — чтобы внуки (питон под реле, колбэк-сервер входа) не пережили окно.
#[cfg(windows)]
fn stop_child(child: &mut Child) {
    let _ = child.kill();
    let _ = child.wait();
}

#[cfg(unix)]
fn stop_child(child: &mut Child) {
    let group = child.id() as libc::pid_t;
    unsafe {
        libc::killpg(group, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(3);
    let mut reaped = false;
    loop {
        match child.try_wait() {
            Ok(Some(_)) => {
                reaped = true;
                break;
            }
            Ok(None) => {}
            Err(_) => break,
        }
        if Instant::now() >= deadline {
            unsafe {
                libc::killpg(group, libc::SIGKILL);
            }
            let _ = child.wait();
            reaped = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    // Лидер вышел, а группа жива, пока жив последний её процесс: добиваем
    // внуков, которые SIGTERM проигнорировали.
    //
    // ⚠⚠ НО ТОЛЬКО ЕСЛИ PID ЕЩЁ НАШ (судьи 19.09). После `wait` номер процесса
    // свободен, и система выдаёт его следующему, кто запустится; на Mac это
    // происходит за секунды. Безусловный `killpg` по пожатому pid — это SIGKILL
    // ЧУЖОЙ группе процессов: у владельца молча умирал бы случайный терминал
    // или браузер, и связать это с закрытием окна было бы нечем.
    if may_signal_group(reaped, unix_group_leader_alive(group)) {
        unsafe {
            libc::killpg(group, libc::SIGKILL);
        }
    }
}

/// Жив ли процесс и сам ли он лидер своей группы. Оба вопроса сразу: pid,
/// который переиспользовали, лидером нашей группы уже не будет, а если и стал
/// им — это чужая группа с чужими детьми.
#[cfg(unix)]
fn unix_group_leader_alive(pid: libc::pid_t) -> bool {
    unsafe { libc::kill(pid, 0) == 0 && libc::getpgid(pid) == pid }
}

/// Можно ли слать сигнал группе процессов. Чистая функция — по ней и стенд,
/// потому что цена ошибки здесь не видна ни в одном журнале: сигнал уходит
/// ЧУЖОМУ процессу, и выглядит это как «у меня само закрылось».
///
/// `reaped` — ребёнка уже пожали (`wait` вернул статус), то есть его pid
/// свободен; `alive_leader` — по этому номеру прямо сейчас живёт лидер группы.
#[cfg_attr(windows, allow(dead_code))]
fn may_signal_group(reaped: bool, alive_leader: bool) -> bool {
    !reaped && alive_leader
}

// ───────────────────────────────── тело под службой (macOS, §6 плана 19.09)
//
// ЗАЧЕМ. Под демоном launchd движок живёт вне графической сессии: WindowServer
// его не видит, TCC ему ничего не выдаст, и тело (`helene-body`) оттуда просто
// не работает. Поэтому движок под службой поднимает ТОЛЬКО мост и кладёт токен
// устройства в `memory/.state/body-token`, а тело поднимает ОКНО — оно и есть
// та самая графическая сессия, и разрешения «Запись экрана» с «Универсальным
// доступом» выданы именно Helene.app.
//
// Это зеркало Windows, где служба живёт в нулевой сессии, а интерактивную
// половину поднимает задача планировщика в сессии владельца.

/// Тело, поднятое окном под службой. Отдельно от `LocalHarness.children`: у
/// этого ребёнка другое условие жизни (он появляется, когда движок уже жив у
/// службы) и другой владелец решения — опция «Управление компьютером».
#[cfg(target_os = "macos")]
static SERVICE_BODY: Mutex<Option<Child>> = Mutex::new(None);

/// Погасить тело, поднятое окном. Зовётся из `kill_children`: выход из строки
/// меню и ⌘Q обязаны уносить его с собой, иначе оно осталось бы висеть на
/// мосту службы после закрытия окна — то есть агент «видел бы экран», когда
/// владелец его закрыл.
#[cfg(target_os = "macos")]
fn stop_service_body() {
    if let Ok(mut guard) = SERVICE_BODY.lock() {
        if let Some(child) = guard.as_mut() {
            stop_child(child);
        }
        *guard = None;
    }
}

/// Токен устройства, который движок под службой положил для окна. Файл
/// перезаписывается движком на каждом старте и лежит правами 0600 — читает его
/// только владелец. Пустой или обрезанный файл — это НЕ токен: пустая строка в
/// `PRAXIS_BODY_TOKEN` означала бы тело, которое мост пускает без ключа.
#[cfg(target_os = "macos")]
fn service_body_token(tree: &Path) -> Option<String> {
    let raw = std::fs::read_to_string(tree.join("memory").join(".state").join("body-token")).ok()?;
    let token = raw.trim().to_string();
    (token.len() >= 16).then_some(token)
}

/// Жив ли мост, к которому тело должно подключиться.
///
/// ⚠ ЗАЧЕМ ЭТА ПРОВЕРКА. Движок под службой убирает ключ устройства, когда
/// гасится сам, — но только если успевает: `launchctl bootout` снимает группу
/// сигналом, и питон уходит без своих `atexit`. Тогда на диске остаётся ключ к
/// мосту, которого уже нет, а окно поднимало бы тело раз за разом в пустоту —
/// с растущей паузой и строкой в журнал на каждый круг. Адрес моста берём
/// оттуда же, откуда его возьмёт тело, — из его конфига.
#[cfg(target_os = "macos")]
fn service_bridge_alive(body_json: &Path) -> bool {
    let Ok(raw) = std::fs::read_to_string(body_json) else { return false };
    let Ok(cfg) = serde_json::from_str::<serde_json::Value>(&raw) else { return false };
    let url = cfg.get("bridge_ws_url").and_then(|v| v.as_str()).unwrap_or("");
    let Some(port) = url.rsplit(':').next().and_then(|p| p.trim().parse::<u16>().ok()) else {
        return false;
    };
    harness_alive(port)
}

/// Надзор за телом под службой. Раз в три секунды: жив ли ребёнок, нужен ли он
/// вообще и можно ли его поднять.
///
/// Условия подъёма названы все и проверяются каждый раз, потому что любое из
/// них меняется на живой системе:
///   * окно работает КЛИЕНТОМ — своих детей у него нет (движок держит служба);
///   * владелец включил «Управление компьютером» (`computer.enabled`);
///   * движок уже положил токен устройства и конфиг тела — то есть мост поднят
///     и ждёт подключения;
///   * `helene-body` в поставке есть.
/// Не сложилось — ждём молча (одна строка в журнал на смену состояния): это
/// обычная жизнь, а не сбой.
#[cfg(target_os = "macos")]
fn watch_service_body(app: tauri::AppHandle, tree: PathBuf, config: PathBuf) {
    use std::sync::atomic::Ordering;
    let exe = install_root().join("helene-body");
    let body_json = tree.join("body").join("body.json");
    let mut backoff: u64 = 0;
    let mut not_before = Instant::now();
    let mut said = String::new();
    loop {
        std::thread::sleep(Duration::from_secs(3));
        let state = app.state::<LocalHarness>();
        if state.stopping.load(Ordering::Relaxed) {
            stop_service_body();
            return;
        }
        // Ребёнок жив? Умер — пауза растёт, как у остальных детей окна.
        {
            let Ok(mut guard) = SERVICE_BODY.lock() else { continue };
            if let Some(child) = guard.as_mut() {
                match child.try_wait() {
                    Ok(Some(status)) => {
                        *guard = None;
                        backoff = if backoff == 0 { 5 } else { (backoff * 2).min(60) };
                        not_before = Instant::now() + Duration::from_secs(backoff);
                        log_line(&format!(
                            "тело под службой завершилось ({status}) — подниму через {backoff} с"
                        ));
                    }
                    Ok(None) => continue,   // живо — больше ничего не нужно
                    Err(_) => continue,     // не смогли спросить — это не смерть
                }
            }
        }
        if Instant::now() < not_before {
            continue;
        }
        // Своих детей нет — значит движок держит кто-то другой (служба или
        // второе окно). Если окно подняло свой движок, тело поднимает он сам.
        let client = state
            .children
            .lock()
            .map(|g| g.iter().all(|m| m.child.is_none()))
            .unwrap_or(false);
        let cfg = match read_config(&config) {
            ConfigRead::Ok(v) => v,
            _ => serde_json::json!({}),
        };
        let wanted = cfg
            .get("computer")
            .and_then(|c| c.get("enabled"))
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let token = service_body_token(&tree);
        let why = if !client {
            "движок подняло само окно — тело поднимает он"
        } else if !wanted {
            "опция «Управление компьютером» выключена"
        } else if !exe.is_file() {
            "в этой сборке нет helene-body"
        } else if !body_json.is_file() || token.is_none() {
            "движок службы ещё не поднял мост (нет конфига тела или токена устройства)"
        } else if !service_bridge_alive(&body_json) {
            "мост службы не отвечает — поднимать тело некуда"
        } else {
            ""
        };
        if !why.is_empty() {
            if said != why {
                said = why.to_string();
                log_line(&format!("тело под службой не поднимаю: {why}"));
            }
            continue;
        }
        let Some(token) = token else { continue };
        let home = tree.join("body");
        let mut cmd = Command::new(&exe);
        cmd.arg("connect")
            .arg("--config")
            .arg(&body_json)
            .current_dir(&home)
            // Токен — ЧЕРЕЗ СРЕДУ, а не аргументом: аргументы видны всей машине
            // в `ps`, и ключ к телу владельца лежал бы там открытым текстом.
            .env("PRAXIS_BODY_TOKEN", &token)
            .env("RUST_LOG", "praxis_body=info")
            .env("HELENE_PARENT_PID", std::process::id().to_string());
        if let Some(log) = child_log(&home, Path::new("body")) {
            if let Ok(err) = log.try_clone() {
                cmd.stdout(Stdio::from(log)).stderr(Stdio::from(err));
            }
        }
        cmd.process_group(0);
        match cmd.spawn() {
            Ok(child) => {
                adopt(&child);
                log_line(&format!(
                    "тело под службой поднято окном (pid {}, конфиг тела {})",
                    child.id(),
                    body_json.display()
                ));
                said = "поднято".to_string();
                if let Ok(mut guard) = SERVICE_BODY.lock() {
                    *guard = Some(child);
                }
            }
            Err(err) => {
                backoff = if backoff == 0 { 5 } else { (backoff * 2).min(60) };
                not_before = Instant::now() + Duration::from_secs(backoff);
                log_line(&format!(
                    "тело под службой не поднялось ({}): {err} — ещё попытка через {backoff} с",
                    exe.display()
                ));
            }
        }
    }
}

fn relay_abort() {
    let mut guard = login_lock();
    abort_login_child(&mut guard);
}

/// Дом реле — из конфига (tree/relay), как и у поднятого реле. Раньше вход и
/// статус смотрели в захардкоженный data/relay: у владельца, перенёсшего
/// дерево, вход «выполнялся» туда, где реле его никогда не искало.
fn relay_home() -> PathBuf {
    // ⚠ Именно КОРНЕВОЕ дерево, а не дерево текущего агента: реле одно на
    // установку (§ build_plan), и после переключения агента в окне кнопка
    // «Войти в подписку» иначе писала бы вход туда, где реле его не ищет.
    base_tree().join("relay")
}

#[tauri::command]
async fn relay_login() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(relay_login_blocking)
        .await
        .map_err(|e| e.to_string())?
}

fn relay_login_blocking() -> Result<String, String> {
    // Логин реле в подписку ChatGPT: колбэк-сервер поднимает встроенный питон,
    // браузер открывается сам. Консоль не нужна — и не появляется.
    // Замок держим на всём протяжении: между «погасить прежнего» и «записать
    // нового» проскакивало второе нажатие, и первый помощник оставался
    // сиротой на порту 1455.
    let mut guard = login_lock();
    abort_login_child(&mut guard);
    let base = install_root();
    let relay = exe_name("helene-relay");
    let exe = base.join(&relay);
    if !exe.exists() {
        return Err(format!("в этой сборке нет {relay}"));
    }
    let home = relay_home();
    let _ = std::fs::create_dir_all(&home);
    let mut cmd = Command::new(&exe);
    cmd.arg("login")
        .current_dir(&home)
        .env("RELAY_LOCAL", "1")
        .env("RELAY_LOG_DIR", home.join("logs"))
        .env("HELENE_PARENT_PID", std::process::id().to_string());
    let python = bundled_python(&base);
    if python.exists() {
        cmd.env("RELAY_PYTHON", &python);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    #[cfg(unix)]
    cmd.process_group(0);
    if let Ok(mut before) = LOGIN_AUTH_BEFORE.lock() {
        *before = auth_signature(&home.join("local_auth").join("auth.json"));
    }
    let child = cmd.spawn().map_err(|e| format!("логин не запустился: {e}"))?;
    // В job-объект окна: выход из трея с незавершённым входом оставлял
    // helene-relay.exe login жить и держать порт колбэка.
    adopt(&child);
    *guard = Some(child);
    // Вход запущен: когда помощник завершится успехом и auth.json станет другим,
    // `relay_status` поставит маркер нового входа (даже если экран настроек опрашивал его
    // не каждые 3 с).
    LOGIN_WAS_PENDING.store(true, std::sync::atomic::Ordering::Relaxed);
    Ok("сейчас откроется браузер — войди в свой аккаунт ChatGPT".into())
}

#[tauri::command]
fn relay_status(app: tauri::AppHandle) -> String {
    let auth = relay_home().join("local_auth").join("auth.json");
    // (жив ли помощник, вышел ли успехом)
    let (pending, helper_ok) = {
        let mut guard = login_lock();
        match guard.as_mut() {
            Some(child) => match child.try_wait() {
                Ok(None) => (true, false),
                Ok(Some(status)) => (false, status.success()),
                Err(_) => (false, false),
            },
            None => (false, false),
        }
    };
    if pending {
        LOGIN_WAS_PENDING.store(true, std::sync::atomic::Ordering::Relaxed);
        return "pending".into();
    }
    if auth.exists() {
        // 25.09 (C.3): реле читает вход ТОЛЬКО при старте. Помощник входа завершился
        // успехом и auth.json стал другим — это новый вход: ставим маркер, и своё реле
        // поднимаем заново сами, а не вешаем владельцу вечное «применится перезапуском».
        // Чужое реле (служба) перечитает маркер на своём тике и сделает то же.
        // Отменённый/упавший вход при старом auth.json маркера не ставит (A4 F6).
        if LOGIN_WAS_PENDING.swap(false, std::sync::atomic::Ordering::Relaxed) {
            let before = LOGIN_AUTH_BEFORE.lock().ok().and_then(|g| *g);
            let after = auth_signature(&auth);
            if login_marks_new_generation(helper_ok, before, after) {
                note_relay_login();
            } else {
                log_line(&format!(
                    "вход в подписку не дал нового auth.json (помощник {}), реле не перезапускаю",
                    if helper_ok { "вышел успехом, файл прежний" } else { "не завершился успехом" }
                ));
            }
        }
        let now_seen = relay_login_generation();
        let seen = RELAY_LOGIN_SEEN.lock().ok().and_then(|g| *g);
        if now_seen.is_some() && now_seen != seen {
            let state = app.state::<LocalHarness>();
            let owns = state
                .children
                .lock()
                .map(|g| g.iter().any(|m| matches!(m.spec, ChildSpec::Relay { .. }) && m.child.is_some()))
                .unwrap_or(false);
            if owns {
                if let Some(cfg) = config_value() {
                    reconcile_relay(&state, &cfg, "новый вход в подписку");
                }
            } else if let Ok(mut g) = RELAY_LOGIN_SEEN.lock() {
                *g = now_seen;
            }
        }
        return "authorized".into();
    }
    "no-auth".into()
}

#[cfg(windows)]
#[tauri::command]
async fn install_service() -> Result<String, String> {
    // Опциональная служба: один UAC. Само окно прав не требует и не получает;
    // рецепт — установщика (`common/service_op.rs`): ждём поднятый процесс,
    // читаем его код и спрашиваем SCM. Раньше результат не читался, и
    // «запрошено» значило «сделано» — отказ в UAC выглядел успехом.
    tauri::async_runtime::spawn_blocking(|| service_op_from_window("install"))
        .await
        .unwrap_or_else(|_| Err("вызов службы прерван".into()))
}

/// macOS: демон launchd. Ставится тем же приёмом, что просьба брокера, —
/// системным диалогом пароля (`osascript … with administrator privileges`),
/// а на машине с беспарольным sudo (раннер CI) через `sudo -n`.
#[cfg(target_os = "macos")]
#[tauri::command]
async fn install_service() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| mac_service_op("install"))
        .await
        .unwrap_or_else(|_| Err("вызов службы прерван".into()))
}

/// Службы на этой платформе нет; окно её карточку прячет (`app_info.platform`),
/// а до этих ручек из интерфейса не дойти — ответ словами на всякий случай.
#[cfg(not(any(windows, target_os = "macos")))]
#[tauri::command]
async fn install_service() -> Result<String, String> {
    Err("службы на этой платформе нет".into())
}

/// Поставить или снять демон launchd. Одна функция на обе кнопки: описание
/// собирает САМ `helene-svc` (`plist --config`), а окно только кладёт его во
/// временный файл и просит администратора перенести на место — иначе форма
/// описания разъехалась бы между тем, кто его пишет, и тем, кто по нему живёт.
///
/// Расписка — по состоянию ПОСЛЕ (launchctl), а не по коду команды: ровно то же
/// правило, что у Windows (`service_op_from_window` спрашивает SCM).
#[cfg(target_os = "macos")]
fn mac_service_op(op: &str) -> Result<String, String> {
    let root = install_root();
    // Конфиг КОРНЕВОГО агента: демон поднимает всех агентов установки сам
    // (`common/agents.rs`), как служба на Windows. Взяв здесь конфиг текущего
    // агента окна, мы бы поставили демон на одного из соседей.
    let config = root.join(CONFIG_NAME);
    let svc = root.join("helene-svc");
    let prompt = format!(
        "{}: {} службу «Работать без входа в систему»",
        product_ui(),
        if op == "install" { "поставить" } else { "снять" }
    );
    let mut staged: Option<PathBuf> = None;
    let line = if op == "install" {
        if !svc.is_file() {
            return Err("в этой сборке нет helene-svc — ставить нечего".into());
        }
        let out = Command::new(&svc)
            .arg("plist")
            .arg("--config")
            .arg(&config)
            .output()
            .map_err(|e| format!("helene-svc не запустился: {e}"))?;
        if !out.status.success() {
            return Err(format!(
                "описание демона не собралось: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        // Временный файл — от имени ВЛАДЕЛЬЦА: под администратором мы делаем
        // ровно три вещи (cp, chown, chmod) плюс загрузку, и ни одна из них не
        // пишет содержимое. Меньше прав — меньше того, чем можно ошибиться.
        //
        // ⚠⚠ И НЕ В ОБЩЕЙ ПАПКЕ ПОД ПРЕДСКАЗУЕМЫМ ИМЕНЕМ (судьи 19.09): между
        // этой записью и нажатием «Да» в диалоге пароля проходят секунды, если
        // не минуты, и `temp_dir()/app.helene.svc.plist` успевал подменить любой
        // процесс учётки — дальше под root копировалось уже чужое описание.
        // Теперь своя папка 0700 со случайным именем, файл под O_EXCL|0600, а
        // хэш содержимого сверяется под root перед `cp`.
        let (dir, tmp, hash) = mac_svc_stage_plist(&out.stdout)?;
        staged = Some(dir);
        mac_svc_install_line(&tmp, &hash)
    } else {
        mac_svc_remove_line()
    };
    let said = mac_svc_run_admin(&line, &prompt);
    if let Some(dir) = staged {
        let _ = std::fs::remove_dir_all(dir);
    }
    let state = mac_svc_state();
    // ⚠ Служба — ЭТО НЕ ДВИЖОК. Демон под `KeepAlive` бывает «running», пока
    // агент в нём падает по кругу, и расписка «Служба поставлена и работает»
    // была бы правдой о launchd и неправдой о машине владельца. Спрашиваем сам
    // канал — анонимно, без ключа (`/api/who`).
    let engine = mac_svc_engine_alive(mac_port_of(&root));
    let note = mac_svc_engine_note(&state, engine);
    let with_note = |text: &str| -> String {
        if note.is_empty() { text.to_string() } else { format!("{text} ({note})") }
    };
    match (op, &said, state.as_str()) {
        ("install", Ok(_), "running") => Ok(with_note("Служба поставлена и работает — агент отвечает без окна.")),
        ("install", Ok(_), "stopped") => Ok(
            "Служба поставлена, но launchd её сейчас не держит. Загляни в data/service.log —              причина там."
                .into(),
        ),
        ("install", Ok(_), "unknown") => Ok(
            "Служба поставлена; спросить launchd о её состоянии не вышло. Что происходит —              видно в data/service.log."
                .into(),
        ),
        ("install", Ok(_), _) => Err(
            "Команда прошла, а описания демона на месте нет — служба не встала. Подробности в              data/service.log."
                .into(),
        ),
        ("install", Err(why), _) => Err(format!("Служба не поставлена: {why}")),
        // ⚠ Мина 8094: службы нет, а порт держит кто-то ещё. Молча сказать
        // «Служба снята» и оставить владельца гадать, почему агент не
        // поднимается, — это то самое молчание прибора вместо факта.
        (_, Ok(_), "absent") => Ok(with_note("Служба снята.")),
        (_, Ok(_), st) => Err(format!("Команда прошла, а служба осталась: {st}.")),
        (_, Err(why), "absent") => Ok(format!("Службы нет ({why}).")),
        (_, Err(why), _) => Err(format!("Служба не снята: {why}")),
    }
}

/// Порт канала этой установки — для анонимной пробы движка. `None` — конфига
/// нет или порт в нём не назван: тогда о движке не говорим ничего.
#[cfg(target_os = "macos")]
fn mac_port_of(root: &Path) -> Option<u16> {
    match read_config(&root.join(CONFIG_NAME)) {
        ConfigRead::Ok(v) => v.get("port").and_then(|p| p.as_u64()).map(|n| n as u16),
        _ => None,
    }
}

/// Поднятая операция со службой из окна: расписка — по SCM, не по «запустил».
#[cfg(windows)]
fn service_op_from_window(op: &str) -> Result<String, String> {
    let script_name = if op == "install" { "install-service.ps1" } else { "uninstall-service.ps1" };
    let script = install_root().join(script_name);
    if !script.exists() {
        return Err(format!("в этой сборке нет {script_name}"));
    }
    let wrapper = std::env::temp_dir().join("helene-service-op.ps1");
    std::fs::write(&wrapper, SERVICE_OP_PS1).map_err(|e| format!("обёртка службы не записалась: {e}"))?;
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &service_op_command(&wrapper, op, product_fs(), Some(&script))]);
    // Дедлайн — на окно UAC и сам скрипт (установка ждёт старта службы);
    // повисший вызов не должен держать окно вечно.
    let out = run_hidden_for(&mut cmd, Duration::from_secs(300))?;
    let verdict = service_op_verdict(out.status.code());
    let state = service_state_blocking();
    match (op, verdict, state.as_str()) {
        ("install", Ok(()), "running") => Ok("Служба поставлена и запущена.".into()),
        ("install", Ok(()), "stopped") => Ok("Служба поставлена, но ещё не запущена — SCM поднимет её сам или запусти из оснастки.".into()),
        ("install", Err(e), "absent") => Err(format!("Служба не установлена: {e}.")),
        ("install", Err(e), st) => Ok(format!("Скрипт вернул ошибку ({e}), но по SCM служба есть: {st}.")),
        (_, Ok(()), "absent") => Ok("Служба снята.".into()),
        (_, Ok(()), st) => Err(format!("Скрипт отработал, а служба по SCM осталась: {st}.")),
        (_, Err(e), "absent") => Ok(format!("Службы нет ({e}).")),
        (_, Err(e), st) => Err(format!("Служба не снята: {e}; по SCM — {st}.")),
    }
}

/// Уведомление Windows из веб-части (заголовок, текст).
#[tauri::command]
fn notify(title: String, body: String) {
    toast(&title, &body);
}

/// Конфиг целиком для экрана настроек плюс где он лежит и где данные.
#[tauri::command]
fn config_load() -> Result<serde_json::Value, String> {
    let base = install_root();
    let path = current_config_path();
    let cfg = match read_config(&path) {
        ConfigRead::Ok(v) => v,
        ConfigRead::Missing => serde_json::json!({}),
        ConfigRead::Broken(why) => return Err(format!("{} не разобрался: {why}", path.display())),
    };
    let home = path.parent().map(Path::to_path_buf).unwrap_or_else(install_root);
    let tree = resolve(&home, cfg.get("tree").and_then(|v| v.as_str()).unwrap_or("data"));
    Ok(serde_json::json!({
        "config": cfg,
        "path": path.display().to_string(),
        "tree": tree.display().to_string(),
        "exe_dir": base.display().to_string(),
        // Кого правим: экран настроек показывает это владельцу, чтобы правка
        // мозга у одного агента не выглядела правкой у всех.
        "agent_id": current_id(),
        "agent_name": with_current(String::new(), |c| c.name.clone()),
        // Отпечаток свежести: окно возвращает его в `config_save`.
        "mtime_ns": file_mtime_ns(&path),
    }))
}

/// Состояние службы по SCM: running / stopped / absent. Прав не требует.
/// async: синхронная команда Tauri исполняется на главном потоке, и висящий
/// sc.exe вешал бы окно целиком (а опрашивают его каждые 2,5 с).
#[cfg(windows)]
#[tauri::command]
async fn service_state() -> String {
    tauri::async_runtime::spawn_blocking(service_state_blocking)
        .await
        .unwrap_or_else(|_| "absent".to_string())
}

/// macOS: состояние демона launchd — `running` | `stopped` | `absent` |
/// `unknown`. Прав не требует (`launchctl print` — чтение), но может и не
/// ответить, и тогда ответ честный «не знаю», а не выдуманное «нет».
#[cfg(target_os = "macos")]
#[tauri::command]
async fn service_state() -> String {
    tauri::async_runtime::spawn_blocking(mac_svc_state_said)
        .await
        .unwrap_or_else(|_| "unknown".to_string())
}

/// То же состояние демона — и строка в журнал о том, отвечает ли ДВИЖОК.
///
/// Машинное слово (`running`/`stopped`/`absent`/`unknown`) остаётся прежним: по
/// нему живут карточка, мастер и их стенды. Но сама по себе строка «running»
/// говорит только про launchd, а владельцу нужно знать про агента — поэтому
/// разница уезжает в журнал, и только когда она МЕНЯЕТСЯ: ручку опрашивают
/// каждые 2,5 с, и строка на каждый опрос была бы не журналом, а шумом.
#[cfg(target_os = "macos")]
fn mac_svc_state_said() -> String {
    static SAID: Mutex<String> = Mutex::new(String::new());
    let state = mac_svc_state();
    let note = mac_svc_engine_note(&state, mac_svc_engine_alive(mac_port_of(&install_root())));
    if !note.is_empty() {
        if let Ok(mut last) = SAID.lock() {
            if *last != note {
                *last = note.to_string();
                log_line(note);
            }
        }
    }
    state
}

/// Вне Windows и macOS службы нет как механизма — `missing`, а не `absent`:
/// второе значит «можно поставить», и окно рисовало бы под него кнопку.
#[cfg(not(any(windows, target_os = "macos")))]
#[tauri::command]
async fn service_state() -> String {
    "missing".to_string()
}

/// Ответ SCM о службе продукта: running | stopped | absent. С дедлайном —
/// повисший sc.exe не должен вешать окно.
#[cfg(windows)]
fn service_state_blocking() -> String {
    let mut cmd = Command::new(sys_exe("sc.exe"));
    cmd.args(["query", product_fs()]);
    match run_hidden_for(&mut cmd, Duration::from_secs(15)) {
        Ok(out) if out.status.success() => {
            // Состояние службы — ASCII (`RUNNING`, `START_PENDING`), sc.exe его не
            // переводит; но ВЕСЬ остальной ответ идёт в кодировке консоли, и
            // `from_utf8_lossy` превращал его в «□» — включая строку ошибки, по
            // которой только и можно понять, чем именно кончился запрос.
            let text = console_text(&out.stdout).to_uppercase();
            if text.contains("RUNNING") || text.contains("START_PENDING") {
                "running".to_string()
            } else {
                "stopped".to_string()
            }
        }
        // ⚠ Здесь сходятся ДВА разных случая: службы нет — и sc.exe не ответил за
        // пятнадцать секунд. Для окна оба значат «нечего показывать», но молчать
        // о втором нельзя: именно такое молчание и держало дефект кодировки
        // незамеченным месяц.
        Err(why) => {
            log_line(&format!("состояние службы не спрошено: {why}"));
            "absent".to_string()
        }
        Ok(out) => {
            let said = console_text(&out.stderr);
            if !said.is_empty() {
                log_line(&format!("sc query ответил отказом: {said}"));
            }
            "absent".to_string()
        }
    }
}

#[cfg(windows)]
#[tauri::command]
async fn remove_service() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| service_op_from_window("uninstall"))
        .await
        .unwrap_or_else(|_| Err("вызов службы прерван".into()))
}

#[cfg(target_os = "macos")]
#[tauri::command]
async fn remove_service() -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(|| mac_service_op("remove"))
        .await
        .unwrap_or_else(|_| Err("вызов службы прерван".into()))
}

#[cfg(not(any(windows, target_os = "macos")))]
#[tauri::command]
async fn remove_service() -> Result<String, String> {
    Err("службы на этой платформе нет".into())
}

/// Что вообще можно отдать Проводнику. Проводник не «показывает», а ЗАПУСКАЕТ
/// то, что ему дали (.exe, .lnk, .bat), а строка сюда приходит из веб-части и
/// из ответа сервера обновлений (кнопка «Скачать» подставляет его url).
/// Поэтому — белый список: http(s)-ссылка либо путь внутри папки программы,
/// дерева данных или %TEMP%.
fn open_target(path: &str) -> Result<std::ffi::OsString, String> {
    let raw = path.trim();
    if raw.is_empty() {
        return Err("пустой путь".into());
    }
    let lower = raw.to_lowercase();
    if lower.starts_with("http://") || lower.starts_with("https://") {
        if raw.contains(['"', '\n', '\r', '\0']) {
            return Err("в ссылке недопустимые знаки".into());
        }
        return Ok(raw.into());
    }
    let target = PathBuf::from(raw)
        .canonicalize()
        .map_err(|_| format!("нет такого пути: {raw}"))?;
    for root in [install_root(), tree_dir(), std::env::temp_dir()] {
        if let Ok(root) = root.canonicalize() {
            if target.starts_with(&root) {
                return Ok(plain_path(&target).into_os_string());
            }
        }
    }
    Err("этот путь вне папок программы — не открываю".into())
}

/// Убрать префикс `\\?\`, который добавляет canonicalize: Проводник такой
/// путь не понимает и вместо папки открыл бы «Документы».
fn plain_path(path: &Path) -> PathBuf {
    let text = path.to_string_lossy().into_owned();
    if let Some(rest) = text.strip_prefix("\\\\?\\UNC\\") {
        return PathBuf::from(format!("\\\\{rest}"));
    }
    match text.strip_prefix("\\\\?\\") {
        Some(rest) => PathBuf::from(rest),
        None => path.to_path_buf(),
    }
}

/// Открыть папку в Проводнике (или ссылку в браузере).
#[cfg(windows)]
#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    let target = open_target(&path)?;
    Command::new(explorer_exe())
        .arg(target)
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// macOS: `open` — папку в Finder, ссылку в браузере. Прочие POSIX: `xdg-open`.
#[cfg(not(windows))]
#[tauri::command]
fn open_path(path: String) -> Result<(), String> {
    let target = open_target(&path)?;
    let tool = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
    Command::new(posix_tool(tool))
        .arg(target)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Показать файл в Проводнике с выделением — для собранных логов.
#[cfg(windows)]
#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let target = open_target(&path)?;
    let mut cmd = Command::new(explorer_exe());
    // Проводнику нужна форма /select,"<путь>" — кавычки ВОКРУГ ПУТИ.
    // Обычный .arg закавычивал бы весь аргумент целиком, и путь с пробелом
    // (профиль «Иван Петров») открывал папку по умолчанию вместо выделения.
    cmd.raw_arg(format!("/select,\"{}\"", target.to_string_lossy()));
    cmd.spawn().map(|_| ()).map_err(|e| e.to_string())
}

/// macOS: `open -R` — Finder с выделенным файлом. Прочие POSIX: открыть папку.
#[cfg(not(windows))]
#[tauri::command]
fn reveal_path(path: String) -> Result<(), String> {
    let target = open_target(&path)?;
    let mut cmd = if cfg!(target_os = "macos") {
        let mut c = Command::new(posix_tool("open"));
        c.arg("-R").arg(&target);
        c
    } else {
        let mut c = Command::new(posix_tool("xdg-open"));
        c.arg(Path::new(&target).parent().map(Path::to_path_buf).unwrap_or_else(|| PathBuf::from(&target)));
        c
    };
    cmd.stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map(|_| ())
        .map_err(|e| e.to_string())
}

/// Раздел «Конфиденциальность и безопасность» Системных настроек macOS — по
/// кнопке «Открыть настройки» карточки «Управление компьютером»: `screen` —
/// «Запись экрана», `accessibility` — «Универсальный доступ». Открывается
/// системное окно, галочку ставит владелец сам: выдать разрешение TCC
/// программно нельзя, и это правильно. Окно зовёт команду только на macOS
/// (карточка рисует кнопки по `tcc` в снимке тела).
#[tauri::command]
fn open_privacy_pane(kind: String) -> Result<(), String> {
    let Some(url) = privacy_pane_url(&kind) else {
        return Err(format!("не знаю такого раздела разрешений: «{kind}»"));
    };
    #[cfg(target_os = "macos")]
    {
        Command::new(posix_tool("open"))
            .arg(url)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
            .map(|_| ())
            .map_err(|e| e.to_string())
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = url;
        Err("открывать нечего: разрешения экрана и ввода на этой системе не выдаются отдельным разделом настроек".into())
    }
}

/// Адрес раздела — чистая функция под стендом: опечатка в нём открыла бы
/// Системные настройки на общей странице, и владелец искал бы галочку сам.
fn privacy_pane_url(kind: &str) -> Option<&'static str> {
    match kind.trim() {
        "screen" => Some("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture"),
        "accessibility" => {
            Some("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        }
        _ => None,
    }
}

/// Известная папка пользователя из реестра, а не склейка из %APPDATA%.
/// Групповая политика «Перенаправление папок» уводит меню «Пуск» и
/// автозагрузку на сетевой диск: склейка молча промахивалась, ярлык ложился
/// не туда, и уведомления не приходили никогда — при бодрой строке в журнале
/// «ярлык создан».
#[cfg(windows)]
fn shell_folder(name: &str, fallback: &str) -> Option<PathBuf> {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let read = |key: &str| -> Option<String> {
        hkcu.open_subkey(key)
            .ok()
            .and_then(|k| k.get_value::<String, _>(name).ok())
            .map(|s| expand_env(&s))
            .filter(|s| !s.trim().is_empty())
    };
    let found = read("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\User Shell Folders")
        .or_else(|| read("Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Shell Folders"));
    match found {
        Some(p) => Some(PathBuf::from(p)),
        None => std::env::var_os("APPDATA").map(|a| PathBuf::from(a).join(fallback)),
    }
}

/// %VAR% в значении реестра (REG_EXPAND_SZ приходит как есть).
#[cfg(windows)]
fn expand_env(raw: &str) -> String {
    let mut out = String::new();
    let mut rest = raw;
    while let Some(start) = rest.find('%') {
        out.push_str(&rest[..start]);
        let tail = &rest[start + 1..];
        match tail.find('%') {
            Some(end) => {
                let name = &tail[..end];
                match std::env::var(name) {
                    Ok(v) => out.push_str(&v),
                    Err(_) => {
                        out.push('%');
                        out.push_str(name);
                        out.push('%');
                    }
                }
                rest = &tail[end + 1..];
            }
            None => {
                out.push('%');
                out.push_str(tail);
                return out;
            }
        }
    }
    out.push_str(rest);
    out
}

#[cfg(windows)]
fn programs_dir() -> Option<PathBuf> {
    shell_folder("Programs", "Microsoft\\Windows\\Start Menu\\Programs")
}

#[cfg(windows)]
fn startup_lnk() -> Option<PathBuf> {
    shell_folder("Startup", "Microsoft\\Windows\\Start Menu\\Programs\\Startup")
        .map(|d| d.join(format!("{}.lnk", product_fs())))
}

/// Автозапуск — ярлык в папке автозагрузки пользователя, без реестра и прав.
#[cfg(windows)]
#[tauri::command]
fn autostart_get() -> bool {
    startup_lnk().map(|p| p.exists()).unwrap_or(false)
}

/// macOS: автозапуск — LaunchAgent пользователя, `~/Library/LaunchAgents/
/// <идентификатор>.plist`; есть файл — есть автозапуск, ровно как ярлык
/// в автозагрузке Windows. Идентификатор — сборки (`app.helene.desk`), а не
/// продукт латиницей: у варианта Праксис свой, и агенты не спорят за имя.
#[cfg(not(windows))]
#[tauri::command]
fn autostart_get() -> bool {
    launch_agent_plist().map(|p| p.is_file()).unwrap_or(false)
}

#[tauri::command]
async fn autostart_set(on: bool) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || autostart_set_blocking(on))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(not(windows))]
fn launch_agent_plist() -> Option<PathBuf> {
    let home = std::env::var_os("HOME")?;
    Some(
        PathBuf::from(home)
            .join("Library")
            .join("LaunchAgents")
            .join(format!("{}.plist", toast_id())),
    )
}

/// Текст LaunchAgent: метка, путь бинаря внутри бандла, подъём при входе.
/// Чистая функция — путь с `&` или `<` в имени папки обязан остаться XML.
/// Без cfg по той же причине, что `applescript_quote`: стенд на неё общий.
#[cfg_attr(windows, allow(dead_code))]
fn launch_agent_text(label: &str, program: &str) -> String {
    fn xml(s: &str) -> String {
        s.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;")
    }
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n\
         <dict>\n\
         \t<key>Label</key>\n\
         \t<string>{}</string>\n\
         \t<key>ProgramArguments</key>\n\
         \t<array>\n\
         \t\t<string>{}</string>\n\
         \t</array>\n\
         \t<key>RunAtLoad</key>\n\
         \t<true/>\n\
         </dict>\n\
         </plist>\n",
        xml(label),
        xml(program)
    )
}

/// macOS: записать plist — или удалить его. Только файл, без `launchctl`, ровно
/// как ярлык в автозагрузке Windows: launchd читает `~/Library/LaunchAgents`
/// сам при входе в систему.
///
/// ⚠ Почему ни `bootstrap`, ни `bootout`. `bootstrap` с `RunAtLoad` тут же
/// поднял бы второй экземпляр (single-instance погасил бы его, значок мигнул бы
/// на ровном месте). А `bootout gui/<uid>/<метка>` ЗАВЕРШАЕТ САМУ работающую
/// программу, если её поднял launchd по этому же plist: вход в систему →
/// автозапуск → тумблер в любую сторону → Hélène исчезает с экрана. Поймано
/// адверсаркой до живой пробы. Старый путь бинаря в уже загруженном задании
/// доживает до выхода из системы — при следующем входе launchd прочитает файл.
#[cfg(not(windows))]
fn autostart_set_blocking(on: bool) -> Result<(), String> {
    let plist = launch_agent_plist().ok_or("не нашёл домашнюю папку (HOME)")?;
    if !on {
        return match std::fs::remove_file(&plist) {
            Ok(()) => Ok(()),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(err) => Err(format!("файл автозапуска не удалился: {err}")),
        };
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    if let Some(dir) = plist.parent() {
        std::fs::create_dir_all(dir).map_err(|e| format!("нет папки LaunchAgents: {e}"))?;
    }
    std::fs::write(&plist, launch_agent_text(toast_id(), &exe.to_string_lossy()))
        .map_err(|e| format!("файл автозапуска не записался: {e}"))?;
    Ok(())
}

#[cfg(windows)]
fn autostart_set_blocking(on: bool) -> Result<(), String> {
    let lnk = startup_lnk().ok_or("не нашёл папку автозагрузки")?;
    if !on {
        // Ошибку удаления больше не глотаем: тумблер рапортовал «Автозапуск
        // выключен», а программа поднималась снова после перезагрузки.
        return match std::fs::remove_file(&lnk) {
            Ok(()) => Ok(()),
            Err(err) if err.kind() == std::io::ErrorKind::NotFound => Ok(()),
            Err(err) => Err(format!("ярлык автозапуска не удалился: {err}")),
        };
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    if let Some(dir) = lnk.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let script = format!(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{}'); $s.TargetPath='{}'; $s.WorkingDirectory='{}'; $s.Save()",
        lnk.display(),
        exe.display(),
        exe.parent().map(|p| p.display().to_string()).unwrap_or_default()
    );
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &script]);
    let out = run_hidden_for(&mut cmd, Duration::from_secs(30))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr).trim().to_string());
    }
    if !lnk.exists() {
        return Err("ярлык автозапуска не появился".into());
    }
    Ok(())
}

/// Адрес этой машины в локальной сети — для QR телефону. Сокет не отправляет
/// ничего: connect на внешний адрес лишь выбирает интерфейс.
#[tauri::command]
fn lan_ip() -> Option<String> {
    let sock = std::net::UdpSocket::bind("0.0.0.0:0").ok()?;
    sock.connect("8.8.8.8:80").ok()?;
    sock.local_addr().ok().map(|a| a.ip().to_string())
}

/// Адрес этой машины в сети Tailscale (100.64.0.0/10), если он установлен и
/// включён: телефон с Tailscale в том же аккаунте достучится из любой сети,
/// не только из этой Wi-Fi. Спрашиваем у их же CLI, ничего не угадываем.
#[tauri::command]
async fn tailscale_ip() -> Option<String> {
    tauri::async_runtime::spawn_blocking(|| {
        // Голого "tailscale.exe" в списке больше нет: по голому имени Windows
        // взяла бы файл из папки программы, а туда пишет и сам агент.
        let mut candidates: Vec<PathBuf> = Vec::new();
        #[cfg(windows)]
        for var in ["ProgramFiles", "ProgramFiles(x86)"] {
            if let Ok(pf) = std::env::var(var) {
                candidates.push(PathBuf::from(pf).join("Tailscale").join("tailscale.exe"));
            }
        }
        // macOS: приложение из App Store / с сайта, потом Homebrew (Apple
        // Silicon и Intel).
        #[cfg(target_os = "macos")]
        for known in [
            "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
            "/opt/homebrew/bin/tailscale",
            "/usr/local/bin/tailscale",
        ] {
            candidates.push(PathBuf::from(known));
        }
        if let Some(found) = find_in_path("tailscale") {
            candidates.push(found);
        }
        for exe in candidates {
            if !exe.is_file() {
                continue;
            }
            let mut cmd = Command::new(&exe);
            cmd.args(["ip", "-4"]);
            let Ok(out) = run_hidden_for(&mut cmd, Duration::from_secs(10)) else { continue };
            if !out.status.success() {
                continue;
            }
            let text = String::from_utf8_lossy(&out.stdout);
            if let Some(ip) = text.lines().map(str::trim).find(|l| l.starts_with("100.")) {
                return Some(ip.to_string());
            }
        }
        None
    })
    .await
    .unwrap_or(None)
}

// Само правило (имя, сужение, слова расписки) — общее со службой, см.
// common/firewall_rule.rs: имена у нас совпадают до буквы, и кто ставит
// вторым, тот переписывает правило первого.
// Брандмауэр, PowerShell и служба — только Windows: на macOS этих файлов в
// сборке нет вовсе, чтобы ни одна их функция не могла быть позвана случайно.
#[cfg(windows)]
include!("../../common/firewall_rule.rs");
// Общее с установщиком и службой (ревью 06.09, §4): проба модели и гард
// исходящего адреса, экранирование PowerShell, поднятая операция со службой,
// штамп времени журналов, случайные байты из CSPRNG.
include!("../../common/model_probe.rs");
#[cfg(windows)]
include!("../../common/ps.rs");
// Список агентов установки — общий со службой: кого поднимать, что показывать
// в трее и на каком порту чей канал (`common/agents.rs`, правило — там же).
include!("../../common/agents.rs");
#[cfg(windows)]
include!("../../common/service_op.rs");
// Демон launchd (`app.helene.svc`) — то же, что service_op.rs, но для macOS:
// plist, строки launchctl, разбор состояния. Включается на ВСЕХ платформах, как
// model_probe: стенды сборки plist и экранирования идут и на Windows, а живые
// части (`mac_svc_state`, `mac_svc_run_admin`) гейтятся внутри файла.
include!("../../common/mac_service.rs");
include!("../../common/stamp.rs");
include!("../../common/random_hex.rs");
include!("../../common/run_hidden.rs");

// Клиентская сторона брокера прав — тоже ОДИН текст на обе стороны трубы, см.
// common/broker.rs. Отсюда нужны имя трубы (`broker_pipe_name`), секрет
// (`broker_token_read`), сборка просьбы (`BrokerAsk`), разбор квитанции
// (`broker_call`) и пути файлов обмена с харнессом (`broker_asks_path`,
// `broker_answers_path`) — там же лежит и контракт руки агента. Внутри файла
// только std и serde_json: набор фич windows-sys у оболочки другой, и первая же
// строка Win32 в общем файле сломала бы ей сборку.
include!("../../common/broker.rs");

#[cfg(windows)]
fn firewall_rule_name(port: u16) -> String {
    format!("name={}", firewall_rule_title(product_fs(), port))
}

// Текст, который сказала родная утилита Windows. Один код на оболочку, службу
// и установщик: у всех трёх была своя копия ВШИТОЙ таблицы cp866, и все три
// врали на нерусской Windows.
#[cfg(windows)]
include!("../../common/console_text.rs");

// ─────────────────────────────────────────────── что именно просим у netsh
//
// Правило ставится ПАЧКОЙ: сначала снос старого, потом добавление своего.
// Кодом пачки считается код ПОСЛЕДНЕЙ команды — в обеих наших пачках последняя
// и есть дело, а предыдущая подготавливает. Снос несуществующего правила netsh
// считает ошибкой, и это норма, а не отказ.
//
// ⚠ ПОЧЕМУ ПАЧКОЙ, А НЕ ДВУМЯ ПОДЪЁМАМИ. Через дверь элевации обе команды
// обязаны пройти под ОДНИМ подтверждением. Два окна UAC подряд — это не только
// два вопроса человеку: на втором можно нажать «Нет», и правило останется
// снятым, то есть телефон перестанет работать вовсе. А раньше снос шёл вообще
// без прав администратора: у обычного пользователя он молча не срабатывал, и
// каждое нажатие «Показать QR» добавляло ЕЩЁ ОДНО правило с тем же именем
// (netsh уникальности имени при добавлении не требует).

#[cfg(windows)]
fn firewall_delete_args(port: u16) -> Vec<String> {
    vec![
        "advfirewall".into(),
        "firewall".into(),
        "delete".into(),
        "rule".into(),
        firewall_rule_name(port),
    ]
}

/// Поставить правило заново. Добавление — последним: его код и есть итог.
#[cfg(windows)]
fn firewall_set_runs(port: u16, program: Option<&str>) -> Vec<Vec<String>> {
    vec![
        firewall_delete_args(port),
        // Сужение (profile=, remoteip=) — в common/firewall_rule.rs, общем со
        // службой: раньше эти две строки жили только здесь, и служба при каждой
        // загрузке машины меняла правило на открытое.
        firewall_add_args(&firewall_rule_title(product_fs(), port), port, program),
    ]
}

/// Снять правило. Одна команда, она же последняя.
#[cfg(windows)]
fn firewall_clear_runs(port: u16) -> Vec<Vec<String>> {
    vec![firewall_delete_args(port)]
}

/// «Зачем» для журнала владельца. Идёт в `broker.log` и читается человеком,
/// поэтому одной строкой и словами: пустое или многострочное объяснение брокер
/// отвергает на границе (common/broker.rs).
#[cfg(windows)]
fn firewall_why(port: u16, adding: bool) -> String {
    if adding {
        format!("правило брандмауэра для телефона, порт {port}")
    } else {
        format!("снять правило брандмауэра, порт {port}")
    }
}

// ───────────────────────────────────────────────────── какой дверью повышаться

/// Каким путём выполняется netsh.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
#[cfg(windows)]
enum FirewallPath {
    /// Своими правами: процесс уже повышен, просить некого.
    Direct,
    /// Окном Windows (UAC) — штатный механизм интерактивного режима.
    Uac,
    /// Брокером службы — штатный механизм режима службы. `RunAs` здесь не
    /// используется вовсе.
    Broker,
}

/// Режим агента из helene.json: `sandbox` | `interactive` | `service`.
/// Пустая строка — режим не записан (старый конфиг).
///
/// ⚠ Ключ — `agent_mode`, а НЕ `mode`. `mode` в helene.json занят под
/// местожительство харнесса ("local" | "remote"), и режим, записанный туда,
/// выключает продукт: окно не поднимает ни трубу, ни руннер (см. `match
/// cfg.get("mode")` в `main`), а служба отказывается стартовать. Правила чтения
/// здесь те же, что в localharness/modes.py::stated, до буквы: строка, объект с
/// `name` и — как поломка, которую всё равно надо понять, — режим, записанный
/// в `mode`.
#[cfg(windows)]
fn agent_mode(cfg: Option<&serde_json::Value>) -> String {
    fn known(raw: Option<&serde_json::Value>) -> Option<String> {
        let name = raw?.as_str()?.trim().to_lowercase();
        matches!(name.as_str(), "sandbox" | "interactive" | "service").then_some(name)
    }
    let Some(cfg) = cfg else { return String::new() };
    let raw = cfg.get("agent_mode");
    if let Some(obj) = raw.and_then(|v| v.as_object()) {
        if let Some(name) = known(obj.get("name")).or_else(|| known(obj.get("mode"))) {
            return name;
        }
    } else if let Some(name) = known(raw) {
        return name;
    }
    known(cfg.get("mode")).unwrap_or_default()
}

/// Дверь для повышения. Развилка вся здесь и целиком чистая: ошибка в ней —
/// это не «не сработало», а «пошло не тем путём», а два пути к одному действию
/// в этом продукте уже расходились однажды.
///
/// ⚠⚠ ЧТО ЗДЕСЬ ИСПРАВЛЕНО И ПОЧЕМУ ЭТО НЕ КОСМЕТИКА. Раньше дверь выбиралась
/// ОДНИМ вопросом — `mode == "service"`. Но служба — не режим и не третий пункт
/// списка, а ОПЦИЯ ПОВЕРХ любого режима (см. localharness/modes.py): режим
/// бывает только `sandbox` или `interactive`. То есть после развода этих двух
/// измерений условие `mode == "service"` не выполняется НИКОГДА, и дверь
/// брокера умерла бы молча: у владельца со службой окно продолжало бы дёргать
/// UAC на каждый чих, ради чего служба и ставилась.
///
/// Поэтому спрашиваем не конфиг, а сам брокер: `broker` — «труба ответила».
/// Живой брокер бывает ровно тогда, когда служба стоит, запущена и брокер в ней
/// не выключен в Настройках, — то есть ровно тогда, когда идти туда можно.
/// `mode` остаётся вторым, ХВОСТОВЫМ вопросом: старый конфиг со словом
/// `service` мы всё ещё понимаем, и отказ брокера в этом случае объясняется
/// владельцу словами про службу, а не молчанием.
#[cfg(windows)]
fn firewall_path(elevated: bool, mode: &str, broker: bool) -> FirewallPath {
    if elevated {
        // Повышаться некуда: netsh отработает прямо здесь. Так живёт машина
        // владельца — администратор с выключенным UAC.
        return FirewallPath::Direct;
    }
    if broker || mode == "service" {
        FirewallPath::Broker
    } else {
        // Сюда же попадает конфиг без режима (старая установка): окно просит
        // права у Windows. Правило от этого не разъедется — его собирает общий
        // common/firewall_rule.rs, — а починка режима в файле дело настроек.
        FirewallPath::Uac
    }
}

/// Слово о том, ЧЬИМИ правами сделано дело. Одно на все двери: расписка
/// владельцу обязана совпадать до буквы. Разойдись эти строки — и два пути к
/// одному действию начали бы обещать разное; в этом продукте так уже было,
/// когда правило брандмауэра ставили и служба, и окно, по-разному.
#[cfg(windows)]
fn firewall_door_words(path: FirewallPath) -> &'static str {
    match path {
        FirewallPath::Direct => "",
        FirewallPath::Uac => " с правами администратора",
        FirewallPath::Broker => " через брокера службы",
    }
}

#[cfg(windows)]
fn firewall_added(port: u16, path: FirewallPath) -> String {
    format!(
        "правило брандмауэра для порта {port} добавлено{} ({FIREWALL_SCOPE_HUMAN})",
        firewall_door_words(path)
    )
}

#[cfg(windows)]
fn firewall_removed(port: u16, path: FirewallPath) -> String {
    format!("правило брандмауэра для порта {port} снято{}", firewall_door_words(path))
}

/// Повышен ли НАШ процесс.
///
/// Не «администратор ли учётка»: у администратора с включённым UAC обычное окно
/// живёт с урезанным токеном, и netsh из него отвечает «требуется повышение».
/// Спрашиваем ровно то, что решает исход: можем ли мы выполнить netsh сами.
#[cfg(windows)]
fn process_is_elevated() -> bool {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::Security::{
        GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return false;
        }
        let mut info = TOKEN_ELEVATION { TokenIsElevated: 0 };
        let mut len: u32 = 0;
        let ok = GetTokenInformation(
            token,
            TokenElevation,
            &mut info as *mut _ as *mut core::ffi::c_void,
            std::mem::size_of::<TOKEN_ELEVATION>() as u32,
            &mut len,
        );
        CloseHandle(token);
        ok != 0 && info.TokenIsElevated != 0
    }
}

#[cfg(not(windows))]
fn process_is_elevated() -> bool {
    false
}

/// Тип токена: 1 — обычный (UAC не при чём), 2 — полный, 3 — урезанный
/// (учётка администраторская, но права сняты фильтром UAC). 0 — не спросили.
#[cfg(windows)]
fn token_elevation_type() -> i32 {
    use windows_sys::Win32::Foundation::{CloseHandle, HANDLE};
    use windows_sys::Win32::Security::{
        GetTokenInformation, TokenElevationType, TOKEN_ELEVATION_TYPE, TOKEN_QUERY,
    };
    use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return 0;
        }
        let mut kind: TOKEN_ELEVATION_TYPE = 0;
        let mut len: u32 = 0;
        let ok = GetTokenInformation(
            token,
            TokenElevationType,
            &mut kind as *mut _ as *mut core::ffi::c_void,
            std::mem::size_of::<TOKEN_ELEVATION_TYPE>() as u32,
            &mut len,
        );
        CloseHandle(token);
        if ok == 0 {
            0
        } else {
            kind
        }
    }
}

#[cfg(not(windows))]
fn token_elevation_type() -> i32 {
    0
}

/// Что эта учётная запись может по части прав администратора.
///
/// `admin` — окно уже повышено. `can_elevate` — повышение пройдёт БЕЗ чужого
/// пароля: либо мы уже повышены, либо UAC урезал администраторский токен и
/// вернёт права по подтверждению. У обычного пользователя здесь `false`, и это
/// не совсем «нельзя»: он может позвать администратора, который введёт свой
/// пароль в окне UAC. Врать про это нельзя — экран режимов рядом с выбором
/// «Служба» пишет про окно Windows отдельной строкой.
///
/// Ручку просит app/src/mode.ts::adminProbe: без неё экран режимов отвечает
/// честным «не знаю».
#[tauri::command]
fn admin_state() -> serde_json::Value {
    admin_verdict(process_is_elevated(), token_elevation_type())
}

/// Тот же ответ, но ЧИСТОЙ функцией: два вызова Win32 выше живьём не подделать,
/// а вывод из них — можно и нужно проверить. Ошибка здесь звучит для владельца
/// как «вариант со службой тебе недоступен» там, где он доступен, — и наоборот,
/// что хуже: обещание, которое на его машине не сбудется.
fn admin_verdict(admin: bool, kind: i32) -> serde_json::Value {
    serde_json::json!({
        "admin": admin,
        // Урезанный токен (3) — это администратор, у которого UAC снял права до
        // подтверждения. Он повысится САМ, чужого пароля не нужно. Обычный
        // токен (1) — обычный пользователь: ему нужен администратор рядом, и
        // называть это «можешь» было бы враньём.
        "can_elevate": admin || kind == 3,
        // Сырой тип токена — чтобы «не знаю» отличалось от «точно нет».
        "elevation": match kind {
            1 => "default",
            2 => "full",
            3 => "limited",
            _ => "unknown",
        },
    })
}

// ─────────────────────────────────────────────────────────────── исполнение

/// Чем кончилась пачка: код ПОСЛЕДНЕЙ команды и то, что netsh о ней сказал.
/// Пустой `said` — текста нет: под UAC netsh отвечает в своё скрытое окно, и до
/// нас доезжает только код.
#[cfg(windows)]
struct NetshDone {
    code: i32,
    said: String,
}

/// Пачка netsh СВОИМИ правами. Зовётся только когда процесс уже повышен: без
/// прав netsh отвечает «требуется повышение», и гонять его впустую значит
/// показать владельцу пустой отказ вместо дела.
#[cfg(windows)]
fn netsh_direct(runs: &[Vec<String>]) -> Result<NetshDone, String> {
    let netsh = sys_exe("netsh.exe");
    let mut done = NetshDone { code: -1, said: String::new() };
    for args in runs {
        let mut cmd = Command::new(&netsh);
        cmd.args(args);
        let out = run_hidden_for(&mut cmd, Duration::from_secs(30))?;
        let mut said = console_text(&out.stdout);
        if said.is_empty() {
            said = console_text(&out.stderr);
        }
        done = NetshDone { code: out.status.code().unwrap_or(-1), said };
    }
    Ok(done)
}

/// Метка в выводе повышающего скрипта. Только ASCII: вывод powershell приезжает
/// в кодировке консоли, и по-русски метка читалась бы через раз.
#[cfg(windows)]
const RUNAS_MARK: &str = "HELENE-RUNAS";

/// Что сказал скрипт повышения.
#[derive(PartialEq, Eq, Debug)]
#[cfg(windows)]
enum RunAs {
    /// Повышение состоялось, вот код пачки.
    Code(i32),
    /// Повышения не было, вот код Windows (1223 — «Нет» в окне UAC).
    Refused(u32),
    /// Повышение прошло, но Windows не вернула код. Врать «получилось» здесь
    /// нельзя, и врать «не получилось» — тоже.
    Unknown,
}

#[cfg(windows)]
fn parse_runas(text: &str) -> Option<RunAs> {
    let line = text.lines().rev().map(str::trim).find(|l| l.starts_with(RUNAS_MARK))?;
    let mut parts = line.split_whitespace().skip(1);
    match (parts.next(), parts.next()) {
        (Some("ok"), Some(n)) => n.parse().ok().map(RunAs::Code),
        (Some("fail"), Some(n)) => n.parse().ok().map(RunAs::Refused),
        (Some("unknown"), _) => Some(RunAs::Unknown),
        _ => None,
    }
}

/// Отказ Windows — человеческими словами. Числа владельцу ничего не говорят, а
/// «не удалось» без причины не говорит вообще ничего.
#[cfg(windows)]
fn runas_refusal(code: u32) -> String {
    match code {
        // ERROR_CANCELLED. Сюда же Windows кладёт политику, которая отклоняет
        // запросы повышения молча, — поэтому названы обе причины.
        // ⚠ Кнопку в окне UAC подписывает Windows на языке СВОЕГО языкового пакета, а не
        // мы: называть её здесь значит обещать человеку слово, которого он на экране не
        // видел. На английской системе под нашей фразой стоит Yes/No уже сегодня.
        1223 => "нужны права администратора: повышение не подтверждено либо \
                 запрещено политикой этого компьютера"
            .into(),
        // ERROR_ACCESS_DISABLED_BY_POLICY.
        1260 => "повышение прав запрещено политикой этого компьютера".into(),
        // ERROR_ACCESS_DENIED.
        5 => "Windows отказала в повышении прав (доступ запрещён)".into(),
        // ERROR_FILE_NOT_FOUND / ERROR_PATH_NOT_FOUND.
        2 | 3 => "не нашёл powershell.exe, которым запрашиваются права администратора".into(),
        0 => "Windows отказала в повышении прав и не назвала причины".into(),
        other => format!("Windows не подняла права (код {other})"),
    }
}

/// Скрипт для ПОВЫШЕННОГО powershell: вся пачка netsh одним подъёмом.
///
/// `$LASTEXITCODE` выставлен заранее не случайно: если netsh не запустится
/// вовсе, переменная осталась бы пустой, и `exit [int]$null` отрапортовал бы
/// НУЛЁМ, то есть успехом. 9009 — то, чем Windows отвечает на «команду не
/// нашли».
#[cfg(windows)]
fn netsh_batch_script(netsh: &str, runs: &[Vec<String>]) -> String {
    let mut script = String::from("$ErrorActionPreference='Continue'; $LASTEXITCODE = 9009; ");
    for args in runs {
        script.push_str("& ");
        script.push_str(&ps_quote(netsh));
        for a in args {
            script.push(' ');
            script.push_str(&ps_quote(a));
        }
        script.push_str(" | Out-Null; ");
    }
    script.push_str("exit [int]$LASTEXITCODE");
    script
}

/// base64 от UTF-16LE — так powershell ждёт `-EncodedCommand`.
///
/// ⚠ ЗАЧЕМ ЭТО ВООБЩЕ. `Start-Process -ArgumentList @(…)` склеивает элементы
/// через пробел и НЕ берёт их в кавычки. Аргумент с пробелом — а у нас это и
/// путь к питону, и имя правила «Helene (8094)» — приехал бы в дочерний
/// powershell разрезанным на куски. Base64 — один токен без пробелов и кавычек,
/// склейке его не испортить; заодно снимается вопрос о вложенных кавычках.
#[cfg(windows)]
fn utf16le_base64(text: &str) -> String {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut bytes: Vec<u8> = Vec::with_capacity(text.len() * 2);
    for unit in text.encode_utf16() {
        bytes.extend_from_slice(&unit.to_le_bytes());
    }
    let mut out = String::with_capacity(bytes.len().div_ceil(3) * 4);
    for chunk in bytes.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = *chunk.get(1).unwrap_or(&0) as u32;
        let b2 = *chunk.get(2).unwrap_or(&0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(ALPHABET[(n >> 18) as usize & 63] as char);
        out.push(ALPHABET[(n >> 12) as usize & 63] as char);
        out.push(if chunk.len() > 1 { ALPHABET[(n >> 6) as usize & 63] as char } else { '=' });
        out.push(if chunk.len() > 2 { ALPHABET[n as usize & 63] as char } else { '=' });
    }
    out
}

/// Скрипт, который просит у Windows права: поднимает powershell с пачкой netsh
/// и печатает ОДНУ строку о том, чем это кончилось.
///
/// ⚠ ПОЧЕМУ ЗДЕСЬ .NET, А НЕ `Start-Process -Verb RunAs`. Причину отказа надо
/// назвать словами, а `Start-Process` её ТЕРЯЕТ: он заворачивает ошибку в
/// `InvalidOperationException` БЕЗ вложенного исключения, и код Windows
/// (1223 — «Нет» в окне UAC) из него уже не достать — остаётся только
/// локализованная фраза, по которой сравнивать нельзя. `Process.Start` с
/// `Verb='runas'` бросает настоящий `Win32Exception`, и код читается честно.
/// Проверено на этой машине: отказ «файл не найден» приезжает кодом 2 через
/// .NET и нулём через Start-Process.
///
/// Строка аргументов здесь одна и без пробелов внутри частей — это второе, за
/// что взят -EncodedCommand: `ProcessStartInfo.Arguments` пришлось бы иначе
/// кавычить руками.
#[cfg(windows)]
fn runas_script(target: &str, encoded: &str, workdir: &str) -> String {
    format!(
        "$ErrorActionPreference='Stop'; \
         try {{ \
         $i = New-Object System.Diagnostics.ProcessStartInfo; \
         $i.FileName = {target}; \
         $i.Arguments = '-NoProfile -NonInteractive -EncodedCommand {encoded}'; \
         $i.WorkingDirectory = {workdir}; \
         $i.Verb = 'runas'; \
         $i.UseShellExecute = $true; \
         $i.WindowStyle = 'Hidden'; \
         $p = [System.Diagnostics.Process]::Start($i); \
         if ($null -eq $p) {{ Write-Output '{RUNAS_MARK} unknown' }} \
         else {{ $p.WaitForExit(); Write-Output ('{RUNAS_MARK} ok ' + [int]$p.ExitCode) }} \
         }} catch {{ \
         $c = 0; $e = $_.Exception; \
         while ($null -ne $e) {{ \
         if ($e -is [System.ComponentModel.Win32Exception]) {{ $c = $e.NativeErrorCode; break }}; \
         $e = $e.InnerException }}; \
         Write-Output ('{RUNAS_MARK} fail ' + [int]$c) }}",
        target = ps_quote(target),
        workdir = ps_quote(workdir)
    )
}

/// Пачка netsh под правами администратора: окно Windows, глагол `runas`.
///
/// ⚠ Это НЕ костыль. Правило брандмауэра требует прав администратора, а окно их
/// не имеет и не просит; в первой редакции проекта механизм был записан на
/// снос, и это была ошибка — для интерактивного режима он штатный. Снимается он
/// только в режиме службы, где ту же работу делает брокер.
///
/// ⚠ Полные пути, а не голые имена. Прежний вариант поднимал `netsh.exe` по
/// имени, а голое имя Windows ищет и в папке процесса — то есть в папке
/// установки, куда пишет и сам агент. Подложенный туда файл исполнился бы
/// ПОВЫШЕННЫМ.
#[cfg(windows)]
fn netsh_via_uac(runs: &[Vec<String>]) -> Result<NetshDone, String> {
    let netsh = sys_exe("netsh.exe");
    if !netsh.is_absolute() {
        return Err("не нашёл netsh.exe в System32 — повышать нечего".into());
    }
    let shell = powershell_exe();
    if !shell.is_absolute() {
        return Err("не нашёл powershell.exe в System32 — им и запрашиваются права".into());
    }
    let script = netsh_batch_script(&netsh.display().to_string(), runs);
    // Рабочая папка повышенного процесса — System32, а НЕ папка установки:
    // туда пишет и сам агент, а текущая папка участвует в поиске программ.
    let outer = runas_script(
        &shell.display().to_string(),
        &utf16le_base64(&script),
        &system_root().join("System32").display().to_string(),
    );
    let mut cmd = Command::new(&shell);
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &outer]);
    // 120 с, а не 30: между запросом и ответом стоит человек с окном UAC.
    let out = run_hidden_for(&mut cmd, Duration::from_secs(120))?;
    match parse_runas(&console_text(&out.stdout)) {
        Some(RunAs::Code(code)) => Ok(NetshDone { code, said: String::new() }),
        Some(RunAs::Refused(code)) => Err(runas_refusal(code)),
        Some(RunAs::Unknown) => Err("Windows не сказала, чем кончилось повышение прав — \
                                     проверь правило в брандмауэре сам"
            .into()),
        // Метки нет вовсе: скрипт не доработал. Раньше на этом месте стоял
        // `catch { exit 5 }`, и любая причина — от отказа в окне UAC до
        // сломанного powershell — выглядела одинаково.
        None => {
            let said = console_text(&out.stderr);
            Err(if said.is_empty() {
                format!("запрос прав администратора не отработал (код {})", out.status.code().unwrap_or(-1))
            } else {
                format!("запрос прав администратора не отработал: {said}")
            })
        }
    }
}

/// Позвать брокера со сроком.
///
/// ⚠ У чтения ответа в `broker_call` таймаута нет: у файла на трубе его не
/// бывает без перекрытого ввода-вывода. Служба, убитая посреди работы, повесила
/// бы вызов навсегда — а «служба не отвечает» должно быть состоянием, а не
/// зависшим окном. Поэтому ждём в отдельном потоке и со сроком; поток, если он
/// всё-таки повис, умрёт сам, когда труба оборвётся.
#[cfg_attr(not(windows), allow(dead_code))]
fn broker_call_deadline(
    pipe: &str,
    ask: &BrokerAsk,
    limit: Duration,
) -> Result<BrokerReceipt, String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let (pipe, ask) = (pipe.to_string(), ask.clone());
    std::thread::spawn(move || {
        let _ = tx.send(broker_call(&pipe, &ask));
    });
    match rx.recv_timeout(limit) {
        Ok(answer) => answer,
        Err(_) => Err(format!(
            "служба не ответила за {} с — проверь её на экране «Система»",
            limit.as_secs()
        )),
    }
}

/// Та же пачка netsh — брокером службы. В режиме службы `RunAs` не используется
/// вовсе: права поднимает служба, а владелец видит просьбу в `broker.log`.
/// Правило брандмауэра через УЗКУЮ дверь брокера: порт и `why`, больше ничего.
///
/// Аргументы netsh здесь не передаются вовсе — их собирает служба тем же
/// `common/firewall_rule.rs`, которым пользуемся мы, поэтому правило не может
/// разъехаться между двумя дверями. Права нулевой сессии для этого не нужны:
/// у службы они и так есть, а `service.firewall` решает, можно ли ей их тратить
/// на брандмауэр.
#[cfg(windows)]
fn firewall_via_broker(port: u16, why: &str) -> Result<NetshDone, String> {
    let tree = tree_dir();
    let Some(token) = broker_token_read(&tree) else {
        return Err(format!(
            "секрета брокера нет ({}) — служба его ещё не заводила. Поставь службу в              Настройках или выбери путь без службы",
            broker_token_path(&tree).display()
        ));
    };
    let pipe = broker_pipe_name(&exe_dir());
    let mut ask = BrokerAsk::new(
        &token,
        BrokerOp::Firewall,
        "",
        &[port.to_string()],
        why,
    );
    ask.timeout_sec = 60;
    let receipt = broker_call_deadline(&pipe, &ask, Duration::from_secs(90))?;
    if !receipt.ok {
        // Слова отказа написаны службой и объясняют владельцу, где галочка.
        // Пересказ своими словами разъехался бы с оригиналом.
        let note = receipt.note.trim();
        return Err(if note.is_empty() {
            "служба отказала и не сказала почему".into()
        } else {
            note.to_string()
        });
    }
    Ok(NetshDone { code: 0, said: receipt.note.clone() })
}

#[cfg(windows)]
fn netsh_via_broker(runs: &[Vec<String>], why: &str) -> Result<NetshDone, String> {
    let netsh = sys_exe("netsh.exe");
    if !netsh.is_absolute() {
        // Брокер голое имя не примет намеренно: Windows искала бы его сначала в
        // папке процесса службы, то есть в папке установки.
        return Err("не нашёл netsh.exe в System32 — брокер берёт только полный путь".into());
    }
    let cmd = netsh.display().to_string();
    let tree = tree_dir();
    let Some(token) = broker_token_read(&tree) else {
        return Err(format!(
            "секрета брокера нет ({}) — служба его ещё не заводила. Поставь службу на экране \
             «Система» или выбери другой режим в Настройках",
            broker_token_path(&tree).display()
        ));
    };
    // Имя трубы считается ТОЛЬКО этой функцией: руками собранное имя разъедется
    // со службой на первом же регистре или хвостовом слэше.
    let pipe = broker_pipe_name(&exe_dir());
    let mut done = NetshDone { code: -1, said: String::new() };
    for args in runs {
        let mut ask = BrokerAsk::new(&token, BrokerOp::Exec, &cmd, args, why);
        ask.timeout_sec = 60;
        let receipt = broker_call_deadline(&pipe, &ask, Duration::from_secs(90))?;
        if !receipt.ok {
            // Отказ приходит уже человеческими словами — они написаны службой и
            // объясняют владельцу, где галочка. Своими словами не пересказываем:
            // пересказ разъедется с оригиналом.
            let note = receipt.note.trim();
            return Err(if note.is_empty() {
                "служба отказала и не сказала почему".into()
            } else {
                note.to_string()
            });
        }
        let Some(code) = receipt.code else {
            return Err(format!(
                "служба не дождалась netsh{}",
                if receipt.note.trim().is_empty() {
                    String::new()
                } else {
                    format!(": {}", receipt.note.trim())
                }
            ));
        };
        let mut said = receipt.err.trim().to_string();
        if said.is_empty() {
            said = receipt.out.trim().to_string();
        }
        done = NetshDone { code, said };
    }
    Ok(done)
}

/// Итог пачки словами. Число само по себе владельцу ничего не говорит, а 9009
/// — это вообще не ответ netsh: так наш скрипт сообщает, что netsh не запустился
/// вовсе (ноль на этом месте означал бы «получилось»).
#[cfg(windows)]
fn netsh_code_words(done: &NetshDone) -> String {
    let mut said = format!("netsh вернул код {}", done.code);
    if done.code == 9009 {
        said.push_str(" — Windows не нашла netsh.exe");
    }
    if !done.said.is_empty() {
        said.push_str(" — ");
        said.push_str(&done.said);
    }
    said
}

/// Выполнить пачку той дверью, которую выбрал режим, и ЗАПИСАТЬ всё, что вышло.
/// Молчаливого проглатывания здесь нет ни в одной ветке: журнал получает и
/// команду, и дверь, и итог.
#[cfg(windows)]
fn netsh_batch(
    runs: &[Vec<String>],
    why: &str,
    path: FirewallPath,
    port: u16,
) -> Result<NetshDone, String> {
    let netsh = sys_exe("netsh.exe").display().to_string();
    let door = match path {
        FirewallPath::Direct => "своими правами",
        FirewallPath::Uac => "через окно Windows",
        FirewallPath::Broker => "через брокера службы",
    };
    for args in runs {
        log_line(&format!(
            "брандмауэр ({door}): {}",
            broker_shown_command(&netsh, args)
        ));
    }
    let done = match path {
        FirewallPath::Direct => netsh_direct(runs),
        FirewallPath::Uac => netsh_via_uac(runs),
        // ⚠ У брокера дверь УЗКАЯ: служба собирает правило сама из общего
        // common/firewall_rule.rs, от нас идёт только порт. Через `exec` это
        // ходило раньше — и упиралось в галочку нулевой сессии, то есть ради
        // одного правила владельцу предлагалось выдать агенту права системы.
        FirewallPath::Broker => firewall_via_broker(port, why),
    };
    match &done {
        Ok(d) if d.code == 0 => log_line(&format!("брандмауэр ({door}): готово")),
        Ok(d) => log_line(&format!("брандмауэр ({door}): {}", netsh_code_words(d))),
        Err(err) => log_line(&format!("брандмауэр ({door}): не вышло — {err}")),
    }
    done
}

/// Разрешить входящие к трубе в брандмауэре Windows. Нужны права
/// администратора; без них — честная ошибка, а не тишина.
#[cfg(windows)]
#[tauri::command]
async fn firewall_allow(port: u16) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || firewall_allow_blocking(port))
        .await
        .map_err(|e| e.to_string())?
}

/// Вне Windows правил не ставим: брандмауэр macOS, если он включён, сам
/// спрашивает владельца про входящие к питону при первом подключении. Не
/// ошибка — телефон от этого не ломается, — но строка в журнале остаётся.
#[cfg(not(windows))]
#[tauri::command]
async fn firewall_allow(port: u16) -> Result<String, String> {
    log_line(&format!(
        "брандмауэр: правило для порта {port} на этой платформе не ставится — система спросит сама"
    ));
    Ok(format!("порт {port}: правило брандмауэра здесь не требуется"))
}

#[cfg(windows)]
fn firewall_allow_blocking(port: u16) -> Result<String, String> {
    let base = exe_dir();
    let cfg = config_value();
    // Правило вешается на ТОТ питон, которым запущена труба, а не на
    // встроенный «по умолчанию»: с явным python в конфиге правило уезжало на
    // несуществующий файл, и функция всё равно рапортовала об успехе.
    let python = match cfg.as_ref() {
        Some(cfg) => python_path(&base, cfg),
        None => base.join("runtime").join("python.exe"),
    };
    if !python.is_file() {
        return Err(format!(
            "не нашёл питон канала ({}) — правило не добавлено",
            python.display()
        ));
    }
    // Живого брокера спрашиваем только тогда, когда он мог бы понадобиться:
    // повышенному процессу повышаться некуда, и лишний стук в трубу ему ни к чему.
    let elevated = process_is_elevated();
    let path = firewall_path(
        elevated,
        &agent_mode(cfg.as_ref()),
        !elevated && broker_alive(&tree_dir()),
    );
    let runs = firewall_set_runs(port, Some(&python.display().to_string()));
    match netsh_batch(&runs, &firewall_why(port, true), path, port) {
        Ok(done) if done.code == 0 => Ok(firewall_added(port, path)),
        Ok(done) => Err(format!("правило не добавлено: {}", netsh_code_words(&done))),
        // В режиме службы отказ брокера — это ещё не «телефон не работает»:
        // правило в этом режиме ставит сама служба при запуске. Сказать об этом
        // надо здесь, иначе владелец останется с отказом и без выхода.
        // Текст уходит в тост окна одной строкой (textContent), поэтому и
        // собран одной строкой: перевод строки там просто схлопнется.
        Err(err) if path == FirewallPath::Broker => Err(format!(
            "{err}. В режиме службы правило брандмауэра ставит сама служба при запуске: \
             включи телефон в настройках и перезапусти её на экране «Система»"
        )),
        Err(err) => Err(err),
    }
}

/// Убрать разрешение брандмауэра (телефон выключили, программу снимают).
/// Во всём продукте до этого был только `add rule` и ни одного `delete`:
/// дыра переживала и выключение телефона, и удаление программы.
#[cfg(windows)]
#[tauri::command]
async fn firewall_clear(port: u16) -> Result<String, String> {
    tauri::async_runtime::spawn_blocking(move || firewall_clear_blocking(port))
        .await
        .map_err(|e| e.to_string())?
}

#[cfg(not(windows))]
#[tauri::command]
async fn firewall_clear(port: u16) -> Result<String, String> {
    log_line(&format!("брандмауэр: правила для порта {port} на этой платформе нет — снимать нечего"));
    Ok(format!("порт {port}: правила брандмауэра здесь нет — снимать нечего"))
}

#[cfg(windows)]
fn firewall_clear_blocking(port: u16) -> Result<String, String> {
    let elevated = process_is_elevated();
    let path = firewall_path(
        elevated,
        &agent_mode(config_value().as_ref()),
        !elevated && broker_alive(&tree_dir()),
    );
    match netsh_batch(&firewall_clear_runs(port), &firewall_why(port, false), path, port) {
        Ok(done) if done.code == 0 => Ok(firewall_removed(port, path)),
        // Снос несуществующего правила netsh считает ошибкой. Для владельца это
        // тот же итог, которого он и хотел: правила нет. Раньше эта команда
        // отвечала «снято» вообще всегда — в том числе когда netsh отказывал за
        // отсутствием прав, и правило оставалось на месте.
        Ok(_) => Ok(format!(
            "правила брандмауэра для порта {port} не нашлось — снимать было нечего"
        )),
        Err(err) if path == FirewallPath::Broker => Err(format!(
            "{err}. Правило осталось; снять его из окна администратора: \
             netsh advfirewall firewall delete rule {}",
            firewall_rule_name(port)
        )),
        Err(err) => Err(err),
    }
}

// ───────────────────────────── рука брокера для агента: просьба и подпись (R6)
//
// ЗАЧЕМ ЭТО ЕСТЬ. Брокер задуман как «агент просит повысить ОДНУ задачу», но
// клиентом брокера до сих пор была ровно одна кнопка — правило брандмауэра в
// этом же файле. У самого агента руки к брокеру не было вовсе: в
// `localharness/` слова broker нет ни разу. Конституция при этом обещала
// брокера как существующий механизм, то есть обещала то, чего нет.
//
// КАК УСТРОЕНО. Агент пишет просьбу в `broker-asks.json` (рука харнесса —
// контракт ниже), оболочка её читает, ПОКАЗЫВАЕТ ВЛАДЕЛЬЦУ и только по «да»
// зовёт брокера своим токеном. Ответ уходит в `broker-answers.json`, а что
// произошло на самом деле — в `broker.log` и в журнал оболочки.
//
// МОЛЧА НЕ ВЫПОЛНЯЕТСЯ НИЧЕГО. Брокер — это права системы; «спрошу потом»
// здесь означало бы «не спрошу». Отказ записывается наравне с согласием: без
// записи отказа журнал отвечает на вопрос «что сделали», но не на вопрос «о чём
// просили».
//
// ⚠ ПОЧЕМУ ПОДТВЕРЖДЕНИЕ — РОДНОЕ ОКНО WINDOWS, а не экран программы. Окно
// продукта живёт в трее и бывает закрыто, а просьба приходит ровно тогда, когда
// агент работает без владельца перед экраном. MessageBox приходит поверх всего
// и не требует поднятого вебвью. Кнопка по умолчанию — «Нет»: случайный Enter
// обязан ОТКАЗАТЬ, а не согласиться.

/// Сколько просьба ждёт владельца, прежде чем протухнуть.
///
/// Не «навсегда». Агент, попросивший права, ждёт ответа минуты, а не сутки, и
/// окно подтверждения над просьбой трёхдневной давности — это подпись вслепую
/// под тем, чего агент уже не хочет. Заодно это снимает очередь модальных окон
/// при первом запуске после долгого перерыва.
const BROKER_WISH_STALE_SEC: u64 = 10 * 60;

/// Сколько окон подтверждения показываем за один проход. Остальные ждут
/// следующего: пять модальных окон подряд — это не согласие, а «жми да».
const BROKER_WISH_PER_PASS: usize = 3;

/// Сколько ответов держим в файле: хвост для харнесса и для экрана «Система».
const BROKER_ANSWERS_KEPT: usize = 50;

/// Сколько знаков команды помещается в окно подтверждения.
///
/// ⚠ Это не косметика, а замок. Аргументов бывает до 256, длина каждого ничем
/// не ограничена: агент, набив первый аргумент пробелами, увёл бы настоящее
/// дело за нижний край окна — владелец подписал бы то, чего не видел. Обрезаем
/// ЯВНО и говорим, что обрезали.
const BROKER_SHOWN_MAX: usize = 1200;

/// Когда оболочка начала слушать просьбы. Уезжает в файл ответов, чтобы
/// харнесс отличал «владелец ещё не ответил» от «слушать некому — окна нет».
static BROKER_WATCH_SINCE: std::sync::OnceLock<String> = std::sync::OnceLock::new();

/// Местное время в том же виде, что в `service.log` и `broker.log`: журнал у
/// владельца один, и две половины одной строки не должны быть в разных
/// часовых поясах.
#[cfg(windows)]
fn local_stamp() -> String {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    let mut t: SYSTEMTIME = unsafe { std::mem::zeroed() };
    unsafe { GetLocalTime(&mut t) };
    format!(
        "[{:02}.{:02}.{} {:02}:{:02}:{:02}]",
        t.wDay, t.wMonth, t.wYear, t.wHour, t.wMinute, t.wSecond
    )
}

/// Вне Windows — тот же штамп, что и у `helene.log` (`common/stamp.rs`,
/// местное время через `localtime_r`); секунд эпохи здесь больше нет.
#[cfg(not(windows))]
fn local_stamp() -> String {
    now_stamp()
}

fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

/// Просьба агента — то, что оболочка приняла из `broker-asks.json`.
///
/// Токена здесь НЕТ и быть не может: агент секрета брокера не знает (в песочнице
/// он его физически не прочитает — файл закрыт на СИСТЕМУ, администраторов и
/// владельца), и передавать его агенту, чтобы он «сам сходил», значило бы отдать
/// брокера целиком. Токен подставляет оболочка, и только после «да».
#[derive(Clone, Debug, PartialEq, Eq)]
struct BrokerWish {
    id: String,
    op: BrokerOp,
    cmd: String,
    args: Vec<String>,
    why: String,
    timeout_sec: u64,
    /// Когда просьба записана (unix-секунды). 0 — харнесс не сказал, тогда
    /// возраст считается по времени файла.
    at_unix: u64,
}

/// Просьба, которую нельзя выполнить, но МОЖНО ответить: id разобрался, всё
/// остальное — нет. Без ответа агент ждал бы молчания и не узнал причины.
#[derive(Clone, Debug, PartialEq, Eq)]
struct BrokerBad {
    id: String,
    why: String,
}

/// Идентификатор просьбы. Чужие знаки не «чистим», а отвергаем: подчищенный id
/// перестал бы совпадать с тем, что ждёт харнесс, и ответ ушёл бы в никуда. А
/// перевод строки в нём подделал бы соседние записи журнала.
fn broker_wish_id(raw: &str) -> Option<String> {
    let id = raw.trim();
    (!id.is_empty()
        && id.len() <= 64
        && id.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'))
    .then(|| id.to_string())
}

/// Разбор файла просьб. Чистая функция: цена ошибки здесь — не «не сработало»,
/// а «владельцу показали не то, что выполнят».
fn broker_wishes(raw: &str) -> Vec<Result<BrokerWish, BrokerBad>> {
    let Ok(value) = serde_json::from_str::<serde_json::Value>(raw) else {
        return Vec::new();
    };
    let rows = match value.get("requests").and_then(|v| v.as_array()) {
        Some(rows) => rows.clone(),
        None => match value.as_array() {
            Some(rows) => rows.clone(),
            None => return Vec::new(),
        },
    };
    let mut out = Vec::new();
    for row in rows.iter().take(64) {
        let Some(id) = row.get("id").and_then(|v| v.as_str()).and_then(broker_wish_id) else {
            // Без годного id ответить некуда: пропускаем молча для агента и
            // громко для журнала (см. вызывающего).
            continue;
        };
        let bad = |why: &str| {
            Err(BrokerBad { id: id.clone(), why: why.to_string() })
        };
        let op_raw = row.get("op").and_then(|v| v.as_str()).unwrap_or("").trim();
        let Some(op) = BrokerOp::parse(op_raw) else {
            out.push(bad(
                "не сказано, какой дверью: op бывает exec (правами СИСТЕМЫ), \
                 spawn_interactive (твоими правами, в твоей сессии) или ping",
            ));
            continue;
        };
        let mut args = Vec::new();
        let mut args_ok = true;
        match row.get("args") {
            None | Some(serde_json::Value::Null) => {}
            Some(serde_json::Value::Array(items)) => {
                for item in items {
                    match item.as_str() {
                        Some(text) => args.push(text.to_string()),
                        None => args_ok = false,
                    }
                }
            }
            Some(_) => args_ok = false,
        }
        if !args_ok {
            out.push(bad(
                "args — массив строк, и никогда одна строка: склейка командной \
                 строки это класс инъекций, а не удобство",
            ));
            continue;
        }
        out.push(Ok(BrokerWish {
            id,
            op,
            cmd: row.get("cmd").and_then(|v| v.as_str()).unwrap_or("").trim().to_string(),
            args,
            why: row.get("why").and_then(|v| v.as_str()).unwrap_or("").trim().to_string(),
            timeout_sec: row
                .get("timeout_sec")
                .and_then(|v| v.as_u64())
                .unwrap_or(BROKER_TIMEOUT_DEFAULT),
            at_unix: row.get("at_unix").and_then(|v| v.as_u64()).unwrap_or(0),
        }));
    }
    out
}

/// Собрать просьбу так, как её ПРИМЕТ служба, и проверить ровно её же
/// проверками (`common/broker.rs`). Смысл — не «удобно», а «владельцу не
/// покажут команду, которую служба всё равно отвергнет»: иначе он подписывал бы
/// отказы. На macOS службы нет, и та же роль у `mac_wish_check`.
#[cfg_attr(not(windows), allow(dead_code))]
fn broker_wish_ask(wish: &BrokerWish, token: &str) -> Result<BrokerAsk, String> {
    let mut ask = BrokerAsk::new(token, wish.op, &wish.cmd, &wish.args, &wish.why);
    ask.id = wish.id.clone();
    ask.timeout_sec = wish.timeout_sec;
    BrokerAsk::parse(&ask.to_json())
}

/// Слова, которыми просьба показывается владельцу. Чистая функция и под тестом:
/// это единственный экран, по которому человек решает отдать права системы, и
/// «зачем» в нём — слова САМОГО АГЕНТА, а не факт.
fn broker_confirm_text(wish: &BrokerWish) -> String {
    broker_confirm_text_for(wish, cfg!(target_os = "macos"))
}

/// То же — с явной платформой, чтобы стенд разобрал обе на любой ОС. На macOS
/// `exec` — права администратора через системный диалог пароля, а не «права
/// системы»: их там нет, и слова окна обязаны совпадать с тем, что произойдёт.
fn broker_confirm_text_for(wish: &BrokerWish, mac: bool) -> String {
    let door = match (wish.op, mac) {
        (BrokerOp::Exec, false) => {
            "ПРАВАМИ СИСТЕМЫ — это выше твоих собственных прав администратора"
        }
        (BrokerOp::Exec, true) => {
            "ПРАВАМИ АДМИНИСТРАТОРА — после «Да» система отдельно спросит твой пароль"
        }
        (BrokerOp::SpawnInteractive, _) => "твоими правами, в твоей сессии",
        (BrokerOp::Ping, _) => "ничего не выполняя (проверка связи)",
        // Узкая дверь: агент выбирает только порт, само правило собирает служба.
        // Показывать её теми же словами, что и «права системы», было бы враньём
        // в сторону страха — а пугать там, где риска нет, тоже обман.
        (BrokerOp::Firewall, _) => "правами службы, и только чтобы поставить правило брандмауэра",
    };
    let shown = broker_shown_command(&wish.cmd, &wish.args);
    let total = shown.chars().count();
    let shown = if total > BROKER_SHOWN_MAX {
        let head: String = shown.chars().take(BROKER_SHOWN_MAX).collect();
        format!(
            "{head}\n\n⚠ КОМАНДА ОБРЕЗАНА: в ней {total} знаков, целиком она в окно не влезает. \
             Конец команды тебе НЕ показан — сама эта длина повод отказать."
        )
    } else {
        shown
    };
    format!(
        "Агент просит выполнить команду {door}.\n\n\
         Зачем — его слова, не проверенный факт:\n{}\n\n\
         Что запустится ровно в этом виде:\n{shown}\n\n\
         Выполнить? Отказ тоже будет записан в журнал.",
        wish.why
    )
}

/// Спросить владельца родным окном Windows. По умолчанию — «Нет».
#[cfg(windows)]
fn ask_owner_yes(title: &str, text: &str) -> bool {
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        MessageBoxW, IDYES, MB_DEFBUTTON2, MB_ICONWARNING, MB_SETFOREGROUND, MB_TOPMOST, MB_YESNO,
    };
    let wide = |s: &str| s.encode_utf16().chain(std::iter::once(0)).collect::<Vec<u16>>();
    let (caption, body) = (wide(title), wide(text));
    let answer = unsafe {
        MessageBoxW(
            std::ptr::null_mut(),
            body.as_ptr(),
            caption.as_ptr(),
            MB_YESNO | MB_ICONWARNING | MB_DEFBUTTON2 | MB_SETFOREGROUND | MB_TOPMOST,
        )
    };
    answer == IDYES
}

/// macOS: тот же вопрос родным диалогом (`display dialog`). Кнопка по умолчанию
/// и кнопка отмены — «Нет»: Enter и Esc отказывают; «Да» приходит строкой
/// `button returned:Да`. Диалог не открылся — отказ, и причина в журнале.
///
/// Сюда доходит просьба агента к брокеру: на macOS брокер — сама оболочка
/// (`broker_prepare`/`broker_execute` ниже), и это окно — первая из двух
/// подписей владельца; вторая, для `exec`, — системный диалог пароля.
#[cfg(target_os = "macos")]
fn ask_owner_yes(title: &str, text: &str) -> bool {
    let (yes, no) = mac_yes_no(&ui_lang());
    match osascript(&mac_confirm_script(title, text, yes, no)) {
        // ⚠ Ответ читается ПО НОМЕРУ КНОПКИ, а не по слову (судьи 19.09).
        // Раньше здесь стояло `contains("button returned:Да")`: у владельца с
        // английским интерфейсом кнопки всё равно были русскими, а стоило бы им
        // стать «Yes/No» — и сравнение молча перестало бы находить согласие, то
        // есть «Да» читалось бы как отказ. Номер называет сам AppleScript, и он
        // не зависит ни от языка, ни от того, как система перерисовала кнопку.
        Ok(out) => out.trim() == "2",
        Err(err) => {
            // «User canceled» — это «Нет», а не поломка; остальное — в журнал.
            if !err.contains("-128") {
                log_line(&format!("окно подтверждения не открылось ({err}) — считаю отказом"));
            }
            false
        }
    }
}

/// Скрипт окна подтверждения: две кнопки и НОМЕР нажатой на выходе.
/// Чистая функция — стенд гоняется на любой ОС.
///
/// Кнопка по умолчанию и кнопка отмены — первая («Нет»): Enter и Esc отказывают.
/// Сравнение внутри AppleScript идёт с тем же списком, что мы ему передали,
/// поэтому «какая это по счёту кнопка» не зависит от языка интерфейса.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_confirm_script(title: &str, text: &str, yes: &str, no: &str) -> String {
    format!(
        "set btns to {{{no}, {yes}}}\n\
         set r to display dialog {text} with title {title} buttons btns \
         default button 1 cancel button 1 with icon caution\n\
         repeat with i from 1 to count of btns\n\
         if (button returned of r) is (item i of btns) then return (i as text)\n\
         end repeat\n\
         return \"0\"",
        no = applescript_quote(no),
        yes = applescript_quote(yes),
        text = applescript_quote(text),
        title = applescript_quote(title),
    )
}

/// Подписи кнопок согласия и отказа на языке интерфейса. Языки — те же, что у
/// `lang/*.json` (ru, en, fr, zh); умолчание английское, как в `lang.ts`:
/// программу ставит кто угодно, и язык, которого человек не знает, — это не
/// «запасной вариант», а закрытая дверь.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_yes_no(lang: &str) -> (&'static str, &'static str) {
    match lang {
        "ru" => ("Да", "Нет"),
        "fr" => ("Oui", "Non"),
        "zh" => ("是", "否"),
        _ => ("Yes", "No"),
    }
}

/// Язык интерфейса из `helene.json` (`lang`). Пусто — не выбран, берём системный
/// на стороне страницы; здесь это значит «английский» (см. `mac_yes_no`).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn ui_lang_from(cfg: &serde_json::Value) -> String {
    let raw = cfg
        .get("lang")
        .or_else(|| cfg.get("ui").and_then(|u| u.get("lang")))
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim()
        .to_lowercase();
    // `ru-RU`, `zh-Hans` — тег может быть полным: берём только первую часть.
    raw.split(['-', '_']).next().unwrap_or("").to_string()
}

#[cfg(target_os = "macos")]
fn ui_lang() -> String {
    match read_config(&install_root().join(CONFIG_NAME)) {
        ConfigRead::Ok(v) => ui_lang_from(&v),
        _ => String::new(),
    }
}

/// Прочие POSIX: спросить некого, а «некого спросить» — это отказ, не согласие.
#[cfg(not(any(windows, target_os = "macos")))]
fn ask_owner_yes(title: &str, _text: &str) -> bool {
    log_line(&format!("окно подтверждения показать нечем ({title}) — считаю отказом"));
    false
}

/// Живой ли брокер прямо сейчас. Не «стоит ли служба» по конфигу, а вопрос
/// самой трубе: служба может быть снята, остановлена или брокер в ней выключен
/// в Настройках — во всех трёх случаях идти туда нельзя.
///
/// `ping` ничего не выполняет и в журнал действий не пишется, поэтому спросить
/// им дёшево. Срок короткий: если трубы нет вовсе, открытие падает сразу.
#[cfg(windows)]
fn broker_alive(tree: &Path) -> bool {
    let Some(token) = broker_token_read(tree) else {
        return false;
    };
    let mut ask = BrokerAsk::new(&token, BrokerOp::Ping, "", &[], "проверка связи с брокером");
    ask.timeout_sec = 5;
    broker_call_deadline(&broker_pipe_name(&exe_dir()), &ask, Duration::from_secs(6))
        .map(|receipt| receipt.ok)
        .unwrap_or(false)
}

/// Вне Windows трубы нет. macOS: брокер — сама оболочка, жива она — жив и он.
/// Прочие POSIX — брокера нет.
#[cfg(not(windows))]
#[allow(dead_code)]
fn broker_alive(_tree: &Path) -> bool {
    cfg!(target_os = "macos")
}

/// Строка в журнал владельца (`broker.log`) И в журнал оболочки рядом с exe.
///
/// Два места намеренно: `broker.log` лежит в дереве, а дерево — дом агента, он
/// туда пишет сам и подчистить его может. Вторая копия ложится вне дерева, и
/// одновременно подделать обе агенту заметно труднее.
///
/// ⚠ Не «невозможно», и врать про это нельзя: обе записи делает процесс
/// владельца, а `helene.log` вдобавок перестанет писаться вовсе, если служба
/// однажды закроет папку установки на запись (задача A4). Настоящий журнал
/// брокера пишет служба — от её строки эта отличается словом «окно».
fn broker_note_owner(tree: &Path, line: &str) {
    use std::io::Write as _;
    let path = broker_log_path(tree);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    // Усечение — как у службы: журнал не должен съесть диск.
    if std::fs::metadata(&path).map(|m| m.len() > 5 * 1024 * 1024).unwrap_or(false) {
        let _ = std::fs::rename(&path, path.with_extension("log.1"));
    }
    if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&path) {
        let _ = writeln!(f, "{} окно · {}", local_stamp(), line);
    }
    log_line(&format!("брокер: {line}"));
}

fn broker_answers_load(tree: &Path) -> Vec<serde_json::Value> {
    let Ok(raw) = std::fs::read_to_string(broker_answers_path(tree)) else {
        return Vec::new();
    };
    serde_json::from_str::<serde_json::Value>(&raw)
        .ok()
        .and_then(|v| v.get("answers").and_then(|a| a.as_array()).cloned())
        .unwrap_or_default()
}

fn broker_answers_save(tree: &Path, rows: &[serde_json::Value]) {
    let path = broker_answers_path(tree);
    if let Some(dir) = path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let keep = rows.len().saturating_sub(BROKER_ANSWERS_KEPT);
    let body = serde_json::json!({
        "v": 1,
        "updated_at": local_stamp(),
        // Чтобы харнесс отличал «владелец ещё не ответил» от «спрашивать
        // некому»: без окна просьбу не увидит никто, и ждать её незачем.
        "desk": {
            "watching_since": BROKER_WATCH_SINCE.get().cloned().unwrap_or_default(),
            "pid": std::process::id(),
        },
        "answers": &rows[keep..],
    });
    let Some(name) = path.file_name().map(|n| n.to_string_lossy().to_string()) else {
        return;
    };
    let tmp = path.with_file_name(format!(".tmp-{name}"));
    let text = serde_json::to_string_pretty(&body).unwrap_or_default();
    if std::fs::write(&tmp, text.as_bytes()).is_ok() {
        // rename на Windows заменяет существующий файл: харнесс никогда не
        // видит файла, дописанного наполовину.
        let _ = std::fs::rename(&tmp, &path);
    }
}

/// Ответ на одну просьбу. Не «успех», а всё, что видела оболочка: решение
/// владельца, слова причины и квитанция брокера целиком.
fn broker_answer_row(
    wish: &BrokerWish,
    decision: &str,
    note: &str,
    receipt: Option<&BrokerReceipt>,
) -> serde_json::Value {
    let mut row = serde_json::json!({
        "id": wish.id,
        "decision": decision,
        "at": local_stamp(),
        "op": wish.op.as_str(),
        "why": wish.why,
        "shown": broker_shown_command(&wish.cmd, &wish.args),
        "note": note,
    });
    if let Some(r) = receipt {
        let map = row.as_object_mut().expect("собран объектом выше");
        map.insert("ok".into(), serde_json::Value::Bool(r.ok));
        map.insert("code".into(), match r.code {
            Some(code) => serde_json::Value::from(code),
            None => serde_json::Value::Null,
        });
        map.insert("pid".into(), match r.pid {
            Some(pid) => serde_json::Value::from(pid),
            None => serde_json::Value::Null,
        });
        map.insert("out".into(), serde_json::Value::String(r.out.clone()));
        map.insert("err".into(), serde_json::Value::String(r.err.clone()));
        map.insert("ms".into(), serde_json::Value::from(r.ms));
    }
    row
}

/// Один проход по файлу просьб. Возвращает `true`, если ответить удалось на
/// всё: непройденное (упёрлись в потолок окон за проход) обязано вернуться в
/// следующем проходе, а не потеряться.
///
/// ⚠ ГОНКА, КОТОРОЙ ЗДЕСЬ НЕТ. Просьба читается ОДИН раз, и владельцу
/// показывается ровно то, что потом уедет брокеру: и `why`, и команда живут в
/// `wish` в памяти. Перечитай мы файл после «да», агент успел бы подменить
/// команду между вопросом и выполнением — классический TOCTOU, и подпись
/// владельца стояла бы под чужим текстом.
fn broker_pass(tree: &Path, raw: &str, file_at: u64) -> bool {
    let wishes = broker_wishes(raw);
    if wishes.is_empty() {
        return true;
    }
    let mut answers = broker_answers_load(tree);
    let mut known: std::collections::HashSet<String> = answers
        .iter()
        .filter_map(|r| r.get("id").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .collect();
    let mut added = 0usize;
    let mut shown = 0usize;
    let mut all = true;
    let now = now_unix();
    for wish in wishes {
        let wish = match wish {
            Ok(wish) => wish,
            Err(bad) => {
                if !known.insert(bad.id.clone()) {
                    continue;
                }
                broker_note_owner(tree, &format!("ОТКАЗ на разборе просьбы {}: {}", bad.id, bad.why));
                answers.push(serde_json::json!({
                    "id": bad.id, "decision": "refused", "at": local_stamp(),
                    "note": bad.why,
                }));
                added += 1;
                continue;
            }
        };
        if !known.insert(wish.id.clone()) {
            continue;
        }
        // Отказ до вопроса владельцу: протухла, брокера нет, просьба такая, что
        // служба её всё равно не примет. Собирается ОДНОЙ величиной, а не
        // разбегается по веткам: развилка «спросили или нет» тут одна.
        let at = if wish.at_unix > 0 { wish.at_unix } else { file_at };
        let mut refusal: Option<String> = None;
        let mut ready: Option<BrokerPrepared> = None;
        if now.saturating_sub(at) > BROKER_WISH_STALE_SEC {
            refusal = Some(format!(
                "просьба ждала дольше {} минут — за это время агент ушёл дальше, и \
                 подписывать её вслепую опаснее, чем отказать. Пусть попросит заново",
                BROKER_WISH_STALE_SEC / 60
            ));
        } else {
            // Кто исполнит и примет ли он такую просьбу — по платформе
            // (`broker_prepare`): служба по трубе на Windows, сама оболочка на
            // macOS. Слова отказа — исполнителя, слово в слово.
            match broker_prepare(tree, &wish) {
                Ok(prepared) => ready = Some(prepared),
                Err(why) => refusal = Some(why),
            }
        }
        // `ping` ничего не выполняет — спрашивать владельца не о чем. Это
        // единственное исключение, и оно ровно такое же, как у службы.
        if refusal.is_none() && wish.op != BrokerOp::Ping {
            if shown >= BROKER_WISH_PER_PASS {
                all = false;
                known.remove(&wish.id);
                continue;
            }
            shown += 1;
            // Отпечаток файла — частью того же текста, что читает владелец:
            // отдельным окном его бы закрыли не читая.
            let extra = ready.as_ref().map(broker_prepared_note).unwrap_or_default();
            if !ask_owner_yes(
                &format!("{}: агент просит права", product_ui()),
                &format!("{}{extra}", broker_confirm_text(&wish)),
            ) {
                refusal = Some("владелец отказал в окне подтверждения".to_string());
            }
        }
        if let Some(note) = refusal {
            broker_note_owner(
                tree,
                &format!(
                    "ОТКАЗ · зачем: {} · команда: {} · {note}",
                    wish.why,
                    broker_shown_command(&wish.cmd, &wish.args)
                ),
            );
            answers.push(broker_answer_row(&wish, "refused", &note, None));
            added += 1;
            continue;
        }
        let prepared = ready.expect("без отказа просьба собрана выше");
        match broker_execute(&prepared, &wish) {
            BrokerRun::Done(receipt) => {
                let outcome = if !receipt.ok {
                    if cfg!(windows) { "ОТКАЗ службы" } else { "не вышло" }.to_string()
                } else {
                    match receipt.code {
                        Some(code) => format!("код {code}"),
                        None => "не дождался".to_string(),
                    }
                };
                broker_note_owner(
                    tree,
                    &format!(
                        "{} · владелец разрешил · {outcome} за {} мс · зачем: {} · команда: {}",
                        wish.op.as_str(),
                        receipt.ms,
                        wish.why,
                        broker_shown_command(&wish.cmd, &wish.args)
                    ),
                );
                answers.push(broker_answer_row(
                    &wish,
                    if receipt.ok { "allowed" } else { "failed" },
                    receipt.note.trim(),
                    Some(&receipt),
                ));
                added += 1;
            }
            // Вторая подпись владельца не состоялась (macOS: отменён диалог
            // пароля). Это ОТКАЗ, а не «не вышло»: агент обязан прочитать его
            // как отказ и не переспрашивать тем же.
            BrokerRun::Refused(note) => {
                broker_note_owner(
                    tree,
                    &format!(
                        "ОТКАЗ · зачем: {} · команда: {} · {note}",
                        wish.why,
                        broker_shown_command(&wish.cmd, &wish.args)
                    ),
                );
                answers.push(broker_answer_row(&wish, "refused", &note, None));
                added += 1;
            }
            BrokerRun::Failed(err) => {
                broker_note_owner(
                    tree,
                    &format!("владелец разрешил, но брокер не ответил: {err} · зачем: {}", wish.why),
                );
                answers.push(broker_answer_row(&wish, "failed", &err, None));
                added += 1;
            }
        }
    }
    if added > 0 {
        broker_answers_save(tree, &answers);
    }
    all
}

// ───────────────────────── чем исполняется подписанная просьба: по платформе
//
// Windows: просьба уезжает службе по трубе с её токеном (`BrokerAsk`), исполняет
// служба. macOS: службы и трубы нет — брокер САМА ОБОЛОЧКА: подписанную просьбу
// она исполняет сама (`spawn_interactive` — правами владельца, `exec` — через
// `osascript … with administrator privileges`, где пароль спрашивает система, и
// этот диалог — вторая подпись владельца). Прочие POSIX: брокера нет. Общий ход
// `broker_pass` один на всех: разбор, протухание, вопрос владельцу, квитанция,
// журнал — расходятся только эти две функции.

/// Что нужно, чтобы исполнить подписанную просьбу.
#[cfg(windows)]
type BrokerPrepared = BrokerAsk;
#[cfg(all(not(windows), not(target_os = "macos")))]
type BrokerPrepared = BrokerWish;
#[cfg(target_os = "macos")]
type BrokerPrepared = MacPrepared;

/// macOS: подписанная просьба И ОТПЕЧАТОК ФАЙЛА, который она зовёт.
///
/// ⚠⚠ ЗАЧЕМ ОТПЕЧАТОК (находка судей 19.09). Владелец подписывает ПУТЬ, а
/// исполняется СОДЕРЖИМОЕ. Между «Да» в окне и запуском команды проходит целый
/// диалог пароля, и всё это время файл по этому пути может переписать кто
/// угодно, кому он доступен на запись, — включая самого агента, если путь ведёт
/// в его дом. Подпись владельца тогда относилась бы к одному файлу, а права
/// получал бы другой.
///
/// Поэтому: sha256 считается ДО вопроса, показывается владельцу восемью знаками
/// и сверяется перед исполнением. Для системных путей (`/usr`, `/bin`, `/sbin`,
/// `/System`, `/opt/homebrew`) отпечатка нет: туда без root не пишут, а лишняя
/// строка в окне подтверждения — это шум, за которым перестают читать нужное.
#[cfg(target_os = "macos")]
#[derive(Clone)]
struct MacPrepared {
    wish: BrokerWish,
    digest: Option<String>,
}

/// Чем кончилось исполнение — ТРИ исхода, не два: отказ владельца во втором
/// диалоге (пароль на macOS) — это «refused», а не «failed», и в файле ответов
/// он обязан зваться своим словом.
enum BrokerRun {
    Done(BrokerReceipt),
    Refused(String),
    Failed(String),
}

/// Windows: токен службы и её же проверки; без токена — службы нет.
#[cfg(windows)]
fn broker_prepare(tree: &Path, wish: &BrokerWish) -> Result<BrokerPrepared, String> {
    let Some(token) = broker_token_read(tree) else {
        return Err("брокера нет: служба не установлена, и повышать права некому. \
                    Служба ставится один раз под администратором на экране «Система»"
            .to_string());
    };
    // Слова отказа — службины, слово в слово: пересказ разъехался бы с
    // оригиналом, а владельцу и агенту нужен один текст.
    broker_wish_ask(wish, &token)
}

/// macOS: та же граница, что у службы, только путь — POSIX; программа обязана
/// быть на месте — просьбу о несуществующей команде владельцу показывать незачем.
#[cfg(target_os = "macos")]
fn broker_prepare(_tree: &Path, wish: &BrokerWish) -> Result<BrokerPrepared, String> {
    // Выключатель владельца — ПЕРВЫМ и на каждой просьбе: конфиг читается
    // заново, значит «выключил» действует немедленно, а не «после перезапуска
    // окна». Отказ идёт БЕЗ диалога: показывать окно подтверждения там, где
    // дверь закрыта целиком, — значит спрашивать о том, что уже решено.
    if !mac_broker_enabled() {
        return Err(BROKER_OFF_SAID.to_string());
    }
    mac_wish_check(wish)?;
    if !wish.op.runs() {
        return Ok(MacPrepared { wish: wish.clone(), digest: None });
    }
    if !Path::new(&wish.cmd).is_file() {
        return Err(format!("программы «{}» нет по этому пути — просить не о чем", wish.cmd));
    }
    // Отпечаток — только для файлов вне системных папок: туда без root не
    // пишут, и подменить их между «Да» и запуском некому.
    let digest = if mac_system_path(&wish.cmd) {
        None
    } else {
        match std::fs::read(&wish.cmd) {
            Ok(bytes) => Some(mac_svc_sha256(&bytes)),
            Err(e) => {
                return Err(format!(
                    "файл «{}» не прочитался ({e}) — я не могу показать владельцу, \
                     ЧТО именно он подписывает",
                    wish.cmd
                ))
            }
        }
    };
    Ok(MacPrepared { wish: wish.clone(), digest })
}

/// Выключатель брокера владельцем (`service.broker` в helene.json). Умолчание —
/// включён: то же, что у службы на Windows (`svc::load_plan`), и по той же
/// причине — выключенный по умолчанию брокер был бы мёртвым кодом.
///
/// Читается ЗАНОВО на каждой просьбе: «действует немедленно» значит именно это.
#[cfg(target_os = "macos")]
fn mac_broker_enabled() -> bool {
    match read_config(&install_root().join(CONFIG_NAME)) {
        ConfigRead::Ok(v) => v
            .get("service")
            .and_then(|x| x.get("broker"))
            .and_then(|b| b.as_bool())
            .unwrap_or(true),
        // Конфига нет или он битый — это не «владелец выключил брокера».
        _ => true,
    }
}

/// Слова отказа при выключенном брокере — одни на журнал и на ответ агенту.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const BROKER_OFF_SAID: &str =
    "брокер выключен владельцем: Настройки → Система (ключ service.broker в helene.json).      Пока он выключен, просьбы получают отказ сразу — окно подтверждения не показывается";

/// Системная ли это папка. Файлы оттуда не переписать без root, значит и
/// отпечаток их содержимого владельцу показывать незачем. Чистая функция —
/// стенд гоняется на любой ОС.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_system_path(cmd: &str) -> bool {
    ["/usr/", "/bin/", "/sbin/", "/System/", "/opt/homebrew/", "/usr/local/"]
        .iter()
        .any(|root| cmd.starts_with(root))
}

/// Строка окна подтверждения про отпечаток — та, что дописывается к общему
/// тексту (`broker_confirm_text`). Windows: пусто, там подпись относится к
/// команде, которую исполняет служба своими проверками.
#[cfg(windows)]
fn broker_prepared_note(_prepared: &BrokerPrepared) -> String {
    String::new()
}

#[cfg(all(not(windows), not(target_os = "macos")))]
fn broker_prepared_note(_prepared: &BrokerPrepared) -> String {
    String::new()
}

/// macOS: владельцу говорится прямо, что содержимое файла ему НЕ показано, и
/// даётся отпечаток — по нему он может сверить файл сам. Восемь знаков, а не
/// шестьдесят четыре: длинную строку не читают вовсе, а восьми хватает, чтобы
/// заметить подмену, и они же стоят в журнале.
#[cfg(target_os = "macos")]
fn broker_prepared_note(prepared: &BrokerPrepared) -> String {
    match &prepared.digest {
        Some(hex) => format!(
            "\n\nФайл вне системных папок: содержимое тебе не показано; отпечаток {}. \
             Он же проверяется перед запуском — подменят файл после «Да», и команда не пойдёт.",
            &hex[..8.min(hex.len())]
        ),
        None => String::new(),
    }
}

#[cfg(not(any(windows, target_os = "macos")))]
fn broker_prepare(_tree: &Path, _wish: &BrokerWish) -> Result<BrokerPrepared, String> {
    Err("брокера на этой платформе нет: повышать права некому".to_string())
}

#[cfg(windows)]
fn broker_execute(prepared: &BrokerPrepared, wish: &BrokerWish) -> BrokerRun {
    let limit = Duration::from_secs(wish.timeout_sec.saturating_add(30));
    match broker_call_deadline(&broker_pipe_name(&exe_dir()), prepared, limit) {
        Ok(receipt) => BrokerRun::Done(receipt),
        Err(err) => BrokerRun::Failed(err),
    }
}

#[cfg(target_os = "macos")]
fn broker_execute(prepared: &BrokerPrepared, _wish: &BrokerWish) -> BrokerRun {
    mac_broker_run(prepared)
}

#[cfg(not(any(windows, target_os = "macos")))]
fn broker_execute(_prepared: &BrokerPrepared, _wish: &BrokerWish) -> BrokerRun {
    BrokerRun::Failed("брокера на этой платформе нет".to_string())
}

/// macOS: граница просьбы — та же, что у службы на Windows (`BrokerAsk::parse`
/// в common/broker.rs), только путь — POSIX: абсолютный и без «..». Голое имя
/// искалось бы по PATH владельца, а `exec` идёт правами администратора —
/// подменённый в PATH файл ничем не лучше подложенного в папку установки.
/// Чистая функция под стендом на любой ОС.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_wish_check(wish: &BrokerWish) -> Result<(), String> {
    let why = wish.why.trim();
    if why.is_empty() {
        return Err("не сказано «зачем». Это поле идёт в журнал владельцу — брокер без него не работает".into());
    }
    if why.chars().count() < 3 || !why.chars().any(|c| c.is_alphabetic()) {
        return Err("«зачем» должно быть словами, а не знаком-заглушкой".into());
    }
    if why.chars().count() > 500 {
        return Err("«зачем» длиннее 500 знаков — это уже не объяснение".into());
    }
    // ⚠ Не только `is_control()` (судьи 19.09): U+2028 и U+2029 — переводы
    // строки для всего, что рисует текст, U+0085 — тоже, а знаки формата
    // (U+200E, U+202E, мягкий перенос) переставляют текст прямо на экране
    // владельца. Одна граница на обе стороны — `broker_invisible`.
    if why.chars().any(broker_invisible) {
        return Err("«зачем» — одной строкой обычными знаками: перевод строки (в том числе \
                    невидимый — U+2028, U+0085) подделывает журнал, а знаки формата \
                    переставляют текст прямо на экране владельца"
            .into());
    }
    if wish.timeout_sec == 0 || wish.timeout_sec > BROKER_TIMEOUT_MAX {
        return Err(format!("timeout_sec бывает от 1 до {BROKER_TIMEOUT_MAX} с"));
    }
    if wish.args.len() > 256 {
        return Err("больше 256 аргументов — это не команда".into());
    }
    if wish.args.iter().any(|a| a.contains('\0')) {
        return Err("в args есть нулевой байт — команда оборвётся на нём".into());
    }
    if wish.args.iter().any(|a| a.chars().any(broker_invisible)) {
        return Err(BROKER_INVISIBLE_SAID.into());
    }
    match wish.op {
        BrokerOp::Ping => Ok(()),
        // Правило брандмауэра — дверь Windows: там его ставит служба ради кнопки
        // «Телефон». На macOS брандмауэр спрашивает владельца сам при первом
        // входящем, и ставить нечего.
        BrokerOp::Firewall => Err("на macOS брандмауэр спрашивает сам — правило ставить не нужно, \
                                   и двери firewall здесь нет"
            .into()),
        BrokerOp::Exec | BrokerOp::SpawnInteractive => mac_cmd_ok(&wish.cmd),
    }
}

#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_cmd_ok(cmd: &str) -> Result<(), String> {
    if cmd.is_empty() {
        return Err("нет команды (поле cmd)".into());
    }
    if cmd.chars().any(broker_invisible) {
        return Err(BROKER_INVISIBLE_SAID.into());
    }
    if !cmd.starts_with('/') {
        return Err(format!(
            "команду надо называть абсолютным путём, а не «{cmd}»: голое имя ищется по PATH, \
             и подменённый там файл исполнился бы чужими правами"
        ));
    }
    if cmd.contains("..") {
        return Err("в пути есть «..» — назови программу прямо".into());
    }
    Ok(())
}

/// Аргумент для /bin/sh — в одинарных кавычках: внутри них sh не разворачивает
/// ничего, а сама одинарная кавычка закрывается, экранируется и открывается
/// снова (`'\''`). Пустой аргумент — `''`. Голым остаётся только то, что sh не
/// тронет ни при каких настройках.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn sh_quote(arg: &str) -> String {
    if !arg.is_empty()
        && arg.bytes().all(|b| b.is_ascii_alphanumeric() || b"/._-+=:,@%".contains(&b))
    {
        return arg.to_string();
    }
    format!("'{}'", arg.replace('\'', "'\\''"))
}

/// Командная строка для /bin/sh: программа и аргументы по одному, каждый в
/// своих кавычках. Это то, что исполнит `do shell script` под администратором,
/// поэтому склейка ЕДИНСТВЕННАЯ и стоит под стендом: цена ошибки здесь —
/// «запустилось не то», а не «не запустилось».
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_shell_line(cmd: &str, args: &[String]) -> String {
    let mut out = sh_quote(cmd);
    for a in args {
        out.push(' ');
        out.push_str(&sh_quote(a));
    }
    out
}

/// Метка кода возврата в выводе. `do shell script` кода команды не отдаёт:
/// ноль он молча глотает, а ненулевой превращает в ошибку скрипта вместе со
/// stderr. Поэтому код печатает сам sh последней строкой, а оболочка его
/// вычитывает (`mac_split_code`).
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const MAC_CODE_MARK: &str = "HELENE-CODE";

/// Скрипт для `osascript -e`: команда правами администратора с системным
/// диалогом пароля — он и есть вторая подпись владельца, своего окна пароля у
/// оболочки нет и быть не должно. stderr слит в stdout (`2>&1`): порознь
/// `do shell script` отдаёт stderr только в тексте ошибки, а квитанции нужны
/// оба потока целиком. `without altering line endings` — иначе AppleScript
/// перепишет переводы строк в `\r`, и метка кода не найдётся.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_admin_script(cmd: &str, args: &[String], prompt: &str, digest: Option<&str>) -> String {
    // ⚠ Сверка отпечатка — ПЕРВОЙ строкой и УЖЕ под правами администратора:
    // владелец подписал путь, а исполнится содержимое, и между «Да» и вводом
    // пароля файл по этому пути мог переписать кто угодно, кому он доступен на
    // запись. Не сошлось — отказ словами (код 91), а не «запустили что дали».
    let guard = match digest {
        Some(hex) => format!(
            "if [ \"$(/usr/bin/shasum -a 256 {file} | /usr/bin/cut -d' ' -f1)\" != {hash} ]; then \
             echo 'файл команды подменили после «Да» — не исполняю' >&2; exit 91; fi; ",
            file = sh_quote(cmd),
            hash = sh_quote(hex),
        ),
        None => String::new(),
    };
    let line = format!(
        "{guard}{} 2>&1; printf '\\n{MAC_CODE_MARK} %s' \"$?\"",
        mac_shell_line(cmd, args)
    );
    format!(
        "do shell script {} with prompt {} with administrator privileges without altering line endings",
        applescript_quote(&line),
        applescript_quote(prompt)
    )
}

/// Сколько команды влезает во ВТОРОЙ экран — системный диалог пароля.
///
/// ⚠ Раньше там стояло только «зачем» словами агента. Но «Да» в нашем окне и
/// пароль в системном — это две РАЗНЫЕ подписи, и вторая ставится на диалоге,
/// который рисует не продукт: владелец видел там объяснение без единого слова о
/// том, что именно сейчас исполнится. Команда обрезана, и обрезка названа.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
const MAC_PROMPT_CMD_MAX: usize = 200;

/// Подсказка для диалога пароля: «зачем» И сама команда, коротко.
/// Чистая функция — по ней стенд на любой ОС.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_password_prompt(product: &str, why: &str, shown: &str) -> String {
    let total = shown.chars().count();
    let short = if total > MAC_PROMPT_CMD_MAX {
        let head: String = shown.chars().take(MAC_PROMPT_CMD_MAX).collect();
        format!("{head}… (обрезано, всего {total} знаков)")
    } else {
        shown.to_string()
    };
    format!("{product}: агент просит права администратора — {why} · команда: {short}")
}

/// Вывод команды и её код — из того, что вернул osascript. Метки нет — код
/// неизвестен (`None`), и это сказано, а не подменено нулём.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
fn mac_split_code(text: &str) -> (String, Option<i32>) {
    let trimmed = text.trim_end_matches(['\n', '\r']);
    let at = trimmed.rfind('\n').map(|i| i + 1).unwrap_or(0);
    if let Some(rest) = trimmed[at..].strip_prefix(MAC_CODE_MARK) {
        if let Ok(code) = rest.trim().parse::<i32>() {
            return (trimmed[..at].trim_end_matches(['\n', '\r']).to_string(), Some(code));
        }
    }
    (trimmed.to_string(), None)
}

/// Сколько сверх `timeout_sec` ждём `exec`: между «Да» и командой стоит человек
/// с диалогом пароля.
#[cfg(target_os = "macos")]
const MAC_PASSWORD_GRACE_SEC: u64 = 120;

/// Что вышло у процесса, поднятого оболочкой: код (None — снят по сроку или
/// сигналом), оба потока целиком, срок вышел или нет.
#[cfg(target_os = "macos")]
struct MacRan {
    pid: u32,
    code: Option<i32>,
    killed: bool,
    out: String,
    err: String,
}

/// Поднять процесс в своей группе, дождаться со сроком, собрать оба потока.
/// По сроку — SIGKILL всей группе: внуки не должны пережить срок, а
/// `run_hidden_for` гасит только лидера и теряет уже собранный вывод.
#[cfg(target_os = "macos")]
fn mac_run_group(cmd: &mut Command, limit: Duration) -> Result<MacRan, String> {
    cmd.stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::piped()).process_group(0);
    let mut child = cmd.spawn().map_err(|e| format!("не поднялось: {e}"))?;
    let pid = child.id();
    let out_pipe = child.stdout.take();
    let err_pipe = child.stderr.take();
    let out_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut p) = out_pipe {
            let _ = p.read_to_end(&mut buf);
        }
        buf
    });
    let err_thread = std::thread::spawn(move || {
        let mut buf = Vec::new();
        if let Some(mut p) = err_pipe {
            let _ = p.read_to_end(&mut buf);
        }
        buf
    });
    let deadline = Instant::now() + limit;
    let mut killed = false;
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) => {}
            Err(err) => return Err(err.to_string()),
        }
        if Instant::now() >= deadline {
            unsafe {
                libc::killpg(pid as libc::pid_t, libc::SIGKILL);
            }
            let _ = child.wait();
            killed = true;
            break None;
        }
        std::thread::sleep(Duration::from_millis(80));
    };
    let out = out_thread.join().unwrap_or_default();
    let err = err_thread.join().unwrap_or_default();
    Ok(MacRan {
        pid,
        code: status.and_then(|s| s.code()),
        killed,
        out: String::from_utf8_lossy(&out).into_owned(),
        err: String::from_utf8_lossy(&err).into_owned(),
    })
}

/// macOS: исполнить подписанную просьбу самой оболочкой и собрать квитанцию
/// той же формы, что отдаёт служба на Windows (`BrokerReceipt`).
#[cfg(target_os = "macos")]
fn mac_broker_run(prepared: &MacPrepared) -> BrokerRun {
    let wish = &prepared.wish;
    let at = local_stamp();
    let started = Instant::now();
    let receipt = |ok: bool, code: Option<i32>, pid: Option<u32>, out: &str, err: &str, note: String| BrokerReceipt {
        id: wish.id.clone(),
        ok,
        op: wish.op.as_str().to_string(),
        code,
        pid,
        out: broker_cut(out),
        err: broker_cut(err),
        ms: started.elapsed().as_millis() as u64,
        at: at.clone(),
        why: wish.why.clone(),
        note,
    };
    match wish.op {
        BrokerOp::Ping => BrokerRun::Done(receipt(
            true,
            Some(0),
            None,
            "",
            "",
            "брокер здесь — само окно: оно живо, просьбы подписывает владелец, пароль администратора \
             для exec спрашивает система"
                .to_string(),
        )),
        // До вопроса владельцу сюда не доходит (`mac_wish_check`); на всякий
        // случай ответ — тот же отказ, а не паника.
        BrokerOp::Firewall => BrokerRun::Refused("на macOS брандмауэр спрашивает сам".to_string()),
        BrokerOp::SpawnInteractive => {
            // Та же сверка, что у `exec`, только своими руками: диалога пароля
            // здесь нет, но окно подтверждения есть, и между «Да» и запуском
            // файл по подписанному пути точно так же мог стать другим.
            if let Some(hex) = &prepared.digest {
                match std::fs::read(&wish.cmd) {
                    Ok(bytes) if &mac_svc_sha256(&bytes) == hex => {}
                    Ok(_) => {
                        return BrokerRun::Refused(format!(
                            "файл «{}» подменили после «Да» — отпечаток не сошёлся, не исполняю",
                            wish.cmd
                        ))
                    }
                    Err(e) => {
                        return BrokerRun::Failed(format!(
                            "файл «{}» не перечитался перед запуском ({e}) — не исполняю",
                            wish.cmd
                        ))
                    }
                }
            }
            let mut cmd = Command::new(&wish.cmd);
            cmd.args(&wish.args).current_dir("/");
            match mac_run_group(&mut cmd, Duration::from_secs(wish.timeout_sec.max(1))) {
                Err(err) => BrokerRun::Failed(err),
                Ok(ran) if ran.killed => BrokerRun::Done(receipt(
                    false,
                    None,
                    Some(ran.pid),
                    &ran.out,
                    &ran.err,
                    format!("убит по таймауту ({} с)", wish.timeout_sec),
                )),
                Ok(ran) => {
                    let note = if ran.code.is_none() { "процесс завершён сигналом".to_string() } else { String::new() };
                    BrokerRun::Done(receipt(true, ran.code, Some(ran.pid), &ran.out, &ran.err, note))
                }
            }
        }
        BrokerOp::Exec => {
            // Подсказка в диалоге пароля — «зачем» словами агента И САМА
            // КОМАНДА: это второй экран и вторая подпись, и подписывать её
            // вслепую владельцу больше не предлагают.
            let prompt = mac_password_prompt(
                product_ui(),
                &wish.why,
                &broker_shown_command(&wish.cmd, &wish.args),
            );
            let script =
                mac_admin_script(&wish.cmd, &wish.args, &prompt, prepared.digest.as_deref());
            let mut cmd = Command::new("/usr/bin/osascript");
            cmd.arg("-e").arg(&script).current_dir("/");
            let limit = Duration::from_secs(wish.timeout_sec.saturating_add(MAC_PASSWORD_GRACE_SEC));
            match mac_run_group(&mut cmd, limit) {
                Err(err) => BrokerRun::Failed(format!("osascript не запустился: {err}")),
                Ok(ran) if ran.killed => BrokerRun::Done(receipt(
                    false,
                    None,
                    None,
                    &ran.out,
                    &ran.err,
                    format!(
                        "не дождался за {} с — диалог закрыт; команда под администратором, если \
                         успела начаться, могла остаться работать",
                        limit.as_secs()
                    ),
                )),
                Ok(ran) if ran.code != Some(0) => {
                    let said = ran.err.trim();
                    // «User canceled (-128)» — владелец закрыл диалог пароля: это его
                    // отказ, а не поломка. Всё остальное (неверный пароль трижды,
                    // сломанный osascript) — «не вышло» словами системы.
                    if said.contains("-128") {
                        return BrokerRun::Refused("владелец отменил диалог пароля".to_string());
                    }
                    BrokerRun::Done(receipt(
                        false,
                        None,
                        None,
                        &ran.out,
                        &ran.err,
                        if said.is_empty() {
                            format!("система не выполнила команду (osascript вернул код {:?})", ran.code)
                        } else {
                            format!("система не выполнила команду: {said}")
                        },
                    ))
                }
                Ok(ran) => {
                    let (out, code) = mac_split_code(&ran.out);
                    let note = if code.is_none() {
                        "код возврата не пришёл — в выводе нет метки".to_string()
                    } else {
                        "вывод и ошибки слиты в один поток: так отдаёт osascript под администратором".to_string()
                    };
                    BrokerRun::Done(receipt(true, code, None, &out, "", note))
                }
            }
        }
    }
}

/// Сторож просьб. Отдельный поток: окно подтверждения блокирует того, кто его
/// показал, и на главном потоке это заморозило бы всю программу.
fn watch_broker_wishes(tree: PathBuf) {
    std::thread::spawn(move || {
        let _ = BROKER_WATCH_SINCE.set(local_stamp());
        let asks = broker_asks_path(&tree);
        // Файл ответов переписывается сразу: харнесс должен видеть, что
        // слушатель есть, ещё до первой просьбы.
        broker_answers_save(&tree, &broker_answers_load(&tree));
        let mut seen: Option<(u64, std::time::SystemTime)> = None;
        loop {
            std::thread::sleep(Duration::from_secs(2));
            let Ok(meta) = std::fs::metadata(&asks) else {
                continue;
            };
            let stamp = (meta.len(), meta.modified().unwrap_or(std::time::UNIX_EPOCH));
            if seen == Some(stamp) {
                continue;
            }
            let Ok(raw) = std::fs::read_to_string(&asks) else {
                continue;
            };
            let at = meta
                .modified()
                .ok()
                .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
                .map(|d| d.as_secs())
                .unwrap_or_else(now_unix);
            // Стоп-кадр запоминаем ТОЛЬКО когда ответили на всё: иначе просьба,
            // не попавшая в потолок окон за проход, не вернулась бы никогда.
            if broker_pass(&tree, &raw, at) {
                seen = Some(stamp);
            }
        }
    });
}

/// Живая проверка адреса и ключа — тем же кодом, что установщик
/// (`common/model_probe.rs`): 200 без списка моделей — не «ok», как отвечал
/// прежний вариант окна на неверный ключ z.ai.
#[tauri::command]
async fn probe_model(base_url: String, key: String, framework: Option<String>) -> serde_json::Value {
    let result = tauri::async_runtime::spawn_blocking(move || {
        probe_model_blocking(&base_url, &key, framework.as_deref().unwrap_or(""))
    })
    .await
    .unwrap_or((false, "проверка не выполнилась".to_string(), Vec::new()));
    serde_json::json!({ "ok": result.0, "note": result.1, "models": result.2 })
}

/// Windows показывает уведомления только от известного ей приложения (AUMID).
/// Установленному через ярлык Start-меню это даёт установщик; переносной сборке
/// достаточно записи в HKCU — она и делается здесь, без прав администратора.
#[cfg(windows)]
fn register_toast_identity(identifier: &str, name: &str, icon: Option<&Path>) {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let path = format!("Software\\Classes\\AppUserModelId\\{identifier}");
    if let Ok((key, _)) = hkcu.create_subkey(path) {
        let _ = key.set_value("DisplayName", &name);
        // Значок рядом с exe — по имени продукта (praxis.ico у варианта), helene.ico
        // остаётся запасным именем для старых поставок.
        let icon = icon.map(Path::to_path_buf).unwrap_or_else(|| {
            let own = exe_dir().join(format!("{}.ico", product_fs().to_lowercase()));
            if own.exists() { own } else { exe_dir().join("helene.ico") }
        });
        if icon.exists() {
            let _ = key.set_value("IconUri", &icon.to_string_lossy().into_owned());
        }
    }
}

#[cfg(not(windows))]
fn register_toast_identity(_identifier: &str, _name: &str, _icon: Option<&Path>) {}

/// Ярлык в меню «Пуск» с AppUserModel.ID — то, без чего Windows молча
/// выбрасывает уведомления непакованного exe (проверено 02.09: запись в HKCU
/// одна не помогает, ярлык — помогает). Установщик создаёт ярлык сам; здесь
/// он чинится, если его нет: скрипт вшит в exe и выполняется без окна.
#[cfg(windows)]
fn ensure_start_menu_shortcut(identifier: &str, name: &str, icon: Option<&Path>) {
    // Папку меню «Пуск» спрашиваем у Windows, а не склеиваем из %APPDATA%:
    // при перенаправлении папок политикой склейка промахивалась молча.
    let Some(programs) = programs_dir() else { return };
    if programs.join(format!("{name}.lnk")).exists() || programs.join(format!("{}.lnk", product_fs())).exists() {
        return;
    }
    let Ok(exe) = std::env::current_exe() else { return };
    // Имя с pid: установщик пишет скрипт по тому же пути в %TEMP%, и общий
    // файл, исполняемый с -ExecutionPolicy Bypass, — TOCTOU по построению.
    let script = std::env::temp_dir().join(format!(
        "helene-start-menu-shortcut-{}.ps1",
        std::process::id()
    ));
    if std::fs::write(&script, include_str!("../resources/start-menu-shortcut.ps1")).is_err() {
        log_line("ярлык меню «Пуск»: не записался скрипт");
        return;
    }
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(&script)
        .arg("-Exe")
        .arg(&exe)
        .arg("-Aumid")
        .arg(identifier)
        .arg("-Name")
        .arg(name);
    if let Some(icon) = icon {
        cmd.arg("-Icon").arg(icon);
    }
    let result = run_hidden_for(&mut cmd, Duration::from_secs(60));
    let _ = std::fs::remove_file(&script);
    match result {
        // Успех скрипта — ещё не ярлык: раньше в журнал уходила расписка
        // «ярлык создан», даже если файла по этому пути не появлялось.
        Ok(out) if out.status.success() => {
            if programs.join(format!("{name}.lnk")).exists() {
                log_line("ярлык меню «Пуск» создан (уведомления)");
            } else {
                log_line(&format!(
                    "ярлык меню «Пуск»: скрипт отчитался, а файла в {} нет — уведомлений может не быть",
                    programs.display()
                ));
            }
        }
        Ok(out) => log_line(&format!(
            "ярлык меню «Пуск» не создался: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        )),
        Err(err) => log_line(&format!("ярлык меню «Пуск»: powershell не отработал: {err}")),
    }
}

#[cfg(not(windows))]
fn ensure_start_menu_shortcut(_identifier: &str, _name: &str, _icon: Option<&Path>) {}

/// Слово агента, когда окно не перед глазами, — уведомлением.
///
/// Читаем хвост архива комнаты окна (его пишет транспорт руннера) и показываем
/// только исходящие строки, только пока окно скрыто или не в фокусе. Старт — с
/// текущего конца файла: прошлые реплики уведомлениями не становятся.
///
/// `show_text` — показывать ли саму реплику (helene.json: notifications.text).
/// Windows держит уведомления в Центре уведомлений и по умолчанию показывает
/// их на экране блокировки: с выключенным тумблером приходит только «новое
/// сообщение», без текста разговора.
fn watch_outbound(app: tauri::AppHandle, tree: PathBuf, agent: String, show_text: bool) {
    std::thread::spawn(move || {
        let archive = tree
            .join("memory")
            .join("groups")
            .join(format!("{WINDOW_ROOM}.jsonl"));
        let mut offset: u64 = std::fs::metadata(&archive).map(|m| m.len()).unwrap_or(0);
        let mut last_body = String::new();
        // Было `Instant::now() - Duration::from_secs(60)`: на Windows ноль
        // Instant — момент загрузки машины, и при старте по автозапуску (аптайм
        // меньше минуты) вычитание паниковало. Поток тихо умирал, и уведомлений
        // не было весь сеанс.
        let mut last_toast: Option<Instant> = None;
        loop {
            std::thread::sleep(Duration::from_secs(2));
            let len = match std::fs::metadata(&archive) {
                Ok(m) => m.len(),
                Err(_) => continue,
            };
            if len < offset {
                offset = 0; // файл пересоздали
            }
            if len == offset {
                continue;
            }
            let mut file = match std::fs::File::open(&archive) {
                Ok(f) => f,
                Err(_) => continue,
            };
            if file.seek(SeekFrom::Start(offset)).is_err() {
                continue;
            }
            let mut buf = String::new();
            if file.read_to_string(&mut buf).is_err() {
                continue; // строка ещё дописывается — придём через два секунды
            }
            let complete = match buf.rfind('\n') {
                Some(i) => &buf[..=i],
                None => continue,
            };
            offset += complete.len() as u64;
            let in_view = app
                .get_webview_window("main")
                .map(|w| w.is_visible().unwrap_or(false) && w.is_focused().unwrap_or(false))
                .unwrap_or(false);
            if in_view {
                continue;
            }
            // Уведомление — только слово агента, и не чаще одного за пятнадцать
            // секунд: служебные пометки («ход не состоялся», ⚠) и очереди из
            // нескольких строк подряд превращали трей в дребезг (слово владельца).
            let mut fresh: Option<String> = None;
            for line in complete.lines() {
                let Ok(row) = serde_json::from_str::<serde_json::Value>(line) else {
                    continue;
                };
                if !row.get("outgoing").and_then(|v| v.as_bool()).unwrap_or(false) {
                    continue;
                }
                let text = row.get("text").and_then(|v| v.as_str()).unwrap_or("").trim();
                if text.is_empty() || text.starts_with('⚠') || text.contains("ход не состоялся") {
                    continue;
                }
                fresh = Some(text.chars().take(240).collect());
            }
            let Some(body) = fresh else { continue };
            let too_soon = last_toast.is_some_and(|t| t.elapsed() < Duration::from_secs(15));
            if body == last_body || too_soon {
                continue;
            }
            last_body = body.clone();
            last_toast = Some(Instant::now());
            if show_text {
                toast(&agent, &body);
            } else {
                toast(&agent, "Новое сообщение — открой окно, чтобы прочитать.");
            }
        }
    });
}

/// Ненастроенный продукт встречает установщик — но только если он ЖИВ.
/// Раньше запуск считался удачным по факту CreateProcess: умерший через
/// двести миллисекунд установщик (нет WebView2, паника) выглядел как успех, и
/// владелец, кликнув по значку, не видел ничего вообще.
fn hand_over_to_setup(base: &Path) -> bool {
    let setup = setup_binary(base);
    let label = setup_label();
    if !setup.exists() {
        log_line(&format!("продукт не настроен, а {label} рядом нет — открываю окно как есть"));
        return false;
    }
    // macOS: бинарь мастера запускается ПРЯМО, а не через `open -W`: здесь нужен
    // pid, чтобы отличить «поднялся» от «умер сразу» (ниже), а `open` его не
    // отдаёт. Цена — окно мастера не выводится на передний план системой, как
    // при запуске бандла; сам мастер активирует своё окно (задача B).
    let mut child = match Command::new(&setup).current_dir(base).spawn() {
        Ok(c) => c,
        Err(err) => {
            log_line(&format!("{label} не запустился: {err} — открываю окно"));
            return false;
        }
    };
    // Две секунды — достаточно, чтобы отличить «поднялся» от «умер сразу».
    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline {
        match child.try_wait() {
            Ok(Some(status)) => {
                log_line(&format!("{label} вышел сразу ({status}) — открываю окно"));
                // Про WebView2 — только на Windows: на macOS вебвью системный.
                let why = if cfg!(windows) {
                    "\n\nЧастая причина — нет Microsoft Edge WebView2 Runtime."
                } else {
                    ""
                };
                message_box_async(
                    format!("{}: установка не открылась", product_ui()),
                    format!("Помощник установки закрылся сразу после запуска.{why}\nПодробности — в helene.log рядом с программой."),
                );
                return false;
            }
            Ok(None) => {}
            Err(_) => break,
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    true
}

/// Окно открылось, но агента этого окна нет: порт держит ЧУЖАЯ установка.
///
/// Здесь важны две вещи сразу. Первая — `base` пустой: ни один запрос окна не
/// уходит на чужой порт, чужие переписка, память и конституция остаются
/// чужими. Вторая — владелец должен увидеть, ЧТО произошло, а не пустой экран
/// «нет связи»: поверх страницы рисуется объяснение с адресом чужого дерева.
///
/// Рисуем из init-скрипта, а не из `app/`: страница окна — общая с браузером и
/// телефоном, а это состояние знает только оболочка.
/// Адрес канала и список агентов — тем же init-скриптом, что и раньше.
///
/// Список нужен окну, чтобы нарисовать переключатель, и он ЖИВОЙ: считается в
/// момент открытия окна, а не берётся из конфига. Ключи каналов соседей сюда
/// не кладём — окно ходит только к своему, а переключение поднимает окно заново
/// с ключом того, к кому переключились.
fn channel_script(base: &Path, port: u16, key: &str, agent: &str, id: &str) -> String {
    let roster: Vec<serde_json::Value> = roster(base)
        .iter()
        .map(|a| {
            serde_json::json!({
                "id": a.id, "name": a.name, "port": a.port,
                "enabled": a.enabled, "conflict": a.conflict, "base": a.base,
            })
        })
        .collect();
    format!(
        "window.DESK_CONFIG_OVERRIDE = {};",
        serde_json::json!({
            "base": format!("http://127.0.0.1:{port}"),
            "key": key,
            "agent": agent,
            "agent_id": id,
            "agents": roster,
            "product": product_ui(),
        })
    )
}

fn blocked_script(port: u16, theirs: Option<&Path>, agent: &str) -> String {
    let whose = match theirs {
        Some(p) => format!("Её данные лежат здесь: {}", p.display()),
        None => "Что это за программа — выяснить не удалось: на /api/home она отвечает не по-нашему.".to_string(),
    };
    let title = format!("Порт {port} держит другая установка Hélène");
    let body = format!(
        "{whose}\n\nАгент этого окна не поднялся, и к чужому дереву окно не подключается: чужая переписка, чужая память и чужая конституция остаются чужими.\n\nЗакрой ту копию и перезапусти Hélène — или смени port в {CONFIG_NAME} рядом с программой."
    );
    let cfg = serde_json::json!({"base": "", "key": "", "agent": agent});
    let t = serde_json::Value::String(title);
    let b = serde_json::Value::String(body);
    format!(
        r#"window.DESK_CONFIG_OVERRIDE = {cfg};
(function () {{
  var title = {t}, body = {b};
  function draw() {{
    if (document.getElementById("helene-blocked")) return;
    var host = document.body || document.documentElement;
    if (!host) return;
    var back = document.createElement("div");
    back.id = "helene-blocked";
    back.setAttribute("style", "position:fixed;inset:0;z-index:2147483647;background:#15171b;color:#e9e9ec;display:flex;align-items:center;justify-content:center;padding:48px;font:16px/1.6 'Segoe UI',system-ui,sans-serif");
    var box = document.createElement("div");
    box.setAttribute("style", "max-width:640px");
    var h = document.createElement("div");
    h.textContent = title;
    h.setAttribute("style", "font-size:22px;font-weight:600;margin:0 0 16px");
    var p = document.createElement("div");
    p.textContent = body;
    p.setAttribute("style", "white-space:pre-wrap;opacity:.85;margin:0 0 22px");
    box.appendChild(h);
    box.appendChild(p);
    var api = window.__TAURI_INTERNALS__;
    if (api && typeof api.invoke === "function") {{
      var btn = document.createElement("button");
      btn.textContent = "Перезапустить Hélène";
      btn.setAttribute("style", "font:inherit;padding:10px 18px;border-radius:8px;border:1px solid #3b4049;background:#242830;color:inherit;cursor:pointer");
      btn.onclick = function () {{ try {{ api.invoke("restart_self"); }} catch (e) {{}} }};
      box.appendChild(btn);
    }}
    back.appendChild(box);
    host.appendChild(back);
  }}
  if (document.readyState === "loading") {{
    document.addEventListener("DOMContentLoaded", draw);
  }} else {{
    draw();
  }}
  setTimeout(draw, 1500);
}})();"#
    )
}

fn main() {
    let base = install_root();
    install_panic_hook();
    // Контекст сборки — один раз и до первого слова в журнале: из него имя продукта.
    let context = tauri::generate_context!();
    init_product(context.config());
    log_line(&format!("старт {} {} ({})", product_fs(), env!("CARGO_PKG_VERSION"), toast_id()));
    let read = read_config(&base.join(CONFIG_NAME));
    let broken = matches!(read, ConfigRead::Broken(_));
    // Битый конфиг НЕ ведёт к установщику: тот проходил по кругу и переписывал
    // helene.json и конституцию с нуля из-за лишней запятой или BOM.
    if let ConfigRead::Broken(why) = &read {
        let path = base.join(CONFIG_NAME);
        log_line(&format!("{CONFIG_NAME} не разобрался ({why}) — установщик НЕ зову, открываю окно"));
        message_box_async(
            format!("{}: файл настроек повреждён", product_ui()),
            format!(
                "{}\n\n{why}\n\nПочини файл или переустанови программу. Пока он не читается, агент не поднимется, а настройки и конституция остаются на месте.",
                path.display()
            ),
        );
    }
    let cfg: Option<serde_json::Value> = match read {
        ConfigRead::Ok(v) => Some(v),
        _ => None,
    };
    let agent = agent_name(cfg.as_ref());
    // К установщику — когда продукт не настроен, но файл при этом ЦЕЛ
    // (его нет вовсе или он читается): поставка приезжает с шаблоном
    // helene.json, и первый запуск обязан открывать визард.
    if !broken && unconfigured(&cfg) && hand_over_to_setup(&base) {
        return;
    }
    let configured = !unconfigured(&cfg);

    let mut children: Vec<Managed> = Vec::new();
    let mut init_script = String::new();
    let mut tree: Option<PathBuf> = None;
    let mut plans: Vec<SpawnPlan> = Vec::new();
    let notify_text = cfg
        .as_ref()
        .and_then(|c| c.get("notifications"))
        .and_then(|n| n.get("text"))
        .and_then(|v| v.as_bool())
        .unwrap_or(true);
    // Кого показывать: `--agent <id>` из ярлыка, иначе корневой. Неизвестный id
    // не подменяется корневым молча — ярлык на удалённого агента обязан сказать
    // об этом, иначе владелец пишет не тому.
    let wanted = arg_after("--agent");
    let mut current = find_agent(&base, wanted.as_deref().unwrap_or(BASE_AGENT_ID))
        .or_else(|| find_agent(&base, BASE_AGENT_ID));
    if let Some(said) = wanted.as_deref() {
        if find_agent(&base, said).is_none() {
            log_line(&format!("агента «{said}» в этой установке нет — открываю корневого"));
            message_box_async(
                format!("{}: такого агента нет", product_ui()),
                format!("Ярлык просит агента «{said}», но в этой установке его нет. Открываю того, кто здесь первый."),
            );
        }
    }
    let agent = current.as_ref().map(|a| a.name.clone()).unwrap_or(agent);
    // Без настройки харнесс не поднимаем: без ключа модели дети бесполезны.
    if let Some(cfg) = cfg.as_ref().filter(|_| configured) {
        // Настройки ТЕКУЩЕГО агента: у корневого это тот же файл, у соседа —
        // его собственный. Всё, что ниже смотрит в `cfg` (режим, уведомления,
        // адрес удалённого), обязано смотреть в его файл, а не в чужой.
        let own = current
            .as_ref()
            .filter(|a| !a.base)
            .map(|a| agent_config(&a.config));
        let cfg = own.as_ref().unwrap_or(cfg);
        match cfg.get("mode").and_then(|v| v.as_str()).unwrap_or("") {
            "local" => {
                plans = build_plans(&base);
                // Секрет трубы заводится ДО подъёма детей: он уходит им в
                // окружение и он же предъявляется трубе из окна. У каждого
                // агента он свой и лежит в его дереве.
                let here = current.as_ref().map(|a| a.id.clone()).unwrap_or_default();
                let mine = plans.iter().find(|p| p.agent == here).cloned();
                if let Some(p) = &mine {
                    set_desk_token(&p.token);
                }
                // ⚠ 17.09. Окно предъявляет каналу ключ из этого места. Пустой ключ = окно
                // получает 403 на КАЖДЫЙ запрос и вечно показывает «не на связи», при
                // живом и здоровом харнессе. Молчать об этом нельзя: снаружи это выглядит
                // как «агент не поднялся», и искать начинают не там.
                if desk_token().is_empty() {
                    log_line(&format!(
                        "ключ канала для окна ПУСТ (агент «{here}», планов {}) — окно получит \
                         403 на каждый запрос; это не сбой агента, а потерянный секрет",
                        plans.len()
                    ));
                } else {
                    log_line(&format!("ключ канала для окна задан (агент «{here}»)"));
                }
                let port = mine.as_ref().map(|p| p.port)
                    .or_else(|| current.as_ref().map(|a| a.port))
                    .unwrap_or(DESK_PORT);
                // Пустой список детей — не провал и не приговор: харнесс уже
                // живёт (служба или другое окно) либо порт занят; надзор
                // попробует снова, когда порт освободится.
                let mut verdict = None;
                for plan in &plans {
                    let (kids, said) = start_children(plan, true);
                    children.extend(kids);
                    if plan.agent == here {
                        verdict = said;
                    }
                }
                tree = mine.as_ref().map(|p| p.tree.clone())
                    .or_else(|| current.as_ref().map(|a| a.tree.clone()));
                if let Some(cur) = current.as_mut() {
                    cur.port = port;
                }
                match verdict {
                    // ЧУЖАЯ установка на нашем порту. Адрес вебвью на неё не
                    // строим вовсе: иначе окно показывало бы чужую переписку,
                    // писало в чужой desk_inbox и правило чужой soul/SOUL.md
                    // через /api/md — ровно туда, куда только что запретили.
                    Some(Verdict::Foreign(theirs)) => {
                        BLOCKED_BY_FOREIGN.store(true, std::sync::atomic::Ordering::Relaxed);
                        init_script = blocked_script(port, theirs.as_deref(), &agent);
                    }
                    _ => {
                        init_script = channel_script(&base, port, &desk_token(), &agent, &here);
                    }
                }
            }
            "remote" => {
                let base_url = cfg.get("base").and_then(|v| v.as_str()).unwrap_or("");
                let key = cfg.get("key").and_then(|v| v.as_str()).unwrap_or("");
                if !base_url.is_empty() {
                    init_script = format!(
                        "window.DESK_CONFIG_OVERRIDE = {};",
                        serde_json::json!({"base": base_url, "key": key, "agent": agent, "product": product_ui()})
                    );
                } else {
                    // Praxis распакован, но адрес сервера ещё не вписан. Раньше окно
                    // открывалось «как есть» и билось об пустой адрес ошибками связи;
                    // установщика у варианта нет по замыслу, поэтому первый запуск
                    // спрашивает адрес и ключ сам (`needs_remote` → карточка в окне,
                    // `config_save` + `restart_self` — те же команды, что у настроек).
                    init_script = format!(
                        "window.DESK_CONFIG_OVERRIDE = {};",
                        serde_json::json!({"base": "", "key": "", "agent": agent,
                                           "product": product_ui(), "needs_remote": true})
                    );
                }
            }
            _ => {}
        }
    }

    // Кто сейчас в окне. Не `State<T>` Tauri, а глобал: переключатель агентов
    // меняет это на живом процессе, а состояние Tauri неизменяемо по замыслу.
    set_current(CurrentAgent {
        id: current.as_ref().map(|a| a.id.clone()).unwrap_or_else(|| BASE_AGENT_ID.into()),
        name: agent.clone(),
        tree: tree.clone(),
        config: current
            .as_ref()
            .map(|a| a.config.clone())
            .unwrap_or_else(|| install_root().join(CONFIG_NAME)),
    });

    let builder = tauri::Builder::default()
        // Один экземпляр — ПЕРВЫМ плагином: повторный запуск не доходит до
        // окна, а поднимает и фокусирует уже живое.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            show_main(app);
        }));
    let built = builder
        // Память окна: размер, положение и «развёрнуто/нет» переживают перезапуск.
        .plugin(tauri_plugin_window_state::Builder::default().build())
        .manage(LocalHarness {
            children: Mutex::new(children),
            stopping: std::sync::atomic::AtomicBool::new(false),
            plans: Mutex::new(plans),
        })
        // Статика окна — с диска (app/static), см. serve_static.
        .register_uri_scheme_protocol("helene", |ctx, request| serve_static(ctx, request))
        .invoke_handler(tauri::generate_handler![
            config_save,
            restart_self,
            install_service,
            remove_service,
            service_state,
            config_load,
            open_path,
            autostart_get,
            autostart_set,
            probe_model,
            lan_ip,
            tailscale_ip,
            firewall_allow,
            firewall_clear,
            admin_state,
            relay_login,
            relay_status,
            relay_account,
            notify,
            app_info,
            update_check,
            update_download,
            update_install,
            logs_bundle,
            reveal_path,
            open_privacy_pane,
            telegram_account,
            voice_fetch,
            carry_export,
            agents_list,
            switch_agent,
            agent_add
        ])
        .setup(move |app| {
            // Продукт зовётся своим именем: заголовок, ярлык, значок, уведомления —
            // Hélène; имя агента — только там, где говорит агент (слово владельца).
            register_toast_identity(toast_id(), product_ui(), None);
            ensure_start_menu_shortcut(toast_id(), product_fs(), None);
            // Каталог значков выбирает сборка (build.rs → HELENE_ICON_DIR): icons/ у
            // Hélène, icons-praxis/ у Praxis. Тот же каталог даёт значок exe
            // через bundle.icon в TAURI_CONFIG.
            let window_icon = tauri::image::Image::from_bytes(include_bytes!(concat!(
                "../", env!("HELENE_ICON_DIR"), "/icon.png"
            )))?;
            #[cfg(not(target_os = "macos"))]
            let tray_icon = tauri::image::Image::from_bytes(include_bytes!(concat!(
                "../", env!("HELENE_ICON_DIR"), "/32x32.png"
            )))?;
            // macOS: строка меню рисует значок сама (template — чёрный силуэт с
            // альфой, система красит его под светлую/тёмную тему). 44×44 —
            // retina-вариант, до 18 pt его ужимает NSImage.
            #[cfg(target_os = "macos")]
            let tray_icon = tauri::image::Image::from_bytes(include_bytes!(concat!(
                "../", env!("HELENE_ICON_DIR"), "/tray-template@2x.png"
            )))?;
            debug_assert_eq!(app.config().identifier, toast_id());
            open_window(app, &init_script, Some(window_icon))?;
            // Передний план: Windows отдаёт его неохотно, когда запустивший нас
            // процесс (установщик) уже вышел, — окно появлялось позади других,
            // и казалось, что не открылось. Короткий «поверх всех» лечит.
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(Duration::from_millis(500));
                // Если владелец за эти полсекунды успел убрать окно в трей —
                // не вытаскивать его обратно поверх всего, чем он занят.
                if !HIDDEN_BY_OWNER.load(std::sync::atomic::Ordering::Relaxed) {
                    show_main(&handle);
                }
            });
            let handle = app.handle().clone();
            std::thread::spawn(move || watch_children(handle));
            // macOS: тело под службой поднимает ОКНО — у движка вне графической
            // сессии его поднять негде (см. watch_service_body). Поток идёт
            // всегда: служба ставится и снимается на живой системе, и решать
            // это раз при старте значило бы требовать перезапуск окна.
            #[cfg(target_os = "macos")]
            {
                let handle = app.handle().clone();
                let tree = current_tree();
                let config = current_config_path();
                std::thread::spawn(move || watch_service_body(handle, tree, config));
            }
            // Раз в сутки — есть ли версия новее; только уведомление.
            std::thread::spawn(update_autocheck);

            // Трей: закрытие окна прячет его, харнесс-дети живут дальше;
            // настоящий выход — только из меню трея.
            use tauri::menu::{Menu, MenuItem, Submenu};
            use tauri::tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent};
            let open = MenuItem::with_id(app, "open", format!("Открыть {}", product_ui()), true, None::<&str>)?;
            let quit = MenuItem::with_id(
                app,
                "quit",
                "Выход (остановить агентов)",
                true,
                None::<&str>,
            )?;
            // Агенты в трее — только когда их больше одного: у единственного
            // подменю из одной строки было бы шумом, а не выбором.
            let here = roster(&base);
            let menu = if here.len() > 1 {
                let mut rows: Vec<MenuItem<tauri::Wry>> = Vec::new();
                for a in &here {
                    let mark = if a.id == current_id() { "• " } else { "   " };
                    let tail = if !a.conflict.is_empty() {
                        format!("  — спорит за порт с «{}»", a.conflict)
                    } else if !a.enabled {
                        "  — снят".to_string()
                    } else {
                        String::new()
                    };
                    rows.push(MenuItem::with_id(
                        app,
                        format!("agent:{}", a.id),
                        format!("{mark}{}{tail}", a.name),
                        a.conflict.is_empty(),
                        None::<&str>,
                    )?);
                }
                let refs: Vec<&dyn tauri::menu::IsMenuItem<tauri::Wry>> =
                    rows.iter().map(|r| r as &dyn tauri::menu::IsMenuItem<tauri::Wry>).collect();
                let agents_menu = Submenu::with_items(app, "Агенты", true, &refs)?;
                Menu::with_items(app, &[&open, &agents_menu, &quit])?
            } else {
                Menu::with_items(app, &[&open, &quit])?
            };
            let tray = TrayIconBuilder::with_id("frame")
                .icon(tray_icon)
                .tooltip(product_ui())
                .menu(&menu)
                .show_menu_on_left_click(false);
            #[cfg(target_os = "macos")]
            let tray = tray.icon_as_template(true);
            tray.on_menu_event(|app, event| match event.id.as_ref() {
                    "open" => show_main(app),
                    "quit" => {
                        let state = app.state::<LocalHarness>();
                        kill_children(&state);
                        app.exit(0);
                    }
                    other => {
                        if let Some(id) = other.strip_prefix("agent:") {
                            // Из трея переключаемся так же, как из окна: одна
                            // дорога, один журнал, одни и те же отказы.
                            if let Err(err) = switch_agent(app.clone(), id.to_string()) {
                                log_line(&format!("переключение на «{id}» не вышло: {err}"));
                                toast(product_ui(), &err);
                            }
                        }
                    }
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_main(tray.app_handle());
                    }
                })
                .build(app)?;

            // Слово агента, когда окно не перед глазами, — уведомлением.
            // ⚠ Смотрим за КАЖДЫМ поднятым агентом, а не только за тем, кто в
            // окне: у второго агента свой Telegram и свои люди, и его слово,
            // пришедшее в закрытую вкладку, иначе не заметил бы никто.
            // Уведомление подписано его именем — оно же стоит в заголовке.
            if tree.is_some() {
                for a in raisable(&base) {
                    watch_outbound(app.handle().clone(), a.tree.clone(), a.name.clone(), notify_text);
                    // …и его просьба к брокеру — окном подтверждения. Тоже не через
                    // вебвью: просьба приходит, когда владелец занят другим, а окно
                    // продукта в этот момент чаще всего в трее.
                    watch_broker_wishes(a.tree.clone());
                }
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            match event {
                // Закрыть окно ≠ убить организм: окно в трей, дети живут.
                tauri::WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    HIDDEN_BY_OWNER.store(true, std::sync::atomic::Ordering::Relaxed);
                    let _ = window.hide();
                    close_hint();
                }
                // Настоящая смерть окна (выход) — дети не остаются сиротами.
                // Кроме переключения агента: там окно сносится нарочно, а
                // агенты установки продолжают жить (см. SWITCHING).
                tauri::WindowEvent::Destroyed => {
                    if SWITCHING.load(std::sync::atomic::Ordering::Relaxed) {
                        return;
                    }
                    let state = window.app_handle().state::<LocalHarness>();
                    kill_children(&state);
                }
                _ => {}
            }
        })
        .build(context);
    // ⚠⚠ ТРЕТИЙ СЛОЙ ТОЙ ЖЕ ЛОВУШКИ, пойманный живой пробой 11.09. Tauri
    // выходит из программы, когда закрылось последнее окно, — а при
    // переключении агента окон на миг нет вовсе: старое снесено, новое ещё
    // строится. Программа выходила молча, вместе со ВСЕМИ агентами установки.
    // Поэтому выход разбирается вручную: пока идёт переключение, «окон не
    // осталось» не значит «владелец закончил».
    let run = match built {
        Ok(app) => {
            app.run(|app, event| {
                // macOS: клик по значку в Dock, когда окно спрятано в строку меню,
                // — это «открыть», как левый клик по значку трея.
                #[cfg(target_os = "macos")]
                if let tauri::RunEvent::Reopen { .. } = &event {
                    show_main(app);
                }
                // ⚠ ⌘Q. На macOS Tauri сам ставит меню с «Quit»: это `terminate:`,
                // а он даёт `LoopDestroyed` → `RunEvent::Exit` БЕЗ `ExitRequested`
                // — и без этой ветки `kill_children` не звался бы вовсе. Движок и
                // канал ушли бы сторожем ppid, а `helene-relay` сторожа не имеет:
                // сирота на порту, и следующий запуск говорил бы «порт реле уже
                // занят — своё реле не поднимаю». `kill_children` гасит и
                // незавершённый `helene-relay login` (`relay_abort` внутри). Из
                // трея и после `app.exit(0)` дети уже мертвы — повтор безвреден.
                #[cfg(not(windows))]
                if let tauri::RunEvent::Exit = &event {
                    let state = app.state::<LocalHarness>();
                    kill_children(&state);
                }
                #[cfg(windows)]
                let _ = app;
                if let tauri::RunEvent::ExitRequested { code, api, .. } = &event {
                    // ⚠⚠ Выход — только по слову владельца («Выход» у значка
                    // часов, `app.exit(0)`, и тогда код назван). Исчезнувшее
                    // окно выходом НЕ считается: при переключении агента окон
                    // на миг нет вовсе, и Tauri гасил всю программу — вместе со
                    // всеми агентами установки. Флага «идёт переключение» здесь
                    // не хватило: просьба о выходе разбирается ПОЗЖЕ, чем
                    // строится новое окно, и к этому мигу флаг уже снят
                    // (поймано живой пробой 11.09, видно по журналу событий).
                    if code.is_none() {
                        api.prevent_exit();
                    }
                }
            });
            Ok(())
        }
        Err(err) => Err(err),
    };
    // Было .expect(): паника в exe без консоли гасила процесс молча — окно
    // просто не появлялось, и причины не было нигде.
    if let Err(err) = run {
        log_line(&format!("окно {} не поднялось: {err}", product_ui()));
        let hint = if webview2_present() {
            String::new()
        } else {
            "\n\nНа этой машине не найден Microsoft Edge WebView2 Runtime — без него окно не откроется. Поставь его с сайта Microsoft (Evergreen Runtime) и запусти снова.".to_string()
        };
        message_box(
            &format!("{} не открылась", product_ui()),
            &format!("{err}{hint}\n\nПодробности — в helene.log рядом с программой."),
        );
    }
}

/// Есть ли на машине WebView2 Runtime — без него окно Tauri не откроется, а
/// продукт нигде о нём не говорил: на Windows 10 LTSC и корпоративных образах
/// это самая частая причина «кликнул и ничего не произошло».
#[cfg(windows)]
fn webview2_present() -> bool {
    use winreg::enums::{HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE};
    use winreg::RegKey;
    const CLIENT: &str = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}";
    let paths = [
        format!("SOFTWARE\\WOW6432Node\\Microsoft\\EdgeUpdate\\Clients\\{CLIENT}"),
        format!("SOFTWARE\\Microsoft\\EdgeUpdate\\Clients\\{CLIENT}"),
    ];
    for root in [HKEY_LOCAL_MACHINE, HKEY_CURRENT_USER] {
        let hive = RegKey::predef(root);
        for path in &paths {
            if let Ok(key) = hive.open_subkey(path) {
                if let Ok(pv) = key.get_value::<String, _>("pv") {
                    if !pv.trim().is_empty() && pv != "0.0.0.0" {
                        return true;
                    }
                }
            }
        }
    }
    false
}

#[cfg(not(windows))]
fn webview2_present() -> bool {
    true
}

/// Первое закрытие окна: сказать, что агент жив и где его найти. Один раз —
/// отметка рядом с exe, чтобы не повторять очевидное.
fn close_hint() {
    let flag = install_root().join(".close-hint-shown");
    if flag.exists() {
        return;
    }
    let _ = std::fs::write(&flag, "1");
    // Где значок: у часов на Windows, в строке меню (и в Dock) на macOS.
    let place = if cfg!(target_os = "macos") { "значок в строке меню" } else { "значок у часов" };
    let body = format!(
        "Окно закрыто, {} продолжает работать. Открыть снова — {place}.",
        with_current("агент".to_string(), |c| c.name.clone())
    );
    toast(product_ui(), &body);
}

/// Есть ли уже запомненный размер окна.
///
/// Файл пишет `tauri_plugin_window_state` рядом с настройками программы. Имя
/// (`.window-state.json`) — его умолчание; мы его не задаём, значит и здесь
/// спрашиваем то же самое. Прочитать не удалось — считаем, что памяти нет:
/// «не знаю» тут безопаснее трактовать как первый запуск, иначе окно откроется
/// крошечным посреди экрана.
fn window_state_remembered<M: tauri::Manager<tauri::Wry>>(manager: &M) -> bool {
    manager
        .path()
        .app_config_dir()
        .map(|dir| dir.join(".window-state.json"))
        .map(|path| path.is_file())
        .unwrap_or(false)
}

/// Построить окно. Вынесено из `setup` целиком, потому что переключение агента
/// строит его ЗАНОВО: init-скрипт (адрес канала и ключ) задаётся только при
/// создании вебвью, и подменить его у живого окна нечем. Закрыть и открыть с
/// новым скриптом — честнее, чем держать в окне два адреса сразу.
/// ⚠⚠ Принимает ЛЮБОГО менеджера, и это несущее свойство, а не удобство.
/// В `setup` окно обязано строиться от самого `App`: собранное там из
/// `AppHandle`, оно уходит в очередь ещё не запущенного цикла событий и не
/// появляется НИКОГДА — процесс жив, дети подняты, журнал чист, окна нет.
/// Поймано живой пробой 11.09; из `switch_agent` (цикл уже крутится) годится
/// и `AppHandle`.
fn open_window<M: tauri::Manager<tauri::Wry>>(
    manager: &M,
    init_script: &str,
    icon: Option<tauri::image::Image<'_>>,
) -> Result<(), Box<dyn std::error::Error>> {
    let mut builder = tauri::WebviewWindowBuilder::new(
        manager,
        "main",
        // Свой протокол вместо вшитого `tauri://`: файлы берутся из
        // app/static на диске (serve_static). На Windows WebView2
        // видит зарегистрированную схему как http://<схема>.localhost.
        tauri::WebviewUrl::CustomProtocol({
            // ⚠ Ключ канала едет и в адресе — запасной ногой к скрипту инициализации,
            // который остаётся главным источником. Адресной строки у окна нет, в журнал
            // канала ключ не попадает (маскируется), а тем же `?key=` уже ходят проба
            // порта и телефон. (17.09 этим искали причину мёртвого окна; она оказалась
            // в кэше страницы — см. `serve_static`.)
            let key = desk_token();
            log_line(&format!(
                "окно открывается: ключ {} ({} знаков), скрипт инициализации {} знаков",
                if key.is_empty() { "ПУСТ" } else { "есть" }, key.len(), init_script.len()
            ));
            // Адрес своего протокола — по платформе: WebView2 (Windows) видит
            // зарегистрированную схему как `http://helene.localhost`, WKWebView
            // (macOS) и WebKitGTK — как `helene://localhost` (см. tauri::webview).
            let origin = if cfg!(windows) { "http://helene.localhost" } else { "helene://localhost" };
            let raw = if key.is_empty() {
                format!("{origin}/index.html")
            } else {
                format!("{origin}/index.html?key={key}")
            };
            tauri::Url::parse(&raw).expect("адрес окна")
        }),
    )
    .title(product_ui())
    .inner_size(1360.0, 860.0)
    .min_inner_size(900.0, 600.0)
    .center()
    .decorations(false)
    .shadow(true);
    // ⚠ «На весь экран» — только когда памяти о размере ещё НЕТ.
    //
    // Рядом живёт `tauri_plugin_window_state`: он помнит ширину, высоту, место
    // и «развёрнуто» в `%APPDATA%\<идентификатор>\.window-state.json`. Пока
    // здесь стояло безусловное `.maximized(true)`, на один вопрос отвечали
    // двое: код — «всегда во весь экран», память — «как поставил вчера».
    // Владелец менял величину окна, а она не держалась, и управлять ею было
    // нечем: экрана настроек для этого нет, а править json руками — не ответ.
    //
    // Первый запуск разворачивать правильно: пустое окно посреди большого
    // экрана — плохая встреча. Дальше решает память.
    if !window_state_remembered(manager) {
        builder = builder.maximized(true);
    }
    if let Some(icon) = icon {
        builder = builder.icon(icon)?;
    }
    if !init_script.is_empty() {
        builder = builder.initialization_script(init_script);
    }
    builder.build()?;
    Ok(())
}

fn show_main(app: &tauri::AppHandle) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.show();
        let _ = window.unminimize();
        let _ = window.set_focus();
        let _ = window.set_always_on_top(true);
        let _ = window.set_always_on_top(false);
    }
}

fn kill_children(state: &tauri::State<LocalHarness>) {
    state.stopping.store(true, std::sync::atomic::Ordering::Relaxed);
    if let Ok(mut guard) = state.children.lock() {
        for m in guard.iter_mut() {
            if let Some(child) = m.child.as_mut() {
                stop_child(child);
            }
        }
        guard.clear();
    };
    // Незавершённый вход в ChatGPT — тоже наш ребёнок: без этого
    // helene-relay.exe login переживал выход из трея и держал порт 1455.
    relay_abort();
    // И тело, поднятое окном под службой: движок службы переживёт выход из
    // окна, а тело обязано уйти вместе с окном — владелец закрыл программу.
    #[cfg(target_os = "macos")]
    stop_service_body();
}

/// Надзор: упавший ребёнок поднимается снова с растущей паузой; шестое падение
/// за десять минут — пауза на десять минут и честное уведомление, чтобы не
/// жечь машину петлёй.
///
/// Замок children берётся только на быстрые действия: раньше под ним шли
/// CreateProcess питона и показ уведомления, а тот же замок берёт выход из
/// трея — и меню не отвечало ровно тогда, когда владелец хотел выйти.
///
/// Здесь же — вторая попытка поднять СВОЙ харнесс, когда своих детей нет
/// вовсе: порт был занят (служба, чужая программа, зомби прежней установки).
/// Раньше это состояние было необратимым до перезапуска exe.
fn watch_children(app: tauri::AppHandle) {
    use std::sync::atomic::Ordering;
    /// Что делать с ребёнком после осмотра — решение принимается под замком,
    /// а исполняется без него.
    enum Act {
        Spawn(ChildSpec),
        Report(String, &'static str),
        /// Ребёнок назвал причину кодом выхода: перезапускать нечего, надо
        /// говорить владельцу, что именно чинить.
        Halt(String, String),
    }
    let mut last_lift = Instant::now();
    loop {
        std::thread::sleep(Duration::from_secs(5));
        let state = app.state::<LocalHarness>();
        if state.stopping.load(Ordering::Relaxed) {
            return;
        }
        // 1) Осмотр под замком: только try_wait и учёт падений.
        let mut acts: Vec<(usize, Act)> = Vec::new();
        let logs: Vec<PathBuf>;
        // Чьих детей нет НИ ОДНОГО — по каждому агенту отдельно.
        let missing: Vec<String>;
        {
            let Ok(mut guard) = state.children.lock() else { continue };
            let alive: Vec<String> = guard.iter().map(|m| m.agent.clone()).collect();
            missing = state
                .plans
                .lock()
                .map(|plans| {
                    plans
                        .iter()
                        .map(|p| p.agent.clone())
                        .filter(|id| !alive.contains(id))
                        .collect()
                })
                .unwrap_or_default();
            let now = Instant::now();
            for (i, m) in guard.iter_mut().enumerate() {
                let label = m.spec.label();
                // Остановлен по причине, которую перезапуск не лечит.
                if m.halted {
                    continue;
                }
                if let Some(at) = m.retry_at {
                    if now >= at {
                        m.retry_at = None;
                        acts.push((i, Act::Spawn(m.spec.clone())));
                    }
                    continue;
                }
                if m.child.is_none() {
                    // Ребёнок, которого не удалось поднять, ждёт своей минуты.
                    m.retry_at = Some(now + Duration::from_secs(30));
                    continue;
                }
                let Ok(Some(status)) = m.child.as_mut().unwrap().try_wait() else { continue };
                m.child = None;
                // Код выхода — это слово ребёнка о причине, и его надо слышать.
                // Руннер выходит 3, когда виноват helene.json или раскладка
                // папки данных, и 2, когда рядом нет папки с кодом агента.
                // Раньше оба крутились в вечном 1→2→4→8→16→32 с и по десять
                // минут дальше — с текстом «поднимаю снова», который был ложью.
                if let Some(code @ (2 | 3)) = status.code() {
                    m.halted = true;
                    m.retry_at = None;
                    let human = m.spec.human();
                    let (why, what) = if code == 3 {
                        (
                            "не может прочитать свои настройки",
                            format!("Проверь {CONFIG_NAME} рядом с программой (или открой Настройки), потом запусти Hélène снова."),
                        )
                    } else {
                        (
                            "не находит свою папку с кодом агента",
                            "Похоже, поставка неполная: переустанови Hélène в эту же папку.".to_string(),
                        )
                    };
                    acts.push((
                        i,
                        Act::Halt(
                            format!("{label} завершился с кодом {code}: {why} — перезапуск здесь не поможет, жду решения владельца"),
                            format!("{human} {why}. {what}"),
                        ),
                    ));
                    continue;
                }
                m.falls.retain(|t| now.duration_since(*t) < Duration::from_secs(600));
                m.falls.push(now);
                if m.falls.len() > 5 {
                    m.falls.clear();
                    m.retry_at = Some(now + Duration::from_secs(600));
                    acts.push((
                        i,
                        Act::Report(
                            format!("{label} завершился ({status}) шестой раз за десять минут — пауза десять минут"),
                            m.spec.human(),
                        ),
                    ));
                    continue;
                }
                let pause = Duration::from_secs(1u64 << (m.falls.len() - 1).min(5));
                m.retry_at = Some(now + pause);
                acts.push((
                    i,
                    Act::Report(
                        format!("{label} завершился ({status}), поднимаю снова через {} с", pause.as_secs()),
                        "",
                    ),
                ));
            }
            logs = guard.iter().filter_map(|m| m.spec.log_path()).collect();
        }
        // Ротация логов живых детей — вне замка: у долго живущего агента
        // deskapp.log рос без границы (проверка была только в момент запуска),
        // а копирование мегабайтов под общим замком держало бы выход из трея.
        for path in logs {
            if rotate_child_log(&path) {
                log_line(&format!("{} обрезан по размеру", path.display()));
            }
        }
        // 2) Действия — без замка.
        for (i, act) in acts {
            match act {
                Act::Report(line, human) => {
                    log_line(&line);
                    if !human.is_empty() {
                        toast(
                            product_ui(),
                            &format!("{human}: не удаётся запустить раз за разом. Открой Настройки → «Собрать логи для поддержки»."),
                        );
                    }
                }
                Act::Halt(line, said) => {
                    log_line(&line);
                    toast(product_ui(), &said);
                }
                Act::Spawn(spec) => {
                    let label = spec.label();
                    let outcome = spec.spawn();
                    let mut line = String::new();
                    if let Ok(mut guard) = state.children.lock() {
                        if let Some(m) = guard.get_mut(i) {
                            match outcome {
                                SpawnOutcome::Started(child) => {
                                    m.child = Some(child);
                                    line = if m.waiting {
                                        format!("{label}: порт освободился — поднято")
                                    } else {
                                        format!("{label} поднят снова")
                                    };
                                    m.waiting = false;
                                }
                                SpawnOutcome::Waiting(why) => {
                                    // Ждём внешнего события: слово — один раз на вход в
                                    // состояние, а не каждые 30 с (25.09, E.1).
                                    m.retry_at = Some(Instant::now() + Duration::from_secs(30));
                                    if !m.waiting {
                                        line = why;
                                    }
                                    m.waiting = true;
                                }
                                SpawnOutcome::Failed => {
                                    m.retry_at = Some(Instant::now() + Duration::from_secs(30));
                                    m.waiting = false;
                                    line = format!("{label} не поднялся, ещё попытка через 30 с");
                                }
                            }
                        }
                    }
                    if !line.is_empty() {
                        log_line(&line);
                    }
                }
            }
        }
        // 3) Чьих-то детей нет совсем — попробовать поднять его харнесс заново.
        // ⚠ Считается ПО АГЕНТУ, а не по всему списку: пока условие было
        // «детей нет вовсе», упавший второй агент не поднимался, пока жив
        // первый, — и молчал об этом.
        if !missing.is_empty() && last_lift.elapsed() >= Duration::from_secs(15) {
            last_lift = Instant::now();
            let plans: Vec<SpawnPlan> = state
                .plans
                .lock()
                .map(|p| p.iter().filter(|p| missing.contains(&p.agent)).cloned().collect())
                .unwrap_or_default();
            for plan in plans {
                let (mut lifted, _) = start_children(&plan, false);
                if !lifted.is_empty() {
                    let mut installed = false;
                    if let Ok(mut guard) = state.children.lock() {
                        let still_gone = !guard.iter().any(|m| m.agent == plan.agent);
                        if still_gone && !state.stopping.load(Ordering::Relaxed) {
                            guard.append(&mut lifted);
                            installed = true;
                        }
                    }
                    if installed {
                        log_line(&format!("порт освободился — код агента{} поднят окном", plan.whose()));
                        // Окно открывалось с экраном «здесь чужая установка»:
                        // адреса харнесса в нём нет, и само оно к своему уже
                        // поднятому агенту не подключится.
                        if plan.agent == current_id() && BLOCKED_BY_FOREIGN.swap(false, Ordering::Relaxed) {
                            log_line("окно открывалось без адреса (чужая установка на порту) — прошу владельца перезапустить его");
                            toast(
                                product_ui(),
                                "Порт освободился, агент этого окна поднялся. Перезапусти Hélène, чтобы окно к нему подключилось.",
                            );
                        }
                    } else {
                        // Пока мы поднимали, окно уже гасят или кто-то успел
                        // раньше: сирот не оставляем.
                        for m in lifted.iter_mut() {
                            if let Some(child) = m.child.as_mut() {
                                stop_child(child);
                            }
                        }
                    }
                }
            }
        }
    }
}

/// Список агентов установки для окна: кто есть, кто сейчас в окне, кто поднят.
///
/// «Поднят» считается по ЖИВЫМ детям этого процесса, а не по конфигу: агент,
/// чей порт занят чужой программой, в конфиге включён — и молчаливая галочка
/// «работает» была бы враньём.
#[tauri::command]
fn agents_list(state: tauri::State<LocalHarness>) -> serde_json::Value {
    let base = install_root();
    let raised: Vec<String> = state
        .children
        .lock()
        .map(|g| g.iter().filter(|m| m.child.is_some()).map(|m| m.agent.clone()).collect())
        .unwrap_or_default();
    let list: Vec<serde_json::Value> = roster(&base)
        .iter()
        .map(|a| {
            let mut got = a.as_json();
            got["raised"] = serde_json::Value::Bool(raised.contains(&a.id));
            got["current"] = serde_json::Value::Bool(a.id == current_id());
            got
        })
        .collect();
    serde_json::json!({ "agents": list, "current": current_id() })
}

/// Показать в окне другого агента этой установки.
///
/// Переключение — это НЕ переезд окна на другой адрес на лету: окно строится
/// заново с init-скриптом того агента (адрес канала + его ключ). Детей при
/// этом никто не гасит: остальные агенты продолжают жить, и переписка в них
/// идёт своим чередом — окно просто смотрит в другую сторону.
#[tauri::command]
fn switch_agent(app: tauri::AppHandle, id: String) -> Result<serde_json::Value, String> {
    let base = install_root();
    let Some(agent) = find_agent(&base, &id) else {
        return Err(format!("агента «{id}» в этой установке нет"));
    };
    if agent.id == current_id() {
        show_main(&app);
        return Ok(serde_json::json!({ "ok": true, "same": true }));
    }
    if !agent.conflict.is_empty() {
        return Err(format!(
            "у агента «{}» тот же порт {}, что у «{}» — окно к нему не пойдёт, пока порт не разведён",
            agent.name, agent.port, agent.conflict
        ));
    }
    let token = ensure_desk_token(&agent.tree).unwrap_or_default();
    set_desk_token(&token);
    set_current(CurrentAgent {
        id: agent.id.clone(),
        name: agent.name.clone(),
        tree: Some(agent.tree.clone()),
        config: agent.config.clone(),
    });
    log_line(&format!("окно переключено на агента «{}» ({})", agent.name, agent.id));
    let script = channel_script(&base, agent.port, &token, &agent.name, &agent.id);
    SWITCHING.store(true, std::sync::atomic::Ordering::Relaxed);
    if let Some(window) = app.get_webview_window("main") {
        // Именно destroy: close() у этого окна означает «спрятать в трей».
        let _ = window.destroy();
    }
    // Метка освобождается не мгновенно; строим новое окно, когда старое её
    // отпустило, иначе Tauri отвечает «окно с таким именем уже есть».
    let handle = app.clone();
    std::thread::spawn(move || {
        for _ in 0..60 {
            if handle.get_webview_window("main").is_none() {
                break;
            }
            std::thread::sleep(Duration::from_millis(50));
        }
        let made = open_window(&handle, &script, None);
        // Флаг снимаем ПОСЛЕ постройки: пока он поднят, смерть окна не гасит
        // детей — а до этой строки как раз и умирает старое.
        SWITCHING.store(false, std::sync::atomic::Ordering::Relaxed);
        if let Err(err) = made {
            log_line(&format!("окно не открылось после переключения: {err}"));
            toast(product_ui(), "Окно не открылось после переключения агента — открой программу заново.");
            return;
        }
        show_main(&handle);
    });
    Ok(serde_json::json!({ "ok": true, "id": agent.id, "name": agent.name }))
}

/// Завести ещё одного агента в этой же установке.
///
/// Делает ровно две вещи: папку с конфигом (`agents/<id>/helene.json`) и запись
/// в списке. Дом агента засевает раннер при первом старте — второй реализации
/// засева здесь нет и не будет. Мозг и ограда наследуются от корневого (владелец
/// настроил их один раз), бот и тело — нет: они у каждого свои.
#[tauri::command]
async fn agent_add(app: tauri::AppHandle, name: String) -> Result<serde_json::Value, String> {
    let base = install_root();
    let named = name.trim().to_string();
    if named.is_empty() {
        return Err("у агента должно быть имя — им он подписывает свои слова".into());
    }
    if roster(&base).len() >= 16 {
        return Err("шестнадцать агентов в одной установке — это уже сервер, а не рабочий стол".into());
    }
    let python = python_path(&base, &config_value().unwrap_or(serde_json::json!({})));
    let script = base.join("app").join("localharness").join("agents_cli.py");
    if !script.exists() {
        return Err(format!("в этой сборке нет {}", script.display()));
    }
    let named_for_cmd = named.clone();
    let made: serde_json::Value = tauri::async_runtime::spawn_blocking(move || {
        // Заводит агента ПИТОН — тот самый модуль, которым список читают раннер
        // и канал. Второй реализации правил (slug, свободный порт, что
        // наследуется) в Rust нет: разъезд двух «завести агента» стоил бы
        // владельцу папки с чужим именем и порта, занятого дважды.
        let mut cmd = Command::new(&python);
        cmd.arg("-u").arg(&script).arg("add").arg("--name").arg(&named_for_cmd)
            .arg("--base").arg(&base)
            .env("PYTHONUTF8", "1")
            .env("HELENE_PARENT_PID", std::process::id().to_string())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(windows)]
        cmd.creation_flags(CREATE_NO_WINDOW);
        let out = cmd.output().map_err(|e| format!("не запустился: {e}"))?;
        let text = String::from_utf8_lossy(&out.stdout).to_string();
        if !out.status.success() {
            let err = String::from_utf8_lossy(&out.stderr).trim().to_string();
            return Err(if err.is_empty() { format!("не вышло: {text}") } else { err });
        }
        serde_json::from_str::<serde_json::Value>(text.trim())
            .map_err(|e| format!("ответ не разобрался ({e}): {text}"))
    })
    .await
    .map_err(|e| e.to_string())??;
    let id = made.get("id").and_then(|v| v.as_str()).unwrap_or_default().to_string();
    log_line(&format!("заведён агент «{named}» ({id}) — поднимется после перезапуска"));
    // Поднимать его прямо сейчас нечем: детей заводит план, а план строится на
    // старте. Перезапуск оболочки — то же, что владелец делает после правки
    // настроек, и он уже описан словами в окне.
    let _ = app;
    Ok(made)
}

/// Версия, папка, журнал — для экрана «О программе». `platform`/`arch` — по
/// ним окно прячет карточки того, чего на этой системе нет (служба, тело,
/// брандмауэр); `root` — корень установки, `exe_dir` оставлен под старым
/// именем с тем же значением: окно показывает его как «папку программы».
#[tauri::command]
fn app_info() -> serde_json::Value {
    let base = install_root();
    serde_json::json!({
        "version": env!("CARGO_PKG_VERSION"),
        "exe_dir": base.display().to_string(),
        "root": base.display().to_string(),
        "log": base.join("helene.log").display().to_string(),
        "platform": std::env::consts::OS,
        "arch": std::env::consts::ARCH,
    })
}

/// Версия из тега: `v0.2.1`, `V1.0.0`, `1.0`, `1.0.0-rc1` → числа.
/// Строго: непонятный тег даёт None, а не нули. Раньше `V1.0.0` (заглавная
/// буква) и `helene-1.0.0` читались как 0.0.0, и мажорный выпуск был
/// неотличим от «обновлений нет».
fn parse_version(raw: &str) -> Option<Vec<u64>> {
    let text = raw.trim();
    let text = text.strip_prefix('v').or_else(|| text.strip_prefix('V')).unwrap_or(text);
    let text = text.split(['-', '+', ' ']).next().unwrap_or("");
    if text.is_empty() {
        return None;
    }
    let mut parts = Vec::new();
    for chunk in text.split('.') {
        let digits: String = chunk.chars().take_while(|c| c.is_ascii_digit()).collect();
        if digits.is_empty() || digits.len() != chunk.len() {
            return None;
        }
        parts.push(digits.parse().ok()?);
    }
    if parts.is_empty() {
        return None;
    }
    // 1.0 и 1.0.0 — одна и та же версия.
    while parts.len() < 3 {
        parts.push(0);
    }
    parts.truncate(4);
    Some(parts)
}

/// Новее ли версия-кандидат. Непонятный тег — не «новее» (и вызывающий об
/// этом скажет владельцу отдельно, а не молчаливым «последняя версия»).
fn version_newer(candidate: &str, current: &str) -> bool {
    match (parse_version(candidate), parse_version(current)) {
        (Some(a), Some(b)) => a > b,
        _ => false,
    }
}

/// Проверка обновлений: JSON по адресу владельца — {"version","url","notes"}.
/// Ничего не скачивает и не подменяет, только говорит, есть ли новее, и даёт
/// ссылку. Замена файлов — решение человека, по его же слову.
/// Архив обновления из списка вложений релиза — СВОЙ, а не первый попавшийся .zip.
///
/// Канал релизов один на обе программы (`helene`), и в одном релизе лежат
/// `Helene-<v>.zip` и `Praxis-<v>.zip`. До 0.5.0 бралось первое вложение с .zip —
/// то есть Praxis скачал бы поставку Hélène с рантаймом и ядром, а Hélène
/// могла получить окно к серверу без ядра. Предпочтение — вложению `<productName>-…zip`
/// (без учёта регистра); `allow_any` (только у Hélène: старые релизы звались
/// просто `Helene.zip`) разрешает откат на любой .zip, у варианта отката нет —
/// без своего архива кнопка ведёт на страницу релиза, как и раньше без вложений.
///
/// `macos` — искать сборку для Mac: `<productName>-<v>-macos-arm64.zip`, и только
/// её (отката на «любой .zip» у Mac нет — старых релизов для него не было).
/// Windows-ветка при этом ОБЯЗАНА Mac-архив пропускать: он тоже начинается с
/// `helene-` и тоже `.zip`, и без этого фильтра Windows скачала бы себе
/// поставку для Mac с первого же релиза, где она лежит выше по списку.
fn pick_update_zip(
    assets: &[serde_json::Value],
    product: &str,
    allow_any: bool,
    macos: bool,
) -> Option<serde_json::Value> {
    let url_of = |x: &serde_json::Value| {
        x.get("browser_download_url")
            .and_then(|u| u.as_str())
            .map(|u| u.to_lowercase())
            .unwrap_or_default()
    };
    let name_of = |x: &serde_json::Value| {
        x.get("name")
            .and_then(|n| n.as_str())
            .map(|n| n.to_lowercase())
            .filter(|n| !n.is_empty())
            .unwrap_or_else(|| url_of(x).rsplit('/').next().unwrap_or("").to_string())
    };
    let is_zip = |x: &serde_json::Value| url_of(x).ends_with(".zip");
    let for_mac = |x: &serde_json::Value| name_of(x).ends_with(UPDATE_MAC_SUFFIX);
    let prefix = format!("{}-", product.trim().to_lowercase());
    if macos {
        return assets
            .iter()
            .find(|x| is_zip(x) && for_mac(x) && name_of(x).starts_with(&prefix))
            .cloned();
    }
    assets
        .iter()
        .find(|x| is_zip(x) && !for_mac(x) && name_of(x).starts_with(&prefix))
        .or_else(|| allow_any.then(|| assets.iter().find(|x| is_zip(x) && !for_mac(x))).flatten())
        .cloned()
}

/// Хвост имени архива для macOS (Apple Silicon): `Helene-<v>-macos-arm64.zip`.
/// Рядом лежит `.sha256`; внутри архива в корне — `install.sh`.
const UPDATE_MAC_SUFFIX: &str = "-macos-arm64.zip";

#[tauri::command]
async fn update_check(url: String) -> Result<serde_json::Value, String> {
    tauri::async_runtime::spawn_blocking(move || update_check_blocking(&url))
        .await
        .map_err(|e| e.to_string())?
}

fn update_check_blocking(url: &str) -> Result<serde_json::Value, String> {
    let url = url.trim().to_string();
    if url.is_empty() {
        return Err("адрес обновлений не задан".into());
    }
    outbound_url_ok(&url)?;
    {
        // redirects(0) — та же причина, что и в probe_model: переадресация
        // уносит заголовки мимо разбора адреса, который сделан выше.
        let agent = ureq::AgentBuilder::new()
            .timeout(Duration::from_secs(12))
            .redirects(0)
            .build();
        let resp = agent.get(&url).call().map_err(|e| format!("не ответил: {e}"))?;
        if (300..400).contains(&resp.status()) {
            return Err(format!(
                "сервер обновлений отвечает переадресацией ({}) — укажи конечный адрес",
                resp.status()
            ));
        }
        let body = resp.into_string().map_err(|e| e.to_string())?;
        let v: serde_json::Value = serde_json::from_str(&body).map_err(|e| format!("это не JSON: {e}"))?;
        // Два формата: свой {"version","url","notes"} и GitHub Releases
        // (.../releases/latest → tag_name, html_url, body, assets[]).
        let text = |key: &str| v.get(key).and_then(|x| x.as_str()).unwrap_or("").to_string();
        let raw_latest = if !text("version").is_empty() { text("version") } else { text("tag_name") };
        if raw_latest.trim().is_empty() {
            return Err("сервер обновлений не назвал версию — проверь адрес".to_string());
        }
        // Непонятный тег больше не выдаётся за «это последняя версия».
        if parse_version(&raw_latest).is_none() {
            return Err(format!("не понял ответ сервера обновлений: версия «{raw_latest}»"));
        }
        let macos = cfg!(target_os = "macos");
        let asset = v
            .get("assets")
            .and_then(|a| a.as_array())
            .and_then(|a| pick_update_zip(a, product_fs(), product_fs() == PRODUCT, macos));
        let current = env!("CARGO_PKG_VERSION");
        // macOS: версия новее есть, а сборки для Mac в ней нет — сказать это
        // словами, а не подсовывать страницу релиза с Windows-архивом. Когда
        // новее ничего нет, отсутствие Mac-архива — не ошибка: ставить нечего.
        if macos && asset.is_none() && version_newer(&raw_latest, current) {
            return Err(format!(
                "есть версия {}, но для macOS сборки этой версии нет",
                raw_latest.trim()
            ));
        }
        let asset_zip = asset
            .as_ref()
            .and_then(|a| a.get("browser_download_url").and_then(|u| u.as_str()).map(str::to_string));
        // Подпись кода у поставки нет; sha256 от GitHub — единственная
        // бесплатная страховка на 80 МБ архива, и она просто не бралась.
        let digest = asset
            .as_ref()
            .and_then(|a| a.get("digest").and_then(|d| d.as_str()).map(str::to_string))
            .unwrap_or_default();
        let url = pick_update_url(&text("version"), &text("url"), asset_zip, &text("html_url"));
        let notes = if !text("notes").is_empty() { text("notes") } else { text("body") };
        // Заметки релиза несут sha256 КАЖДОГО архива (installer/RELEASE.md,
        // шаг 4): Windows, Praxis и macOS. Когда GitHub не отдал digest ассета,
        // сумма берётся из строки, где назван именно наш архив; первое попавшееся
        // 64-hex слово — это сумма Windows-архива, и на Mac она отвергла бы верный
        // файл как «сумма не совпала».
        let asset_name = asset
            .as_ref()
            .and_then(|a| a.get("name").and_then(|n| n.as_str()).map(str::to_string))
            .unwrap_or_default();
        let sha_from_notes = sha_for_asset(&notes, &asset_name);
        // Первая строка описания релиза — обычно заголовок «# Изменения»:
        // окно печатало его как «что нового».
        let notes: String = notes
            .lines()
            .map(str::trim)
            .find(|l| !l.is_empty() && !l.starts_with('#'))
            .unwrap_or("")
            .chars()
            .take(200)
            .collect();
        let latest = raw_latest.trim().trim_start_matches(['v', 'V']).to_string();
        let digest = digest.trim().trim_start_matches("sha256:").to_lowercase();
        Ok(serde_json::json!({
            "current": current,
            "latest": latest,
            "newer": version_newer(&raw_latest, current),
            "url": url,
            "notes": notes,
            "sha256": if digest.is_empty() { sha_from_notes } else { digest },
        }))
    }
}

/// Куда ведёт «Скачать и установить»: архив, а не страница и не запись в API.
///
/// ⚠⚠⚠ Живой случай 20.09.2026 (0.7.1 у владельца, «Не получилось.»): выбор шёл
/// по НЕПУСТОТЕ поля `url` — «свой JSON назвал адрес». Но ответ GitHub Releases
/// тоже несёт `url`, и там это адрес САМОЙ ЗАПИСИ релиза в API
/// (`https://api.github.com/repos/…/releases/392320465`). Поле не пустое никогда,
/// поэтому ветка своего формата перебивала GitHub ВСЕГДА: окно скачивало не архив,
/// а JSON-запись, и `update_file_name` честно отвечал «ссылка ведёт не на архив
/// .zip». Кнопка не работала ни разу за всё время, пока адрес вёл на GitHub.
///
/// Форматы различаются не по `url`, а по тому, чем назвали версию: свой JSON —
/// полем `version`, GitHub — `tag_name` (эту же развилку делает `raw_latest` выше).
/// Без своего `version` адрес берётся у выбранного архива, и только если архива
/// нет — страница релиза (`html_url`), чтобы человеку было куда пойти руками.
fn pick_update_url(own_version: &str, own_url: &str, asset_zip: Option<String>, html_url: &str) -> String {
    if !own_version.trim().is_empty() && !own_url.trim().is_empty() {
        return own_url.to_string();
    }
    asset_zip.unwrap_or_else(|| {
        if html_url.trim().is_empty() { own_url.to_string() } else { html_url.to_string() }
    })
}

/// sha256 архива из заметок релиза: строка, где назван сам архив, иначе первое
/// 64-hex слово (заметки до 0.7.1 несли одну сумму — Windows-архива).
fn sha_for_asset(notes: &str, asset_name: &str) -> String {
    let hex_in = |line: &str| -> Option<String> {
        line.split_whitespace()
            .map(|w| w.trim_matches(|c: char| !c.is_ascii_hexdigit()).to_lowercase())
            .find(|w| w.len() == 64 && w.bytes().all(|b| b.is_ascii_hexdigit()))
    };
    let name = asset_name.trim().to_lowercase();
    if !name.is_empty() {
        if let Some(sha) = notes
            .lines()
            .filter(|l| l.to_lowercase().contains(&name))
            .find_map(hex_in)
        {
            return sha;
        }
    }
    hex_in(notes).unwrap_or_default()
}

/// Проверка обновлений при старте — раз в сутки, без вопросов и без
/// скачивания (слово владельца 07.09: «режима обновления нет»). Есть версия
/// новее — уведомление Windows с адресом кнопки; ставить или нет — решение
/// человека в «Настройках». Отпечаток проверки — в дереве данных
/// (`memory/.state/update-check.json`), чтобы не дёргать GitHub на каждый
/// запуск и не повторять уведомление о той же версии. Выключается
/// `update.auto: false` в helene.json; без `update.url` молчит.
fn update_autocheck() {
    std::thread::sleep(Duration::from_secs(90));
    let Some(cfg) = config_value() else { return };
    let update = cfg.get("update").cloned().unwrap_or(serde_json::Value::Null);
    if update.get("auto").and_then(|v| v.as_bool()) == Some(false) {
        return;
    }
    let url = update.get("url").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
    if url.is_empty() {
        return;
    }
    let stamp_path = tree_dir().join("memory").join(".state").join("update-check.json");
    let previous: serde_json::Value = std::fs::read_to_string(&stamp_path)
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(serde_json::Value::Null);
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let checked_at = previous.get("checked_at").and_then(|v| v.as_u64()).unwrap_or(0);
    if now.saturating_sub(checked_at) < 24 * 3600 {
        return;
    }
    let result = update_check_blocking(&url);
    let (latest, newer) = match &result {
        Ok(v) => (
            v.get("latest").and_then(|x| x.as_str()).unwrap_or("").to_string(),
            v.get("newer").and_then(|x| x.as_bool()).unwrap_or(false),
        ),
        Err(why) => {
            log_line(&format!("проверка обновлений при старте: {why}"));
            (String::new(), false)
        }
    };
    let notified_before = previous.get("notified").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let notify = newer && !latest.is_empty() && notified_before != latest;
    if notify {
        toast(
            product_ui(),
            &format!("Есть версия {latest}. Настройки → О программе → «Скачать и установить»."),
        );
        log_line(&format!("проверка обновлений при старте: есть версия {latest}"));
    }
    let record = serde_json::json!({
        "checked_at": now,
        "latest": latest,
        "newer": newer,
        "notified": if notify { latest.clone() } else { notified_before },
    });
    if let Some(dir) = stamp_path.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    let _ = std::fs::write(&stamp_path, serde_json::to_string_pretty(&record).unwrap_or_default());
}

/// Папка «Загрузки» владельца: известная папка из реестра, а не склейка.
/// Реестр не ответил или папки нет — %USERPROFILE%\Downloads, затем %TEMP%.
#[cfg(windows)]
fn downloads_dir() -> PathBuf {
    let known = shell_folder("{374DE290-123F-4565-9164-39C4925E467B}", "Downloads")
        .filter(|p| p.is_dir() && !p.ends_with("Roaming\\Downloads"));
    if let Some(p) = known {
        return p;
    }
    if let Some(profile) = std::env::var_os("USERPROFILE") {
        let p = PathBuf::from(profile).join("Downloads");
        if p.is_dir() {
            return p;
        }
    }
    std::env::temp_dir()
}

/// POSIX: `~/Downloads`, иначе временная папка.
#[cfg(not(windows))]
fn downloads_dir() -> PathBuf {
    if let Some(home) = std::env::var_os("HOME") {
        let p = PathBuf::from(home).join("Downloads");
        if p.is_dir() {
            return p;
        }
    }
    std::env::temp_dir()
}

/// Имя файла архива из ссылки — только буквы, цифры, точка, дефис, подчёркивание;
/// обязательно .zip. Всё остальное — не наш файл.
fn update_file_name(url: &str) -> Result<String, String> {
    let raw = url.trim_end_matches('/').rsplit('/').next().unwrap_or("");
    let raw = raw.split(['?', '#']).next().unwrap_or("");
    let name: String = raw.chars().filter(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '-' | '_')).collect();
    if !name.to_lowercase().ends_with(".zip") || name.starts_with('.') || name.len() < 5 {
        return Err(format!("ссылка ведёт не на архив .zip: {raw}"));
    }
    Ok(name)
}

/// Скачать архив обновления в «Загрузки» и сверить sha256 (задача A §2).
/// Сумма — из `update_check` (digest ассета GitHub или вторая строка заметок
/// релиза). Не совпала — файл удаляется, ответ отказ: подписи кода у поставки
/// нет, и сумма — единственная проверка, что скачано то, что выложено.
/// `sha_ok`: true — совпала; null — сверять было не с чем (сумма не пришла).
#[tauri::command]
async fn update_download(url: String, sha256: Option<String>) -> Result<serde_json::Value, String> {
    // Отказ обязан оставить след. 20.09.2026: у владельца кнопка отвечала
    // «Не получилось.», а в helene.log не было НИ ОДНОЙ строки об этом — успех
    // логировался, отказ нет, и причину («ссылка ведёт не на архив .zip»)
    // пришлось искать по исходникам вместо журнала.
    let out = update_download_inner(url.clone(), sha256).await;
    if let Err(ref why) = out {
        log_line(&format!("обновление не скачалось ({url}): {why}"));
    }
    out
}

async fn update_download_inner(url: String, sha256: Option<String>) -> Result<serde_json::Value, String> {
    let url = url.trim().to_string();
    if !url.to_lowercase().starts_with("https://") {
        return Err("архив обновления скачивается только по https".into());
    }
    outbound_url_ok(&url)?;
    let name = update_file_name(&url)?;
    let expected = sha256.unwrap_or_default().trim().trim_start_matches("sha256:").to_lowercase();
    if !expected.is_empty() && (expected.len() != 64 || !expected.bytes().all(|b| b.is_ascii_hexdigit())) {
        return Err("контрольная сумма релиза не похожа на sha256".into());
    }
    tauri::async_runtime::spawn_blocking(move || {
        use sha2::{Digest, Sha256};
        use std::io::{Read, Write};
        let dir = downloads_dir();
        std::fs::create_dir_all(&dir).map_err(|e| format!("нет папки «Загрузки»: {e}"))?;
        let target = dir.join(&name);
        let tmp = dir.join(format!(".{name}.part"));
        // Переадресации здесь нужны: browser_download_url GitHub ведёт на
        // objects.githubusercontent.com. Секретов в запросе нет — уносить нечего.
        let agent = ureq::AgentBuilder::new()
            .timeout_connect(Duration::from_secs(20))
            .timeout_read(Duration::from_secs(120))
            .build();
        let resp = agent.get(&url).call().map_err(|e| format!("не скачалось: {e}"))?;
        let mut reader = resp.into_reader();
        let mut file = std::fs::File::create(&tmp).map_err(|e| format!("не записалось: {e}"))?;
        let mut hasher = Sha256::new();
        let mut buf = vec![0u8; 256 * 1024];
        let mut total: u64 = 0;
        loop {
            let n = reader.read(&mut buf).map_err(|e| format!("обрыв скачивания: {e}"))?;
            if n == 0 {
                break;
            }
            total += n as u64;
            if total > 2_000_000_000 {
                drop(file);
                let _ = std::fs::remove_file(&tmp);
                return Err("архив больше 2 ГБ — это не поставка Hélène".into());
            }
            hasher.update(&buf[..n]);
            file.write_all(&buf[..n]).map_err(|e| format!("не записалось: {e}"))?;
        }
        file.flush().map_err(|e| e.to_string())?;
        drop(file);
        let digest = format!("{:x}", hasher.finalize());
        if !expected.is_empty() && digest != expected {
            let _ = std::fs::remove_file(&tmp);
            return Err(format!(
                "контрольная сумма не совпала (скачано {digest}, в релизе {expected}) — файл удалён, попробуй позже"
            ));
        }
        if target.exists() {
            std::fs::remove_file(&target).map_err(|e| format!("не заменился прежний архив: {e}"))?;
        }
        std::fs::rename(&tmp, &target).map_err(|e| format!("не подменилось: {e}"))?;
        log_line(&format!("обновление скачано: {} ({total} байт, sha256 {digest})", target.display()));
        Ok(serde_json::json!({
            "path": target.display().to_string(),
            "bytes": total,
            "sha256": digest,
            "sha_ok": if expected.is_empty() { serde_json::Value::Null } else { serde_json::Value::Bool(true) },
        }))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Распаковать скачанный архив рядом (в «Загрузках») и запустить его
/// `helene-setup.exe`. Установщик сам гасит работающую программу — включая
/// это окно — и ставит поверх по правилам resources/ОБНОВЛЕНИЕ.md.
/// Путь принимается только из «Загрузок» или %TEMP% и только .zip.
///
/// macOS: тот же смысл, другие руки. Архив распаковывает `ditto -x -k`
/// (права и симлинки внутри `runtime/` свой zip-читатель потерял бы), дальше
/// дело отдаётся `install.sh --from <zip> --relaunch` из архива — отсоединённо,
/// в своей группе процессов, — а оболочка гасит детей и выходит сама через
/// полторы секунды (ответ окну успевает дойти). Скрипту передаётся
/// `HELENE_OLD_PID`, чтобы он мог дождаться нашей смерти, прежде чем менять
/// файлы под ногами.
#[tauri::command]
async fn update_install(app: tauri::AppHandle, path: String, force_extensions: Option<bool>) -> Result<serde_json::Value, String> {
    let force_extensions = force_extensions.unwrap_or(false);
    let archive = PathBuf::from(path.trim())
        .canonicalize()
        .map_err(|_| "архива нет по этому пути".to_string())?;
    if !archive.extension().map(|e| e.eq_ignore_ascii_case("zip")).unwrap_or(false) {
        return Err("это не .zip".into());
    }
    let allowed = [downloads_dir(), std::env::temp_dir()]
        .iter()
        .filter_map(|r| r.canonicalize().ok())
        .any(|r| archive.starts_with(&r));
    if !allowed {
        return Err("устанавливаю только архивы из «Загрузок» или временной папки".into());
    }
    // Хэндл нужен только POSIX-ветке (выход после запуска install.sh); на
    // Windows программу гасит сам установщик.
    #[cfg(windows)]
    let _ = app;
    tauri::async_runtime::spawn_blocking(move || {
        #[cfg(windows)]
        {
            let stem = archive.file_stem().map(|s| s.to_string_lossy().into_owned()).unwrap_or_else(|| "Helene".into());
            let dest = archive.parent().map(Path::to_path_buf).unwrap_or_else(downloads_dir).join(&stem);
            if dest.exists() {
                std::fs::remove_dir_all(&dest).map_err(|e| format!("не очистилась папка распаковки: {e}"))?;
            }
            update_install_windows(&archive, &dest, force_extensions)
        }
        #[cfg(not(windows))]
        {
            update_install_posix(app, &archive, force_extensions)
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Windows-половина `update_install`: Expand-Archive и `helene-setup.exe`
/// из распакованного. Текст тот же, что был в теле команды до порта.
#[cfg(windows)]
fn update_install_windows(archive: &Path, dest: &Path, force_extensions: bool) -> Result<serde_json::Value, String> {
    let script = format!(
        "$ProgressPreference='SilentlyContinue'; Expand-Archive -LiteralPath {} -DestinationPath {} -Force; exit $LASTEXITCODE",
        ps_quote(&plain_path(archive).to_string_lossy()),
        ps_quote(&plain_path(dest).to_string_lossy()),
    );
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", &script]);
    let out = run_hidden_for(&mut cmd, Duration::from_secs(600))?;
    if !out.status.success() {
        return Err(format!(
            "архив не распаковался: {}",
            String::from_utf8_lossy(&out.stderr).trim().chars().take(200).collect::<String>()
        ));
    }
    // helene-setup.exe — в корне архива или в его единственной подпапке.
    let mut setup = dest.join("helene-setup.exe");
    if !setup.exists() {
        let subdirs: Vec<PathBuf> = std::fs::read_dir(dest)
            .map_err(|e| e.to_string())?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| p.is_dir())
            .collect();
        if let [one] = subdirs.as_slice() {
            setup = one.join("helene-setup.exe");
        }
    }
    if !setup.exists() {
        return Err(format!("в архиве нет helene-setup.exe (распаковано в {})", dest.display()));
    }
    let workdir = setup.parent().map(Path::to_path_buf).unwrap_or_else(|| dest.to_path_buf());
    // `--update --dir <наш корень>`: мастер возьмёт решения из самой установки
    // и поставит поверх молча. Визард он откроет сам, если решений не нашлось.
    //
    // ⚠ 20.09.2026, слова владельца: «блин, он мне ставить собрался, а не
    // обновлять». До этого мастер звался без аргументов, и «Скачать и
    // установить» приводило в полный визард — имя агента, конституция, модель
    // заново, хотя всё это уже решено. Ждать его здесь нельзя и не нужно:
    // мастер первым делом гасит работающую программу, включая это окно.
    let root = install_root();
    let mut cmd = Command::new(&setup);
    cmd.current_dir(&workdir).arg("--update").arg("--dir").arg(&root);
    // 25.09 (K): «обновить без несовместимых расширений» — только явным словом владельца.
    if force_extensions {
        cmd.arg("--force-extensions");
    }
    cmd.spawn().map_err(|e| format!("установщик не запустился: {e}"))?;
    log_line(&format!(
        "обновление: запущен установщик {} (--update --dir {})",
        setup.display(),
        root.display()
    ));
    Ok(serde_json::json!({ "setup": setup.display().to_string(), "dir": workdir.display().to_string() }))
}

/// Имя записи `install.sh` в списке архива: в корне (`install.sh`) или в
/// единственной папке верхнего уровня (`Helene/install.sh`). Чистая функция
/// над выводом `unzip -Z1`: две разные раскладки архива — и ни одна не должна
/// читаться как «скрипта нет».
fn install_script_entry(listing: &str) -> Option<String> {
    let names: Vec<&str> = listing.lines().map(str::trim).filter(|l| !l.is_empty()).collect();
    if names.iter().any(|n| *n == "install.sh") {
        return Some("install.sh".to_string());
    }
    let nested: Vec<&str> = names
        .iter()
        .copied()
        .filter(|n| n.ends_with("/install.sh") && n.matches('/').count() == 1)
        .collect();
    match nested.as_slice() {
        [one] => Some((*one).to_string()),
        _ => None,
    }
}

/// Обновление на POSIX (macOS): из архива достаётся ОДИН файл — `install.sh`
/// (`unzip -p`), и он запускается отсоединённо с `--from <zip> --relaunch`;
/// распаковывает архив целиком уже сам скрипт. Раньше здесь лежала полная
/// распаковка сотен мегабайт в `~/Downloads/<stem>/` ради одного файла — а
/// потом install.sh распаковывал zip второй раз. Смысл Windows-ветки тот же:
/// архив рядом с собой → установщик из него → оболочка отдаёт дело и выходит.
#[cfg(not(windows))]
fn update_install_posix(app: tauri::AppHandle, archive: &Path, force_extensions: bool) -> Result<serde_json::Value, String> {
    let unzip = posix_tool("unzip");
    let mut list = Command::new(&unzip);
    list.arg("-Z1").arg(archive);
    let out = run_hidden_for(&mut list, Duration::from_secs(120))?;
    if !out.status.success() {
        return Err(format!(
            "архив не читается: {}",
            String::from_utf8_lossy(&out.stderr).trim().chars().take(200).collect::<String>()
        ));
    }
    let Some(entry) = install_script_entry(&String::from_utf8_lossy(&out.stdout)) else {
        return Err("в архиве нет install.sh — это не поставка для macOS".into());
    };
    let mut take = Command::new(&unzip);
    take.arg("-p").arg(archive).arg(&entry);
    let out = run_hidden_for(&mut take, Duration::from_secs(120))?;
    if !out.status.success() || out.stdout.is_empty() {
        return Err(format!(
            "install.sh не достался из архива: {}",
            String::from_utf8_lossy(&out.stderr).trim().chars().take(200).collect::<String>()
        ));
    }
    // Скрипт — во временную папку с pid: %TEMP% общий, и файл, исполняемый
    // sh, не должен быть тем, что кто-то успел положить туда раньше.
    let workdir = std::env::temp_dir().join(format!("helene-update-{}", std::process::id()));
    std::fs::create_dir_all(&workdir).map_err(|e| format!("не создалась временная папка: {e}"))?;
    let script = workdir.join("install.sh");
    std::fs::write(&script, &out.stdout).map_err(|e| format!("install.sh не записался: {e}"))?;
    // Вывод скрипта — в журнал в корне установки, а не в /dev/null: любой `die`
    // до открытия мастера иначе оставлял бы владельца без программы и без следа.
    let root = install_root();
    let sh_log_path = root.join("install-sh.log");
    let sh_log = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&sh_log_path)
        .map_err(|e| format!("не открылся {}: {e}", sh_log_path.display()))?;
    let sh_err = sh_log.try_clone().map_err(|e| e.to_string())?;
    let mut cmd = Command::new(posix_tool("sh"));
    cmd.arg(&script)
        .arg("--from")
        .arg(archive)
        .arg("--relaunch");
    // Ревью 25.09 (A4 F2): галочка «обновлять, даже если расширения не пройдут» на macOS
    // едет в install.sh → decisions JSON → Setup.force_extensions; раньше терялась.
    if force_extensions {
        cmd.arg("--force-extensions");
    }
    cmd
        // install.sh ждёт нашей смерти по этому pid (до ~10 с), прежде чем
        // менять файлы; поэтому ниже выход обязан состояться, а не «быть запрошен».
        .env("HELENE_OLD_PID", std::process::id().to_string())
        .env("HELENE_ROOT", &root)
        .current_dir(&workdir)
        .stdin(Stdio::null())
        .stdout(Stdio::from(sh_log))
        .stderr(Stdio::from(sh_err))
        .process_group(0);
    cmd.spawn().map_err(|e| format!("install.sh не запустился: {e}"))?;
    log_line(&format!(
        "обновление: запущен {} --from {} --relaunch (его вывод — в {}); выхожу через 1,5 с",
        script.display(),
        archive.display(),
        sh_log_path.display()
    ));
    // Выход — отдельным потоком и с паузой: ответ этой команды должен доехать
    // до окна, а дети — умереть до того, как install.sh начнёт менять файлы.
    // `app.exit` лишь просит цикл событий выйти; если тот не вышел за три
    // секунды, процесс гасится сам — install.sh иначе дождался бы потолка и
    // убил бы нас, а дети и вход в подписку к этому моменту уже остановлены.
    std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(1500));
        let state = app.state::<LocalHarness>();
        kill_children(&state);
        relay_abort();
        app.exit(0);
        std::thread::sleep(Duration::from_secs(3));
        log_line("обновление: цикл событий не вышел за три секунды — завершаю процесс сам");
        std::process::exit(0);
    });
    Ok(serde_json::json!({ "setup": script.display().to_string(), "dir": workdir.display().to_string() }))
}

/// Вход агента в Telegram своим аккаунтом: шаги status / send / code / logout
/// выполняет помощник на встроенном Python; ответ — его JSON как есть.
#[tauri::command]
async fn telegram_account(
    step: String,
    api_id: String,
    api_hash: String,
    phone: String,
    code: String,
    password: String,
) -> Result<serde_json::Value, String> {
    let base = install_root();
    // Дерево — то же, с которым живёт окно, а не «data» от текущей папки
    // процесса: запущенный не ярлыком helene.exe клал сессию Telethon в чужое
    // место, и руннер не находил её никогда.
    let tree = current_tree();
    tauri::async_runtime::spawn_blocking(move || {
        let python = bundled_python(&base);
        let script = base.join("app").join("localharness").join("mtproto_login.py");
        if !python.exists() || !script.exists() {
            return Err("в этой сборке нет помощника входа".to_string());
        }
        let session = tree.join("telegram").join("account");
        let mut cmd = Command::new(python);
        cmd.arg("-u")
            .arg(script)
            .arg("--session").arg(&session)
            // ⚠ Секреты — ОКРУЖЕНИЕМ, не аргументами. Командную строку чужого
            // процесса на Windows читает любой процесс того же пользователя
            // (Win32_Process.CommandLine), и она же пишется в аудит запусков
            // (Sysmon 1 / 4688). Пароль двухфакторки Telegram и api_hash —
            // секреты долгоживущие. Помощник читает их из этих переменных
            // (localharness/mtproto_login.py, `_from_env`).
            .env("HELENE_TG_API_ID", api_id.trim())
            .env("HELENE_TG_API_HASH", api_hash.trim())
            .env("HELENE_TG_PHONE", phone.trim())
            .env("HELENE_TG_CODE", code.trim())
            .env("HELENE_TG_PASSWORD", password)
            .arg(step.trim())
            .current_dir(&base)
            .env("PYTHONUTF8", "1")
            .env("HELENE_PARENT_PID", std::process::id().to_string());
        // Вход в Telegram ждёт сеть и код: минута, но не бесконечность.
        let out = run_hidden_for(&mut cmd, Duration::from_secs(90))?;
        let text = String::from_utf8_lossy(&out.stdout);
        let line = text.lines().rev().find(|l| l.trim_start().starts_with('{')).unwrap_or("");
        serde_json::from_str::<serde_json::Value>(line)
            .map_err(|_| format!("помощник входа ответил не JSON: {}", text.trim().chars().take(200).collect::<String>()))
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Скачать модель слуха или голос синтеза — `app/localharness/voice.py`
/// (`--get <модель>` либо `--get-voice <голос>`, по `kind`).
///
/// Ждать здесь нечего: модель весит от 0,5 до 1,6 ГБ, и на медленной сети это
/// минуты. Поэтому команда только ЗАПУСКАЕТ помощника и возвращается, а ход
/// дела помощник пишет в дерево (`memory/.state/voice-download.json`), откуда
/// его читает канал и показывает окно. Ребёнок усыновляется job-объектом, как
/// остальные: закрыл окно — качать некому, и окно об этом скажет, увидев
/// протухшую запись, вместо вечного «качаю…».
#[tauri::command]
fn voice_fetch(model: String, kind: Option<String>) -> Result<String, String> {
    let base = install_root();
    let python = bundled_python(&base);
    let script = base.join("app").join("localharness").join("voice.py");
    if !python.exists() || !script.exists() {
        return Err("в этой сборке нет помощника голоса (app/localharness/voice.py)".into());
    }
    let name = model.trim().to_string();
    if name.is_empty() {
        return Err("не сказано, какую модель качать".into());
    }
    // Слух и речь качает ОДИН помощник, и кнопка у них одна: две команды с
    // разными путями расходились бы на первой же правке раскладки.
    let speaking = kind.as_deref().unwrap_or("hear") == "speak";
    let mut cmd = Command::new(python);
    cmd.arg("-u")
        .arg(script)
        .arg("--tree")
        .arg(tree_dir())
        .arg(if speaking { "--get-voice" } else { "--get" })
        .arg(&name)
        .current_dir(&base)
        .env("PYTHONUTF8", "1")
        .env("HELENE_PARENT_PID", std::process::id().to_string());
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    #[cfg(unix)]
    cmd.process_group(0);
    match cmd.spawn() {
        Ok(child) => {
            adopt(&child);
            Ok(if speaking {
                format!("качаю голос {name}")
            } else {
                format!("качаю модель {name}")
            })
        }
        Err(err) => Err(format!("помощник голоса не запустился: {err}")),
    }
}

/// Экспорт агента одним архивом — `app/localharness/carry.py export`: данные
/// с личным git, helene.json с ключами и паспорт `helene-carry.json`. Возвращает
/// путь к архиву; окно показывает его в Проводнике. Импорт на ПК — только из
/// консоли при закрытой программе: подменять data/ под живым харнессом нельзя,
/// и команды на это у окна намеренно нет.
#[tauri::command]
async fn carry_export() -> Result<String, String> {
    let base = install_root();
    tauri::async_runtime::spawn_blocking(move || {
        let python = bundled_python(&base);
        let script = base.join("app").join("localharness").join("carry.py");
        if !python.exists() || !script.exists() {
            return Err("в этой сборке нет помощника переноса (app/localharness/carry.py)".to_string());
        }
        let mut cmd = Command::new(python);
        cmd.arg("-u")
            .arg(script)
            .arg("export")
            .arg("--config")
            // Конфиг ТЕКУЩЕГО агента: перенос увозит того, кто в окне, а не
            // всегда первого. Раньше это было одно и то же — с 11.09 нет.
            .arg(current_config_path())
            .current_dir(&base)
            .env("PYTHONUTF8", "1")
            .env("HELENE_PARENT_PID", std::process::id().to_string());
        // Память агента бывает на сотни мегабайт; десять минут — не бесконечность.
        let out = run_hidden_for(&mut cmd, Duration::from_secs(600))?;
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
            _ => {
                let err = String::from_utf8_lossy(&out.stderr);
                let tail: String = err.trim().chars().rev().take(300).collect::<Vec<_>>().into_iter().rev().collect();
                Err(format!("экспорт не удался: {}", if tail.is_empty() { text.trim().to_string() } else { tail }))
            }
        }
    })
    .await
    .map_err(|e| e.to_string())?
}

/// Собрать логи для поддержки в один zip во временной папке: helene.log,
/// вывод харнесса, службы и реле. Файлы сперва копируются: дети держат свои
/// логи открытыми, и архиватор напрямую их не читает.
#[tauri::command]
async fn logs_bundle() -> Result<String, String> {
    // Дерево берём то, с которым это окно живёт (Identity), а не считаем
    // заново от текущей папки процесса.
    let tree = current_tree();
    tauri::async_runtime::spawn_blocking(move || logs_bundle_blocking(tree))
        .await
        .map_err(|e| e.to_string())?
}

/// Секрет в тексте лога закрывается ЗДЕСЬ, при копировании в архив.
///
/// Источники сегодня чистые (труба пишет путь без query, реле само пишет
/// «value redacted») — но архив владелец отправляет постороннему человеку, а
/// удержать это свойство нечем: любой новый `log.info` в любой из четырёх
/// подсистем попадёт в тот же zip. Маска — механизм, а не надежда.
///
/// Формы: `key=…`, `token=…`, `Bearer …`, `sk-…`, `dk-…` (регистр не важен).
fn mask_secrets(text: &str) -> String {
    const MARKS: [&str; 3] = ["key=", "token=", "bearer "];
    const PREFIXES: [&str; 2] = ["sk-", "dk-"];
    const HIDDEN: &[u8] = "<скрыто>".as_bytes();
    // Символы, из которых состоит сам секрет: на первом чужом байте (пробел,
    // кавычка, запятая, любой не-ASCII) значение кончилось.
    fn body_byte(b: u8) -> bool {
        b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~' | b'+' | b'/' | b':' | b'%' | b'*' | b'=')
    }
    let src = text.as_bytes();
    let mut out: Vec<u8> = Vec::with_capacity(src.len());
    let mut i = 0usize;
    while i < src.len() {
        let rest = &src[i..];
        // key= / token= / Bearer — прячем значение, сам маркер оставляем.
        if let Some(len) = MARKS
            .iter()
            .find(|m| rest.len() >= m.len() && rest[..m.len()].eq_ignore_ascii_case(m.as_bytes()))
            .map(|m| m.len())
        {
            out.extend_from_slice(&rest[..len]);
            i += len;
            let mut j = i;
            while j < src.len() && body_byte(src[j]) {
                j += 1;
            }
            if j > i {
                out.extend_from_slice(HIDDEN);
                i = j;
            }
            continue;
        }
        // sk-… / dk-… — только на границе слова, иначе съело бы «task-1».
        let word_start = i == 0 || !body_byte(src[i - 1]);
        if word_start {
            if let Some(len) = PREFIXES
                .iter()
                .find(|p| rest.len() >= p.len() && rest[..p.len()].eq_ignore_ascii_case(p.as_bytes()))
                .map(|p| p.len())
            {
                let mut j = i + len;
                while j < src.len() && body_byte(src[j]) {
                    j += 1;
                }
                // Меньше восьми знаков — это не ключ, а слово через дефис.
                if j - i >= 8 {
                    out.extend_from_slice(&rest[..len]);
                    out.extend_from_slice(HIDDEN);
                    i = j;
                    continue;
                }
            }
        }
        out.push(src[i]);
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// Хвост файла в текст: логи режутся ротацией по 5 МБ, но у реле свои правила,
/// а маска работает по тексту — читаем не больше разумного.
const BUNDLE_TAIL: u64 = 8 * 1024 * 1024;

fn read_tail(path: &Path) -> Option<String> {
    let mut f = std::fs::File::open(path).ok()?;
    let len = f.metadata().ok()?.len();
    if len > BUNDLE_TAIL {
        f.seek(SeekFrom::Start(len - BUNDLE_TAIL)).ok()?;
    }
    let mut buf = Vec::new();
    f.read_to_end(&mut buf).ok()?;
    Some(String::from_utf8_lossy(&buf).into_owned())
}

fn logs_bundle_blocking(tree: PathBuf) -> Result<String, String> {
    let base = install_root();
    // Конфиг здесь не обязателен: логи нужнее всего именно тогда, когда
    // helene.json испорчен, а config_load()? отказывал ровно в этом случае.
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    // Прошлые архивы и папки сборки — вон из %TEMP%. Они там копились с самого
    // первого нажатия: %TEMP% читает любой процесс этой учётной записи, а
    // внутри — журналы, которые владелец собирал именно потому, что что-то
    // пошло не так. Чистим ДО создания своей папки, чтобы не снести её же.
    let mut dropped = 0usize;
    if let Ok(rd) = std::fs::read_dir(std::env::temp_dir()) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().into_owned();
            if !name.starts_with("helene-logs-") {
                continue;
            }
            let path = e.path();
            let gone = if path.is_dir() {
                std::fs::remove_dir_all(&path).is_ok()
            } else {
                std::fs::remove_file(&path).is_ok()
            };
            if gone {
                dropped += 1;
            }
        }
    }
    if dropped > 0 {
        let where_ = if cfg!(windows) { "%TEMP%" } else { "$TMPDIR" };
        log_line(&format!("прошлых архивов логов удалено из {where_}: {dropped}"));
    }
    let stage = std::env::temp_dir().join(format!("helene-logs-{stamp}"));
    std::fs::create_dir_all(&stage).map_err(|e| e.to_string())?;
    let mut copied: Vec<String> = Vec::new();
    let mut sources = vec![base.join("helene.log")];
    for name in ["deskapp.log", "runner.log", "service.log", "desk.log"] {
        sources.push(tree.join(name));
    }
    if let Ok(rd) = std::fs::read_dir(tree.join("relay").join("logs")) {
        sources.extend(rd.flatten().map(|e| e.path()).filter(|p| p.is_file()));
    }
    for src in sources {
        if !src.is_file() {
            continue;
        }
        let Some(name) = src.file_name() else { continue };
        // Не copy, а «прочитать → закрыть секреты → записать».
        let Some(text) = read_tail(&src) else { continue };
        if std::fs::write(stage.join(name), mask_secrets(&text)).is_ok() {
            copied.push(name.to_string_lossy().into_owned());
        }
    }
    if copied.is_empty() {
        let _ = std::fs::remove_dir_all(&stage);
        return Err("логов пока нет".into());
    }
    let out = std::env::temp_dir().join(format!("helene-logs-{stamp}.zip"));
    #[cfg(windows)]
    let mut cmd = {
        let script = format!(
            "Compress-Archive -Force -Path '{}\\*' -DestinationPath '{}'",
            stage.display(),
            out.display()
        );
        let mut cmd = Command::new(powershell_exe());
        cmd.args(["-NoProfile", "-NonInteractive", "-Command", &script]);
        cmd
    };
    // POSIX: `zip -j` — файлы в корень архива, без пути папки сборки.
    #[cfg(not(windows))]
    let mut cmd = {
        let mut cmd = Command::new(posix_tool("zip"));
        cmd.arg("-q").arg("-j").arg("-r").arg(&out).arg(&stage);
        cmd
    };
    let o = run_hidden_for(&mut cmd, Duration::from_secs(120))?;
    let _ = std::fs::remove_dir_all(&stage);
    if !o.status.success() || !out.is_file() {
        let err = String::from_utf8_lossy(&o.stderr).trim().to_string();
        return Err(if err.is_empty() { "архив не собрался".into() } else { err });
    }
    // Что именно уедет постороннему — владелец видит ДО отправки, поимённо.
    // Возвращаемое значение остаётся путём: его получает reveal_path.
    log_line(&format!("архив логов собран: {} ({})", out.display(), copied.join(", ")));
    message_box_info(
        &format!("{}: логи для поддержки", product_ui()),
        &format!(
            "Архив: {}\n\nВ него попали:\n · {}\n\nКлючи, токены и строки Bearer в тексте закрыты словом «скрыто». Всё остальное — как есть: пути на этой машине, имена файлов и куски сообщений об ошибках. Отправляй только тому, кому доверяешь.",
            out.display(),
            copied.join("\n · ")
        ),
    );
    Ok(out.display().to_string())
}

#[cfg(test)]
mod tests {
    #[test]
    fn login_marker_needs_success_and_a_changed_auth_json() {
        use std::time::{Duration, SystemTime};
        let t0 = SystemTime::UNIX_EPOCH + Duration::from_secs(1_000);
        let t1 = SystemTime::UNIX_EPOCH + Duration::from_secs(2_000);
        let old = Some((Some(t0), 10u64));
        let new = Some((Some(t1), 12u64));
        assert!(super::login_marks_new_generation(true, old, new), "успех + новый файл");
        assert!(super::login_marks_new_generation(true, None, new), "успех + файл появился");
        assert!(!super::login_marks_new_generation(true, old, old), "успех, но файл прежний");
        assert!(!super::login_marks_new_generation(false, old, new), "помощник упал/отменён");
        assert!(!super::login_marks_new_generation(true, old, None), "файла нет");
    }

    /// Константы окна — те же, что в `ui-kit/contract.json` (одно место для
    /// трёх языков; задача A п. 1.12).
    #[test]
    fn contract_json_matches_constants() {
        let c: serde_json::Value = serde_json::from_str(include_str!("../../ui-kit/contract.json")).unwrap();
        assert_eq!(c["ports"]["desk"], super::DESK_PORT);
        assert_eq!(c["ports"]["relay"], super::RELAY_PORT);
        assert_eq!(c["config_name"], super::CONFIG_NAME);
    }

    /// `config_save` с отпечатком свежести: чужая правка на диске — отказ
    /// `stale` с текущим отпечатком, черновик окна не записывается; со свежим
    /// отпечатком — записывается. Без отпечатка (старое окно) — как раньше.
    #[test]
    fn config_save_refuses_stale_draft() {
        use super::config_save_at;
        let dir = std::env::temp_dir().join(format!("helene-cfg-{}", std::process::id()));
        let _ = std::fs::create_dir_all(&dir);
        let target = dir.join("helene.json");
        let first = config_save_at(&target, r#"{"a":1}"#, None).unwrap();
        assert_eq!(first["ok"], true);
        let seen = first["mtime_ns"].as_str().expect("отпечаток строкой").to_string();
        // Кто-то записал поверх (руннер, установщик, Блокнот) — отпечаток сменился.
        std::thread::sleep(std::time::Duration::from_millis(30));
        std::fs::write(&target, "{\"a\":2}").unwrap();
        let stale = config_save_at(&target, r#"{"a":3}"#, Some(&seen)).unwrap();
        assert_eq!(stale["ok"], false);
        assert_eq!(stale["code"], "stale");
        assert!(stale["mtime_ns"].as_str().is_some());
        assert_eq!(std::fs::read_to_string(&target).unwrap(), "{\"a\":2}", "устаревший черновик не записан");
        let fresh = stale["mtime_ns"].as_str().unwrap().to_string();
        let ok = config_save_at(&target, r#"{"a":3}"#, Some(&fresh)).unwrap();
        assert_eq!(ok["ok"], true);
        assert!(std::fs::read_to_string(&target).unwrap().contains('3'));
        // Пустой отпечаток — старое окно, пишем без проверки.
        assert_eq!(config_save_at(&target, r#"{"a":4}"#, Some(""))
            .unwrap()["ok"], true);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn static_rel_stays_inside_the_folder() {
        use super::static_rel;
        assert_eq!(static_rel("/").as_deref(), Some("index.html"));
        assert_eq!(static_rel("").as_deref(), Some("index.html"));
        assert_eq!(static_rel("/assets/index-abc.js").as_deref(), Some("assets/index-abc.js"));
        assert_eq!(
            static_rel("/assets/%D1%88%D1%80%D0%B8%D1%84%D1%82.woff2").as_deref(),
            Some("assets/шрифт.woff2")
        );
        assert!(static_rel("/../helene.json").is_none());
        assert!(static_rel("/assets/..%2F..%2Fhelene.json").is_none());
        assert!(static_rel("/C:/Windows/win.ini").is_none());
        assert!(static_rel("/a\\b").is_none());
        // Лишние слэши в начале — не выход наружу, а тот же относительный путь.
        assert_eq!(static_rel("//etc").as_deref(), Some("etc"));
    }

    #[test]
    fn static_mime_by_extension() {
        use super::static_mime;
        assert_eq!(static_mime("index.html"), "text/html; charset=utf-8");
        assert_eq!(static_mime("assets/x.js"), "text/javascript; charset=utf-8");
        assert_eq!(static_mime("assets/f.woff2"), "font/woff2");
        assert_eq!(static_mime("noext"), "application/octet-stream");
    }

    /// Страница окна не имеет права залечь в кэше WebView2.
    ///
    /// ЖИВОЙ СЛУЧАЙ 17.09: под общим `no-cache` окно обновлённой программы грузило
    /// старый `index.html`, тот тянул старый бандл, и внутри работал код прошлой
    /// версии — 403 на каждый запрос при живом харнессе. Имя страницы одно и то же
    /// всегда, отпечатка содержимого в нём нет, перепроверять нечем.
    #[test]
    fn the_page_is_never_stored_while_hashed_files_still_are() {
        use super::{cache_rule, static_mime};
        assert_eq!(cache_rule(static_mime("index.html")), "no-store");
        for rel in ["assets/index-Chmg1V9r.js", "assets/x.css", "assets/f.woff2", "favicon.svg"] {
            assert_eq!(cache_rule(static_mime(rel)), "no-cache", "{rel}");
        }
    }

    use super::{
        admin_verdict, agent_name, blocked_script, broker_answer_row, broker_confirm_text,
        broker_wish_ask, broker_wish_id, broker_wishes, decode_config, ensure_desk_token,
        mask_secrets, parse_version, read_desk_token, unconfigured, version_newer, BrokerOp,
        BrokerReceipt, BrokerWish,
    };
    // Брандмауэр, UAC, PowerShell и кодировка консоли — только Windows: этих
    // функций в сборке для macOS нет, и стенды на них помечены так же.
    #[cfg(windows)]
    use super::{
        agent_mode, console_text, expand_env, firewall_add_args, firewall_added,
        firewall_clear_runs, firewall_path, firewall_removed, firewall_rule_name,
        firewall_rule_title, firewall_set_runs, firewall_why, netsh_batch_script,
        netsh_code_words, parse_runas, ps_quote, runas_refusal, runas_script, utf16le_base64,
        BrokerAsk, FirewallPath, NetshDone, RunAs, FIREWALL_SCOPE_HUMAN, PRODUCT, RUNAS_MARK,
    };
    use serde_json::json;

    // ───────────────────────────────────────────── D1/D2: элевация и её дверь
    //
    // Проверяется здесь не «работает netsh», а РАЗВИЛКА и СЛОВА: живьём эти
    // ветки требуют то окна UAC, то установленной службы, и потому проверить их
    // прогоном на столе можно ровно по одной за раз. Цена ошибки в развилке —
    // не «не сработало», а «пошло не тем путём».

    /// Режим агента живёт в СВОЁМ ключе. `mode` в helene.json занят под
    /// местожительство харнесса (local|remote), и режим оттуда читается только
    /// как поломка, которую всё равно надо понять, — ровно как в
    /// localharness/modes.py::stated.
    #[cfg(windows)]
    #[test]
    fn agent_mode_is_read_from_its_own_key() {
        assert_eq!(agent_mode(Some(&json!({ "agent_mode": "service" }))), "service");
        assert_eq!(agent_mode(Some(&json!({ "agent_mode": " Service " }))), "service");
        assert_eq!(
            agent_mode(Some(&json!({ "agent_mode": { "name": "sandbox" } }))),
            "sandbox"
        );
        // `mode` со значением режима — чужой ключ, но понять его надо.
        assert_eq!(agent_mode(Some(&json!({ "mode": "service" }))), "service");
        // А `mode` в своём значении режимом не является ни на минуту.
        assert_eq!(agent_mode(Some(&json!({ "mode": "local" }))), "");
        assert_eq!(agent_mode(Some(&json!({ "mode": "remote" }))), "");
        assert_eq!(agent_mode(Some(&json!({}))), "");
        assert_eq!(agent_mode(None), "");
        // Мусор — это «не записан», а не режим.
        assert_eq!(agent_mode(Some(&json!({ "agent_mode": 7 }))), "");
        assert_eq!(agent_mode(Some(&json!({ "agent_mode": "служба" }))), "");
    }

    /// Дверь выбирается ЖИВЫМ брокером, а не словом в конфиге. Повышенный
    /// процесс не идёт ни в UAC, ни к брокеру: ему некуда повышаться.
    ///
    /// ⚠ Ради чего тест переписан: служба стала опцией ПОВЕРХ режима, режим
    /// бывает только sandbox|interactive, и старое условие `mode == "service"`
    /// не выполнялось бы никогда — дверь брокера умерла бы молча.
    #[cfg(windows)]
    #[test]
    fn the_door_is_chosen_by_the_mode() {
        // Брокер жив — идём брокером в ЛЮБОМ режиме: служба ставится ровно ради
        // того, чтобы не дёргать UAC на каждый чих.
        assert_eq!(firewall_path(false, "sandbox", true), FirewallPath::Broker);
        assert_eq!(firewall_path(false, "interactive", true), FirewallPath::Broker);
        assert_eq!(firewall_path(false, "", true), FirewallPath::Broker);
        // Брокера нет — окно Windows, тоже в любом режиме.
        assert_eq!(firewall_path(false, "sandbox", false), FirewallPath::Uac);
        assert_eq!(firewall_path(false, "interactive", false), FirewallPath::Uac);
        assert_eq!(firewall_path(false, "", false), FirewallPath::Uac);
        // Старый конфиг со словом «service» понимаем и без живой трубы: отказ
        // брокера объяснит владельцу, что со службой, а молчание — не объяснит.
        assert_eq!(firewall_path(false, "service", false), FirewallPath::Broker);
        // Повышенному процессу повышаться некуда — ни одной из дверей.
        for mode in ["sandbox", "interactive", "service", ""] {
            for broker in [true, false] {
                assert_eq!(firewall_path(true, mode, broker), FirewallPath::Direct);
            }
        }
    }

    /// Два пути к одному действию обязаны обещать владельцу ОДНО И ТО ЖЕ. Так
    /// уже расходилось: правило ставили и служба, и окно, по-разному.
    #[cfg(windows)]
    #[test]
    fn both_doors_promise_the_same_thing() {
        let uac = firewall_added(8094, FirewallPath::Uac);
        let broker = firewall_added(8094, FirewallPath::Broker);
        let direct = firewall_added(8094, FirewallPath::Direct);
        for said in [&uac, &broker, &direct] {
            assert!(said.contains(FIREWALL_SCOPE_HUMAN), "{said}");
            assert!(said.contains("для порта 8094 добавлено"), "{said}");
        }
        // Разница между дверями — ровно одно слово о правах, и ничего больше.
        assert_eq!(uac.replace(" с правами администратора", ""), direct);
        assert_eq!(broker.replace(" через брокера службы", ""), direct);
        assert_ne!(uac, broker);
        let cleared = firewall_removed(8094, FirewallPath::Broker);
        assert_eq!(cleared.replace(" через брокера службы", ""), firewall_removed(8094, FirewallPath::Direct));
    }

    /// Пачка: снос старого, потом добавление своего. Порядок важен — кодом
    /// пачки считается код ПОСЛЕДНЕЙ команды, а снос несуществующего правила
    /// netsh честно считает ошибкой.
    #[cfg(windows)]
    #[test]
    fn a_rule_is_replaced_not_piled_up() {
        let runs = firewall_set_runs(8094, Some(r"C:\Helene\runtime\python.exe"));
        assert_eq!(runs.len(), 2);
        assert!(runs[0].contains(&"delete".to_string()), "{:?}", runs[0]);
        assert!(runs[1].contains(&"add".to_string()), "{:?}", runs[1]);
        // Снос и добавление — про ОДНО имя, иначе снос ничего не находит.
        let name = firewall_rule_name(8094);
        assert!(runs[0].contains(&name), "{:?}", runs[0]);
        assert!(runs[1].contains(&format!("name={}", firewall_rule_title(PRODUCT, 8094))));
        assert_eq!(firewall_clear_runs(8094), vec![runs[0].clone()]);
    }

    /// Скрипт для повышенного powershell: аргументы целы, а пустой
    /// `$LASTEXITCODE` не превращается в «успех».
    #[cfg(windows)]
    #[test]
    fn the_elevated_script_keeps_arguments_whole() {
        let runs = firewall_set_runs(8094, Some(r"C:\Program Files\Hélène\python.exe"));
        let script = netsh_batch_script(r"C:\Windows\System32\netsh.exe", &runs);
        assert!(!script.contains('\n'), "скрипт должен быть одной строкой: {script}");
        assert!(!script.contains('"'), "двойных кавычек в скрипте быть не должно: {script}");
        assert!(script.contains("$LASTEXITCODE = 9009"), "{script}");
        assert!(script.ends_with("exit [int]$LASTEXITCODE"), "{script}");
        // Имя правила со скобками и пробелом — одним аргументом.
        assert!(script.contains("'name=Helene (8094)'"), "{script}");
        assert!(script.contains(r"'program=C:\Program Files\Hélène\python.exe'"), "{script}");
        // Обе команды на месте, в своём порядке.
        let delete = script.find("'delete'").expect("нет сноса");
        let add = script.find("'add'").expect("нет добавления");
        assert!(delete < add, "{script}");
        // Своя кавычка внутри аргумента удваивается, а не рвёт строку.
        assert_eq!(ps_quote("это 'моё'"), "'это ''моё'''");
    }

    /// -EncodedCommand — единственный способ передать пачку целиком:
    /// `Start-Process -ArgumentList` склеивает элементы пробелом и не берёт их в
    /// кавычки, так что аргумент с пробелом приехал бы разрезанным.
    #[cfg(windows)]
    #[test]
    fn encoded_command_is_utf16le_base64() {
        // Известный вектор: "hi" в UTF-16LE — 68 00 69 00.
        assert_eq!(utf16le_base64("hi"), "aABpAA==");
        assert_eq!(utf16le_base64(""), "");
        let encoded = utf16le_base64("& 'C:\\путь с пробелом\\netsh.exe' 'name=Helene (8094)'");
        assert!(encoded.len() % 4 == 0, "{encoded}");
        assert!(
            encoded.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'+' || b == b'/' || b == b'='),
            "в base64 попал чужой знак: {encoded}"
        );
        // Ни пробела, ни кавычки — иначе склейка в -ArgumentList его разрежет.
        assert!(!encoded.contains(' ') && !encoded.contains('\''), "{encoded}");
    }

    /// Внешний скрипт печатает ОДНУ строку об исходе — и она разбирается.
    #[cfg(windows)]
    #[test]
    fn the_outcome_of_the_uac_window_is_read_not_guessed() {
        let script = runas_script(
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "aABpAA==",
            r"C:\Windows\System32",
        );
        assert!(script.contains(RUNAS_MARK), "{script}");
        assert!(script.contains("$i.Verb = 'runas'"), "{script}");
        assert!(script.contains("-EncodedCommand aABpAA=="), "{script}");
        assert!(script.contains(r"$i.WorkingDirectory = 'C:\Windows\System32'"), "{script}");
        assert!(!script.contains('\n'), "{script}");
        // ⚠ Именно .NET, а не Start-Process: тот теряет код Windows, и «Нет» в
        // окне UAC становится неотличимо от любой другой поломки.
        assert!(!script.contains("Start-Process"), "{script}");

        assert_eq!(parse_runas("HELENE-RUNAS ok 0"), Some(RunAs::Code(0)));
        assert_eq!(parse_runas("шум\r\nHELENE-RUNAS ok 1\r\n"), Some(RunAs::Code(1)));
        assert_eq!(parse_runas("HELENE-RUNAS fail 1223"), Some(RunAs::Refused(1223)));
        assert_eq!(parse_runas("HELENE-RUNAS unknown"), Some(RunAs::Unknown));
        // Метки нет — это «не знаю», а не «получилось». Прежний код на этом
        // месте молча выходил нулём, то есть рапортовал об успехе.
        assert_eq!(parse_runas("Start-Process : всё плохо"), None);
        assert_eq!(parse_runas(""), None);
    }

    /// Отказ называется словами. «Нет» в окне UAC — не поломка, и говорить о
    /// нём надо так, чтобы человек понял, что произошло.
    #[cfg(windows)]
    #[test]
    fn a_refusal_is_said_in_words() {
        // Кнопку UAC подписывает Windows на своём языке — текст обязан назвать
        // исход («не подтверждено»), а не обещать слово «Нет» с чужого экрана.
        let cancelled = runas_refusal(1223);
        assert!(cancelled.contains("не подтверждено"), "{cancelled}");
        assert!(cancelled.contains("политик"), "{cancelled}");
        assert!(runas_refusal(1260).contains("политик"));
        assert!(runas_refusal(0).contains("не назвала причины"));
        // Незнакомый код не проглатывается: владелец увидит хотя бы число.
        assert!(runas_refusal(4242).contains("4242"));
    }

    /// 9009 — не ответ netsh, а наш признак «программа не запустилась вовсе».
    /// Ноль на этом месте означал бы «получилось».
    #[cfg(windows)]
    #[test]
    fn a_code_is_explained_not_just_printed() {
        let missing = NetshDone { code: 9009, said: String::new() };
        assert!(netsh_code_words(&missing).contains("не нашла netsh.exe"));
        let refused = NetshDone { code: 1, said: "Требуется повышение прав".into() };
        let words = netsh_code_words(&refused);
        assert!(words.contains("код 1") && words.contains("Требуется повышение"), "{words}");
    }

    /// Просьба к брокеру собирается такой, какой служба её ПРИМЕТ. Проверка
    /// идёт разбором той же стороны, что стоит в службе (common/broker.rs):
    /// разъедься эти половины, и отказ вылез бы только живьём, у владельца.
    #[cfg(windows)]
    #[test]
    fn the_broker_is_asked_the_way_the_service_expects() {
        for (adding, runs) in [
            (true, firewall_set_runs(8094, Some(r"C:\Helene\runtime\python.exe"))),
            (false, firewall_clear_runs(8094)),
        ] {
            let why = firewall_why(8094, adding);
            for args in &runs {
                let ask = BrokerAsk::new(
                    "0123456789abcdef",
                    BrokerOp::Exec,
                    // Полный путь — голое имя брокер отвергает: Windows искала
                    // бы его сначала в папке процесса службы.
                    r"C:\Windows\System32\netsh.exe",
                    args,
                    &why,
                );
                let back = BrokerAsk::parse(&ask.to_json()).expect("служба не приняла бы просьбу");
                assert_eq!(&back.args, args);
                assert_eq!(back.why, why);
                assert!(back.why.len() >= 3 && !back.why.contains('\n'), "{why}");
            }
        }
    }

    /// R8: что окно узнаёт о правах. Без этой ручки экран режимов не мог честно
    /// сказать, почему вариант со службой недоступен, — а «недоступно без
    /// причины» человек читает как поломку продукта.
    #[test]
    fn what_the_window_learns_about_admin_rights() {
        // Уже повышены: и «админ», и «повысится» — да, независимо от типа токена.
        for kind in [0, 1, 2, 3] {
            let v = admin_verdict(true, kind);
            assert_eq!(v["admin"], true, "{v}");
            assert_eq!(v["can_elevate"], true, "{v}");
        }
        // Урезанный токен — администратор под UAC: повысится сам, по кнопке.
        let limited = admin_verdict(false, 3);
        assert_eq!(limited["admin"], false);
        assert_eq!(limited["can_elevate"], true);
        assert_eq!(limited["elevation"], "limited");
        // Обычный пользователь: не «нельзя», а «нужен администратор рядом».
        // Обещать ему службу по кнопке было бы враньём.
        let plain = admin_verdict(false, 1);
        assert_eq!(plain["can_elevate"], false);
        assert_eq!(plain["elevation"], "default");
        // Не спросили — это «не знаю», и оно обязано отличаться от «точно нет».
        assert_eq!(admin_verdict(false, 0)["elevation"], "unknown");
        assert_eq!(admin_verdict(false, 0)["can_elevate"], false);
        // Полный токен без повышения бывает при выключенном UAC у админа: тогда
        // process_is_elevated() уже сказал «да», и сюда мы не попадаем.
        assert_eq!(admin_verdict(false, 2)["elevation"], "full");
    }

    // ───────────────────────────────── R6: просьба агента к брокеру и подпись
    //
    // Проверяется здесь не «выполнилось», а ГРАНИЦА: что владельцу показали, что
    // уехало брокеру и что записалось. Живьём эта ветка требует установленной
    // службы и живого человека у окна подтверждения, то есть прогоном ловится
    // по одному случаю за раз.

    /// Просьба разбирается, а негодная — ОСТАЁТСЯ ОТВЕЧАЕМОЙ. Молчание в ответ
    /// агент читает как «ещё думают» и ждёт до таймаута; отказ он читает как
    /// отказ и идёт дальше.
    #[test]
    fn a_wish_is_parsed_and_a_bad_one_can_still_be_answered() {
        let raw = r#"{"v":1,"requests":[
            {"id":"a1","op":"spawn_interactive","cmd":"C:\\Windows\\System32\\notepad.exe",
             "args":["C:\\файл.txt"],"why":"владелец попросил открыть файл","at_unix":100},
            {"id":"a2","op":"rm -rf","why":"шутка"},
            {"id":"a3","op":"exec","cmd":"C:\\a.exe","args":"раз && два","why":"склейка"},
            {"op":"exec","cmd":"C:\\a.exe","why":"без id"},
            {"id":"жирный+id","op":"exec","cmd":"C:\\a.exe","why":"негодный id"}
        ]}"#;
        let wishes = broker_wishes(raw);
        // Две строки выпали целиком: без годного id ответ отправить некуда.
        assert_eq!(wishes.len(), 3, "{wishes:?}");
        let first = wishes[0].as_ref().expect("первая просьба должна разобраться");
        assert_eq!(first.id, "a1");
        assert_eq!(first.op, BrokerOp::SpawnInteractive);
        assert_eq!(first.args, vec![r"C:\файл.txt".to_string()]);
        assert_eq!(first.at_unix, 100);
        let bad = wishes[1].as_ref().unwrap_err();
        assert_eq!(bad.id, "a2");
        assert!(bad.why.contains("какой дверью"), "{}", bad.why);
        let bad = wishes[2].as_ref().unwrap_err();
        assert_eq!(bad.id, "a3");
        assert!(bad.why.contains("массив строк"), "{}", bad.why);
        // Мусор вместо файла — это «просьб нет», а не паника.
        assert!(broker_wishes("не json").is_empty());
        assert!(broker_wishes("{}").is_empty());
    }

    /// Идентификатор не «чистится», а проверяется: подчищенный id перестал бы
    /// совпадать с тем, что ждёт харнесс, а перевод строки в нём подделал бы
    /// соседние записи журнала.
    #[test]
    fn a_wish_id_is_checked_not_cleaned() {
        assert_eq!(broker_wish_id(" a-1_B "), Some("a-1_B".to_string()));
        for bad in ["", "   ", "a b", "a\nб", "ид", "a)(", &"x".repeat(65)] {
            assert!(broker_wish_id(bad).is_none(), "пропустил «{bad}»");
        }
    }

    /// Секрет брокера подставляет ОБОЛОЧКА. Свой токен агент не знает и знать
    /// не должен: в песочнице файл секрета ему вообще не читается, а если бы он
    /// умел присылать токен сам, подтверждение владельца стало бы формальностью.
    #[test]
    fn the_secret_is_the_shells_and_never_the_agents() {
        let raw = r#"{"requests":[{"id":"a1","op":"exec","token":"чужой-токен",
            "cmd":"C:\\Windows\\System32\\netsh.exe","args":["advfirewall"],
            "why":"правило для телефона"}]}"#;
        let wish = broker_wishes(raw).remove(0).expect("просьба не разобралась");
        let ask = broker_wish_ask(&wish, "0123456789abcdef").expect("служба не приняла бы");
        assert_eq!(ask.token, "0123456789abcdef");
        assert_eq!(ask.id, "a1");
        // И сам json просьбы, уехавший брокеру, чужого токена не несёт.
        assert!(!ask.to_json().contains("чужой-токен"));
    }

    /// Что служба отвергнет, владельцу не показывают. Иначе он подписывал бы
    /// отказы: нажал «да» — и получил ошибку разбора вместо дела.
    #[test]
    fn the_owner_is_never_asked_about_a_request_the_service_would_refuse() {
        let base = BrokerWish {
            id: "a1".into(),
            op: BrokerOp::Exec,
            cmd: r"C:\Windows\System32\netsh.exe".into(),
            args: vec!["advfirewall".into()],
            why: "правило для телефона".into(),
            timeout_sec: 60,
            at_unix: 0,
        };
        assert!(broker_wish_ask(&base, "0123456789abcdef").is_ok());
        // Пустое «зачем» — единственная человеческая строка журнала.
        let mute = BrokerWish { why: "  ".into(), ..base.clone() };
        assert!(broker_wish_ask(&mute, "0123456789abcdef").unwrap_err().contains("зачем"));
        // Перевод строки в «зачем» подделал бы соседние записи журнала.
        let forged = BrokerWish { why: "раз\nвсё хорошо".into(), ..base.clone() };
        assert!(broker_wish_ask(&forged, "0123456789abcdef")
            .unwrap_err()
            .contains("одной строкой"));
        // Голое имя программы = подмена файла в папке установки правами СИСТЕМЫ.
        let bare = BrokerWish { cmd: "netsh.exe".into(), ..base.clone() };
        assert!(broker_wish_ask(&bare, "0123456789abcdef")
            .unwrap_err()
            .contains("полным путём"));
        // Срок сверх потолка службы.
        let forever = BrokerWish { timeout_sec: 100_000, ..base };
        assert!(broker_wish_ask(&forever, "0123456789abcdef").unwrap_err().contains("600"));
    }

    /// Экран подтверждения — единственное место, по которому человек решает
    /// отдать права системы. В нём обязаны быть три вещи: чьими правами,
    /// ЧТО именно запустится и что «зачем» — слова агента, а не факт.
    #[test]
    fn the_owner_sees_the_door_the_command_and_whose_words_these_are() {
        let wish = BrokerWish {
            id: "a1".into(),
            op: BrokerOp::Exec,
            cmd: r"C:\Windows\System32\netsh.exe".into(),
            args: vec!["advfirewall".into(), "name=Helene (8094)".into()],
            why: "правило брандмауэра для телефона".into(),
            timeout_sec: 60,
            at_unix: 0,
        };
        let text = broker_confirm_text_for(&wish, false);
        assert!(text.contains("ПРАВАМИ СИСТЕМЫ"), "{text}");
        assert!(text.contains("его слова"), "{text}");
        assert!(text.contains("правило брандмауэра для телефона"), "{text}");
        assert!(
            text.contains(r#"C:\Windows\System32\netsh.exe advfirewall "name=Helene (8094)""#),
            "{text}"
        );
        // «Отказ тоже будет записан» — с заглавной, в начале предложения; стенду
        // важно слово, а не регистр.
        assert!(text.to_lowercase().contains("отказ"), "{text}");
        // Вторая дверь называется своими правами, а не системными: путать их
        // нельзя, разница между ними и есть весь вопрос.
        let side = BrokerWish { op: BrokerOp::SpawnInteractive, ..wish.clone() };
        let text = broker_confirm_text_for(&side, false);
        assert!(text.contains("твоими правами"), "{text}");
        assert!(!text.contains("ПРАВАМИ СИСТЕМЫ"), "{text}");

        // Длинная команда не уводит дело за нижний край окна МОЛЧА: обрезку
        // владелец видит, и она сама названа поводом отказать.
        let padded = BrokerWish {
            args: vec![" ".repeat(4000), "del /q C:\\*".into()],
            ..side
        };
        let text = broker_confirm_text_for(&padded, false);
        assert!(text.contains("КОМАНДА ОБРЕЗАНА"), "{text}");
        assert!(text.contains("повод отказать"), "{text}");
        // Хвост, ради которого набивали пробелы, до окна и не доехал — но и
        // окно не делает вид, что показало команду целиком.
        assert!(!text.contains("del /q"), "хвост показан, значит обрезки не было");
        assert!(text.len() < 4000, "окно всё-таки распухло: {}", text.len());
        // На macOS «прав системы» нет: та же дверь названа правами администратора,
        // и сказано, что пароль спросит сама система. Стенд разбирает обе
        // платформы явно — на раннере macOS он падал, беря слова своей ОС.
        let text = broker_confirm_text_for(&wish, true);
        assert!(text.contains("ПРАВАМИ АДМИНИСТРАТОРА"), "{text}");
        assert!(text.contains("пароль"), "{text}");
        assert!(!text.contains("ПРАВАМИ СИСТЕМЫ"), "{text}");
        assert!(text.contains("правило брандмауэра для телефона"), "{text}");
        let text = broker_confirm_text_for(&BrokerWish { op: BrokerOp::SpawnInteractive, ..wish.clone() }, true);
        assert!(text.contains("твоими правами"), "{text}");
    }

    /// Ответ агенту — не «успех», а всё, что видела оболочка. И в нём нет
    /// секрета: файл ответов лежит в дереве, то есть в доме агента.
    #[test]
    fn the_answer_carries_the_whole_receipt_and_no_secret() {
        let wish = BrokerWish {
            id: "a1".into(),
            op: BrokerOp::Exec,
            cmd: r"C:\Windows\System32\netsh.exe".into(),
            args: vec!["advfirewall".into()],
            why: "правило для телефона".into(),
            timeout_sec: 60,
            at_unix: 0,
        };
        let receipt = BrokerReceipt {
            id: "a1".into(),
            ok: true,
            op: "exec".into(),
            code: Some(1),
            pid: Some(4242),
            out: "вывод".into(),
            err: "ошибки".into(),
            ms: 120,
            at: "[04.09.2026 13:00:00]".into(),
            why: "правило для телефона".into(),
            note: String::new(),
        };
        let row = broker_answer_row(&wish, "allowed", "", Some(&receipt));
        assert_eq!(row["id"], "a1");
        assert_eq!(row["decision"], "allowed");
        assert_eq!(row["code"], 1);
        assert_eq!(row["pid"], 4242);
        assert_eq!(row["out"], "вывод");
        assert_eq!(row["ms"], 120);
        // Незавершённый процесс — код ОТСУТСТВУЕТ, а не «0».
        let waiting = BrokerReceipt { code: None, ..receipt };
        assert!(broker_answer_row(&wish, "failed", "не дождался", Some(&waiting))["code"].is_null());
        // Отказ до брокера: квитанции нет вовсе, но причина есть всегда.
        let refused = broker_answer_row(&wish, "refused", "владелец отказал", None);
        assert_eq!(refused["note"], "владелец отказал");
        assert!(refused.get("code").is_none());
        assert!(!refused.to_string().contains("token"));
    }

    // ───────────────────────────── брокер на macOS: оболочка исполняет сама
    //
    // Живьём эта ветка требует Mac с человеком у двух диалогов; здесь — всё
    // чистое: слова окна, граница просьбы, строка для sh, скрипт osascript и
    // разбор его вывода. Гоняется на любой ОС.

    fn mac_wish(op: BrokerOp, cmd: &str, args: &[&str], why: &str) -> BrokerWish {
        BrokerWish {
            id: "m1".into(),
            op,
            cmd: cmd.into(),
            args: args.iter().map(|a| a.to_string()).collect(),
            why: why.into(),
            timeout_sec: 60,
            at_unix: 0,
        }
    }

    /// На macOS `exec` — права администратора через системный диалог пароля,
    /// «прав системы» там нет: окно обязано говорить то, что произойдёт. На
    /// Windows слова прежние.
    #[test]
    fn on_macos_the_exec_door_is_the_administrator_password_dialog() {
        let wish = mac_wish(BrokerOp::Exec, "/bin/mkdir", &["-p", "/usr/local/helene"], "создать папку");
        let mac = broker_confirm_text_for(&wish, true);
        assert!(mac.contains("ПРАВАМИ АДМИНИСТРАТОРА"), "{mac}");
        assert!(mac.contains("спросит твой пароль"), "{mac}");
        assert!(!mac.contains("ПРАВАМИ СИСТЕМЫ"), "{mac}");
        assert!(mac.contains("/bin/mkdir -p /usr/local/helene"), "{mac}");
        let win = broker_confirm_text_for(&wish, false);
        assert!(win.contains("ПРАВАМИ СИСТЕМЫ"), "{win}");
        assert!(!win.contains("пароль"), "{win}");
        // Вторая дверь на обеих платформах — своими правами.
        let side = mac_wish(BrokerOp::SpawnInteractive, "/usr/bin/id", &[], "кто я");
        assert!(broker_confirm_text_for(&side, true).contains("твоими правами"));
        assert_eq!(broker_confirm_text(&wish), broker_confirm_text_for(&wish, cfg!(target_os = "macos")));
    }

    /// Граница на macOS — та же, что у службы, только путь POSIX. Что
    /// отвергается здесь, владельцу не показывают.
    #[test]
    fn macos_border_wants_an_absolute_path_and_the_same_why() {
        let ok = mac_wish(BrokerOp::Exec, "/bin/mkdir", &["-p", "/usr/local/helene"], "создать папку");
        assert!(mac_wish_check(&ok).is_ok());
        assert!(mac_wish_check(&mac_wish(BrokerOp::Ping, "", &[], "проверка связи")).is_ok());
        let bare = mac_wish(BrokerOp::Exec, "mkdir", &[], "создать папку");
        assert!(mac_wish_check(&bare).unwrap_err().contains("абсолютным путём"));
        let dots = mac_wish(BrokerOp::Exec, "/usr/bin/../bin/mkdir", &[], "создать папку");
        assert!(mac_wish_check(&dots).unwrap_err().contains("«..»"));
        let none = mac_wish(BrokerOp::SpawnInteractive, "", &[], "создать папку");
        assert!(mac_wish_check(&none).unwrap_err().contains("нет команды"));
        let firewall = mac_wish(BrokerOp::Firewall, "", &["8094"], "правило для телефона");
        assert!(mac_wish_check(&firewall).unwrap_err().contains("брандмауэр спрашивает сам"));
        let mute = BrokerWish { why: "  ".into(), ..ok.clone() };
        assert!(mac_wish_check(&mute).unwrap_err().contains("зачем"));
        let forged = BrokerWish { why: "раз\nвсё хорошо".into(), ..ok.clone() };
        assert!(mac_wish_check(&forged).unwrap_err().contains("одной строкой"));
        let forever = BrokerWish { timeout_sec: 100_000, ..ok.clone() };
        assert!(mac_wish_check(&forever).unwrap_err().contains("600"));
        let never = BrokerWish { timeout_sec: 0, ..ok.clone() };
        assert!(mac_wish_check(&never).is_err());
        let nul = BrokerWish { args: vec!["a\0b".into()], ..ok };
        assert!(mac_wish_check(&nul).unwrap_err().contains("нулевой байт"));
    }

    /// Строка для /bin/sh: каждый аргумент в своих кавычках, одинарная кавычка
    /// внутри не рвёт их, а `$(…)`, пробелы и звёздочки sh не разворачивает.
    #[test]
    fn the_shell_line_keeps_every_argument_whole() {
        assert_eq!(sh_quote("abc"), "abc");
        assert_eq!(sh_quote("/usr/local/bin/x-y_z.1"), "/usr/local/bin/x-y_z.1");
        assert_eq!(sh_quote(""), "''");
        assert_eq!(sh_quote("a b"), "'a b'");
        assert_eq!(sh_quote("it's"), "'it'\\''s'");
        assert_eq!(sh_quote("$(rm -rf /)"), "'$(rm -rf /)'");
        assert_eq!(sh_quote("*"), "'*'");
        assert_eq!(
            mac_shell_line("/bin/mkdir", &["-p".into(), "/usr/local/my dir".into()]),
            "/bin/mkdir -p '/usr/local/my dir'"
        );
        assert_eq!(mac_shell_line("/usr/bin/id", &[]), "/usr/bin/id");
    }

    /// Скрипт osascript: команда под администратором с подсказкой владельцу,
    /// stderr слит в stdout, код печатается меткой, переводы строк не
    /// переписываются — и всё экранировано для AppleScript (кавычка, слэш).
    #[test]
    fn the_admin_script_is_escaped_for_applescript_and_carries_the_code_mark() {
        let script = mac_admin_script("/bin/mkdir", &["/tmp/it's here".into()], "Hélène: зачем \"так\"", None);
        assert!(script.starts_with("do shell script \""), "{script}");
        assert!(
            script.ends_with("with administrator privileges without altering line endings"),
            "{script}"
        );
        // Одинарная кавычка — для sh, и слэш перед ней удвоен — для AppleScript.
        assert!(script.contains(r#"'/tmp/it'\\''s here'"#), "{script}");
        assert!(script.contains(r#" 2>&1; printf '\\nHELENE-CODE %s' \"$?\""#), "{script}");
        assert!(script.contains(r#"with prompt "Hélène: зачем \"так\"""#), "{script}");
        assert!(!script.contains('\n'), "перевод строки порвал бы -e: {script}");
    }

    /// ⚠ п.6 (судьи 19.09): владелец подписывает ПУТЬ, а исполняется
    /// СОДЕРЖИМОЕ. Сверка отпечатка обязана стоять ПЕРЕД командой и уже под
    /// правами администратора — иначе подмена после «Да» проходит незамеченной.
    #[test]
    fn the_admin_script_checks_the_file_before_running_it() {
        let script = mac_admin_script("/Users/tom/bin/tool", &[], "зачем", Some("feed0000"));
        let check = script.find("shasum").expect("нет сверки отпечатка");
        let run = script.find("/Users/tom/bin/tool 2>&1").expect("нет самой команды");
        assert!(check < run, "сверка обязана стоять ДО команды: {script}");
        assert!(script.contains("feed0000"), "{script}");
        assert!(script.contains("exit 91"), "{script}");
        assert!(script.contains("подменили"), "отказ обязан быть словами: {script}");
        // Системный путь отпечатка не несёт — лишней строки в скрипте нет.
        let plain = mac_admin_script("/usr/bin/id", &[], "зачем", None);
        assert!(!plain.contains("shasum"), "{plain}");
    }

    /// Системные папки — те, куда без root не пишут. Дом владельца и его
    /// «Загрузки» системными не считаются никогда.
    #[test]
    fn system_folders_need_no_fingerprint() {
        for good in ["/usr/bin/id", "/bin/sh", "/sbin/ping", "/System/Library/x",
                     "/opt/homebrew/bin/brew", "/usr/local/bin/tool"] {
            assert!(mac_system_path(good), "{good}");
        }
        for bad in ["/Users/tom/bin/tool", "/tmp/x", "/Volumes/usb/id", "/opt/other/x",
                    "/private/tmp/id"] {
            assert!(!mac_system_path(bad), "{bad}");
        }
    }

    /// Второй экран — системный диалог пароля. Туда едет не только «зачем», но и
    /// сама команда: подписывать вслепую владельцу больше не предлагают.
    #[test]
    fn the_password_prompt_shows_the_command_too() {
        let said = mac_password_prompt("Hélène", "почистить кэш", "/usr/bin/rm -rf /tmp/x");
        assert!(said.contains("почистить кэш"), "{said}");
        assert!(said.contains("/usr/bin/rm -rf /tmp/x"), "{said}");
        // Длинную команду режем и ГОВОРИМ, что обрезали: молча урезанная строка
        // в окне подписи — это враньё в сторону спокойствия.
        let long = "x".repeat(400);
        let cut = mac_password_prompt("Hélène", "зачем", &long);
        assert!(cut.contains("обрезано, всего 400 знаков"), "{cut}");
        assert!(cut.chars().count() < 400, "{cut}");
    }

    /// ⚠ п.12: ответ диалога читается по НОМЕРУ кнопки. Слово «Да» на другом
    /// языке интерфейса просто не нашлось бы, и согласие читалось бы отказом.
    #[test]
    fn the_confirm_dialog_speaks_the_ui_language_and_answers_by_index() {
        assert_eq!(mac_yes_no("ru"), ("Да", "Нет"));
        assert_eq!(mac_yes_no("fr"), ("Oui", "Non"));
        assert_eq!(mac_yes_no("zh"), ("是", "否"));
        // Умолчание — английский, как в lang.ts: язык, которого человек не
        // знает, это не «запасной вариант», а закрытая дверь.
        assert_eq!(mac_yes_no(""), ("Yes", "No"));
        assert_eq!(mac_yes_no("de"), ("Yes", "No"));

        let cfg = serde_json::json!({"lang": "ru-RU"});
        assert_eq!(ui_lang_from(&cfg), "ru");
        assert_eq!(ui_lang_from(&serde_json::json!({"ui": {"lang": "FR"}})), "fr");
        assert_eq!(ui_lang_from(&serde_json::json!({})), "");

        let (yes, no) = mac_yes_no("ru");
        let script = mac_confirm_script("Заголовок", "Текст", yes, no);
        // Кнопка по умолчанию и отмена — первая, то есть «Нет»: Enter и Esc
        // отказывают.
        assert!(script.contains("default button 1"), "{script}");
        assert!(script.contains("cancel button 1"), "{script}");
        // «Нет» стоит первой, «Да» второй — окно вернёт «2» на согласие.
        let no_at = script.find("\"Нет\"").expect("нет кнопки отказа");
        let yes_at = script.find("\"Да\"").expect("нет кнопки согласия");
        assert!(no_at < yes_at, "{script}");
        assert!(script.contains("(i as text)"), "ответ обязан быть номером: {script}");
    }

    /// ⚠ п.8: ключ дерева предъявляется только тому, кто назвался нашим
    /// продуктом И нашим корнем установки. «Ответил 403» — это не «свой».
    #[test]
    fn the_key_goes_only_to_our_own_install() {
        let root = std::env::temp_dir();
        let ours = WhoSaid { product: "Hélène".into(), root: root.display().to_string() };
        assert!(who_is_ours(&ours, "Hélène", &root));
        // Чужой продукт на том же корне — не наш.
        let other = WhoSaid { product: "Praxis".into(), root: root.display().to_string() };
        assert!(!who_is_ours(&other, "Hélène", &root));
        // Наш продукт, но другая установка — тоже не наш: у неё своё дерево.
        let elsewhere = WhoSaid { product: "Hélène".into(), root: root.join("другая").display().to_string() };
        assert!(!who_is_ours(&elsewhere, "Hélène", &root));
        // Промолчал о корне — не наш: молчание прибора это не факт.
        let mute = WhoSaid { product: "Hélène".into(), root: String::new() };
        assert!(!who_is_ours(&mute, "Hélène", &root));
    }

    /// ⚠ п.5: по пожатому ребёнку сигнал группе не идёт — его номер мог
    /// достаться чужому процессу.
    #[test]
    fn a_reaped_child_is_never_signalled_by_group() {
        assert!(!may_signal_group(true, true), "пожатый — не наш, кто бы там ни жил");
        assert!(!may_signal_group(true, false));
        assert!(!may_signal_group(false, false), "никого нет — некого и бить");
        assert!(may_signal_group(false, true));
    }

    /// ⚠ п.2: граница macOS отвергает невидимые знаки во всех трёх полях —
    /// владелец решает по тому, что увидел в окне.
    #[test]
    fn the_mac_border_refuses_invisible_separators() {
        let wish = |cmd: &str, arg: &str, why: &str| BrokerWish {
            id: "a1".into(),
            op: BrokerOp::Exec,
            cmd: cmd.into(),
            args: vec![arg.into()],
            why: why.into(),
            timeout_sec: 60,
            at_unix: 0,
        };
        assert!(mac_wish_check(&wish("/usr/bin/id", "-u", "узнать номер")).is_ok());

        let why_bad = mac_wish_check(&wish("/usr/bin/id", "-u", "узнать\u{2028}rm -rf /"))
            .expect_err("U+2028 в «зачем» обязан быть отказом");
        assert!(why_bad.contains("невидимый"), "{why_bad}");

        let arg_bad = mac_wish_check(&wish("/usr/bin/id", "-u\u{200e}", "узнать номер"))
            .expect_err("знак формата в аргументе обязан быть отказом");
        assert!(arg_bad.contains("невидимые знаки"), "{arg_bad}");

        let cmd_bad = mac_wish_check(&wish("/usr/bin/i\u{00ad}d", "-u", "узнать номер"))
            .expect_err("мягкий перенос в пути обязан быть отказом");
        assert!(cmd_bad.contains("невидимые знаки"), "{cmd_bad}");

        // Неразрывный пробел — обычный видимый знак, из-за него не отказываем.
        assert!(mac_wish_check(&wish("/usr/bin/id", "-u", "узнать\u{00a0}номер")).is_ok());
    }

    /// Вывод команды и её код — из того, что вернул osascript; без метки код
    /// НЕИЗВЕСТЕН, а не ноль.
    #[test]
    fn the_code_mark_is_read_back_and_never_invented() {
        assert_eq!(mac_split_code("hello\nworld\nHELENE-CODE 3\n"), ("hello\nworld".to_string(), Some(3)));
        assert_eq!(mac_split_code("HELENE-CODE 0"), (String::new(), Some(0)));
        assert_eq!(mac_split_code("no mark at all"), ("no mark at all".to_string(), None));
        assert_eq!(mac_split_code("x\nHELENE-CODE zz"), ("x\nHELENE-CODE zz".to_string(), None));
        assert_eq!(mac_split_code(""), (String::new(), None));
    }

    /// Кнопки «Открыть настройки» ведут ровно в два раздела; чужое слово — не
    /// «общая страница настроек», а отказ.
    #[test]
    fn privacy_panes_are_exactly_two() {
        assert_eq!(
            privacy_pane_url("screen"),
            Some("x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")
        );
        assert_eq!(
            privacy_pane_url(" accessibility "),
            Some("x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")
        );
        assert_eq!(privacy_pane_url("camera"), None);
        assert_eq!(privacy_pane_url(""), None);
    }

    /// netsh отвечает в кодировке консоли. Без разбора OEM причина отказа
    /// приезжала владельцу сплошными «□».
    ///
    /// ⚠ Страницы спрашиваются ПОИМЁННО. Проверять `console_text` на байтах
    /// cp866 нельзя с тех пор, как он спрашивает страницу у системы: те же два
    /// байта — «Ок» только на русской Windows, а на английской это «Ä¬», и
    /// такой стенд краснел бы ровно там, где код как раз прав.
    #[cfg(windows)]
    #[test]
    fn what_netsh_said_is_readable() {
        assert_eq!(decode_codepage(&[0x8E, 0xAA], 866), "Ок");
        assert_ne!(decode_codepage(&[0x8E, 0xAA], 437), "Ок");
        assert_eq!(console_text("Ok\r\n".as_bytes()), "Ok");
        assert_eq!(console_text("уже UTF-8".as_bytes()), "уже UTF-8");
    }

    /// Сужение правила не зависит от двери: аргументы одни и те же, из
    /// common/firewall_rule.rs.
    #[cfg(windows)]
    #[test]
    fn the_rule_itself_does_not_depend_on_the_door() {
        let runs = firewall_set_runs(8094, Some(r"C:\Helene\runtime\python.exe"));
        assert_eq!(
            runs[1],
            firewall_add_args(
                &firewall_rule_title(PRODUCT, 8094),
                8094,
                Some(r"C:\Helene\runtime\python.exe")
            )
        );
    }

    #[test]
    fn secrets_are_closed_on_the_way_into_the_support_archive() {
        // Ровно те формы, которые названы в требовании: key=, token=,
        // Bearer, sk-, dk-. Регистр не важен, значение не должно уцелеть.
        let masked = mask_secrets(
            "GET /api/state?key=abc123def456 token=Tk_9911 Authorization: Bearer sk-ant-api03-XYZ\n\
             ключ dk-live-9988776655 и sk-proj-AAAA1111\n",
        );
        assert!(!masked.contains("abc123def456"), "{masked}");
        assert!(!masked.contains("Tk_9911"), "{masked}");
        assert!(!masked.contains("ant-api03-XYZ"), "{masked}");
        assert!(!masked.contains("live-9988776655"), "{masked}");
        assert!(!masked.contains("proj-AAAA1111"), "{masked}");
        assert!(masked.contains("key=<скрыто>"), "{masked}");
        assert!(masked.contains("Bearer <скрыто>"), "{masked}");
        // Кириллица вокруг не должна пострадать: журнал продукта по-русски.
        assert!(masked.contains("ключ "), "{masked}");
        // А обычный текст с дефисом — не секрет: sk-/dk- ловятся только с
        // начала слова, и всё, что короче восьми знаков, остаётся как есть.
        let plain = mask_secrets("задача task-17, приставка sk-1 и слово monkey= пустое");
        assert!(plain.contains("task-17"), "{plain}");
        assert!(plain.contains("sk-1"), "{plain}");
    }

    // Несколько агентов в одной установке (11.09): планы подъёма и адрес окна.
    use super::*;

    /// Установка с двумя агентами на диске — для тестов ниже.
    fn two_agents(tag: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("helene-two-{}-{tag}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("agents").join("mira")).unwrap();
        std::fs::write(
            root.join(CONFIG_NAME),
            r#"{"mode":"local","app":"app/deskapp.py","runner":"app/localharness/runner.py",
                "python":"runtime/python.exe","tree":"data","port":8094,
                "agent":{"name":"Hélène"},"relay":{"enabled":true},
                "model":{"key":"sk-live"}}"#,
        )
        .unwrap();
        std::fs::write(
            root.join("agents").join("mira").join(CONFIG_NAME),
            r#"{"mode":"local","app":"../../app/deskapp.py","runner":"../../app/localharness/runner.py",
                "python":"../../runtime/python.exe","tree":"data","port":8095,
                "agent":{"name":"Мира"},"relay":{"enabled":true},
                "model":{"key":"sk-second"}}"#,
        )
        .unwrap();
        root
    }

    /// Каждый агент поднимается СВОЕЙ парой, в своём доме и на своём порту.
    #[test]
    fn every_agent_gets_its_own_pair_and_port() {
        let root = two_agents("plans");
        let plans = build_plans(&root);
        assert_eq!(plans.len(), 2, "оба агента обязаны попасть в планы");
        assert_eq!(plans[0].agent, "main");
        assert_eq!(plans[1].agent, "mira");
        assert_eq!((plans[0].port, plans[1].port), (8094, 8095));
        assert_ne!(plans[0].tree, plans[1].tree);
        // Секрет канала у каждого дерева свой: общий означал бы, что окно
        // одного агента открывает канал другого.
        assert!(!plans[0].token.is_empty() && plans[0].token != plans[1].token);
        // Руннер каждого читает ЕГО конфиг, а не корневой.
        let runner_cfg = |p: &SpawnPlan| {
            p.specs
                .iter()
                .find_map(|s| match s {
                    ChildSpec::Script { script, args, .. }
                        if script.to_string_lossy().contains("runner.py") =>
                    {
                        args.last().cloned()
                    }
                    _ => None,
                })
                .unwrap_or_default()
        };
        assert!(runner_cfg(&plans[0]).ends_with(CONFIG_NAME));
        assert!(runner_cfg(&plans[1]).contains("mira"), "{}", runner_cfg(&plans[1]));
        let _ = std::fs::remove_dir_all(&root);
    }

    /// КАЖДОМУ ребёнку сказано, чей конфиг читать — не только руннеру.
    ///
    /// ⚠ Приёмка 11.09, живая проба на поддельной установке: канал соседа читал и
    /// ПЕРЕПИСЫВАЛ корневой `helene.json` (`agentcfg.save` вернул `ok: true`, ключ мозга
    /// лёг в чужой файл). Причина не в ручках канала: `readers.config_path()` ищет файл
    /// от `__file__`, а код у всех агентов установки общий — `localharness/agents.py`
    /// намеренно пишет соседу `../../app`. Руннер об этом знал (`--config`), канал — нет:
    /// в него уходил ОДИН порт. Стенд пинит именно то, чего не хватало.
    #[test]
    fn every_child_is_told_whose_config_to_read() {
        let root = two_agents("configs");
        let plans = build_plans(&root);
        assert_eq!(plans.len(), 2);
        // Конфиги двух агентов — разные файлы, иначе равенство ниже ничего не значит.
        assert_ne!(plans[0].config, plans[1].config);
        for plan in &plans {
            let mut seen = 0;
            for spec in &plan.specs {
                if let ChildSpec::Script { config, script, .. } = spec {
                    assert_eq!(
                        config, &plan.config,
                        "ребёнок {} агента {} читал бы чужой конфиг",
                        script.display(), plan.agent
                    );
                    seen += 1;
                }
            }
            assert!(seen >= 1, "у агента {} нет ни одного питоновского ребёнка", plan.agent);
        }
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Реле одно на установку: порт у него один, вход в подписку один, и второе
    /// реле дралось бы за них с первым.
    #[test]
    fn only_the_base_agent_raises_the_relay() {
        let root = two_agents("relay");
        let plans = build_plans(&root);
        let relays = |p: &SpawnPlan| {
            p.specs
                .iter()
                .filter(|s| matches!(s, ChildSpec::Relay { .. }))
                .count()
        };
        assert_eq!(relays(&plans[0]), 1, "у корневого реле просили — оно в плане");
        assert_eq!(relays(&plans[1]), 0, "у соседа реле в плане быть не должно");
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Ненастроенный сосед получает канал (иначе его негде настроить), но не
    /// раннер (иначе он ошибался бы на каждом ходе без ключа модели).
    #[test]
    fn a_neighbour_without_a_brain_gets_the_channel_but_not_the_runner() {
        let root = two_agents("half");
        std::fs::write(
            root.join("agents").join("mira").join(CONFIG_NAME),
            r#"{"mode":"local","app":"../../app/deskapp.py","runner":"../../app/localharness/runner.py",
                "python":"../../runtime/python.exe","tree":"data","port":8095,
                "agent":{"name":"Мира"},"model":{"key":""}}"#,
        )
        .unwrap();
        let plans = build_plans(&root);
        let scripts: Vec<String> = plans[1]
            .specs
            .iter()
            .map(|s| s.label())
            .collect();
        assert!(scripts.iter().any(|s| s == "deskapp.py"), "{scripts:?}");
        assert!(!scripts.iter().any(|s| s == "runner.py"), "{scripts:?}");
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Окно получает адрес СВОЕГО агента и список остальных — по нему рисуется
    /// переключатель. Ключей соседей в скрипте нет: окно ходит только к своему.
    #[test]
    fn the_window_learns_its_own_channel_and_the_roster() {
        let root = two_agents("script");
        let script = channel_script(&root, 8095, "secret-of-mira", "Мира", "mira");
        assert!(script.contains("http://127.0.0.1:8095"), "{script}");
        assert!(script.contains("secret-of-mira"));
        assert!(script.contains(r#""agent_id":"mira""#), "{script}");
        assert!(script.contains("Hélène") && script.contains("Мира"), "{script}");
        assert_eq!(script.matches("secret-of-mira").count(), 1, "чужих ключей в окне нет");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn desk_token_is_one_per_tree_and_survives_rereading() {
        // Секрет трубы должен быть ОДИН на дерево: окно и служба предъявляют
        // его оба, свой у каждого = 403 от собственного харнесса.
        let tree = std::env::temp_dir().join(format!(
            "helene-test-token-{}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&tree);
        std::fs::create_dir_all(&tree).unwrap();
        let first = ensure_desk_token(&tree).expect("секрет не завёлся");
        assert!(first.len() >= 32 && first.bytes().all(|b| b.is_ascii_alphanumeric()));
        assert_eq!(ensure_desk_token(&tree).as_deref(), Some(first.as_str()));
        assert_eq!(read_desk_token(&tree).as_deref(), Some(first.as_str()));
        // Мусор вместо секрета читается как «секрета нет», а не как секрет.
        std::fs::write(tree.join("memory").join(".state").join("desk-token"), "коротко").unwrap();
        assert_eq!(read_desk_token(&tree), None);
        let _ = std::fs::remove_dir_all(&tree);
    }

    #[test]
    fn foreign_holder_never_becomes_the_address_of_this_window() {
        // Главное свойство экрана «здесь чужая установка»: адреса чужого
        // харнесса в нём нет вовсе — ни порта, ни ключа.
        let s = blocked_script(8094, Some(std::path::Path::new("C:\\Чужая\\data")), "Hélène");
        assert!(!s.contains("127.0.0.1:8094"), "{s}");
        assert!(s.contains("\"base\":\"\"") || s.contains("\"base\": \"\""), "{s}");
        assert!(s.contains("Чужая"), "{s}");
    }

    #[test]
    fn config_reads_through_bom_and_utf16() {
        // Так пишут Блокнот, VS Code и Set-Content в PowerShell 5.1 — раньше
        // всё это читалось как «конфига нет» и запускало установщик заново.
        let plain = b"{\"a\": 1}".to_vec();
        assert_eq!(decode_config(&plain).unwrap(), "{\"a\": 1}");
        let mut with_bom = vec![0xEF, 0xBB, 0xBF];
        with_bom.extend_from_slice(&plain);
        assert_eq!(decode_config(&with_bom).unwrap(), "{\"a\": 1}");
        let mut utf16 = vec![0xFF, 0xFE];
        for unit in "{\"a\": 1}".encode_utf16() {
            utf16.extend_from_slice(&unit.to_le_bytes());
        }
        assert_eq!(decode_config(&utf16).unwrap(), "{\"a\": 1}");
    }

    /// Имя архива обновления — из ссылки, только безопасные знаки и .zip.
    #[test]
    fn update_file_name_is_a_plain_zip() {
        use super::update_file_name;
        assert_eq!(
            update_file_name("https://github.com/josephsteuerjr/helene/releases/download/v0.3.3/Helene-0.3.3.zip").unwrap(),
            "Helene-0.3.3.zip"
        );
        assert_eq!(update_file_name("https://x/y/Helene.zip?token=1").unwrap(), "Helene.zip");
        assert_eq!(update_file_name("https://x/y/He..%2F..%2Flene.zip").unwrap(), "He..2F..2Flene.zip");
        assert!(update_file_name("https://x/y/setup.exe").is_err());
        assert!(update_file_name("https://x/y/.zip").is_err());
        assert!(update_file_name("https://x/y/").is_err());
    }

    /// В одном релизе два архива — каждая программа берёт свой; у варианта отката
    /// на чужой .zip нет.
    #[test]
    fn update_zip_is_the_products_own_archive() {
        use super::pick_update_zip;
        let both = serde_json::json!([
            {"name": "Praxis-0.5.0.zip", "browser_download_url": "https://x/Praxis-0.5.0.zip"},
            {"name": "Helene-0.5.0.zip.sha256", "browser_download_url": "https://x/Helene-0.5.0.zip.sha256"},
            {"name": "Helene-0.5.0.zip", "browser_download_url": "https://x/Helene-0.5.0.zip"}
        ]);
        let both = both.as_array().unwrap();
        assert_eq!(pick_update_zip(both, "Helene", true, false).unwrap()["name"], "Helene-0.5.0.zip");
        assert_eq!(pick_update_zip(both, "Praxis", false, false).unwrap()["name"], "Praxis-0.5.0.zip");
        // Старый релиз: только Helene.zip без версии в имени — Hélène берёт его как раньше,
        // Praxis не берёт ничего.
        let legacy = serde_json::json!([
            {"name": "Helene.zip", "browser_download_url": "https://x/Helene.zip"}
        ]);
        let legacy = legacy.as_array().unwrap();
        assert_eq!(pick_update_zip(legacy, "Helene", true, false).unwrap()["name"], "Helene.zip");
        assert!(pick_update_zip(legacy, "Praxis", false, false).is_none());
        // Имя вложения может отсутствовать — тогда оно из ссылки.
        let nameless = serde_json::json!([
            {"browser_download_url": "https://x/y/praxis-0.5.1.ZIP"}
        ]);
        let nameless = nameless.as_array().unwrap();
        assert!(pick_update_zip(nameless, "Praxis", false, false).is_some());
        assert!(pick_update_zip(nameless, "Helene", false, false).is_none());
    }

    /// С 0.7.1 в релизе лежит и сборка для Mac. Windows её не берёт, даже когда
    /// она первая в списке и начинается с того же `Helene-`; Mac берёт только её
    /// и не откатывается на Windows-архив, когда своего нет.
    #[test]
    fn update_zip_keeps_windows_and_mac_archives_apart() {
        use super::pick_update_zip;
        let release = serde_json::json!([
            {"name": "Helene-0.7.1-macos-arm64.zip", "browser_download_url": "https://x/Helene-0.7.1-macos-arm64.zip"},
            {"name": "Helene-0.7.1-macos-arm64.zip.sha256", "browser_download_url": "https://x/Helene-0.7.1-macos-arm64.zip.sha256"},
            {"name": "Praxis-0.7.1.zip", "browser_download_url": "https://x/Praxis-0.7.1.zip"},
            {"name": "Helene-0.7.1.zip", "browser_download_url": "https://x/Helene-0.7.1.zip"}
        ]);
        let release = release.as_array().unwrap();
        assert_eq!(pick_update_zip(release, "Helene", true, false).unwrap()["name"], "Helene-0.7.1.zip");
        assert_eq!(pick_update_zip(release, "Helene", true, true).unwrap()["name"], "Helene-0.7.1-macos-arm64.zip");
        assert_eq!(pick_update_zip(release, "Praxis", false, false).unwrap()["name"], "Praxis-0.7.1.zip");
        // Релиз без Mac-сборки: Mac не получает ничего — ни Windows-архива по
        // префиксу, ни «любого .zip» по откату.
        let windows_only = serde_json::json!([
            {"name": "Helene-0.7.1.zip", "browser_download_url": "https://x/Helene-0.7.1.zip"},
            {"name": "Helene.zip", "browser_download_url": "https://x/Helene.zip"}
        ]);
        let windows_only = windows_only.as_array().unwrap();
        assert!(pick_update_zip(windows_only, "Helene", true, true).is_none());
        // И наоборот: релиз только с Mac-архивом Windows не подхватывает даже
        // через откат на любой .zip.
        let mac_only = serde_json::json!([
            {"name": "Helene-0.7.1-macos-arm64.zip", "browser_download_url": "https://x/Helene-0.7.1-macos-arm64.zip"}
        ]);
        let mac_only = mac_only.as_array().unwrap();
        assert!(pick_update_zip(mac_only, "Helene", true, false).is_none());
    }

    /// Заметки с тремя суммами: Mac берёт свою строку, Windows — свою; без
    /// имени актива или без своей строки — первая сумма, как было.
    #[test]
    fn notes_sha_follows_the_named_asset() {
        let notes = "Hélène 0.7.1
                     sha256 Helene-0.7.1.zip: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
                     sha256 Praxis-0.7.1.zip: bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb
                     sha256 Helene-0.7.1-macos-arm64.zip: cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc
";
        assert_eq!(sha_for_asset(notes, "Helene-0.7.1-macos-arm64.zip"), "c".repeat(64));
        assert_eq!(sha_for_asset(notes, "Helene-0.7.1.zip"), "a".repeat(64));
        assert_eq!(sha_for_asset(notes, ""), "a".repeat(64));
        assert_eq!(sha_for_asset(notes, "Helene-0.9.0.zip"), "a".repeat(64));
        assert_eq!(sha_for_asset("нет сумм", "Helene-0.7.1.zip"), "");
    }

    /// ⚠⚠⚠ Кнопка «Скачать и установить» вела на ЗАПИСЬ релиза в API, а не на
    /// архив: ответ GitHub несёт своё поле `url`
    /// (`https://api.github.com/repos/…/releases/392320465`), а выбор шёл по его
    /// непустоте — «раз назван, значит это свой JSON». Поле не пустое никогда,
    /// поэтому на GitHub-адресе кнопка не работала ни разу: `update_file_name`
    /// отвечал «ссылка ведёт не на архив .zip», окно печатало «Не получилось.».
    /// Живой случай 20.09.2026 у владельца (0.7.1 → 0.8.2).
    #[test]
    fn update_url_is_the_archive_not_the_api_record() {
        use super::pick_update_url;
        let api = "https://api.github.com/repos/josephsteuerjr/praxis/releases/392320465";
        let zip = "https://github.com/josephsteuerjr/praxis/releases/download/v0.8.2/Helene-0.8.2.zip";
        let page = "https://github.com/josephsteuerjr/praxis/releases/tag/v0.8.2";
        // GitHub: версия названа tag_name (своего `version` нет) — берётся архив,
        // а `url` записи игнорируется, каким бы он ни был.
        assert_eq!(pick_update_url("", api, Some(zip.to_string()), page), zip);
        // GitHub без подходящего архива — страница релиза, человеку есть куда пойти.
        assert_eq!(pick_update_url("", api, None, page), page);
        // Свой JSON: версия названа полем `version`, адрес — полем `url`.
        assert_eq!(pick_update_url("0.9.0", zip, None, ""), zip);
        // Свой JSON, где кто-то заодно положил и assets: адрес владельца сильнее.
        assert_eq!(pick_update_url("0.9.0", zip, Some(page.to_string()), page), zip);
        // Пусто со всех сторон — пустая строка, а не паника.
        assert_eq!(pick_update_url("", "", None, ""), "");
        // Свой JSON без адреса — то же правило, что у GitHub: архив, потом страница.
        assert_eq!(pick_update_url("0.9.0", "", Some(zip.to_string()), page), zip);
    }

    /// Корень установки — первая папка вверх от exe с паспортом сборки; без
    /// паспорта в пяти уровнях — сама папка exe (так было на Windows всегда).
    #[test]
    fn install_root_is_the_first_folder_up_with_the_passport() {
        use super::{install_root_from, BUILD_PASSPORT};
        let root = std::env::temp_dir().join(format!("helene-root-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        // Раскладка macOS: <корень>/Helene.app/Contents/MacOS/helene.
        let exe_dir = root.join("Helene.app").join("Contents").join("MacOS");
        std::fs::create_dir_all(&exe_dir).unwrap();
        // Паспорта нет нигде — папка exe, как раньше.
        assert_eq!(install_root_from(&exe_dir), exe_dir);
        std::fs::write(root.join(BUILD_PASSPORT), "{}").unwrap();
        assert_eq!(install_root_from(&exe_dir), root);
        // Раскладка Windows: паспорт рядом с exe — корень и есть папка exe.
        std::fs::write(exe_dir.join(BUILD_PASSPORT), "{}").unwrap();
        assert_eq!(install_root_from(&exe_dir), exe_dir);
        // Глубже пяти уровней паспорт не ищется: шесть папок вниз — уже нет.
        let deep = root.join("a").join("b").join("c").join("d").join("e").join("f");
        std::fs::create_dir_all(&deep).unwrap();
        assert_eq!(install_root_from(&deep), deep);
        let five = root.join("a").join("b").join("c").join("d").join("e");
        assert_eq!(install_root_from(&five), root);
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Имена по платформе: суффикс `.exe` только на Windows, питон из коробки —
    /// `runtime/python.exe` там и `runtime/bin/python3` на остальных, мастер —
    /// `helene-setup.exe` рядом или бандл `Helene Setup.app`.
    #[test]
    fn platform_names_follow_the_target() {
        use super::{bundled_python, exe_name, setup_binary};
        let base = std::path::Path::new("base");
        if cfg!(windows) {
            assert_eq!(exe_name("helene-relay"), "helene-relay.exe");
            assert_eq!(bundled_python(base), base.join("runtime").join("python.exe"));
            assert_eq!(setup_binary(base), base.join("helene-setup.exe"));
        } else {
            assert_eq!(exe_name("helene-relay"), "helene-relay");
            assert_eq!(bundled_python(base), base.join("runtime").join("bin").join("python3"));
            if cfg!(target_os = "macos") {
                assert!(setup_binary(base).ends_with("Helene Setup.app/Contents/MacOS/helene-setup"));
            }
        }
    }

    /// Текст диалога и уведомления приходит и от агента, и от сервера обновлений:
    /// кавычка, слэш и перевод строки не имеют права рвать AppleScript.
    #[test]
    fn applescript_literal_escapes_quotes_slashes_and_newlines() {
        use super::applescript_quote;
        assert_eq!(applescript_quote("просто"), "\"просто\"");
        assert_eq!(applescript_quote("он сказал \"нет\""), "\"он сказал \\\"нет\\\"\"");
        assert_eq!(applescript_quote("C:\\путь"), "\"C:\\\\путь\"");
        assert_eq!(applescript_quote("раз\nдва\tтри"), "\"раз\\nдва\\tтри\"");
    }

    /// LaunchAgent: метка, путь бинаря и подъём при входе; `&` в имени папки
    /// остаётся XML, а не ломает plist.
    #[test]
    fn launch_agent_plist_names_label_program_and_run_at_load() {
        use super::launch_agent_text;
        let text = launch_agent_text("app.helene.desk", "/Users/me/Apps & Tools/Helene/Helene.app/Contents/MacOS/helene");
        assert!(text.contains("<key>Label</key>\n\t<string>app.helene.desk</string>"), "{text}");
        assert!(text.contains("<string>/Users/me/Apps &amp; Tools/Helene/Helene.app/Contents/MacOS/helene</string>"), "{text}");
        assert!(text.contains("<key>RunAtLoad</key>\n\t<true/>"), "{text}");
        assert!(text.starts_with("<?xml"), "{text}");
        assert!(text.trim_end().ends_with("</plist>"), "{text}");
    }

    #[test]
    fn version_parsing_is_strict_about_the_tag() {
        assert_eq!(parse_version("v0.2.1"), Some(vec![0, 2, 1]));
        assert_eq!(parse_version("V1.0.0"), Some(vec![1, 0, 0]));
        assert_eq!(parse_version("1.0"), Some(vec![1, 0, 0]));
        assert_eq!(parse_version("v1.0.0-rc1"), Some(vec![1, 0, 0]));
        // Непонятное — именно непонятное, а не «нули» (иначе мажорный выпуск
        // печатался бы как «это последняя версия»).
        assert_eq!(parse_version("helene-1.0.0"), None);
        assert_eq!(parse_version(""), None);
        assert_eq!(parse_version("release"), None);
    }

    #[test]
    fn newer_version_is_compared_by_numbers() {
        assert!(version_newer("v0.10.0", "0.2.1"));
        assert!(version_newer("V1.0.0", "0.2.1"));
        assert!(!version_newer("v0.2.1", "0.2.1"));
        assert!(!version_newer("1.0", "1.0.0"));
        assert!(!version_newer("release-1.0", "0.2.1"));
        assert!(!version_newer("", "0.2.1"));
    }

    #[test]
    fn verbatim_prefix_is_stripped_for_explorer() {
        use std::path::{Path, PathBuf};
        assert_eq!(
            super::plain_path(Path::new("\\\\?\\C:\\Users\\Иван Петров\\логи.zip")),
            PathBuf::from("C:\\Users\\Иван Петров\\логи.zip")
        );
        assert_eq!(
            super::plain_path(Path::new("\\\\?\\UNC\\server\\share\\x")),
            PathBuf::from("\\\\server\\share\\x")
        );
        assert_eq!(
            super::plain_path(Path::new("C:\\обычный\\путь")),
            PathBuf::from("C:\\обычный\\путь")
        );
    }

    #[cfg(windows)]
    #[test]
    fn env_vars_in_registry_paths_expand() {
        std::env::set_var("HELENE_TEST_DIR", "C:\\Users\\test");
        assert_eq!(expand_env("%HELENE_TEST_DIR%\\Menu"), "C:\\Users\\test\\Menu");
        assert_eq!(expand_env("C:\\plain\\path"), "C:\\plain\\path");
        assert_eq!(expand_env("%NO_SUCH_VAR_HERE%\\x"), "%NO_SUCH_VAR_HERE%\\x");
    }

    #[test]
    fn first_run_is_unconfigured() {
        assert!(unconfigured(&None));
        assert!(unconfigured(&Some(json!({
            "model": {"base_url": "https://example.invalid/v1", "model": "demo", "key": ""}
        }))));
    }

    #[test]
    fn explicit_setup_receipt_allows_keyless_local_model() {
        assert!(!unconfigured(&Some(json!({
            "setup_complete": true,
            "model": {"base_url": "http://127.0.0.1:11434/v1", "model": "local", "key": ""}
        }))));
    }

    #[test]
    fn legacy_config_with_key_stays_configured() {
        assert!(!unconfigured(&Some(json!({
            "model": {"key": "legacy-key"}
        }))));
    }

    #[test]
    fn agent_name_comes_from_owner_not_from_product() {
        assert_eq!(agent_name(None), "Агент");
        assert_eq!(agent_name(Some(&json!({"agent": {"name": "  Вера "}}))), "Вера");
        assert_eq!(
            agent_name(Some(&json!({"telegram": {"agent_name": "Старое"}}))),
            "Старое"
        );
        assert_eq!(
            agent_name(Some(&json!({"agent": {"name": ""}, "telegram": {"agent_name": ""}}))),
            "Агент"
        );
    }
}
