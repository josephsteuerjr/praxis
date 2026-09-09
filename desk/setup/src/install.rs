//! Установка Hélène: поставка → папка пользователя, конфиг, конституция,
//! ярлыки, запись об удалении, служба по желанию. Каждый шаг — расписка.
//!
//! Раскладка: установщик лежит ВНУТРИ поставки (папка из build_dist.py:
//! helene.exe, app/, runtime/, tree/, helene-svc.exe, helene-relay.exe…).
//! Установка = копия этой папки в %LocalAppData%\Programs\Helene плюс то,
//! чего в поставке нет по построению: helene.json и data/soul/SOUL.md.
//! Служба лежит В ТОЙ ЖЕ папке (svc/src/main.rs берёт current_exe), а права
//! администратора нужны не ради Program Files, а ради SCM: регистрация,
//! остановка и снятие службы — отдельный поднятый вызов.
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use serde::{Deserialize, Serialize};

#[cfg(windows)]
use std::os::windows::process::CommandExt;
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

/// Имя в файловой системе (папка, ярлык, ключ реестра) — латиницей;
/// на экране и в «Приложениях» — по-французски.
pub const PRODUCT: &str = "Helene";
pub const PRODUCT_UI: &str = "Hélène";
pub const AUMID: &str = "app.helene.desk";
pub const SETUP_AUMID: &str = "app.helene.setup";
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Порт встроенного реле в установленной программе по умолчанию
/// (shell/src/main.rs поднимает реле на нём же, если `relay.port` не сказал
/// иначе). Адрес мозга собирается отсюда, а не константой в строке.
pub const RELAY_PORT: u16 = 5011;

/// Куда смотреть за новой версией. Тот же адрес, что в шаблоне поставки
/// (installer/build_dist.py::HELENE_JSON): установщик шаблон не переносит и
/// писал свой конфиг без этого ключа — «Проверить обновления» отказывало всегда.
///
/// Выпуски переехали в репозиторий ядра (`praxis`): отдельный репозиторий «для
/// поставок» больше не живёт — решение владельца 09.09.
pub const UPDATE_URL: &str = "https://api.github.com/repos/josephsteuerjr/praxis/releases/latest";

/// Прежний адрес выпусков. Он стоит в конфигах всех установок до 0.5.1, а
/// адрес обновлений установщик по правилу НЕ переписывает — это решение
/// владельца. Здесь ровно одно исключение: наше собственное прежнее значение,
/// буква в букву, переносится на новое. Иначе установленная копия осталась бы
/// смотреть в архивный репозиторий и молча перестала бы видеть выпуски —
/// человек об этом узнал бы только по тишине.
pub const UPDATE_URL_WAS: &str = "https://api.github.com/repos/josephsteuerjr/helene/releases/latest";

// Общее с оболочкой и службой (ревью 06.09, §4): проба модели и гард
// исходящего адреса, экранирование PowerShell, поднятая операция со службой,
// случайные байты из CSPRNG, внешняя программа с дедлайном.
include!("../../common/model_probe.rs");
include!("../../common/ps.rs");
include!("../../common/service_op.rs");
include!("../../common/random_hex.rs");
include!("../../common/run_hidden.rs");

/// Имя правила брандмауэра — из того же файла, что у оболочки и службы
/// (`common/firewall_rule.rs`): установщик снимает правило при удалении, и
/// до 07.09 собирал имя руками мимо `firewall_rule_title` (ревью 06.09, §4
/// п. 15). Остальное из файла установщику не нужно — отсюда модуль.
mod firewall_rule {
    #![allow(dead_code)]
    include!("../../common/firewall_rule.rs");

    pub(super) fn title(product: &str, port: u16) -> String {
        firewall_rule_title(product, port)
    }
}

/// Файлы поставки, по которым мы узнаём её папку.
const PAYLOAD_MARKERS: [&str; 3] = ["helene.exe", "app", "runtime"];

/// Что не переносим из поставки в установку: конфиг пишем свой, данные
/// рождаются на месте, журналы прошлых прогонов установленной программе не нужны.
const SKIP_FROM_PAYLOAD: [&str; 6] = [
    "helene.json",
    "data",
    ".close-hint-shown",
    "helene.log",
    "install.log",
    "helene-service-op.ps1",
];

#[derive(Deserialize, Clone)]
pub struct Setup {
    pub agent: String,
    pub owner: String,
    pub constitution: String,
    pub accepted: bool,
    pub provider: String,
    #[serde(default = "default_chatgpt_model")]
    pub chatgpt_model: String,
    #[serde(default)]
    pub reasoning_effort: String,
    pub api: Endpoint,
    #[serde(default = "default_anthropic")]
    pub anthropic: Endpoint,
    pub local: LocalEndpoint,
    pub telegram: Telegram,
    /// ОГРАДА РУК: `sandbox` | `interactive`. В helene.json уезжает ключом
    /// `agent_mode` — НЕ `mode`: тот занят под местожительство харнесса
    /// (`local`|`remote`) и читается оболочкой и службой (shell/src/main.rs,
    /// svc/src/main.rs::load_plan). Подробности — localharness/modes.py.
    ///
    /// ⚠ «service» оградой НЕ является: служба — опция поверх любой из двух
    /// (поле `service` ниже). Значение приходило сюда от визарда первой волны,
    /// где их держали одним списком из трёх; читается ради совместимости и
    /// оградой не считается — см. `Setup::mode()`.
    #[serde(default)]
    pub agent_mode: String,
    /// Ставить ли службу. САМОСТОЯТЕЛЬНОЕ решение владельца, поверх любой
    /// ограды: защита папки установки, жизнь без окна, брокер прав. Ограду не
    /// трогает.
    pub service: bool,
    /// Галочка внутри опции службы: доступ агента к правам системы. В конфиг
    /// уходит как `service.session0` — туда, где её уже читает служба. Без
    /// службы не действует: исполнять некому.
    #[serde(default)]
    pub session0: bool,
    /// Вторая галочка опции: ставит ли служба правило брандмауэра для кнопки
    /// «Телефон» (`service.firewall`). Экрана у неё нет — умолчание приезжает
    /// от визарда из modes.FIREWALL_DEFAULT; старый визард её не шлёт вовсе,
    /// и тогда действует то же умолчание (см. `default_firewall`).
    #[serde(default = "default_firewall")]
    pub firewall: bool,
    /// Управление компьютером — опция ПОВЕРХ любой ограды и без службы: тело
    /// руки `computer` (окна, экран, клавиатура и мышь) поднимает харнесс в
    /// сессии владельца, снаружи ограды (`localharness/body.py`). В конфиг
    /// уходит блоком `computer` — `enabled` отсюда, четыре права все, порт
    /// умолчанием; сузить права можно в Настройках. Старый визард поля не
    /// шлёт — тогда выключено (modes.COMPUTER_DEFAULT).
    #[serde(default)]
    pub computer: bool,
    pub dir: String,
}

/// Четыре права руки `computer` — те же строки, что проверяет дерево
/// (`_COMPUTER_ACTION_SCOPES` в tree/agent.py) и рисует окно. Порядок — показа.
const COMPUTER_SCOPES: [&str; 4] = ["computer.read", "computer.files", "computer.process", "computer.apps"];

/// Порт моста тела по умолчанию (body.DEFAULT_PORT). Не 9473: там тело Праксис.
const COMPUTER_PORT: u16 = 9480;
/// Порт канала окна по умолчанию — как в `ui-kit/contract.json`.
const DESK_PORT: u16 = 8094;

fn computer_block(enabled: bool) -> serde_json::Value {
    serde_json::json!({ "enabled": enabled, "port": COMPUTER_PORT, "scopes": COMPUTER_SCOPES })
}

impl Setup {
    /// ОГРАДА, приведённая к двум известным.
    ///
    /// ⚠⚠ ЗДЕСЬ БЫЛ P0. Раньше эта функция знала три значения, и «service»
    /// было одним из них: владелец, выбравший службу, получал в конфиг
    /// `agent_mode: "service"`, а оно означало `sandbox.enabled = false` —
    /// ограда снималась молча. Теперь службы здесь нет ни в каком виде.
    ///
    /// Пустое поле и старое `"service"` — визард первой волны. Ограду он не
    /// присылал вовсе, вывести её не из чего, и умолчанием берём песочницу:
    /// это и умолчание самого визарда, и более узкие права из двух. Молча
    /// расширить их «за» владельца — ровно та ошибка, из которой всё выросло.
    pub fn mode(&self) -> &'static str {
        match self.agent_mode.trim() {
            "interactive" => "interactive",
            _ => "sandbox",
        }
    }

    /// Ставить ли службу. Собственное поле владельца плюс след старого визарда:
    /// там служба приезжала третьим значением `agent_mode`, и терять её нельзя.
    pub fn wants_service(&self) -> bool {
        self.service || self.agent_mode.trim() == "service"
    }

    /// Нулевая сессия действует только вместе со службой: без неё исполнять
    /// некому, а `true` в файле читался бы как разрешение.
    pub fn wants_session0(&self) -> bool {
        self.session0 && self.wants_service()
    }
}

/// Умолчание галочки брандмауэра — то же, что modes.FIREWALL_DEFAULT: с
/// `false` кнопка «Телефон» под службой требовала бы нулевой сессии, то есть
/// одно узкое действие продукта просило бы отдать агенту всю машину.
fn default_firewall() -> bool {
    true
}

fn default_chatgpt_model() -> String {
    "gpt-5.6-sol".into()  // выбор владельца по умолчанию
}

fn default_anthropic() -> Endpoint {
    Endpoint { base_url: "https://api.z.ai/api/anthropic".into(), model: "glm-5.3".into(), key: String::new() }
}

#[derive(Deserialize, Clone)]
pub struct Endpoint {
    pub base_url: String,
    pub model: String,
    pub key: String,
}

#[derive(Deserialize, Clone)]
pub struct LocalEndpoint {
    pub base_url: String,
    pub model: String,
}

#[derive(Deserialize, Clone)]
pub struct Telegram {
    pub bot_token: String,
    pub owner_id: String,
}

#[derive(Serialize, Clone)]
pub struct Step {
    pub label: String,
    pub ok: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
}

#[derive(Serialize, Clone)]
pub struct Receipt {
    pub dir: String,
    pub exe: String,
    pub service: String,
    /// Предупреждение службы (СИСТЕМА запускает код из папки, куда пишет
    /// пользователь). Пришло файлом от `helene-svc install` — на экран расписки
    /// его выводит `setup/ui/src/scenes/install.ts`. Отдельным полем, а не
    /// строкой шага: примечания шагов на итоговом экране не показываются, и
    /// предупреждение осталось бы только в install.log.
    #[serde(skip_serializing_if = "Option::is_none")]
    pub warning: Option<String>,
    pub steps: Vec<Step>,
}

#[derive(Serialize, Clone)]
pub struct Progress {
    pub step: usize,
    pub total: usize,
    pub label: String,
}

/// Что уже стоит на машине — чтобы визард не делал вид, будто ставит впервые.
#[derive(Serialize, Clone)]
pub struct Installed {
    pub dir: String,
    pub version: String,
    pub agent: String,
}

#[derive(Serialize)]
pub struct Defaults {
    pub dir: String,
    pub payload: Option<String>,
    pub version: String,
    pub installed: Option<Installed>,
}

pub fn exe_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Системные программы — только полным путём из %SystemRoot%.
/// `Command::new("powershell.exe")` ищет exe СНАЧАЛА в папке своего процесса, а
/// установщик лежит ВНУТРИ распакованной поставки (обычно в «Загрузках»): файл
/// `powershell.exe`, положенный рядом с `helene-setup.exe`, исполнялся бы вместо
/// системного — и дальше эти же вызовы уходят под UAC. Те же строки, что у
/// оболочки (`shell/src/main.rs::sys_exe`).
fn system_root() -> PathBuf {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:\\Windows"))
}

pub fn sys_exe(name: &str) -> PathBuf {
    let full = system_root().join("System32").join(name);
    if full.exists() {
        full
    } else {
        PathBuf::from(name)
    }
}

pub fn powershell_exe() -> PathBuf {
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

/// Папка установки по умолчанию. None, если Windows не сказала LOCALAPPDATA:
/// подставлять "." нельзя — cwd установщика это папка поставки, и установка
/// пошла бы копировать поставку в саму себя.
pub fn default_dir() -> Option<PathBuf> {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty())
        .map(|base| base.join("Programs").join(PRODUCT))
}

/// Папка поставки: рядом с установщиком лежат helene.exe, app/ и runtime/.
pub fn payload_dir() -> Option<PathBuf> {
    let here = exe_dir();
    if PAYLOAD_MARKERS.iter().all(|m| here.join(m).exists()) {
        Some(here)
    } else {
        None
    }
}

pub fn defaults() -> Defaults {
    Defaults {
        dir: default_dir().map(|p| p.to_string_lossy().into_owned()).unwrap_or_default(),
        payload: payload_dir().map(|p| p.to_string_lossy().into_owned()),
        version: VERSION.to_string(),
        installed: installed_info(),
    }
}

// ---------------------------------------------------------------- пути

/// Путь в сравнимом виде: канонизируем ближайшего существующего предка (папка
/// установки может ещё не существовать), остаток дописываем, регистр гасим.
/// Без этого проверка «не ставим ли мы поставку в саму себя» ловится только на
/// побуквенно одинаковых путях, а `C:\x` и `C:\X\..\x` проходят мимо.
fn norm_path(p: &Path) -> String {
    let mut rest: Vec<std::ffi::OsString> = Vec::new();
    let mut cur = p.to_path_buf();
    loop {
        if let Ok(c) = std::fs::canonicalize(&cur) {
            let mut out = c;
            for part in rest.iter().rev() {
                out.push(part);
            }
            return out.to_string_lossy().to_lowercase().trim_end_matches('\\').to_string();
        }
        match cur.file_name() {
            Some(n) => rest.push(n.to_os_string()),
            None => break,
        }
        if !cur.pop() {
            break;
        }
    }
    p.to_string_lossy().to_lowercase().trim_end_matches('\\').to_string()
}

/// `a` — это `b` или лежит внутри `b`.
fn inside_or_same(a: &str, b: &str) -> bool {
    a == b || a.starts_with(&format!("{b}\\"))
}

// ---------------------------------------------------------------- PowerShell

/// Текст, который печатают консольные программы. Windows PowerShell 5.1 на
/// системе с OEMCP=866 отдаёт кириллицу в CP866; читать её как UTF-8 значит
/// показать владельцу вместо причины строку из «?????». Сначала пробуем UTF-8
/// (английские системы и большинство утилит), при неудаче — CP866.
fn console_text(bytes: &[u8]) -> String {
    match std::str::from_utf8(bytes) {
        Ok(s) => s.to_string(),
        Err(_) => bytes.iter().map(|b| decode_cp866(*b)).collect(),
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

fn run_hidden(cmd: &mut Command) -> Result<std::process::Output, String> {
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.output().map_err(|e| e.to_string())
}

fn powershell(script: &str) -> Result<std::process::Output, String> {
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", script]);
    run_hidden(&mut cmd)
}

// ---------------------------------------------------------------- копирование

/// Текст ошибки файловой операции человеческими словами: «os error 32» владельцу
/// ничего не говорит, а закрыть занявшую файл программу он может.
fn io_note(path: &Path, e: &std::io::Error) -> String {
    match e.raw_os_error() {
        Some(32) | Some(33) => format!(
            "файл занят другой программой: {} — закрой окно {PRODUCT_UI} и, если стоит служба, останови её",
            path.display()
        ),
        Some(5) => format!("нет доступа к {}", path.display()),
        Some(112) => format!("на диске нет места: {}", path.display()),
        _ => format!("{}: {e}", path.display()),
    }
}

fn copy_dir(src: &Path, dst: &Path, skip_root: &[&str]) -> Result<usize, String> {
    copy_dir_skip(src, dst, skip_root, &[])
}

/// `skip_rel` — пути от корня поставки (`app/static`, `runtime`), которые
/// не копируются: обновление оставляет их такими, какие стоят (см. `install`).
fn copy_dir_skip(src: &Path, dst: &Path, skip_root: &[&str], skip_rel: &[&str]) -> Result<usize, String> {
    // Приёмник внутри источника — рекурсия видела бы собственную копию и уходила
    // вглубь до переполнения стека (2,4 ГБ мусора за 25 секунд на прогоне).
    let (ns, nd) = (norm_path(src), norm_path(dst));
    if inside_or_same(&nd, &ns) {
        return Err(format!(
            "нельзя копировать {} внутрь самой себя ({})",
            src.display(),
            dst.display()
        ));
    }
    copy_tree(src, dst, skip_root, skip_rel, "")
}

fn copy_tree(src: &Path, dst: &Path, skip_root: &[&str], skip_rel: &[&str], rel: &str) -> Result<usize, String> {
    std::fs::create_dir_all(dst).map_err(|e| io_note(dst, &e))?;
    let mut count = 0;
    for entry in std::fs::read_dir(src).map_err(|e| io_note(src, &e))? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name();
        if skip_root.iter().any(|s| name == *s) {
            continue;
        }
        let name_text = name.to_string_lossy();
        let here = if rel.is_empty() { name_text.to_string() } else { format!("{rel}/{name_text}") };
        if skip_rel.iter().any(|s| *s == here) {
            continue;
        }
        let from = entry.path();
        let to = dst.join(&name);
        if from.is_dir() {
            count += copy_tree(&from, &to, &[], skip_rel, &here)?;
        } else {
            std::fs::copy(&from, &to).map_err(|e| io_note(&to, &e))?;
            count += 1;
        }
    }
    Ok(count)
}

// ------------------------------------------------- обновление поверх: что менять

/// Манифест статики окна, который кладёт сборка (`installer/build_dist.py`):
/// sha256 каждого файла и общий отпечаток `digest`.
const STATIC_MANIFEST: &str = ".helene-static.json";

fn static_digest(root: &Path) -> Option<String> {
    let v = read_json(&root.join("app").join("static").join(STATIC_MANIFEST))?;
    v.get("digest").and_then(|d| d.as_str()).filter(|d| !d.is_empty()).map(str::to_string)
}

#[derive(Debug, PartialEq, Clone, Copy)]
enum StaticPlan {
    /// Первая установка (или установка без статики): положить с манифестом.
    Fresh,
    /// Выпуск статику не менял — папку пользователя не трогать вовсе.
    Keep,
    /// Выпуск статику менял — заменить, прежнюю отложить в `app/static.prev`.
    Replace,
}

/// Решение о статике окна — по манифестам ДВУХ ПОСТАВОК (новой и той, что
/// ставилась раньше), а не по файлам пользователя. Слово владельца 07.09:
/// «если я не трогал статику — оставлять пользовательскую». Правил ли он
/// `app/static` руками — его дело, установщик по этому не решает никогда.
/// Поставка без манифеста (старее 0.3.3) сравнить себя не может — заменяет,
/// как раньше.
fn static_plan(payload: &Path, dir: &Path) -> StaticPlan {
    if !dir.join("app").join("static").join("index.html").exists() {
        return StaticPlan::Fresh;
    }
    match (static_digest(payload), static_digest(dir)) {
        (Some(new), Some(old)) if new == old => StaticPlan::Keep,
        _ => StaticPlan::Replace,
    }
}

/// Рантайм одинаков по паспорту сборки (`helene-build.json`): версия Python,
/// суммы скачанного, список пакетов. Одинаковый — не копировать 200 МБ впустую.
/// Установка без живого `runtime/python.exe` — не одинаковый ни при чём.
fn runtime_same(payload: &Path, dir: &Path) -> bool {
    if !dir.join("runtime").join("python.exe").exists() {
        return false;
    }
    let (Some(new), Some(old)) = (read_json(&payload.join("helene-build.json")), read_json(&dir.join("helene-build.json"))) else {
        return false;
    };
    ["python", "downloads", "packages"].iter().all(|k| new.get(k).is_some() && new.get(k) == old.get(k))
}

// ---------------------------------------------------------------- конфиг

/// helene.json из решений установки. Относительные пути: папка переносима целиком.
/// `prev_relay_key` — ключ реле прошлой установки: генерировать новый на каждом
/// обновлении значит менять отпечаток блока модели, а по нему boot.project_brain
/// перезаписывает memory/llm.json и молча отменяет выбор мозга самим агентом.
/// `relay_port` — фактический порт встроенного реле: 5011 может держать реле
/// прежнего поколения продукта, и тогда установка выбирает свободный. Порт
/// приходит сюда ОДИН, чтобы `relay.port` и `model.base_url` не разъехались:
/// оболочка поднимает реле по первому, а мозг стучится по второму.
fn config_json(s: &Setup, prev_relay_key: Option<String>, relay_port: u16) -> serde_json::Value {
    let mut model = serde_json::json!({ "framework": "openai", "max_tokens": 8192 });
    let mut relay = serde_json::Value::Null;
    match s.provider.as_str() {
        "chatgpt" => {
            // Ключ реле = ключ мозга: случайный, обязателен Bearer-ом на локальном порту.
            // CSPRNG (BCryptGenRandom, `common/random_hex.rs`); до 07.09 здесь был
            // RandomState + время. Отказ системного генератора — не повод
            // подставлять генератор похуже: это ключ, а не косметика.
            let key = prev_relay_key.unwrap_or_else(|| {
                format!("sk-frame-{}", random_hex(24).expect("системный генератор случайных чисел (BCryptGenRandom) отказал"))
            });
            // БЕЗ /v1: реле объявляет /chat/completions и /v1/models, маршрута
            // /v1/chat/completions у него нет — с /v1 каждый вызов модели давал 404.
            model["base_url"] = format!("http://127.0.0.1:{relay_port}").into();
            let chosen = s.chatgpt_model.trim();
            model["model"] = (if chosen.is_empty() { "gpt-5.6-sol" } else { chosen }).into();
            model["key"] = key.into();
            relay = serde_json::json!({ "enabled": true, "port": relay_port });
        }
        "local" => {
            model["base_url"] = s.local.base_url.trim().into();
            model["model"] = s.local.model.trim().into();
            // Ключа у Ollama/LM Studio нет, но ядро без ключа вообще не создаёт
            // клиента (live/llm.py: `if not key: return None`) — агент молчал бы
            // на каждый ход. Заглушка: оба сервера Authorization игнорируют.
            model["key"] = "local".into();
        }
        "anthropic" => {
            model["framework"] = "anthropic".into();
            model["base_url"] = s.anthropic.base_url.trim().into();
            model["model"] = s.anthropic.model.trim().into();
            model["key"] = s.anthropic.key.trim().into();
        }
        _ => {
            model["base_url"] = s.api.base_url.trim().into();
            model["model"] = s.api.model.trim().into();
            model["key"] = s.api.key.trim().into();
        }
    }
    let effort = s.reasoning_effort.trim().to_lowercase();
    if ["low", "medium", "high", "xhigh"].contains(&effort.as_str()) {
        model["reasoning_effort"] = effort.into();
    }
    // Ограда и служба — ДВА независимых ответа. `mode` здесь — местожительство
    // харнесса (`local`), ограда живёт СВОИМ ключом `agent_mode`: см.
    // localharness/modes.py, там же раскладка (modes.apply). Раскладываем прямо
    // здесь, чтобы установленный конфиг был согласован сразу, а не после
    // первого старта руннера — владелец открывает этот файл и читает его.
    let agent_mode = s.mode();
    let mut cfg = serde_json::json!({
        "mode": "local",
        "agent_mode": agent_mode,
        "sandbox": { "enabled": agent_mode == "sandbox", "network": true },
        "service": { "session0": s.wants_session0(), "firewall": s.firewall },
        // Третий ответ, тоже независимый: тело руки `computer`. Все четыре
        // права сразу — сузить владелец может в Настройках.
        "computer": computer_block(s.computer),
        "python": "runtime/python.exe",
        "app": "app/deskapp.py",
        "runner": "app/localharness/runner.py",
        "tree": "data",
        "code": "tree",
        "port": DESK_PORT,
        "agent": { "name": s.agent.trim() },
        "owner": { "name": s.owner.trim(), "room": PRODUCT_UI },
        "model": model,
        "telegram": {
            "bot_token": s.telegram.bot_token.trim(),
            "owner_id": s.telegram.owner_id.trim().parse::<i64>().unwrap_or(0),
        },
        "read_dotenv": false,
        // Адрес обновлений: без него «Проверить обновления» в окне отказывает
        // всегда («адрес обновлений не задан»), а умолчания не было ни в одной
        // из трёх подсистем — тот же адрес, что кладёт шаблон поставки
        // (installer/build_dist.py::HELENE_JSON). Владелец может его сменить в
        // настройках, и обновление продукта его выбор не тронет (см. merge_config).
        "update": { "url": UPDATE_URL },
        "setup_complete": true,
        "installed": { "version": VERSION, "service": s.wants_service() },
    });
    if !relay.is_null() {
        cfg["relay"] = relay;
    }
    cfg
}

/// Ключи, которыми владеет визард: всё остальное в существующем helene.json —
/// решения владельца, принятые ПОСЛЕ установки (песочница, телефон, ручки среды,
/// вход MTProto, адрес обновлений, evaluator…), и обновление их не трогает.
/// `agent_mode` здесь обязателен: без него переустановка поверх не перезаписала
/// бы режим, выбранный в визарде, — владелец выбрал бы одно, а получил старое.
/// А вот блоки `sandbox` и `service` целиком визард НЕ переписывает: в них
/// живут и чужие решения владельца (`sandbox.network`, `service.broker`).
/// Их он сливает по одному полю ниже.
const WIZARD_KEYS: [&str; 12] = [
    "mode", "agent_mode", "python", "app", "runner", "tree", "code",
    "agent", "owner", "model", "setup_complete", "installed",
];

/// Слить свежие решения визарда с тем, что уже лежит в установленном helene.json.
fn merge_config(existing: Option<serde_json::Value>, fresh: serde_json::Value, s: &Setup) -> serde_json::Value {
    let Some(serde_json::Value::Object(old)) = existing else { return fresh };
    let serde_json::Value::Object(new) = fresh else { return serde_json::Value::Object(old) };
    let mut out = old;
    for k in WIZARD_KEYS {
        if let Some(v) = new.get(k) {
            if k == "model" {
                continue; // ниже, по полям
            }
            out.insert(k.to_string(), v.clone());
        }
    }
    // `model` — по полям, а не целиком (ревью 06.09, §3, решение 4). Визард
    // знает адрес, имя модели, ключ, framework, потолок и усилие; окно после
    // установки заводит рядом `model.keys` (ключи всех провайдеров, чтобы
    // переключение не стирало их) и `model.compact_model` (модель свёрток).
    // Переписать блок целиком значило бы молча стереть их при обновлении
    // поверх — boot.py об этом предупреждал, но не мог помешать.
    if let Some(serde_json::Value::Object(fresh_model)) = new.get("model") {
        let mut model = match out.get("model") {
            Some(serde_json::Value::Object(m)) => m.clone(),
            _ => serde_json::Map::new(),
        };
        for (k, v) in fresh_model {
            model.insert(k.clone(), v.clone());
        }
        out.insert("model".into(), serde_json::Value::Object(model));
    }
    // Реле: появилось — ставим, провайдер сменился — убираем.
    match new.get("relay") {
        Some(v) => {
            out.insert("relay".into(), v.clone());
        }
        None => {
            out.remove("relay");
        }
    }
    // Telegram: визард владеет только двумя полями и только когда их заполнили —
    // пустое поле на переустановке не должно стирать уже работающего бота, а
    // mode/api_id/api_hash/phone (вход MTProto) визард не знает вовсе.
    let mut tg = match out.get("telegram") {
        Some(serde_json::Value::Object(m)) => m.clone(),
        _ => serde_json::Map::new(),
    };
    let bot = s.telegram.bot_token.trim();
    if !bot.is_empty() {
        tg.insert("bot_token".into(), bot.into());
    } else if !tg.contains_key("bot_token") {
        tg.insert("bot_token".into(), "".into());
    }
    let oid = s.telegram.owner_id.trim();
    if !oid.is_empty() {
        tg.insert("owner_id".into(), oid.parse::<i64>().unwrap_or(0).into());
    } else if !tg.contains_key("owner_id") {
        tg.insert("owner_id".into(), 0.into());
    }
    out.insert("telegram".into(), serde_json::Value::Object(tg));
    // Раскладка обоих ответов по ручкам: одно поле из блока, остальное — не
    // наше. `sandbox.network` (сеть контейнера), `service.broker` (выключатель
    // брокера) и `service.firewall` (правило брандмауэра) — решения владельца,
    // принятые ПОСЛЕ установки; переписать блок целиком значило бы молча их
    // отменить. Экрана у брандмауэра в визарде нет вовсе, поэтому ключ только
    // ДОСТАВЛЯЕТСЯ, если его нет, — ровно как `sandbox.network`.
    let mode = s.mode();
    for (block, key, value) in [
        ("sandbox", "enabled", serde_json::Value::Bool(mode == "sandbox")),
        ("service", "session0", serde_json::Value::Bool(s.wants_session0())),
        // Тело руки `computer`: выключатель — визарда, права и порт — владельца
        // (права он сужает в Настройках, порт правит руками). Блок прошлой
        // установки мог родиться без них — тогда доставляем умолчания.
        ("computer", "enabled", serde_json::Value::Bool(s.computer)),
    ] {
        let mut b = match out.get(block) {
            Some(serde_json::Value::Object(m)) => m.clone(),
            _ => serde_json::Map::new(),
        };
        b.insert(key.into(), value);
        if block == "sandbox" && !b.contains_key("network") {
            b.insert("network".into(), true.into());
        }
        if block == "service" && !b.contains_key("firewall") {
            b.insert("firewall".into(), s.firewall.into());
        }
        if block == "computer" {
            if !b.contains_key("port") {
                b.insert("port".into(), COMPUTER_PORT.into());
            }
            if !b.contains_key("scopes") {
                b.insert("scopes".into(), serde_json::json!(COMPUTER_SCOPES));
            }
        }
        out.insert(block.to_string(), serde_json::Value::Object(b));
    }
    // Единственный случай, когда адрес обновлений всё-таки переписывается:
    // там стоит НАШ прежний адрес (буква в букву). Выпуски переехали, и копия
    // с прежним адресом перестала бы их видеть; чужой адрес, вписанный
    // владельцем, по-прежнему неприкосновенен.
    if let Some(serde_json::Value::Object(upd)) = out.get_mut("update") {
        if upd.get("url").and_then(|v| v.as_str()) == Some(UPDATE_URL_WAS) {
            upd.insert("url".into(), serde_json::Value::String(UPDATE_URL.to_string()));
        }
    }
    // Ключи, которые визард НЕ переписывает, но доставляет, если их нет вовсе:
    // конфиг прошлой установки мог родиться до того, как ключ появился, и
    // тогда «Проверить обновления» отказывало бы и после обновления продукта.
    // Свой адрес обновлений, вписанный владельцем, остаётся нетронутым.
    for k in ["port", "read_dotenv", "update"] {
        if !out.contains_key(k) {
            if let Some(v) = new.get(k) {
                out.insert(k.to_string(), v.clone());
            }
        }
    }
    serde_json::Value::Object(out)
}

/// helene.json так, как его сохранил редактор ВЛАДЕЛЬЦА: UTF-8, UTF-8 с меткой
/// BOM, UTF-16 с меткой — та же функция, что `decode_config` в оболочке и
/// службе. ⚠ До 06.09 здесь стояло голое `read_to_string` + `from_str`: файл,
/// правленный Блокнотом (BOM), не разбирался, `existing` становился `None`, и
/// `merge_config` молча возвращал свежий конфиг — решения владельца
/// (монтирование, телефон, ручки среды, вход MTProto) и ключ реле терялись при
/// обновлении поверх. Найдено ревью 06.09.
fn decode_config(bytes: &[u8]) -> Option<String> {
    if bytes.starts_with(&[0xFF, 0xFE]) || bytes.starts_with(&[0xFE, 0xFF]) {
        let big = bytes[0] == 0xFE;
        let body = &bytes[2..];
        if body.len() % 2 != 0 {
            return None;
        }
        let units: Vec<u16> = body
            .chunks_exact(2)
            .map(|p| if big { u16::from_be_bytes([p[0], p[1]]) } else { u16::from_le_bytes([p[0], p[1]]) })
            .collect();
        return String::from_utf16(&units).ok();
    }
    let body = bytes.strip_prefix(&[0xEF, 0xBB, 0xBF]).unwrap_or(bytes);
    String::from_utf8(body.to_vec()).ok()
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    let raw = std::fs::read(path).ok()?;
    serde_json::from_str(&decode_config(&raw)?).ok()
}

fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent).map_err(|e| io_note(parent, &e))?;
    }
    // Прежнее содержимое — рядом в .bak: отката у установки нет, и это
    // единственный след, по которому можно вернуть настройки руками.
    if path.exists() {
        let mut bak = path.as_os_str().to_os_string();
        bak.push(".bak");
        let _ = std::fs::copy(path, PathBuf::from(bak));
    }
    let tmp = path.with_extension("tmp");
    std::fs::write(&tmp, text).map_err(|e| io_note(&tmp, &e))?;
    std::fs::rename(&tmp, path).map_err(|e| io_note(path, &e))?;
    Ok(())
}

// ---------------------------------------------------------------- процессы

/// Сколько процессов запущено из этой папки (кроме нас самих). None — спросить
/// не вышло. Путь сравниваем через StartsWith, а не -like: у -like `[` и `]`
/// подстановочные, и путь со скобками не совпадает сам с собой.
fn procs_under(dir: &Path) -> Option<usize> {
    let script = format!(
        "$d='{}'; @(Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d,'OrdinalIgnoreCase') -and $_.ProcessId -ne {} }}).Count",
        ps_escape(&format!("{}\\", dir.display())),
        std::process::id()
    );
    let out = powershell(&script).ok()?;
    if !out.status.success() {
        return None;
    }
    console_text(&out.stdout).trim().parse::<usize>().ok()
}

/// Остановить всё, что запущено из папки установки: окно, детей харнесса, реле.
/// Возвращает true, когда после этого из папки не работает НИЧЕГО: раньше здесь
/// стояли глухие 600 мс, и копирование начиналось поверх ещё живых файлов.
pub fn stop_running(dir: &Path) -> bool {
    let script = format!(
        "$d='{}'; Get-CimInstance Win32_Process | Where-Object {{ $_.ExecutablePath -and $_.ExecutablePath.StartsWith($d,'OrdinalIgnoreCase') -and $_.ProcessId -ne {} }} | ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}",
        ps_escape(&format!("{}\\", dir.display())),
        std::process::id()
    );
    let _ = powershell(&script);
    for _ in 0..20 {
        std::thread::sleep(std::time::Duration::from_millis(400));
        match procs_under(dir) {
            Some(0) => return true,
            // Спросить не вышло — не врём, что чисто, но и не висим: решать будет
            // проверка занятости файлов.
            None => return false,
            Some(_) => continue,
        }
    }
    false
}

/// Файлы, которые нельзя заменить прямо сейчас. Пустой список — путь свободен.
/// Дешевле споткнуться здесь, чем посреди копирования: после падения на середине
/// установка остаётся смесью двух версий, и отката у неё нет.
fn locked_files(dir: &Path) -> Vec<String> {
    let mut busy = Vec::new();
    for name in ["helene.exe", "helene-svc.exe", "helene-relay.exe", "helene-setup.exe",
                 "helene-bridge.exe", "helene-body.exe"] {
        let path = dir.join(name);
        if !path.exists() {
            continue;
        }
        // Себя самого не проверяем: установщик, запущенный из папки установки,
        // держит собственный exe всегда.
        if std::env::current_exe().map(|p| p == path).unwrap_or(false) {
            continue;
        }
        if let Err(e) = std::fs::OpenOptions::new().write(true).open(&path) {
            if matches!(e.raw_os_error(), Some(32) | Some(33)) {
                busy.push(name.to_string());
            }
        }
    }
    busy
}

// ---------------------------------------------------------------- порты

/// Слушает ли кто-то 127.0.0.1:port прямо сейчас.
fn port_busy(port: u16) -> bool {
    use std::net::{Ipv4Addr, SocketAddr, TcpStream};
    let addr = SocketAddr::from((Ipv4Addr::LOCALHOST, port));
    TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(350)).is_ok()
}

/// Кто держит порт — человеческим именем процесса. Нужно не для механики, а
/// чтобы владелец видел причину: «5011 занят vera-relay.exe» вместо молчания.
fn port_holder(port: u16) -> String {
    let script = format!(
        "$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; \
         if ($c) {{ $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p) {{ \"$($p.ProcessName).exe\" }} }}"
    );
    powershell(&script)
        .map(|o| console_text(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// Свободный порт для встроенного реле. Сначала желанный (прошлый выбор или
/// 5011), потом соседние. None — все заняты, и это препятствие, а не мелочь:
/// молча оставить 5011 значит отправить мозг в ЧУЖОЕ реле (прежнего поколения
/// продукта), то есть в чужую подписку.
fn free_relay_port(desired: u16) -> Option<u16> {
    if !port_busy(desired) {
        return Some(desired);
    }
    (RELAY_PORT..RELAY_PORT.saturating_add(20)).find(|p| *p != desired && !port_busy(*p))
}

/// Имя агента из установленного helene.json — для снятия ярлыков с его именем.
fn installed_agent_name(dir: &Path) -> Option<String> {
    let cfg = read_json(&dir.join("helene.json"))?;
    let name = cfg.get("agent")?.get("name")?.as_str()?.trim().to_string();
    if name.is_empty() { None } else { Some(name) }
}

/// Папка установленной программы по записи в «Приложениях».
#[cfg(windows)]
fn registered_dir() -> Option<PathBuf> {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let key = RegKey::predef(HKEY_CURRENT_USER)
        .open_subkey(format!("Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{PRODUCT}"))
        .ok()?;
    let path: String = key.get_value("InstallLocation").ok()?;
    if path.trim().is_empty() { None } else { Some(PathBuf::from(path)) }
}

#[cfg(not(windows))]
fn registered_dir() -> Option<PathBuf> {
    None
}

/// Что уже установлено: сначала по записи в «Приложениях», иначе по папке по умолчанию.
pub fn installed_info() -> Option<Installed> {
    let dir = registered_dir().or_else(default_dir)?;
    let cfg = read_json(&dir.join("helene.json"))?;
    if cfg.get("setup_complete").and_then(|v| v.as_bool()) != Some(true) {
        return None;
    }
    Some(Installed {
        dir: dir.display().to_string(),
        version: cfg
            .get("installed")
            .and_then(|i| i.get("version"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
        agent: cfg
            .get("agent")
            .and_then(|a| a.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string(),
    })
}

// ---------------------------------------------------------------- ярлыки

/// Путь к настоящей папке рабочего стола: на Windows 11 с резервным копированием
/// папок OneDrive это %USERPROFILE%\OneDrive\Рабочий стол, а не %USERPROFILE%\Desktop.
/// Ярлык создавался по известной папке, а удалялся склейкой из USERPROFILE — и
/// переживал удаление программы.
fn desktop_dir() -> Option<PathBuf> {
    let out = powershell("[Environment]::GetFolderPath('Desktop')").ok()?;
    let path = console_text(&out.stdout).trim().to_string();
    if path.is_empty() {
        return std::env::var_os("USERPROFILE").map(|p| PathBuf::from(p).join("Desktop"));
    }
    Some(PathBuf::from(path))
}

fn shortcuts(exe: &Path, name: &str, icon: Option<&Path>) -> Result<String, String> {
    let script = std::env::temp_dir().join("helene-start-menu-shortcut.ps1");
    std::fs::write(&script, include_str!("../../shell/resources/start-menu-shortcut.ps1"))
        .map_err(|e| e.to_string())?;
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-ExecutionPolicy", "Bypass", "-File"])
        .arg(&script)
        .arg("-Exe")
        .arg(exe)
        .arg("-Aumid")
        .arg(AUMID)
        .arg("-Name")
        .arg(name);
    if let Some(icon) = icon {
        cmd.arg("-Icon").arg(icon);
    }
    let out = run_hidden(&mut cmd)?;
    if !out.status.success() {
        return Err(console_text(&out.stderr).trim().to_string());
    }
    let mut note = console_text(&out.stdout).trim().to_string();
    // Рабочий стол — обычный ярлык, без свойств. Результат читаем: раньше он
    // выбрасывался, и расписка «Ярлыки: ok» относилась только к меню «Пуск».
    let desktop = powershell(&format!(
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Desktop')+'\\{}.lnk'); $s.TargetPath='{}'; $s.WorkingDirectory='{}'; {} $s.Save()",
        ps_escape(name),
        ps_escape(&exe.display().to_string()),
        ps_escape(&exe.parent().map(|p| p.display().to_string()).unwrap_or_default()),
        icon.map(|i| format!("$s.IconLocation='{},0';", ps_escape(&i.display().to_string()))).unwrap_or_default()
    ));
    match desktop {
        Ok(out) if out.status.success() => note.push_str("; ярлык рабочего стола ok"),
        Ok(out) => {
            let err = console_text(&out.stderr).trim().to_string();
            note.push_str(&format!("; ярлык рабочего стола не создан: {err}"));
        }
        Err(e) => note.push_str(&format!("; ярлык рабочего стола не создан: {e}")),
    }
    Ok(note)
}

/// Запись в «Приложениях» Windows: удаление через тот же установщик.
#[cfg(windows)]
fn register_uninstall(dir: &Path, size_kb: u32, icon: Option<&Path>) -> Result<(), String> {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let hkcu = RegKey::predef(HKEY_CURRENT_USER);
    let (key, _) = hkcu
        .create_subkey(format!(
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{PRODUCT}"
        ))
        .map_err(|e| e.to_string())?;
    let setup = dir.join("helene-setup.exe");
    let exe = dir.join("helene.exe");
    let set = |name: &str, value: String| key.set_value(name, &value).map_err(|e| e.to_string());
    set("DisplayName", PRODUCT_UI.to_string())?;
    set("DisplayVersion", VERSION.to_string())?;
    set("Publisher", PRODUCT_UI.to_string())?;
    set("InstallLocation", dir.display().to_string())?;
    set("DisplayIcon", icon.map(|i| i.display().to_string()).unwrap_or_else(|| exe.display().to_string()))?;
    set("UninstallString", format!("\"{}\" --uninstall", setup.display()))?;
    set("QuietUninstallString", format!("\"{}\" --uninstall --quiet", setup.display()))?;
    // Размер и дата: без них «Установленные приложения» показывают пустоту там,
    // где владелец ищет, что занимает место.
    let _ = key.set_value("EstimatedSize", &size_kb);
    let _ = set("InstallDate", install_date());
    key.set_value("NoModify", &1u32).map_err(|e| e.to_string())?;
    key.set_value("NoRepair", &1u32).map_err(|e| e.to_string())?;
    // Та же запись, что делает оболочка: имя и значок для центра уведомлений.
    if let Ok((toast, _)) =
        hkcu.create_subkey(format!("Software\\Classes\\AppUserModelId\\{AUMID}"))
    {
        let _ = toast.set_value("DisplayName", &PRODUCT_UI);
        let png = dir.join("data").join("icon.png");
        let icon_uri = if png.exists() { png } else { dir.join("helene.ico") };
        let _ = toast.set_value("IconUri", &icon_uri.display().to_string());
    }
    Ok(())
}

#[cfg(not(windows))]
fn register_uninstall(_dir: &Path, _size_kb: u32, _icon: Option<&Path>) -> Result<(), String> {
    Ok(())
}

fn install_date() -> String {
    powershell("(Get-Date).ToString('yyyyMMdd')")
        .ok()
        .map(|o| console_text(&o.stdout).trim().to_string())
        .filter(|s| s.len() == 8)
        .unwrap_or_default()
}

fn dir_size_kb(dir: &Path) -> u32 {
    fn walk(p: &Path, acc: &mut u64) {
        let Ok(rd) = std::fs::read_dir(p) else { return };
        for e in rd.flatten() {
            match e.metadata() {
                Ok(m) if m.is_dir() => walk(&e.path(), acc),
                Ok(m) => *acc += m.len(),
                Err(_) => {}
            }
        }
    }
    let mut bytes = 0u64;
    walk(dir, &mut bytes);
    (bytes / 1024).min(u32::MAX as u64) as u32
}

// ---------------------------------------------------------------- реле

/// Дом реле на время установки: вход в ChatGPT делается ДО копирования, а
/// учётные данные переезжают в data/relay установленной программы.
pub fn relay_home() -> PathBuf {
    std::env::temp_dir().join("helene-setup-relay")
}

/// Убрать временный дом реле. Там лежит auth.json с живым refresh_token к
/// аккаунту ChatGPT владельца: раньше он оставался в %TEMP% навсегда — установка
/// его копировала, а не переносила, и ни снятие, ни выход из визарда о нём не знали.
/// Заодно это лечит ложное «Вход выполнен» от протухшей учётки прошлого запуска.
pub fn relay_cleanup() {
    let home = relay_home();
    if home.exists() {
        let _ = std::fs::remove_dir_all(&home);
    }
}

pub fn relay_status() -> String {
    if relay_home().join("local_auth").join("auth.json").exists() {
        return "authorized".into();
    }
    if let Ok(mut guard) = LOGIN.lock() {
        if let Some(child) = guard.as_mut() {
            if child.try_wait().ok().flatten().is_none() {
                return "pending".into();
            }
        }
    }
    "no-auth".into()
}

/// Незавершённый вход в ChatGPT: один за раз. Повторное нажатие отменяет
/// прежнюю попытку — иначе помощник реле держит порт 1455, а новые попытки
/// падают и роняют браузерный обратный вызов с 400.
static LOGIN: Mutex<Option<Child>> = Mutex::new(None);

/// Отменить незавершённый вход: дерево процессов целиком (реле + помощник).
/// Учётные данные НЕ трогаем: отмена бывает и между двумя попытками входа.
pub fn relay_abort() {
    let Ok(mut guard) = LOGIN.lock() else { return };
    if let Some(mut child) = guard.take() {
        if child.try_wait().ok().flatten().is_none() {
            let mut kill = Command::new(sys_exe("taskkill.exe"));
            kill.args(["/PID", &child.id().to_string(), "/T", "/F"]);
            let _ = run_hidden(&mut kill);
            let _ = child.wait();
        }
    }
}

/// Список моделей самого реле: поднять реле из поставки на временном порту, спросить
/// /v1/models, погасить. Без захардкоженного списка — что реле отдаёт, то и выбор.
pub fn relay_models() -> Result<Vec<String>, String> {
    let payload = payload_dir().ok_or("рядом с установщиком нет поставки")?;
    let exe = payload.join("helene-relay.exe");
    if !exe.exists() {
        return Err("в этой поставке нет helene-relay.exe".into());
    }
    let home = relay_home();
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    let port = 5019u16;
    let mut cmd = Command::new(&exe);
    cmd.arg("serve")
        .current_dir(&home)
        .env("RELAY_PORT", port.to_string())
        .env("RELAY_LOCAL", "1")
        .env("RELAY_LOG_DIR", home.join("logs"));
    let python = payload.join("runtime").join("python.exe");
    if python.exists() {
        cmd.env("RELAY_PYTHON", &python);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    let mut child = cmd.spawn().map_err(|e| format!("реле не запустилось: {e}"))?;
    // Маршрут реле — /v1/models. По /models оно отдаёт голый 404, и кнопка
    // «Показать модели реле» не работала никогда.
    let url = format!("http://127.0.0.1:{port}/v1/models");
    let agent = ureq::AgentBuilder::new().timeout(std::time::Duration::from_secs(3)).build();
    let mut models = Vec::new();
    let mut last_err = String::new();
    for _ in 0..16 {
        std::thread::sleep(std::time::Duration::from_millis(500));
        if let Ok(Some(status)) = child.try_wait() {
            last_err = format!("реле вышло с кодом {status}; возможно, порт {port} занят");
            break;
        }
        match agent.get(&url).call() {
            Ok(resp) => {
                models = model_ids(&resp.into_string().unwrap_or_default());
                break;
            }
            Err(err) => last_err = err.to_string(),
        }
    }
    let _ = child.kill();
    let _ = child.wait();
    if models.is_empty() {
        return Err(format!("реле не ответило списком моделей: {}", last_err.chars().take(160).collect::<String>()));
    }
    Ok(models)
}

/// Логин реле в подписку ChatGPT: реле лежит в поставке, браузер откроется сам.
pub fn relay_login() -> Result<String, String> {
    relay_abort();
    let payload = payload_dir().ok_or("рядом с установщиком нет поставки")?;
    let exe = payload.join("helene-relay.exe");
    if !exe.exists() {
        return Err("в этой поставке нет helene-relay.exe".into());
    }
    let home = relay_home();
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(&exe);
    cmd.arg("login")
        .current_dir(&home)
        .env("RELAY_LOCAL", "1")
        .env("RELAY_LOG_DIR", home.join("logs"));
    let python = payload.join("runtime").join("python.exe");
    if python.exists() {
        cmd.env("RELAY_PYTHON", &python);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    let mut child = cmd.spawn().map_err(|e| format!("логин не запустился: {e}"))?;
    // Обещать браузер по факту создания процесса нельзя: реле падало на старте
    // (занятый порт 1455, нет сети), а владелец видел «сейчас откроется».
    std::thread::sleep(std::time::Duration::from_millis(1200));
    if let Ok(Some(status)) = child.try_wait() {
        let tail = relay_log_tail();
        return Err(format!(
            "вход не запустился (реле вышло с кодом {status}). {tail}Возможно, прошлая попытка ещё держит порт 1455 — подожди несколько секунд и нажми снова."
        ));
    }
    if let Ok(mut guard) = LOGIN.lock() {
        *guard = Some(child);
    }
    Ok("Открываю браузер: заверши вход в свой аккаунт ChatGPT там".into())
}

/// Последние строки журнала реле: причина падения вместо молчания.
fn relay_log_tail() -> String {
    let dir = relay_home().join("logs");
    let Ok(rd) = std::fs::read_dir(&dir) else { return String::new() };
    let mut newest: Option<(std::time::SystemTime, PathBuf)> = None;
    for e in rd.flatten() {
        if let Ok(m) = e.metadata() {
            if let Ok(t) = m.modified() {
                if newest.as_ref().map(|(bt, _)| t > *bt).unwrap_or(true) {
                    newest = Some((t, e.path()));
                }
            }
        }
    }
    let Some((_, path)) = newest else { return String::new() };
    let Ok(text) = std::fs::read_to_string(&path) else { return String::new() };
    let tail: Vec<&str> = text.lines().rev().take(3).collect();
    if tail.is_empty() {
        return String::new();
    }
    format!("Журнал реле: {}. ", tail.into_iter().rev().collect::<Vec<_>>().join(" / ").chars().take(240).collect::<String>())
}

/// Перенос учётных данных ChatGPT в установленную программу. Именно ПЕРЕНОС:
/// копия оставляла живой refresh_token в общедоступной %TEMP% навсегда.
fn move_relay_auth(dir: &Path) -> Option<Result<String, String>> {
    let from = relay_home().join("local_auth");
    if !from.is_dir() {
        return None;
    }
    let to = dir.join("data").join("relay").join("local_auth");
    Some(match copy_dir(&from, &to, &[]) {
        Ok(n) => {
            relay_cleanup();
            Ok(format!("вход в ChatGPT перенесён ({n} файлов)"))
        }
        Err(e) => Err(format!("вход в ChatGPT не перенёсся: {e}")),
    })
}

/// Живая проверка адреса и ключа — `common/model_probe.rs`, тем же кодом, что
/// в окне. Успех — только настоящий список моделей.
pub fn probe_model(base_url: &str, key: &str, framework: &str) -> (bool, String, Vec<String>) {
    probe_model_blocking(base_url, key, framework)
}

// ---------------------------------------------------------------- служба

/// Один поднятый вызов (`common/service_op.rs`): пишем обёртку во временную
/// папку и запускаем её через UAC, ЧИТАЯ код возврата (-PassThru): отказ от
/// прав больше не выглядит успехом. `name` — имя службы в SCM: не только своё,
/// но и прежних поколений продукта (Vera, Frame), которые прошлое снятие
/// оставило живыми под LocalSystem.
fn service_op(op: &str, name: &str, script: Option<&Path>) -> Result<(), String> {
    let wrapper = std::env::temp_dir().join("helene-service-op.ps1");
    std::fs::write(&wrapper, SERVICE_OP_PS1).map_err(|e| io_note(&wrapper, &e))?;
    let out = powershell(&service_op_command(&wrapper, op, name, script))?;
    service_op_verdict(out.status.code())
}

/// Служба: один UAC на машинную часть. Ждём завершения скрипта, потом
/// спрашиваем SCM сами — квитанция о фактическом состоянии, не «запустил».
fn install_service(dir: &Path) -> String {
    let script = dir.join("install-service.ps1");
    if !script.exists() || !dir.join("helene-svc.exe").exists() {
        return "missing".into();
    }
    if let Err(e) = service_op("install", PRODUCT, Some(&script)) {
        // Скрипт мог отработать и всё равно вернуть ненулевой код — верим SCM,
        // но причину не теряем.
        let state = service_state();
        if state == "absent" {
            return format!("failed: {e}");
        }
        return state;
    }
    service_state()
}

/// Предупреждение, которое служба кладёт рядом с конфигом при установке
/// (`svc/src/main.rs::install` → `service-warning.txt`): служба работает как
/// СИСТЕМА и запускает код из папки, куда пишет обычный пользователь.
///
/// Своим `println!` служба сказать этого не может: её зовут скрытым поднятым
/// процессом, наверх едет только код возврата. Поэтому читаем файл сами — после
/// установки — и кладём текст в расписку, где владелец его увидит.
fn service_warning(dir: &Path) -> Option<String> {
    let text = std::fs::read_to_string(dir.join("service-warning.txt")).ok()?;
    let text = text.trim().to_string();
    if text.is_empty() {
        None
    } else {
        Some(text)
    }
}

/// Права администратора у этой учётной записи — для одной вещи: карточка
/// «Служба» на экране режима должна быть недоступна с НАЗВАННОЙ причиной, а не
/// просто серой.
#[derive(Serialize)]
pub struct AdminRights {
    /// Учётка состоит в группе администраторов: UAC она подтвердить сможет.
    pub can: bool,
    /// Windows ответила внятно. false — визард ничего не запрещает.
    pub certain: bool,
    /// Установщик уже запущен с правами администратора.
    pub elevated: bool,
}

/// ⚠ Почему не `IsInRole(Administrator)` в одиночку: у администратора,
/// работающего БЕЗ элевации (обычное состояние), токен отфильтрован, и
/// `IsInRole` отвечает false — карточка службы стала бы недоступной ровно тому,
/// у кого права есть. SID группы в `TokenGroups` при этом остаётся (помечен
/// deny-only), поэтому смотрим сами группы токена. Состав локальной группы
/// спрашиваем только вторым заходом: на доменной машине запрос бывает
/// медленным и может отказать — тогда это «не знаю», а не «нет».
const ADMIN_PROBE_PS1: &str = r#"$id=[Security.Principal.WindowsIdentity]::GetCurrent()
$member=$false
try { $member = @($id.Groups | ForEach-Object { $_.Value }) -contains 'S-1-5-32-544' } catch {}
$listed='skip'
if (-not $member) {
  try {
    $me=$id.User.Value
    $g=Get-LocalGroupMember -SID 'S-1-5-32-544' -ErrorAction Stop
    $listed=([bool](@($g | ForEach-Object { $_.SID.Value }) -contains $me)).ToString()
  } catch { $listed='unknown' }
}
$elev=$false
try { $elev=(New-Object Security.Principal.WindowsPrincipal($id)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator) } catch {}
"$member|$listed|$elev""#;

pub fn admin_rights() -> AdminRights {
    // Windows молчит — не запрещаем ничего: заставой остаётся UAC при
    // установке, и его отказ визард уже показывает словами.
    let unknown = AdminRights { can: true, certain: false, elevated: false };
    let Ok(out) = powershell(ADMIN_PROBE_PS1) else { return unknown };
    let text = console_text(&out.stdout);
    let parts: Vec<&str> = text.trim().split('|').collect();
    if parts.len() != 3 {
        return unknown;
    }
    let member = parts[0].trim().eq_ignore_ascii_case("true");
    let listed = parts[1].trim();
    AdminRights {
        can: member || listed.eq_ignore_ascii_case("true"),
        certain: member || listed.eq_ignore_ascii_case("true") || listed.eq_ignore_ascii_case("false"),
        elevated: parts[2].trim().eq_ignore_ascii_case("true"),
    }
}

pub fn service_state() -> String {
    service_state_of(PRODUCT)
}

/// С дедлайном, как в окне: повисший sc.exe не должен вешать визард
/// (ревью 06.09, §4 п. 13).
pub fn service_state_of(name: &str) -> String {
    let mut cmd = Command::new(sys_exe("sc.exe"));
    cmd.args(["query", name]);
    match run_hidden_for(&mut cmd, std::time::Duration::from_secs(15)) {
        Ok(out) if out.status.success() => {
            let text = console_text(&out.stdout).to_uppercase();
            if text.contains("RUNNING") || text.contains("START_PENDING") {
                "running".into()
            } else {
                "stopped".into()
            }
        }
        _ => "absent".into(),
    }
}

// ------------------------------------------------- службы прежних поколений

/// Имена, под которыми этот же продукт жил раньше. Это не «чужие программы»:
/// Vera и Frame — он же двумя переименованиями назад, и репозиторий помнит их
/// сам (`localharness/fence.py` знает `vera.shell.*`, ключ реле до сих пор
/// зовётся `sk-frame-…`, труба окна — `frame.desk.v1`).
/// Мина, ради которой это заведено: прежнее снятие сносило ФАЙЛЫ и оставляло
/// живую службу. На машине автора так и осталась `Vera` — Running, Auto,
/// LocalSystem, exe в папке пользователя, а её ребёнок держит порт 5011,
/// то есть новый агент ушёл бы в реле прошлого поколения.
pub const KNOWN_SERVICE_NAMES: [&str; 4] = ["Vera", "Frame", "Praxis", PRODUCT];

#[derive(Serialize, Clone)]
pub struct LegacyService {
    pub name: String,
    pub state: String,   // Running / Stopped / …
    pub start: String,   // Auto / Manual / Disabled
    pub account: String, // LocalSystem — то есть права системы
    pub path: String,    // ImagePath: откуда стартует
    /// Служба текущей установки: её снимает сам установщик, отдельного согласия
    /// не спрашиваем — иначе одна и та же служба была бы в двух местах экрана.
    pub ours: bool,
}

/// Сам exe из ImagePath. ImagePath — командная строка целиком: в кавычках,
/// когда в пути есть пробел, и без них — с аргументами следом
/// («…\vera-svc.exe run --config \\?\…\vera.json» — живой пример со стола
/// автора). Резать по первому пробелу или по « -» нельзя: и то и другое даёт
/// несуществующий путь и ломает проверку «это наша папка или чужая».
fn image_exe(cmdline: &str) -> String {
    let t = cmdline.trim();
    if let Some(rest) = t.strip_prefix('"') {
        return rest.split('"').next().unwrap_or("").to_string();
    }
    let lower = t.to_lowercase();
    match lower.find(".exe") {
        Some(i) => t[..i + 4].to_string(),
        None => t.split(' ').next().unwrap_or(t).to_string(),
    }
}

/// Опрос SCM по известным именам. `home` — папка, которую сейчас ставят или
/// снимают: служба, чей exe лежит в ней, помечается «своей».
pub fn legacy_services(home: Option<&Path>) -> Vec<LegacyService> {
    let filter = KNOWN_SERVICE_NAMES
        .iter()
        .map(|n| format!("Name='{}'", ps_escape(n)))
        .collect::<Vec<_>>()
        .join(" or ");
    let script = format!(
        "@(Get-CimInstance Win32_Service -Filter \"{filter}\" | Select-Object Name,State,StartMode,StartName,PathName) | ConvertTo-Json -Compress -Depth 3"
    );
    let Ok(out) = powershell(&script) else { return Vec::new() };
    parse_services(console_text(&out.stdout).trim(), home)
}

/// Разбор ответа PowerShell. Отдельно от вызова, потому что форма ответа —
/// мина: PowerShell 5.1 отдаёт ОБЪЕКТ, а не массив, когда служба нашлась одна
/// (а именно так на машине, где живёт одна `Vera`), и парсер, ждущий массив,
/// нашёл бы ноль служб ровно в том случае, ради которого всё и делается.
fn parse_services(body: &str, home: Option<&Path>) -> Vec<LegacyService> {
    if body.is_empty() {
        return Vec::new();
    }
    let Ok(parsed) = serde_json::from_str::<serde_json::Value>(body) else {
        return Vec::new();
    };
    let rows: Vec<serde_json::Value> = match parsed {
        serde_json::Value::Array(a) => a,
        v @ serde_json::Value::Object(_) => vec![v],
        _ => Vec::new(),
    };
    let home_norm = home.map(norm_path);
    rows.into_iter()
        .filter_map(|r| {
            let text = |k: &str| r.get(k).and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
            let name = text("Name");
            if name.is_empty() {
                return None;
            }
            let path = text("PathName");
            let exe = image_exe(&path);
            let ours = match (&home_norm, PathBuf::from(&exe).parent()) {
                (Some(h), Some(p)) => inside_or_same(&norm_path(p), h),
                _ => false,
            };
            Some(LegacyService {
                name,
                state: text("State"),
                start: text("StartMode"),
                account: text("StartName"),
                path,
                ours,
            })
        })
        .collect()
}

/// Снять названную службу — ТОЛЬКО по явному согласию владельца (кнопка в
/// сцене). Тем же рецептом, что и свою: сначала гасим автоперезапуск и
/// автозапуск, потом снимаем, потом опрашиваем SCM до исчезновения.
pub fn remove_service(name: &str) -> Result<String, String> {
    if !KNOWN_SERVICE_NAMES.contains(&name) {
        return Err(format!("незнакомое имя службы: {name}"));
    }
    if service_state_of(name) == "absent" {
        return Ok(format!("службы «{name}» на машине нет"));
    }
    let err = service_op("uninstall", name, None).err();
    // Верим SCM, а не коду возврата: живая служба LocalSystem — вся суть находки.
    if service_state_of(name) != "absent" {
        return Err(match err {
            Some(e) => format!("служба «{name}» осталась: {e}"),
            None => format!("служба «{name}» осталась зарегистрированной"),
        });
    }
    Ok(format!("служба «{name}» снята"))
}

// ---------------------------------------------------------------- установка

/// Проверки, общие для визарда и для `--install <json>`: у безоконного пути
/// своих проверок не было вовсе, и пустая конституция становилась вечной
/// (boot.ensure_layout пишет её только когда файла НЕТ, а он есть — пустой).
fn validate_setup(s: &Setup) -> Result<(), String> {
    if !s.accepted {
        return Err("конституция не принята".into());
    }
    if s.agent.trim().is_empty() {
        return Err("не задано имя агента".into());
    }
    if s.owner.trim().is_empty() {
        return Err("не задано имя владельца".into());
    }
    if s.constitution.trim().is_empty() {
        return Err("конституция пуста".into());
    }
    if s.constitution.contains("{{") {
        return Err("в конституции остался незаполненный шаблон ({{…}})".into());
    }
    match s.provider.as_str() {
        "api" | "anthropic" => {
            let e = if s.provider == "api" { &s.api } else { &s.anthropic };
            if e.base_url.trim().is_empty() {
                return Err("не задан адрес модели".into());
            }
            if e.model.trim().is_empty() {
                return Err("не задано имя модели".into());
            }
            if e.key.trim().is_empty() {
                return Err("не задан ключ модели".into());
            }
        }
        "local" => {
            if s.local.base_url.trim().is_empty() {
                return Err("не задан адрес локальной модели".into());
            }
            if s.local.model.trim().is_empty() {
                return Err("не задано имя локальной модели".into());
            }
        }
        _ => {}
    }
    Ok(())
}

/// Сама установка. `progress` зовётся перед каждым шагом.
pub fn install(s: &Setup, mut progress: impl FnMut(Progress)) -> Result<Receipt, String> {
    validate_setup(s)?;
    let payload = payload_dir().ok_or_else(|| {
        "рядом с установщиком нет поставки (helene.exe, app/, runtime/) — запусти его из папки Hélène".to_string()
    })?;
    let dir = if s.dir.trim().is_empty() {
        default_dir().ok_or("Windows не сказала, где %LOCALAPPDATA%: укажи папку установки явно")?
    } else {
        PathBuf::from(s.dir.trim())
    };
    // Поставка и установка не должны пересекаться ни в одну сторону: dst внутри
    // src уводил копирование в бесконечную матрёшку (переполнение стека, гигабайты
    // мусора), а dir == payload гасил живого агента и падал на первом же файле.
    let (np, nd) = (norm_path(&payload), norm_path(&dir));
    if inside_or_same(&nd, &np) || inside_or_same(&np, &nd) {
        return Err(format!(
            "папка установки ({}) и папка поставки ({}) не должны совпадать или лежать одна в другой. \
             Если {PRODUCT_UI} уже установлена здесь, настройки меняются в окне программы, а не установщиком.",
            dir.display(),
            payload.display()
        ));
    }

    let service_before = service_state();
    let mut pre_steps: Vec<Step> = Vec::new();
    if dir.exists() {
        // Служба работает от LocalSystem: Stop-Process из-под обычного
        // пользователя её не убьёт, а её exe лежит в этой же папке и будет занят.
        if service_before != "absent" {
            match service_op("uninstall", PRODUCT, Some(&dir.join("uninstall-service.ps1"))) {
                Ok(()) => pre_steps.push(Step {
                    label: "Прежняя служба снята".into(),
                    ok: true,
                    note: None,
                }),
                Err(e) => {
                    if service_state() == "running" {
                        return Err(format!(
                            "служба Windows «{PRODUCT}» продолжает работать и держит файлы программы ({e}). \
                             Останови её (sc stop {PRODUCT}) и повтори установку."
                        ));
                    }
                    pre_steps.push(Step {
                        label: "Прежняя служба".into(),
                        ok: false,
                        note: Some(e),
                    });
                }
            }
        }
        if !stop_running(&dir) {
            let busy = locked_files(&dir);
            if !busy.is_empty() {
                return Err(format!(
                    "часть программы ещё работает и держит файлы: {}. Закрой окно {PRODUCT_UI} \
                     (полностью, включая значок в трее) и повтори установку.",
                    busy.join(", ")
                ));
            }
        }
    }

    // Порт реле. 5011 записывался константой ВСЕГДА и ни разу не проверялся, а
    // держать его может реле ПРЕЖНЕГО поколения продукта (у автора — vera-relay.exe
    // от LocalSystem). Оболочка в этом случае честно не поднимает своё реле
    // (shell/src/main.rs: harness_alive(port)), но model.base_url всё равно
    // указывал на 5011 — и мозг новой установки уходил в ЧУЖОЕ реле, то есть в
    // чужую подписку и чужую сессию, а владелец видел только «модель не ответила».
    // Проверяем ПОСЛЕ гашения своих процессов (иначе собственное реле переустановки
    // считалось бы препятствием) и ДО копирования: отказ не должен оставлять
    // папку недоделанной.
    let mut relay_port = read_json(&dir.join("helene.json"))
        .and_then(|c| c.get("relay").and_then(|r| r.get("port")).and_then(|v| v.as_u64()))
        .filter(|p| *p > 0 && *p <= u16::MAX as u64)
        .map(|p| p as u16)
        .unwrap_or(RELAY_PORT);
    if s.provider == "chatgpt" && port_busy(relay_port) {
        let holder = port_holder(relay_port);
        let who = if holder.is_empty() { String::new() } else { format!(" ({holder})") };
        match free_relay_port(relay_port) {
            Some(free) => {
                pre_steps.push(Step {
                    label: "Порт реле".into(),
                    ok: true,
                    note: Some(format!(
                        "порт {relay_port} занят другой программой{who} — реле и адрес мозга настроены на {free}"
                    )),
                });
                relay_port = free;
            }
            None => {
                return Err(format!(
                    "порт {relay_port} занят другой программой{who}, и свободного порта рядом ({}–{}) нет. \
                     Пока порт держит чужая программа, {PRODUCT_UI} обращалась бы к ЕЁ реле, а не к своему. \
                     Закрой её (если это служба прежней версии — сними её на предыдущем шаге) и повтори установку.",
                    RELAY_PORT,
                    RELAY_PORT + 19
                ));
            }
        }
    }

    // Шагов пять; служба добавляет шестой — поставить её или, если галку сняли,
    // снять прежнюю. Снятие перед копированием шага не занимает: оно уже позади.
    let total = if s.wants_service() || service_before != "absent" { 6 } else { 5 };
    let mut steps = pre_steps;
    let mut n = 0;
    let mut tick = |label: &str, progress: &mut dyn FnMut(Progress)| {
        n += 1;
        progress(Progress { step: n, total, label: label.to_string() });
    };

    tick("Копирую файлы программы", &mut progress);
    // Что заменяется, а что нет (resources/ОБНОВЛЕНИЕ.md): exe, app/, tree/,
    // server/, документы — всегда; data/ и helene.json — никогда (конфиг
    // сливается ниже); app/static — по тому, менял ли её ВЫПУСК; runtime/ —
    // только если сменился состав. Решения принимаются ДО копирования: паспорт
    // и манифест старой установки копия перепишет.
    let plan = static_plan(&payload, &dir);
    let runtime_kept = runtime_same(&payload, &dir);
    let mut skip_rel: Vec<&str> = Vec::new();
    if plan == StaticPlan::Keep {
        skip_rel.push("app/static");
    }
    if runtime_kept {
        skip_rel.push("runtime");
    }
    if plan == StaticPlan::Replace {
        let prev = dir.join("app").join("static.prev");
        if prev.exists() {
            std::fs::remove_dir_all(&prev).map_err(|e| io_note(&prev, &e))?;
        }
        let current = dir.join("app").join("static");
        std::fs::rename(&current, &prev).map_err(|e| io_note(&current, &e))?;
    }
    // helene.json и data/ поставки не копируем: конфиг пишем свой, данные рождаются здесь.
    let copied = copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &skip_rel)?;
    steps.push(Step { label: "Файлы программы".into(), ok: true, note: Some(format!("{copied} файлов")) });
    steps.push(Step {
        label: "Интерфейс окна".into(),
        ok: true,
        note: Some(match plan {
            StaticPlan::Fresh => "положен из поставки".to_string(),
            StaticPlan::Keep => "выпуск его не менял — оставлен твой (app\\static не тронута)".to_string(),
            StaticPlan::Replace => "обновлён; прежняя версия лежит рядом — app\\static.prev".to_string(),
        }),
    });
    if runtime_kept {
        steps.push(Step { label: "Рантайм".into(), ok: true, note: Some("состав не менялся — не копировался".into()) });
    }

    tick("Записываю настройки и конституцию", &mut progress);
    let cfg_path = dir.join("helene.json");
    let existing = read_json(&cfg_path);
    // Ключ реле переносим: новый случайный ключ на каждом обновлении менял
    // отпечаток блока модели, и boot.project_brain молча отменял выбор мозга,
    // сделанный самим агентом (switch_brain).
    let prev_relay_key = existing
        .as_ref()
        .and_then(|c| c.get("model"))
        .and_then(|m| m.get("key"))
        .and_then(|k| k.as_str())
        .filter(|k| k.starts_with("sk-frame-"))
        .map(|k| k.to_string());
    let mut merged = merge_config(existing.clone(), config_json(s, prev_relay_key, relay_port), s);
    // Окну — знать, что интерфейс обновлён, а прежний отложен рядом: ключ
    // `installed.static_prev` живёт, пока лежит папка `app/static.prev`.
    if let Some(installed) = merged.get_mut("installed").and_then(|v| v.as_object_mut()) {
        if plan == StaticPlan::Replace || dir.join("app").join("static.prev").exists() {
            installed.insert("static_prev".into(), "app/static.prev".into());
        } else {
            installed.remove("static_prev");
        }
    }
    let cfg = serde_json::to_string_pretty(&merged).map_err(|e| e.to_string())?;
    write_atomic(&cfg_path, &(cfg + "\n"))?;
    // Конституция — только если её ещё нет: принятый при установке текст и всё,
    // что владелец и агент правили после, обновление не трогает (тот же договор,
    // что у localharness/boot.py).
    let soul_path = dir.join("data").join("soul").join("SOUL.md");
    let soul_exists = std::fs::read_to_string(&soul_path).map(|t| !t.trim().is_empty()).unwrap_or(false);
    let mut cfg_note = if existing.is_some() {
        Some("настройки обновлены, прежние решения сохранены".to_string())
    } else {
        None
    };
    if soul_exists {
        cfg_note = Some(match cfg_note {
            Some(n) => format!("{n}; конституция оставлена как есть"),
            None => "конституция оставлена как есть".to_string(),
        });
    } else {
        let soul = s.constitution.replace("\r\n", "\n");
        write_atomic(&soul_path, &soul)?;
    }
    steps.push(Step { label: "Настройки и конституция".into(), ok: true, note: cfg_note });
    if s.provider == "chatgpt" {
        match move_relay_auth(&dir) {
            Some(Ok(note)) => steps.push(Step { label: "Подписка ChatGPT".into(), ok: true, note: Some(note) }),
            Some(Err(note)) => steps.push(Step { label: "Подписка ChatGPT".into(), ok: false, note: Some(note) }),
            None => steps.push(Step {
                label: "Подписка ChatGPT".into(),
                ok: false,
                note: Some("вход не выполнен — агент не сможет обратиться к модели, пока не войдёшь в настройках".into()),
            }),
        }
    }

    let exe = dir.join("helene.exe");
    tick("Создаю ярлыки", &mut progress);
    // Ярлыки и значок — продукта, не агента (слово владельца).
    let name = PRODUCT.to_string();
    let icon: Option<PathBuf> = None;
    match shortcuts(&exe, &name, icon.as_deref()) {
        Ok(note) => steps.push(Step { label: "Ярлыки".into(), ok: true, note: Some(note) }),
        Err(err) => steps.push(Step { label: "Ярлыки".into(), ok: false, note: Some(err) }),
    }

    tick("Регистрирую удаление в «Приложениях»", &mut progress);
    match register_uninstall(&dir, dir_size_kb(&dir), icon.as_deref()) {
        Ok(()) => steps.push(Step { label: "Запись об удалении".into(), ok: true, note: None }),
        Err(err) => steps.push(Step { label: "Запись об удалении".into(), ok: false, note: Some(err) }),
    }

    let mut warning: Option<String> = None;
    let service = if s.wants_service() {
        tick("Ставлю службу Windows (появится окно прав администратора)", &mut progress);
        let state = install_service(&dir);
        let note = match state.as_str() {
            "missing" => "в этой поставке нет службы (helene-svc.exe)".to_string(),
            "absent" => "служба не поставлена: права администратора не были даны".to_string(),
            other => other.to_string(),
        };
        // Служба зарегистрирована — значит её предупреждение (СИСТЕМА запускает
        // код из папки, куда пишет пользователь) относится к этой машине, и
        // владелец обязан прочитать его здесь, а не в service.log.
        let note = match service_warning(&dir) {
            Some(warn) if state == "running" || state == "stopped" => {
                warning = Some(warn.clone());
                format!("{note} · {warn}")
            }
            _ => note,
        };
        steps.push(Step { label: "Служба".into(), ok: state == "running", note: Some(note) });
        state
    } else {
        if service_before != "absent" {
            // Галку сняли, а служба осталась бы жить от LocalSystem, и helene.json
            // при этом писал бы service:false — конфиг врал бы о состоянии машины.
            tick("Снимаю прежнюю службу Windows", &mut progress);
            match service_op("uninstall", PRODUCT, Some(&dir.join("uninstall-service.ps1"))) {
                Ok(()) => steps.push(Step { label: "Прежняя служба снята".into(), ok: true, note: None }),
                Err(e) => steps.push(Step {
                    label: "Прежняя служба".into(),
                    ok: false,
                    note: Some(format!("осталась на машине: {e}. Сними вручную: sc stop {PRODUCT} и sc delete {PRODUCT}")),
                }),
            }
        }
        "skipped".to_string()
    };

    tick("Готово", &mut progress);
    Ok(Receipt {
        dir: dir.display().to_string(),
        exe: exe.display().to_string(),
        service,
        warning,
        steps,
    })
}

// ---------------------------------------------------------------- снятие

/// Снятие оставило данные — тогда хвост не должен уносить установщик: без него
/// и без записи в «Приложениях» доснять папку средствами продукта было бы нечем.
static KEEP_SETUP_EXE: AtomicBool = AtomicBool::new(false);

/// Хвост снятия: сам установщик занят, пока работает, поэтому его и папку
/// доудаляет отложенная команда. Зовётся ПОСЛЕ окна с сообщением: пока окно
/// открыто, exe занят, и `del` из-под него не срабатывал.
/// Командную строку cmd отдаём как есть (raw_arg): std::process::Command
/// иначе экранирует кавычки как \", чего cmd не понимает.
pub fn uninstall_finish() {
    let dir = exe_dir();
    let mut parts = vec!["ping 127.0.0.1 -n 3 >nul".to_string()];
    // Профили WebView2, которые создаёт Tauri: без этого от каждого поколения
    // продукта на диске оставались десятки мегабайт, на которые уже ничто не
    // ссылается. Свой собственный профиль установщика убираем только здесь —
    // пока окно живо, он занят.
    for var in ["LOCALAPPDATA", "APPDATA"] {
        if let Some(base) = std::env::var_os(var) {
            for id in [AUMID, SETUP_AUMID] {
                let p = PathBuf::from(&base).join(id);
                parts.push(format!("rmdir /s /q \"{}\"", p.display()));
            }
        }
    }
    if !KEEP_SETUP_EXE.load(Ordering::Relaxed) {
        parts.push(format!("del /q \"{}\"", dir.join("helene-setup.exe").display()));
        parts.push(format!("rmdir \"{}\"", dir.display()));
    }
    let mut cmd = Command::new(sys_exe("cmd.exe"));
    // Рабочая папка хвоста — не наша: из-под неё rmdir не срабатывал, когда
    // визард запускали прямо из папки программы.
    cmd.current_dir(std::env::temp_dir());
    cmd.arg("/C");
    cmd.raw_arg(parts.join(" & "));
    cmd.creation_flags(CREATE_NO_WINDOW);
    let _ = cmd.spawn();
}

/// Порт трубы установленной программы — чтобы снять правило брандмауэра тем же
/// именем, каким его завела оболочка.
fn installed_port(dir: &Path) -> u16 {
    read_json(&dir.join("helene.json"))
        .and_then(|c| c.get("port").and_then(|p| p.as_u64()))
        .unwrap_or(8094) as u16
}

/// Похоже ли на распакованный архив, а не на установленную программу.
fn looks_like_payload(dir: &Path) -> bool {
    if !PAYLOAD_MARKERS.iter().all(|m| dir.join(m).exists()) {
        return false;
    }
    match read_json(&dir.join("helene.json")) {
        Some(cfg) => cfg.get("setup_complete").and_then(|v| v.as_bool()) != Some(true),
        // helene.json нет вовсе — это уже не поставка (в поставке он есть).
        None => false,
    }
}

// ------------------------------------------------- песочница (AppContainer)

/// Префиксы профилей AppContainer, которые заводит этот продукт: имя считается
/// из пути установки (`fence.container_name`), поэтому каждая новая папка и
/// каждое переименование продукта добавляли ещё один профиль — и ни один не
/// удалялся. На машине автора их накопилось двенадцать, включая `vera.shell.*`
/// от версии 0.1.0: профили пережили и снятие продукта, и его переименование.
const FENCE_PREFIXES: [&str; 3] = ["helene.shell.", "vera.shell.", "frame.shell."];

/// Имена профилей продукта, живущие у этого пользователя. Реестр — единственный
/// способ узнать их: `DeriveAppContainerSidFromAppContainerName` считает SID из
/// имени и подстановочных знаков не понимает, так что `--also vera.shell.*`
/// в буквальном виде не сняло бы ничего.
#[cfg(windows)]
fn fence_profiles() -> Vec<String> {
    use winreg::enums::HKEY_CURRENT_USER;
    use winreg::RegKey;
    let Ok(root) = RegKey::predef(HKEY_CURRENT_USER).open_subkey(
        "Software\\Classes\\Local Settings\\Software\\Microsoft\\Windows\\CurrentVersion\\AppContainer\\Mappings",
    ) else {
        return Vec::new();
    };
    let mut out: Vec<String> = Vec::new();
    for sid in root.enum_keys().flatten() {
        let Ok(key) = root.open_subkey(&sid) else { continue };
        let Ok(moniker) = key.get_value::<String, _>("Moniker") else { continue };
        let m = moniker.trim().to_lowercase();
        if FENCE_PREFIXES.iter().any(|p| m.starts_with(p)) && !out.contains(&m) {
            out.push(m);
        }
    }
    out.sort();
    // Потолок на всякий случай: каждое имя — это проход icacls по дереву.
    out.truncate(32);
    out
}

#[cfg(not(windows))]
fn fence_profiles() -> Vec<String> {
    Vec::new()
}

/// Снять права песочницы и удалить её профили — ДО удаления файлов, пока рядом
/// ещё лежат `runtime/python.exe` и `app/localharness/fence.py`.
///
/// `fence.revoke()` написан и проверен инженером харнесса, но снятие продукта
/// его не звало ни разу: профили копились, а ACE мёртвых контейнеров оставались
/// на папке `data`, которую снятие оставляет владельцу. Заодно передаём имена
/// профилей прежних поколений (`vera.shell.*`) — они уже накоплены.
/// Папку `data` обходит сам `fence.revoke` (root и root/data), поэтому
/// отдельного прохода для `--purge` не нужно.
fn fence_revoke(dir: &Path) -> Option<Result<String, String>> {
    let python = dir.join("runtime").join("python.exe");
    let script = dir.join("app").join("localharness").join("fence.py");
    if !python.exists() || !script.exists() {
        return None;
    }
    // Свой контейнер: имя считается из ПУТИ УСТАНОВКИ, поэтому корень передаём
    // как есть. Здесь же снимаются ACE с папки data — той единственной, что
    // переживает снятие, если владелец выбрал «оставить данные».
    let own = fence_call(&python, &script, dir, &[]);
    // Профили прежних поколений — отдельным вызовом и по МАЛЕНЬКОЙ папке.
    // `fence.revoke` для каждого имени гонит icacls /T по всему дереву, а в
    // установленной программе это 12 тысяч файлов: живой замер — 18 секунд на
    // один проход, то есть пятнадцать имён превратили бы снятие в пять минут
    // немого ожидания. Чистить корень их именами и не нужно: он всё равно
    // удаляется через секунду, а на переживающей папке data проход дешёвый.
    let also = fence_profiles();
    let legacy = if also.is_empty() {
        None
    } else {
        Some(fence_call(&python, &script, &dir.join("data"), &also))
    };
    let mut removed = 0usize;
    let mut trouble: Vec<String> = Vec::new();
    for r in [Some(own), legacy].into_iter().flatten() {
        match r {
            Ok(n) => removed += n,
            Err(e) => trouble.push(e),
        }
    }
    // Оба вызова спотыкаются об одно и то же (старый fence.py, нет питона) —
    // повторять владельцу одну причину дважды незачем.
    trouble.dedup();
    if !trouble.is_empty() {
        return Some(Err(format!("песочница: {}", trouble.join("; "))));
    }
    Some(Ok(format!(
        "профилей AppContainer снято: {removed} (искали {})",
        also.len() + 1
    )))
}

/// Один вызов `fence.py --revoke <root> [--also …]`. -> сколько профилей снято.
fn fence_call(python: &Path, script: &Path, root: &Path, also: &[String]) -> Result<usize, String> {
    let mut cmd = Command::new(python);
    cmd.arg(script).arg("--revoke").arg(root);
    if !also.is_empty() {
        cmd.arg("--also");
        for name in also {
            cmd.arg(name);
        }
    }
    let out = run_hidden(&mut cmd).map_err(|e| format!("не удалось позвать fence.py: {e}"))?;
    let text = console_text(&out.stdout);
    if !out.status.success() {
        let err = console_text(&out.stderr);
        return Err(format!(
            "fence.py вернул ошибку: {}",
            err.trim().lines().last().unwrap_or("").chars().take(200).collect::<String>()
        ));
    }
    // fence.py печатает по строке на профиль; «удалён» — успех, hr=0x… — нет.
    // Молчание при нулевом коде — это НЕ успех: у поставки, собранной до того,
    // как `revoke` появился, в fence.py нет блока `__main__` вовсе, и такой
    // вызов тихо ничего не делает. Считать это снятием значило бы врать.
    if !text.contains("профиль") {
        return Err(
            "fence.py этой поставки не понимает --revoke (собран до того, как снятие появилось) — профили AppContainer остались"
                .into(),
        );
    }
    Ok(text.matches("удалён").count())
}

/// Удаление: программа, ярлыки, запись — данные остаются, если не попросили иначе.
pub fn uninstall(purge: bool) -> Result<String, String> {
    let dir = exe_dir();
    // `--uninstall` работал по папке, где лежит exe, без единой проверки: команда
    // из README, выполненная в распакованном архиве, уничтожала архив и при этом
    // сносила запись, ярлыки и службу НАСТОЯЩЕЙ установки.
    if let Some(registered) = registered_dir() {
        if norm_path(&registered) != norm_path(&dir) {
            return Err(format!(
                "это не папка установки: {PRODUCT_UI} установлена в {}. Сними её оттуда \
                 (или через «Приложения» Windows) — здесь удалять нечего.",
                registered.display()
            ));
        }
    } else if looks_like_payload(&dir) {
        return Err(format!(
            "это папка поставки (распакованный архив), а не установленная программа: {}. \
             Здесь нечего снимать — просто удали папку.",
            dir.display()
        ));
    }

    let port = installed_port(&dir);
    let mut problems: Vec<String> = Vec::new();
    let mut service_left = false;

    if service_state() != "absent" {
        let script = dir.join("uninstall-service.ps1");
        let script = if script.exists() { Some(script) } else { None };
        if let Err(e) = service_op("uninstall", PRODUCT, script.as_deref()) {
            problems.push(format!("служба Windows не снялась: {e}"));
        }
        // Верим SCM, а не коду возврата: живая служба LocalSystem с автозапуском
        // — та самая мина, из-за которой следующая установка получала чужого агента.
        if service_state() != "absent" {
            service_left = true;
            if !problems.iter().any(|p| p.starts_with("служба")) {
                problems.push("служба Windows осталась зарегистрированной".into());
            }
        }
    }

    if !stop_running(&dir) && !locked_files(&dir).is_empty() {
        problems.push(format!("часть программы ещё работает: {}", locked_files(&dir).join(", ")));
    }

    // Песочница — ДО удаления файлов: fence.py и рантайм, которым его звать,
    // лежат в этой же папке и через минуту их не станет.
    let fence = fence_revoke(&dir);
    match &fence {
        Some(Err(e)) => problems.push(e.clone()),
        None => problems.push(
            "песочница: рантайма рядом нет — профили AppContainer не сняты (сними их вручную: \
             runtime\\python.exe app\\localharness\\fence.py --revoke <папка>)"
                .into(),
        ),
        Some(Ok(_)) => {}
    }

    #[cfg(windows)]
    {
        use winreg::enums::HKEY_CURRENT_USER;
        use winreg::RegKey;
        let hkcu = RegKey::predef(HKEY_CURRENT_USER);
        let _ = hkcu.delete_subkey_all(format!(
            "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{PRODUCT}"
        ));
        let _ = hkcu.delete_subkey_all(format!("Software\\Classes\\AppUserModelId\\{AUMID}"));
    }
    // Ярлыки: с именем продукта и с именем агента (после установки они его).
    let mut names = vec![PRODUCT.to_string()];
    if let Some(agent) = installed_agent_name(&dir) {
        names.push(agent);
    }
    let desktop = desktop_dir();
    for name in &names {
        if let Some(appdata) = std::env::var_os("APPDATA") {
            let programs = PathBuf::from(&appdata).join("Microsoft\\Windows\\Start Menu\\Programs");
            let _ = std::fs::remove_file(programs.join(format!("{name}.lnk")));
            // Автозапуск ставит сама программа (shell: Startup\Helene.lnk) —
            // без этого Windows при каждом входе пыталась бы запустить удалённый exe.
            let _ = std::fs::remove_file(programs.join("Startup").join(format!("{name}.lnk")));
        }
        // Ярлык рабочего стола удаляем по ТОЙ ЖЕ известной папке, по которой
        // создавали: при переносе папок в OneDrive %USERPROFILE%\Desktop — не она.
        if let Some(d) = &desktop {
            let _ = std::fs::remove_file(d.join(format!("{name}.lnk")));
        }
        if let Some(profile) = std::env::var_os("USERPROFILE") {
            let _ = std::fs::remove_file(PathBuf::from(profile).join("Desktop").join(format!("{name}.lnk")));
        }
    }
    // Правило брандмауэра заводит сама программа («Открыть порт телефону») и
    // никогда не убирала: разрешающее входящее правило на путь внутри удалённой
    // папки оставалось навсегда.
    let mut fw = Command::new(sys_exe("netsh.exe"));
    fw.args([
        "advfirewall", "firewall", "delete", "rule",
        &format!("name={}", firewall_rule::title(PRODUCT, port)),
    ]);
    let _ = run_hidden(&mut fw);

    // Учётные данные ChatGPT во временной папке установщика — тоже наши.
    relay_cleanup();

    // Файлы программы: всё, кроме data/ и самого установщика (он занят) — их
    // доудалит отложенная команда после выхода. Ошибки удаления собираем: раньше
    // они выбрасывались, и расписка была положительной всегда.
    let mut left: Vec<String> = Vec::new();
    for entry in std::fs::read_dir(&dir).map_err(|e| io_note(&dir, &e))? {
        let entry = entry.map_err(|e| e.to_string())?;
        let name = entry.file_name();
        if name == "data" && !purge {
            continue;
        }
        if name == "helene-setup.exe" {
            continue;
        }
        // Служба осталась жива — её exe и скрипт снятия единственное, чем её
        // потом можно убрать. Удалить их значило бы отрезать последний способ.
        if service_left && (name == "helene-svc.exe" || name == "uninstall-service.ps1") {
            continue;
        }
        let path = entry.path();
        let res = if path.is_dir() { std::fs::remove_dir_all(&path) } else { std::fs::remove_file(&path) };
        if res.is_err() {
            left.push(name.to_string_lossy().into_owned());
        }
    }
    if !left.is_empty() {
        problems.push(format!("не удалось удалить: {}", left.join(", ")));
    }

    let data = dir.join("data");
    KEEP_SETUP_EXE.store(!purge, Ordering::Relaxed);

    let mut text = if purge {
        if data.exists() {
            format!("{PRODUCT_UI} удалена, но папка данных осталась: {}", data.display())
        } else {
            format!("{PRODUCT_UI} удалена вместе с данными")
        }
    } else {
        // Владелец читает «данные агента» как память и записи. На деле там же
        // лежат ключ модели, вход в ChatGPT и сессия его Telegram-аккаунта.
        format!(
            "{PRODUCT_UI} удалена. Данные агента остались в {} — там же ключ модели, вход в ChatGPT \
             и сессия Telegram. Удали эту папку, если отдаёшь компьютер. Установщик оставлен рядом: \
             им можно доснять данные позже.",
            data.display()
        )
    };
    if service_left {
        text.push_str(&format!(
            " ВНИМАНИЕ: служба Windows «{PRODUCT}» осталась на машине и работает от имени системы. \
             Сними её вручную: sc stop {PRODUCT} и sc delete {PRODUCT} из командной строки администратора."
        ));
    }
    if !problems.is_empty() {
        text.push_str(&format!(" Не всё получилось: {}.", problems.join("; ")));
    }
    if !purge {
        let _ = std::fs::write(
            dir.join("КАК-ВЕРНУТЬСЯ.md"),
            format!(
                "# Что здесь осталось\n\n{PRODUCT_UI} снята, но папка `data` оставлена по твоему выбору.\n\n\
                 В ней лежат:\n\n- память агента и его конституция (`memory/`, `soul/`);\n\
                 - **ключ модели** (`memory/llm.json`);\n\
                 - **вход в аккаунт ChatGPT** (`relay/local_auth/auth.json`), если он был;\n\
                 - **сессия твоего Telegram-аккаунта** (`telegram/`), если он был подключён.\n\n\
                 Это секреты. Если отдаёшь или продаёшь компьютер — удали папку целиком.\n\n\
                 ## Вернуться\n\nПоставь {PRODUCT_UI} в эту же папку ({}) — агент подхватит свою память.\n\n\
                 Но настройки программы (`helene.json`) сняты вместе с ней, и визард восстановит не всё. \
                 Вход в Telegram твоим собственным аккаунтом (MTProto: `api_id`, `api_hash`, номер) он не \
                 знает вовсе: сессия в `telegram/` цела, а войти по ней будет нечем, пока эти поля не введёшь \
                 заново в окне, в «Настройки → Telegram». Там же вернутся телефон-компаньон, песочница и \
                 адрес обновлений.\n\n\
                 ## Доснять\n\nЗапусти рядом `helene-setup.exe --uninstall --purge --quiet` или просто удали эту папку.\n",
                dir.display()
            ),
        );
    }
    Ok(text)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Предупреждение службы едет к владельцу файлом: её `println!` уходит в
    /// скрытое поднятое окно. Читаем то, что она положила, — и молчим, когда
    /// файла нет или он пуст (служба в защищённой папке предупреждать не о чем).
    #[test]
    fn service_warning_is_read_from_file() {
        let dir = std::env::temp_dir().join("helene-test-service-warning");
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(service_warning(&dir), None);
        std::fs::write(dir.join("service-warning.txt"), "  
").unwrap();
        assert_eq!(service_warning(&dir), None);
        std::fs::write(dir.join("service-warning.txt"), "ВНИМАНИЕ: СИСТЕМА из папки пользователя
").unwrap();
        assert_eq!(
            service_warning(&dir).as_deref(),
            Some("ВНИМАНИЕ: СИСТЕМА из папки пользователя")
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn setup_for(provider: &str) -> Setup {
        Setup {
            agent: "Вера".into(),
            owner: "Егор".into(),
            constitution: "Текст".into(),
            accepted: true,
            provider: provider.into(),
            chatgpt_model: "gpt-5.6-sol".into(),
            reasoning_effort: String::new(),
            api: Endpoint { base_url: "https://api.openai.com/v1".into(), model: "gpt-5.4".into(), key: "sk-1".into() },
            anthropic: default_anthropic(),
            local: LocalEndpoint { base_url: "http://127.0.0.1:11434/v1".into(), model: "qwen3".into() },
            telegram: Telegram { bot_token: String::new(), owner_id: String::new() },
            agent_mode: "sandbox".into(),
            service: false,
            session0: false,
            firewall: default_firewall(),
            computer: false,
            dir: String::new(),
        }
    }

    /// Тело руки `computer`: выключено по умолчанию, блок в конфиге есть
    /// всегда (с портом и всеми четырьмя правами), включение — только словом
    /// визарда; переустановка не стирает ни сужённые права, ни порт владельца.
    #[test]
    fn computer_block_is_written_and_merged() {
        let mut s = setup_for("api");
        let cfg = config_json(&s, None, RELAY_PORT);
        assert_eq!(cfg["computer"]["enabled"], false);
        assert_eq!(cfg["computer"]["port"], COMPUTER_PORT);
        assert_eq!(cfg["computer"]["scopes"].as_array().map(|a| a.len()), Some(4));
        s.computer = true;
        assert_eq!(config_json(&s, None, RELAY_PORT)["computer"]["enabled"], true);

        let old = serde_json::json!({
            "computer": { "enabled": true, "port": 9499, "scopes": ["computer.read"] },
        });
        let s = setup_for("api");
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["computer"]["enabled"], false, "выключатель — слово визарда");
        assert_eq!(out["computer"]["port"], 9499, "порт владельца не переписан");
        assert_eq!(out["computer"]["scopes"], serde_json::json!(["computer.read"]), "сужённые права уцелели");

        // Конфиг прошлой установки без блока вовсе — блок доставляется целиком.
        let out = merge_config(Some(serde_json::json!({ "port": 8094 })), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["computer"]["port"], COMPUTER_PORT);
        assert_eq!(out["computer"]["scopes"].as_array().map(|a| a.len()), Some(4));
        // Старый визард поля `computer` не шлёт — serde подставляет false.
        let raw: Setup = serde_json::from_value(serde_json::json!({
            "agent": "А", "owner": "Б", "constitution": "В", "accepted": true, "provider": "api",
            "api": { "base_url": "u", "model": "m", "key": "k" },
            "local": { "base_url": "", "model": "" },
            "telegram": { "bot_token": "", "owner_id": "" },
            "service": false, "dir": "",
        })).unwrap();
        assert!(!raw.computer);
    }

    /// Реле объявляет /chat/completions, а не /v1/chat/completions: адрес с /v1
    /// давал 404 на каждый вызов модели.
    #[test]
    fn relay_base_url_has_no_v1() {
        let cfg = config_json(&setup_for("chatgpt"), None, RELAY_PORT);
        assert_eq!(cfg["model"]["base_url"], "http://127.0.0.1:5011");
    }

    /// Ядро без ключа не создаёт клиента вовсе — локальной модели нужна заглушка.
    #[test]
    fn local_provider_gets_key_stub() {
        let cfg = config_json(&setup_for("local"), None, RELAY_PORT);
        assert_eq!(cfg["model"]["key"], "local");
    }

    #[test]
    fn relay_key_is_reused() {
        let cfg = config_json(&setup_for("chatgpt"), Some("sk-frame-old".into()), RELAY_PORT);
        assert_eq!(cfg["model"]["key"], "sk-frame-old");
    }

    /// Обновление не должно стирать то, чего визард не знает.
    #[test]
    fn merge_keeps_owner_settings() {
        let old = serde_json::json!({
            "sandbox": { "enabled": true },
            "phone": { "enabled": true },
            "env": { "TZ": "Europe/Samara" },
            "max_tool_iters": 40,
            "port": 9000,
            "telegram": { "bot_token": "111:AAA", "owner_id": 7, "mode": "mtproto", "api_id": 12345 },
            "agent": { "name": "Старая" },
        });
        let s = setup_for("api");
        let fresh = config_json(&s, None, RELAY_PORT);
        let out = merge_config(Some(old), fresh, &s);
        assert_eq!(out["sandbox"]["enabled"], true);
        assert_eq!(out["phone"]["enabled"], true);
        assert_eq!(out["env"]["TZ"], "Europe/Samara");
        assert_eq!(out["max_tool_iters"], 40);
        assert_eq!(out["port"], 9000);
        assert_eq!(out["telegram"]["mode"], "mtproto");
        assert_eq!(out["telegram"]["api_id"], 12345);
        // Пустое поле визарда не стирает уже работающего бота.
        assert_eq!(out["telegram"]["bot_token"], "111:AAA");
        assert_eq!(out["telegram"]["owner_id"], 7);
        // А своё визард переписывает.
        assert_eq!(out["agent"]["name"], "Вера");
        assert_eq!(out["setup_complete"], true);
    }

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("helene-{tag}-{}-{}", std::process::id(), random_hex(4).unwrap_or_default()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    fn put(root: &Path, rel: &str, text: &str) {
        let path = root.join(rel);
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, text).unwrap();
    }

    fn payload_with(root: &Path, digest: &str, build: &str) {
        put(root, "helene.exe", "exe");
        put(root, "app/static/index.html", "<html>new</html>");
        put(root, "app/static/assets/a.js", "new js");
        put(root, &format!("app/static/{STATIC_MANIFEST}"), &format!("{{\"v\":1,\"digest\":\"{digest}\",\"files\":{{}}}}"));
        put(root, "app/deskapp.py", "py");
        put(root, "runtime/python.exe", "py-exe");
        put(root, "helene-build.json", build);
    }

    const BUILD_A: &str = r#"{"python":"3.14.5","downloads":{"x":"1"},"packages":["aiohttp==3.14.3"]}"#;
    const BUILD_B: &str = r#"{"python":"3.14.5","downloads":{"x":"1"},"packages":["aiohttp==3.14.4"]}"#;

    /// Статика окна при обновлении поверх — три случая (задача A §2): выпуск её
    /// не менял, а пользователь правил — его файлы целы; выпуск менял —
    /// заменена, прежняя в `static.prev`; первая установка — положена с
    /// манифестом. Решает манифест поставки, а не содержимое папки пользователя.
    #[test]
    fn static_follows_the_release_not_the_user() {
        let payload = temp_dir("payload");
        let dir = temp_dir("install");
        payload_with(&payload, "d1", BUILD_A);
        // 1. Первая установка.
        assert_eq!(static_plan(&payload, &dir), StaticPlan::Fresh);
        copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &[]).unwrap();
        assert!(dir.join("app/static").join(STATIC_MANIFEST).exists(), "манифест уехал в установку");
        // 2. Пользователь правил статику; выпуск её не менял (тот же отпечаток).
        put(&dir, "app/static/index.html", "<html>мой</html>");
        put(&dir, "app/static/custom.css", "body{}");
        let plan = static_plan(&payload, &dir);
        assert_eq!(plan, StaticPlan::Keep);
        copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &["app/static"]).unwrap();
        assert_eq!(std::fs::read_to_string(dir.join("app/static/index.html")).unwrap(), "<html>мой</html>");
        assert!(dir.join("app/static/custom.css").exists());
        assert!(!dir.join("app/static.prev").exists());
        // 3. Выпуск статику менял (другой отпечаток) — заменена, прежняя рядом.
        payload_with(&payload, "d2", BUILD_A);
        put(&payload, "app/static/index.html", "<html>v2</html>");
        assert_eq!(static_plan(&payload, &dir), StaticPlan::Replace);
        std::fs::rename(dir.join("app/static"), dir.join("app/static.prev")).unwrap();
        copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &[]).unwrap();
        assert_eq!(std::fs::read_to_string(dir.join("app/static/index.html")).unwrap(), "<html>v2</html>");
        assert_eq!(std::fs::read_to_string(dir.join("app/static.prev/index.html")).unwrap(), "<html>мой</html>");
        assert!(dir.join("app/static.prev/custom.css").exists(), "правки пользователя не пропали");
        assert!(!dir.join("app/static/custom.css").exists(), "новая статика — чистая");
        // Поставка без манифеста сравнить себя не может — заменяет.
        std::fs::remove_file(payload.join("app/static").join(STATIC_MANIFEST)).unwrap();
        assert_eq!(static_plan(&payload, &dir), StaticPlan::Replace);
        let _ = std::fs::remove_dir_all(&payload);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Рантайм не копируется при равном паспорте — и копируется, когда состав
    /// пакетов другой или установленного рантайма нет.
    #[test]
    fn runtime_is_skipped_only_when_the_passport_matches() {
        let payload = temp_dir("payload-rt");
        let dir = temp_dir("install-rt");
        payload_with(&payload, "d1", BUILD_A);
        assert!(!runtime_same(&payload, &dir), "рантайма ещё нет — копировать");
        copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &[]).unwrap();
        assert!(runtime_same(&payload, &dir));
        // Тот же паспорт: runtime пропускается, остальное едет.
        put(&payload, "runtime/python.exe", "py-exe-2");
        put(&payload, "app/deskapp.py", "py-2");
        copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &["runtime"]).unwrap();
        assert_eq!(std::fs::read_to_string(dir.join("runtime/python.exe")).unwrap(), "py-exe");
        assert_eq!(std::fs::read_to_string(dir.join("app/deskapp.py")).unwrap(), "py-2");
        // Другой состав пакетов — копировать.
        put(&payload, "helene-build.json", BUILD_B);
        assert!(!runtime_same(&payload, &dir));
        let _ = std::fs::remove_dir_all(&payload);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Константы установщика — те же, что в `ui-kit/contract.json` (одно
    /// место для трёх языков; задача A п. 1.12).
    #[test]
    fn contract_json_matches_constants() {
        let c: serde_json::Value = serde_json::from_str(include_str!("../../ui-kit/contract.json")).unwrap();
        assert_eq!(c["ports"]["desk"], DESK_PORT);
        assert_eq!(c["ports"]["relay"], RELAY_PORT);
        assert_eq!(c["ports"]["body"], COMPUTER_PORT);
        assert_eq!(c["computer_scopes"], serde_json::json!(COMPUTER_SCOPES));
        assert_eq!(config_json(&setup_for("api"), None, RELAY_PORT)["port"], c["ports"]["desk"]);
    }

    /// `model` сливается по полям: ключи всех провайдеров и модель свёрток,
    /// заведённые окном, переживают переустановку; своё визард переписывает.
    #[test]
    fn merge_keeps_model_extras() {
        let old = serde_json::json!({
            "model": {
                "framework": "anthropic", "base_url": "https://api.z.ai/api/anthropic",
                "model": "glm-5.3", "key": "zai-1",
                "keys": { "api": "sk-1", "anthropic": "zai-1", "chatgpt": "sk-frame-x" },
                "compact_model": "glm-4.5-flash", "max_tokens": 4096,
            },
            "evaluator": { "model": "glm-4.5-flash" },
        });
        let s = setup_for("api");
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["model"]["framework"], "openai", "провайдер — слово визарда");
        assert_eq!(out["model"]["base_url"], "https://api.openai.com/v1");
        assert_eq!(out["model"]["model"], "gpt-5.4");
        assert_eq!(out["model"]["key"], "sk-1");
        assert_eq!(out["model"]["max_tokens"], 8192, "потолок — визарда");
        assert_eq!(out["model"]["keys"]["anthropic"], "zai-1", "ключи окна уцелели");
        assert_eq!(out["model"]["keys"]["chatgpt"], "sk-frame-x");
        assert_eq!(out["model"]["compact_model"], "glm-4.5-flash", "модель свёрток уцелела");
        assert_eq!(out["evaluator"]["model"], "glm-4.5-flash");
        // Конфиг без блока model вовсе — блок доставляется целиком.
        let out = merge_config(Some(serde_json::json!({ "port": 8094 })), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["model"]["model"], "gpt-5.4");
    }

    #[test]
    fn merge_drops_relay_when_provider_changed() {
        let old = serde_json::json!({ "relay": { "enabled": true, "port": 5011 } });
        let s = setup_for("api");
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert!(out.get("relay").is_none());
    }

    /// Адрес обновлений: без него окно отказывает всегда, а умолчания не было
    /// ни в конфиге установки, ни в оболочке, ни в настройках.
    #[test]
    fn config_carries_update_url() {
        let cfg = config_json(&setup_for("api"), None, RELAY_PORT);
        assert_eq!(cfg["update"]["url"], UPDATE_URL);
        assert!(UPDATE_URL.starts_with("https://"));
    }

    /// Конфиг прошлой установки мог родиться без блока update — тогда его надо
    /// доставить; а вписанный владельцем адрес обновление трогать не смеет.
    #[test]
    fn update_url_is_added_but_never_overwritten() {
        let s = setup_for("api");
        let old = serde_json::json!({ "agent": { "name": "Старая" } });
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["update"]["url"], UPDATE_URL);

        let mine = serde_json::json!({ "update": { "url": "https://example.org/my.json" } });
        let out = merge_config(Some(mine), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["update"]["url"], "https://example.org/my.json");
    }

    /// Переезд выпусков: наш ПРЕЖНИЙ адрес переносится на новый, чужой — нет.
    /// Без этого установка, обновлённая с прежнего канала, осталась бы смотреть
    /// в архивный репозиторий и перестала бы видеть выпуски молча.
    #[test]
    fn our_old_update_url_migrates_but_a_custom_one_does_not() {
        let s = setup_for("api");
        let old = serde_json::json!({ "update": { "url": UPDATE_URL_WAS, "auto": false } });
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["update"]["url"], UPDATE_URL);
        assert_eq!(out["update"]["auto"], false, "чужие поля блока не теряются");

        let custom = serde_json::json!({ "update": { "url": "https://example.org/my.json" } });
        let out = merge_config(Some(custom), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["update"]["url"], "https://example.org/my.json");
        assert_ne!(UPDATE_URL, UPDATE_URL_WAS, "переезд объявлен, а адреса совпадают");
    }

    /// Ограда уезжает в СВОЙ ключ. `mode` в helene.json занят под
    /// местожительство харнесса: «sandbox» там — окно, которое не поднимает ни
    /// трубу, ни руннер, и служба, которая отказывается стартовать.
    #[test]
    fn mode_goes_to_agent_mode_and_never_into_mode() {
        for (picked, sandbox) in [("sandbox", true), ("interactive", false)] {
            let mut s = setup_for("api");
            s.agent_mode = picked.into();
            let cfg = config_json(&s, None, RELAY_PORT);
            assert_eq!(cfg["agent_mode"], picked);
            assert_eq!(cfg["mode"], "local", "местожительство харнесса трогать нельзя");
            assert_eq!(cfg["sandbox"]["enabled"], sandbox);
        }
    }

    /// ⚠⚠ P0. Служба — не ограда: она ставится ПОВЕРХ любой из двух и ни одну
    /// не снимает. Пока их держали одним списком, владелец, выбравший службу с
    /// песочницей, получал `sandbox.enabled = false` — ограду снимали молча.
    #[test]
    fn service_never_takes_the_fence_off() {
        for fence in ["sandbox", "interactive"] {
            let mut s = setup_for("api");
            s.agent_mode = fence.into();
            let without = config_json(&s, None, RELAY_PORT);
            s.service = true;
            let with = config_json(&s, None, RELAY_PORT);
            assert_eq!(with["agent_mode"], without["agent_mode"], "{fence}");
            assert_eq!(
                with["sandbox"]["enabled"], without["sandbox"]["enabled"],
                "служба сняла ограду «{fence}» — это тот самый P0"
            );
            assert_eq!(with["installed"]["service"], true);
            assert_eq!(without["installed"]["service"], false);
        }
    }

    /// Визард первой волны слал службу третьим значением `agent_mode`. Службу
    /// оттуда читаем (иначе выбор владельца пропал бы), а оградой её не
    /// считаем: оградой становится песочница — умолчание визарда и более узкие
    /// права из двух.
    #[test]
    fn legacy_service_mode_is_a_service_not_a_fence() {
        let mut s = setup_for("api");
        s.agent_mode = "service".into();
        assert!(s.wants_service());
        assert_eq!(s.mode(), "sandbox");
        let cfg = config_json(&s, None, RELAY_PORT);
        assert_eq!(cfg["agent_mode"], "sandbox");
        assert_eq!(cfg["sandbox"]["enabled"], true);
        assert_eq!(cfg["installed"]["service"], true);
    }

    /// Пустой ключ — визард старого выпуска, ограду он не присылал вовсе.
    /// Молча расширять права нельзя: умолчание — песочница.
    #[test]
    fn missing_fence_defaults_to_sandbox() {
        let mut s = setup_for("api");
        s.agent_mode = String::new();
        assert_eq!(s.mode(), "sandbox");
        assert!(!s.wants_service());
        s.service = true;
        assert_eq!(s.mode(), "sandbox", "служба ограду не выбирает");
        assert!(s.wants_service());
    }

    /// Нулевая сессия живёт в `service.session0` (там её читает служба) и
    /// действует только вместе со службой: без неё исполнять некому.
    #[test]
    fn session0_only_with_the_service() {
        let mut s = setup_for("api");
        s.session0 = true;
        for fence in ["sandbox", "interactive"] {
            s.agent_mode = fence.into();
            s.service = false;
            assert_eq!(config_json(&s, None, RELAY_PORT)["service"]["session0"], false);
            s.service = true;
            assert_eq!(config_json(&s, None, RELAY_PORT)["service"]["session0"], true);
        }
    }

    /// Правило брандмауэра — своя галочка, не нулевая сессия. Одним ключом их
    /// держали до 04.09, и кнопка «Телефон» под службой была мертва, пока
    /// владелец не отдаст агенту права системы.
    #[test]
    fn firewall_is_its_own_key_and_defaults_to_yes() {
        let mut s = setup_for("api");
        s.service = true;
        let cfg = config_json(&s, None, RELAY_PORT);
        assert_eq!(cfg["service"]["firewall"], true);
        assert_eq!(cfg["service"]["session0"], false);
        s.firewall = false;
        assert_eq!(config_json(&s, None, RELAY_PORT)["service"]["firewall"], false);
    }

    /// Переустановка поверх: ограду визард переписывает (иначе выбор остался бы
    /// на бумаге), а соседей в тех же блоках — нет.
    #[test]
    fn merge_rewrites_mode_but_keeps_block_neighbours() {
        let old = serde_json::json!({
            "agent_mode": "service",
            "sandbox": { "enabled": false, "network": false },
            "service": { "session0": true, "broker": false, "firewall": false },
        });
        let mut s = setup_for("api");
        s.agent_mode = "sandbox".into();
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["agent_mode"], "sandbox");
        assert_eq!(out["sandbox"]["enabled"], true);
        // Сеть контейнера, выключатель брокера и правило брандмауэра — решения
        // владельца ПОСЛЕ установки: переписать блок целиком значило бы молча
        // их отменить. Экрана у брандмауэра в визарде нет вовсе.
        assert_eq!(out["sandbox"]["network"], false);
        assert_eq!(out["service"]["broker"], false);
        assert_eq!(out["service"]["firewall"], false);
        assert_eq!(out["service"]["session0"], false);
    }

    /// Тот же P0 на переустановке. В файле лежит наследие первой волны:
    /// `agent_mode: "service"` при живой ограде. Визард приходит со службой —
    /// и ограда обязана остаться на месте.
    #[test]
    fn merge_keeps_the_fence_when_the_service_stays() {
        let old = serde_json::json!({
            "agent_mode": "service",
            "sandbox": { "enabled": true, "network": true },
            "service": { "session0": false },
        });
        let mut s = setup_for("api");
        s.agent_mode = "sandbox".into();
        s.service = true;
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["agent_mode"], "sandbox");
        assert_eq!(out["sandbox"]["enabled"], true, "ограда снялась молча — это P0");
        assert_eq!(out["installed"]["service"], true);
    }

    /// Ключ брандмауэра ДОСТАВЛЯЕТСЯ в старый конфиг, где его не было вовсе:
    /// без него служба читает своё умолчание, но владелец, открывший файл,
    /// не видит ручки.
    #[test]
    fn merge_delivers_firewall_when_absent() {
        let old = serde_json::json!({ "service": { "broker": true } });
        let mut s = setup_for("api");
        s.service = true;
        let out = merge_config(Some(old), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["service"]["firewall"], true);
        assert_eq!(out["service"]["broker"], true);
    }

    /// Порт реле пишется В ДВА места, и они не должны разъезжаться: оболочка
    /// поднимает реле по relay.port, а мозг стучится по model.base_url.
    #[test]
    fn relay_port_is_written_consistently() {
        let cfg = config_json(&setup_for("chatgpt"), None, 5023);
        assert_eq!(cfg["relay"]["port"], 5023);
        assert_eq!(cfg["model"]["base_url"], "http://127.0.0.1:5023");
    }

    /// Снимать по имени можно только известные имена продукта: `remove_service`
    /// зовут из окна, и произвольное имя службы туда попадать не должно.
    /// ImagePath со стола автора и наш собственный: exe надо выделить точно,
    /// иначе «своя это служба или прежнего поколения» решается наугад.
    #[test]
    fn image_exe_survives_arguments_and_spaces() {
        assert_eq!(
            image_exe(r"C:\Users\yegor\AppData\Local\Programs\Vera\vera-svc.exe run --config \\?\C:\x\vera.json"),
            r"C:\Users\yegor\AppData\Local\Programs\Vera\vera-svc.exe"
        );
        assert_eq!(
            image_exe("\"C:\\Program Files\\Hélène\\helene-svc.exe\" run"),
            "C:\\Program Files\\Hélène\\helene-svc.exe"
        );
        assert_eq!(image_exe(r"C:\x\helene-svc.EXE"), r"C:\x\helene-svc.EXE");
    }

    /// Настоящий ответ PowerShell со стола автора: ОДНА служба — значит объект,
    /// а не массив. Парсер, ждущий массив, промолчал бы именно там, где надо
    /// говорить. Плюс отделение своей службы от чужой по папке.
    #[test]
    fn parses_single_object_and_array() {
        let one = r#"{"Name":"Vera","State":"Running","StartMode":"Auto","StartName":"LocalSystem","PathName":"C:\\Users\\yegor\\AppData\\Local\\Programs\\Vera\\vera-svc.exe run --config \\\\?\\C:\\x\\vera.json"}"#;
        let got = parse_services(one, None);
        assert_eq!(got.len(), 1);
        assert_eq!(got[0].name, "Vera");
        assert_eq!(got[0].state, "Running");
        assert_eq!(got[0].account, "LocalSystem");
        assert!(!got[0].ours);

        // Та же служба, но папка установки — её собственная: это «наша», её
        // снимает сам установщик, второй раз спрашивать согласие незачем.
        let home = PathBuf::from(r"C:\Users\yegor\AppData\Local\Programs\Vera");
        assert!(parse_services(one, Some(&home))[0].ours);

        let two = r#"[{"Name":"Vera","State":"Running","StartMode":"Auto","StartName":"LocalSystem","PathName":"C:\\a\\vera-svc.exe"},
                      {"Name":"Frame","State":"Stopped","StartMode":"Manual","StartName":"LocalSystem","PathName":"C:\\b\\frame-svc.exe"}]"#;
        assert_eq!(parse_services(two, None).len(), 2);

        // Пусто и мусор — ноль служб, а не паника.
        assert!(parse_services("", None).is_empty());
        assert!(parse_services("не json", None).is_empty());
    }

    #[test]
    fn remove_service_refuses_unknown_names() {
        assert!(remove_service("Spooler").is_err());
        assert!(KNOWN_SERVICE_NAMES.contains(&"Vera"));
        assert!(KNOWN_SERVICE_NAMES.contains(&"Frame"));
        assert!(KNOWN_SERVICE_NAMES.contains(&PRODUCT));
    }

    /// Текст ошибок PowerShell на русской системе приходит в CP866.
    #[test]
    fn console_text_decodes_cp866() {
        let bytes = [0xe0, 0xef, 0xa4, 0xae, 0xac, 0x20, 0xad, 0xa5, 0xe2];
        assert_eq!(console_text(&bytes), "рядом нет");
        assert_eq!(console_text("plain ascii".as_bytes()), "plain ascii");
    }

    #[test]
    fn inside_or_same_catches_nesting() {
        assert!(inside_or_same(r"c:\a\b", r"c:\a"));
        assert!(inside_or_same(r"c:\a", r"c:\a"));
        assert!(!inside_or_same(r"c:\ab", r"c:\a"));
        assert!(!inside_or_same(r"c:\a", r"c:\a\b"));
    }

    #[test]
    fn validate_rejects_empty_and_template() {
        let mut s = setup_for("api");
        s.agent = "  ".into();
        assert!(validate_setup(&s).is_err());
        let mut s = setup_for("api");
        s.constitution = "Меня зовут {{agent}}".into();
        assert!(validate_setup(&s).is_err());
        let mut s = setup_for("anthropic");
        s.anthropic.key = String::new();
        assert!(validate_setup(&s).is_err());
    }

}
