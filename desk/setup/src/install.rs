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
//!
//! macOS (порт 0.7.1, ветка port/macos). Та же поставка, но без службы, тела
//! (`computer`), брандмауэра, ярлыков и реестра — всего, чего на Mac нет по
//! построению. Раскладка: `~/Applications/Helene/` — корень; в нём
//! `Helene.app` (оболочка), `Helene Setup.app` (этот установщик),
//! `helene-relay`, `runtime/bin/python3`, `app/`, `tree/`, `data/`,
//! `helene.json`, `helene-build.json`. Установщик лежит ВНУТРИ бандла, поэтому
//! корень поставки ищется вверх по родителям до первой папки с паспортом
//! сборки (`exe_dir`), а не «рядом с exe». Всё платформенное — под
//! `#[cfg(windows)]` / `#[cfg(not(windows))]`; вторая ветка и есть macOS
//! (другие Unix продукт не собирает — решение владельца: только Apple Silicon).
//! Ветки Windows не менялись ни строкой.
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
// PowerShell, служба и кодовые страницы консоли — только Windows: на macOS
// их не зовёт никто, и включать их значило бы тащить мёртвый код в бинарь.
include!("../../common/model_probe.rs");
#[cfg(windows)]
include!("../../common/ps.rs");
#[cfg(windows)]
include!("../../common/service_op.rs");
// Демон launchd (`app.helene.svc`) на macOS: описание, строки launchctl, разбор
// состояния. Один текст на службу, окно и мастер — см. common/mac_service.rs.
include!("../../common/mac_service.rs");
include!("../../common/random_hex.rs");
include!("../../common/run_hidden.rs");

/// Имя правила брандмауэра — из того же файла, что у оболочки и службы
/// (`common/firewall_rule.rs`): установщик снимает правило при удалении, и
/// до 07.09 собирал имя руками мимо `firewall_rule_title` (ревью 06.09, §4
/// п. 15). Остальное из файла установщику не нужно — отсюда модуль.
#[cfg(windows)]
mod firewall_rule {
    #![allow(dead_code)]
    include!("../../common/firewall_rule.rs");

    pub(super) fn title(product: &str, port: u16) -> String {
        firewall_rule_title(product, port)
    }
}

/// Файлы поставки, по которым мы узнаём её папку.
#[cfg(windows)]
const PAYLOAD_MARKERS: [&str; 3] = ["helene.exe", "app", "runtime"];
/// На macOS оболочка — бандл `Helene.app`, а не exe рядом.
#[cfg(not(windows))]
const PAYLOAD_MARKERS: [&str; 3] = [SHELL_APP, "app", "runtime"];

/// Имена бандлов на macOS. Оболочку запускают через `open`, установщик лежит
/// внутри своего бандла — отсюда и поиск корня вверх (`exe_dir`).
#[cfg(not(windows))]
pub const SHELL_APP: &str = "Helene.app";
#[cfg(not(windows))]
pub const SETUP_APP: &str = "Helene Setup.app";

/// Что в корне установки — сам установщик: при снятии его пропускает цикл
/// удаления (он занят/работает), доудаляет хвост `uninstall_finish`.
#[cfg(windows)]
const SETUP_ENTRY: &str = "helene-setup.exe";
#[cfg(not(windows))]
const SETUP_ENTRY: &str = SETUP_APP;

/// Как позвать установщик из командной строки — для записок владельцу.
#[cfg(windows)]
const SETUP_CMD: &str = "helene-setup.exe";
#[cfg(not(windows))]
const SETUP_CMD: &str = "\"Helene Setup.app/Contents/MacOS/helene-setup\"";

/// Питон рантайма относительно корня — тот же путь уезжает в helene.json
/// ключом `python`, по нему оболочка поднимает канал и движок.
#[cfg(windows)]
pub const PYTHON_REL: &str = "runtime/python.exe";
#[cfg(not(windows))]
pub const PYTHON_REL: &str = "runtime/bin/python3";

/// Штатный движок поставки. Владелец может поставить свой (`keep_own_runner`),
/// и тогда обновление его не трогает, а расписка называет это словами.
const DEFAULT_RUNNER_REL: &str = "app/localharness/runner.py";

/// Реле подписки ChatGPT: бинарь в корне, на Unix без расширения.
#[cfg(windows)]
pub const RELAY_NAME: &str = "helene-relay.exe";
#[cfg(not(windows))]
pub const RELAY_NAME: &str = "helene-relay";

/// Папка статики окна в подписях расписки — разделителем этой системы.
#[cfg(windows)]
const STATIC_REL: &str = "app\\static";
#[cfg(not(windows))]
const STATIC_REL: &str = "app/static";

/// Где живёт значок программы, когда окно закрыто: трей на Windows, строка
/// меню на macOS — в отказе «закрой окно полностью».
#[cfg(windows)]
const TRAY_WORD: &str = "значок в трее";
#[cfg(not(windows))]
const TRAY_WORD: &str = "значок в строке меню";

/// Оболочка установленной программы: `helene.exe` рядом с остальным или бандл
/// `Helene.app`. Путь уезжает в расписку (`Receipt.exe`) и дальше в `open_frame`.
pub fn shell_exe(dir: &Path) -> PathBuf {
    #[cfg(windows)]
    {
        dir.join("helene.exe")
    }
    #[cfg(not(windows))]
    {
        dir.join(SHELL_APP)
    }
}

/// Питон рантайма по корню — см. `PYTHON_REL`.
pub fn python_exe(dir: &Path) -> PathBuf {
    let mut p = dir.to_path_buf();
    for part in PYTHON_REL.split('/') {
        p.push(part);
    }
    p
}

/// Реле по корню поставки или установки — см. `RELAY_NAME`.
pub fn relay_exe(dir: &Path) -> PathBuf {
    dir.join(RELAY_NAME)
}

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
    /// 25.09 (K): обновлять, даже если репетиция показала, что расширение владельца не
    /// загрузится под новой версией (`--force-extensions`). По умолчанию — отказ словами,
    /// старая версия остаётся живой.
    #[serde(default)]
    pub force_extensions: bool,
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
    ///
    /// С 0.8.0 служба есть и на macOS — демон launchd `app.helene.svc`
    /// (`common/mac_service.rs`). Механизмы разные (SCM против launchd), вопрос
    /// владельцу один: жить ли агенту без открытого окна. На прочих POSIX её
    /// нет, и `true` из JSON туда не проходит: в helene.json уезжает
    /// `installed.service: false`, а не обещание.
    pub fn wants_service(&self) -> bool {
        (cfg!(windows) || cfg!(target_os = "macos"))
            && (self.service || self.agent_mode.trim() == "service")
    }

    /// Поднимать ли тело тула `computer`. Есть на Windows (UIA) и на macOS
    /// (Accessibility, порт тела 19.09); на прочих POSIX тела нет, и `true` из
    /// JSON в конфиг не проходит: движок отказал бы словами, а конфиг обещал бы
    /// окна и мышь.
    pub fn wants_computer(&self) -> bool {
        (cfg!(windows) || cfg!(target_os = "macos")) && self.computer
    }

    /// Нулевая сессия действует только вместе со службой: без неё исполнять
    /// некому, а `true` в файле читался бы как разрешение.
    ///
    /// На macOS её нет как механизма: демон launchd идёт ОТ ИМЕНИ ВЛАДЕЛЬЦА
    /// (`UserName` в описании), а права администратора агент просит системным
    /// диалогом пароля через брокер. Записать `true` там значило бы обещать
    /// дверь, которой нет.
    pub fn wants_session0(&self) -> bool {
        cfg!(windows) && self.session0 && self.wants_service()
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
    /// `windows` | `macos` | `linux` — по нему визард прячет то, чего на этой
    /// системе нет (служба, тело, брандмауэр), не спрашивая оболочку.
    pub platform: String,
    pub arch: String,
}

/// Корень поставки или установки: папка, где лежат helene.json, app/, runtime/.
/// На Windows это папка самого exe — паспорт сборки лежит рядом с ним.
#[cfg(windows)]
pub fn exe_dir() -> PathBuf {
    std::env::current_exe()
        .ok()
        .and_then(|p| p.parent().map(Path::to_path_buf))
        .unwrap_or_else(|| PathBuf::from("."))
}

/// На macOS установщик лежит внутри бандла (`Helene Setup.app/Contents/MacOS/`),
/// и «рядом с exe» — это не корень. Правило одно на оболочку и установщик: от
/// исполняемого файла вверх по родителям, не больше пяти уровней, до первой
/// папки с паспортом сборки `helene-build.json`; паспорта нет, но exe лежит в
/// `*.app/Contents/MacOS` — корень над бандлом; иначе папка exe, как на Windows.
///
/// ⚠ Считается ОДИН раз за процесс (`OnceLock`, как `install_root()` в
/// оболочке). Снятие удаляет паспорт вместе с остальным, и повторный вызов уже
/// после этого находил бы не корень, а папку `MacOS` внутри бандла: хвост
/// `uninstall_finish` бил `rm -rf` мимо, бандл мастера и `~/Applications/Helene`
/// оставались при расписке «удалена вместе с данными». Правило про бандл —
/// второй заслон: повторное снятие из папки, где остались только `data/` и
/// `Helene Setup.app` (паспорта уже нет), обязано найти тот же корень, а не
/// снести собственный бинарь, оставив `data/` с ключами целой.
#[cfg(not(windows))]
pub fn exe_dir() -> PathBuf {
    static ROOT: std::sync::OnceLock<PathBuf> = std::sync::OnceLock::new();
    ROOT.get_or_init(|| {
        let here = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(Path::to_path_buf))
            .unwrap_or_else(|| PathBuf::from("."));
        root_from_exe_dir(&here)
    })
    .clone()
}

/// Корень по папке исполняемого файла — паспорт, потом бандл, потом сама папка.
#[cfg(not(windows))]
fn root_from_exe_dir(here: &Path) -> PathBuf {
    root_above(here).or_else(|| bundle_root(here)).unwrap_or_else(|| here.to_path_buf())
}

/// Первая папка с `helene-build.json`, начиная с `from` и до пяти родителей выше.
#[cfg(not(windows))]
fn root_above(from: &Path) -> Option<PathBuf> {
    let mut cur = Some(from.to_path_buf());
    for _ in 0..=5 {
        let dir = cur?;
        if dir.join("helene-build.json").is_file() {
            return Some(dir);
        }
        cur = dir.parent().map(Path::to_path_buf);
    }
    None
}

/// `…/Что-то.app/Contents/MacOS` → папка, в которой лежит бандл. Иначе None.
#[cfg(not(windows))]
fn bundle_root(exe_dir: &Path) -> Option<PathBuf> {
    let is = |p: Option<&Path>, name: &str| p.and_then(Path::file_name).map(|n| n == name).unwrap_or(false);
    if !is(Some(exe_dir), "MacOS") {
        return None;
    }
    let contents = exe_dir.parent()?;
    if !is(Some(contents), "Contents") {
        return None;
    }
    let app = contents.parent()?;
    let bundle = app.file_name()?.to_str()?.ends_with(".app");
    if !bundle {
        return None;
    }
    app.parent().map(Path::to_path_buf)
}

/// Системные программы — только полным путём из %SystemRoot%.
/// `Command::new("powershell.exe")` ищет exe СНАЧАЛА в папке своего процесса, а
/// установщик лежит ВНУТРИ распакованной поставки (обычно в «Загрузках»): файл
/// `powershell.exe`, положенный рядом с `helene-setup.exe`, исполнялся бы вместо
/// системного — и дальше эти же вызовы уходят под UAC. Те же строки, что у
/// оболочки (`shell/src/main.rs::sys_exe`).
#[cfg(windows)]
fn system_root() -> PathBuf {
    std::env::var_os("SystemRoot")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:\\Windows"))
}

#[cfg(windows)]
pub fn sys_exe(name: &str) -> PathBuf {
    let full = system_root().join("System32").join(name);
    if full.exists() {
        full
    } else {
        PathBuf::from(name)
    }
}

#[cfg(windows)]
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
#[cfg(windows)]
pub fn default_dir() -> Option<PathBuf> {
    std::env::var_os("LOCALAPPDATA")
        .map(PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty())
        .map(|base| base.join("Programs").join(PRODUCT))
}

/// macOS: `~/Applications/Helene` — программы пользователя, без прав
/// администратора, как `%LocalAppData%\Programs` на Windows. None без $HOME —
/// по той же причине, что и выше: "." здесь была бы папка поставки.
#[cfg(not(windows))]
pub fn default_dir() -> Option<PathBuf> {
    home_dir().map(|home| home.join("Applications").join(PRODUCT))
}

/// Домашняя папка пользователя — `$HOME`; на macOS всё своё лежит под ней.
#[cfg(not(windows))]
fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .filter(|p| !p.as_os_str().is_empty())
}

/// Чем объяснить, что папку по умолчанию вывести не удалось.
#[cfg(windows)]
const NO_DEFAULT_DIR: &str = "Windows не сказала, где %LOCALAPPDATA%: укажи папку установки явно";
#[cfg(not(windows))]
const NO_DEFAULT_DIR: &str = "система не сказала, где домашняя папка ($HOME): укажи папку установки явно";

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
        platform: std::env::consts::OS.to_string(),
        arch: std::env::consts::ARCH.to_string(),
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
            return out.to_string_lossy().to_lowercase().trim_end_matches(SEP).to_string();
        }
        match cur.file_name() {
            Some(n) => rest.push(n.to_os_string()),
            None => break,
        }
        if !cur.pop() {
            break;
        }
    }
    p.to_string_lossy().to_lowercase().trim_end_matches(SEP).to_string()
}

/// Разделитель путей этой системы: `\` на Windows, `/` на macOS. Одна буква,
/// чтобы сравнение путей ниже не знало, на какой системе оно работает.
const SEP: char = std::path::MAIN_SEPARATOR;

/// `a` — это `b` или лежит внутри `b`.
fn inside_or_same(a: &str, b: &str) -> bool {
    a == b || a.starts_with(&format!("{b}{SEP}"))
}

// ---------------------------------------------------------------- PowerShell

// Текст, который печатают консольные программы: Windows PowerShell 5.1, sc.exe
// и netsh отвечают в кодовой странице консоли системы, а не в UTF-8. Читать её
// как UTF-8 — показать владельцу вместо причины строку из «?????».
#[cfg(windows)]
include!("../../common/console_text.rs");

fn run_hidden(cmd: &mut Command) -> Result<std::process::Output, String> {
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    cmd.output().map_err(|e| e.to_string())
}

#[cfg(windows)]
fn powershell(script: &str) -> Result<std::process::Output, String> {
    let mut cmd = Command::new(powershell_exe());
    cmd.args(["-NoProfile", "-NonInteractive", "-Command", script]);
    run_hidden(&mut cmd)
}

/// Вывод Unix-утилиты (pgrep, lsof, launchctl) строкой: они говорят UTF-8, и
/// кодовые страницы Windows здесь ни при чём. Хвост и перевод строки снимаем —
/// это подпись человеку или число, а не значение с пробелами.
#[cfg(not(windows))]
fn console_text(bytes: &[u8]) -> String {
    String::from_utf8_lossy(bytes).trim().to_string()
}

// ---------------------------------------------------------------- копирование

/// Текст ошибки файловой операции человеческими словами: «os error 32» владельцу
/// ничего не говорит, а закрыть занявшую файл программу он может.
#[cfg(windows)]
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

/// То же на macOS — своими номерами errno: EACCES/EPERM, ENOSPC, ETXTBSY.
#[cfg(not(windows))]
fn io_note(path: &Path, e: &std::io::Error) -> String {
    match e.raw_os_error() {
        Some(26) => format!("файл занят работающей программой: {} — закрой {PRODUCT_UI}", path.display()),
        Some(13) | Some(1) => format!("нет доступа к {}", path.display()),
        Some(28) => format!("на диске нет места: {}", path.display()),
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
        // Символические ссылки — как есть, а не содержимым. В рантайме macOS
        // (python-build-standalone) `bin/python3 -> python3.14`, `lib/…dylib`
        // ссылаются друг на друга; скопировать цель вместо ссылки значило бы
        // раздуть рантайм и разорвать эти связи. Биты исполнения `fs::copy`
        // на Unix переносит сам. На Windows ссылок в поставке нет по построению.
        #[cfg(unix)]
        {
            let meta = std::fs::symlink_metadata(&from).map_err(|e| io_note(&from, &e))?;
            if meta.file_type().is_symlink() {
                let target = std::fs::read_link(&from).map_err(|e| io_note(&from, &e))?;
                // Прежняя ссылка или файл на этом месте — прочь: `symlink` поверх
                // существующего имени отказывает, а писать в цель старой ссылки нельзя.
                if std::fs::symlink_metadata(&to).is_ok() {
                    std::fs::remove_file(&to).map_err(|e| io_note(&to, &e))?;
                }
                std::os::unix::fs::symlink(&target, &to).map_err(|e| io_note(&to, &e))?;
                count += 1;
                continue;
            }
        }
        if from.is_dir() {
            count += copy_tree(&from, &to, &[], skip_rel, &here)?;
        } else {
            // ⚠ macOS: `fs::copy` поверх существующего файла пишет В ТОТ ЖЕ vnode
            // (open(O_TRUNC) + fcopyfile), а кэш подписи ядра привязан к vnode:
            // перезаписанный на месте Mach-O, который уже исполнялся
            // (`Helene.app/Contents/MacOS/helene`, `helene-relay`,
            // `runtime/bin/python3.14`, любой `.so`), при следующем запуске
            // умирает с «Killed: 9» (Go #42684). Поэтому прежний файл сначала
            // убираем — копия ложится новым inode, заодно работает clonefile.
            // На Windows порядок прежний: там занятый файл честно отказывает.
            #[cfg(unix)]
            if let Ok(meta) = std::fs::symlink_metadata(&to) {
                if !meta.is_dir() {
                    std::fs::remove_file(&to).map_err(|e| io_note(&to, &e))?;
                }
            }
            std::fs::copy(&from, &to).map_err(|e| io_note(&to, &e))?;
            count += 1;
        }
    }
    Ok(count)
}

/// Снять карантин Gatekeeper со всей папки установки — ДО первого запуска.
///
/// Архив, скачанный браузером и распакованный Finder, несёт `com.apple.quarantine`
/// на каждом файле, а копирование (`fcopyfile`/`clonefile`) переносит его дальше.
/// «Правая кнопка → Открыть» одобряет ТОЛЬКО бандл, по которому кликнули: окно
/// откроется, а `runtime/bin/python3.14`, `helene-relay` и `git` Gatekeeper
/// убьёт на exec — движок молчит без единого слова. `find -xattrname` трогает
/// только файлы, у которых атрибут есть: `xattr -dr` на всей папке ругался бы
/// на каждый файл без него, и по коду выхода нельзя было бы понять, снялось ли.
/// -> число снятых не считаем: `find` молчит; расписка — снялось или нет.
#[cfg(not(windows))]
fn strip_quarantine(dir: &Path) -> Result<(), String> {
    let mut cmd = Command::new("find");
    cmd.arg(dir)
        .args(["-xattrname", "com.apple.quarantine", "-exec", "xattr", "-d", "com.apple.quarantine", "{}", "+"]);
    let out = run_hidden_for(&mut cmd, std::time::Duration::from_secs(300))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(console_text(&out.stderr).chars().take(200).collect())
    }
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
/// Установка без живого питона рантайма (`PYTHON_REL`) — не одинаковый ни при чём.
fn runtime_same(payload: &Path, dir: &Path) -> bool {
    if !python_exe(dir).exists() {
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
        // права сразу — сузить владелец может в Настройках. Где тела нет
        // (прочие POSIX) — блок есть, выключатель всегда false (`wants_computer`).
        "computer": computer_block(s.wants_computer()),
        "python": PYTHON_REL,
        "app": "app/deskapp.py",
        "runner": DEFAULT_RUNNER_REL,
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

/// Движок, который владелец поставил вместо штатного, — оставить его.
///
/// ⚠⚠ Живой случай 20.09.2026 (Mac, Сергей): его агент жил на собственном
/// движке `data/extensions/runner.py` (там же его приборы), а обновление
/// 0.8.1 → 0.8.3 молча вернуло штатный `app/localharness/runner.py`. Документы
/// при этом обещают, что `helene.json` сливается, а не переписывается: ключ
/// `runner` был в списке визарда и затирался безусловно.
///
/// Правило: чужой путь остаётся, только если файл по нему ЕСТЬ. Сломанный или
/// исчезнувший путь чинится штатным — иначе обновление оставило бы установку
/// без движка вовсе. Пустое старое значение и совпадение со свежим — обычный
/// случай, решать нечего.
///
/// -> `Some(путь)` = оставить этот, `None` = писать значение визарда.
/// Чистая: существование файла приходит проверкой снаружи.
fn keep_own_runner(old: Option<&str>, fresh: &str, exists: impl Fn(&str) -> bool) -> Option<String> {
    let old = old.unwrap_or_default().trim();
    if old.is_empty() || old == fresh.trim() {
        return None;
    }
    exists(old).then(|| old.to_string())
}

/// Слить свежие решения визарда с тем, что уже лежит в установленном helene.json.
fn merge_config(existing: Option<serde_json::Value>, fresh: serde_json::Value, s: &Setup) -> serde_json::Value {
    let Some(serde_json::Value::Object(old)) = existing else { return fresh };
    let serde_json::Value::Object(new) = fresh else { return serde_json::Value::Object(old) };
    let mut out = old;
    // Свой движок владельца переживает обновление: см. `keep_own_runner`.
    let own_runner = keep_own_runner(
        out.get("runner").and_then(|v| v.as_str()),
        new.get("runner").and_then(|v| v.as_str()).unwrap_or_default(),
        |rel| std::path::Path::new(&s.dir).join(rel).is_file(),
    );
    for k in WIZARD_KEYS {
        if let Some(v) = new.get(k) {
            if k == "model" {
                continue; // ниже, по полям
            }
            if k == "runner" && own_runner.is_some() {
                continue; // остаётся прежний, чужой визарду
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
        ("computer", "enabled", serde_json::Value::Bool(s.wants_computer())),
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
#[cfg(windows)]
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
#[cfg(windows)]
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
#[cfg(windows)]
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

// --- то же на macOS: ps/kill вместо CIM и Stop-Process -------------------------
//
// Процессы установки узнаём по ПУТИ ИСПОЛНЯЕМОГО ФАЙЛА (`ps -o comm`): оболочка
// запущена через `open` полным путём бандла, детей (канал, движок, реле, питон)
// она поднимает тоже полными путями из своего корня.
//
// ⚠ Не по командной строке (`pgrep -f`): тот образец совпадал с ЛЮБЫМ
// процессом, у которого путь папки стоит в аргументах, — `sh
// ~/Applications/Helene/install.sh` (обновление и снятие через него), `tail -f
// …/helene.log`, редактор с открытым helene.json. Первый же `stop_running`
// мастера убивал сценарий обновления («Terminated: 15»): `--relaunch` и
// «обновлено» не выполнялись, JSON с ключом оставался в кэше.

/// Одна строка `ps`: процесс, его родитель, путь исполняемого файла.
#[cfg(not(windows))]
struct Proc {
    pid: u32,
    ppid: u32,
    comm: String,
}

/// Разбор `ps -axo pid=,ppid=,comm=`: два числа, дальше путь — с пробелами,
/// как у «Helene Setup.app».
#[cfg(not(windows))]
fn parse_ps(text: &str) -> Vec<Proc> {
    // `ps -axo pid=,ppid=,comm=` выравнивает числа пробелами, и их между полями
    // несколько: `splitn` по одному пробелу давал пустой второй токен, `parse`
    // падал, и таблица выходила ПУСТОЙ на каждой строке — остановка процессов
    // никого не находила (девятый круг CI, стенд `processes_are_matched_by_…`).
    // Режем по первому пробелу и снимаем пробелы перед следующим полем; путь
    // программы (третье поле) может содержать пробелы — он берётся целиком.
    text.lines()
        .filter_map(|line| {
            let t = line.trim_start();
            let (pid_s, rest) = t.split_once(char::is_whitespace)?;
            let rest = rest.trim_start();
            let (ppid_s, comm) = rest.split_once(char::is_whitespace).unwrap_or((rest, ""));
            let pid = pid_s.parse().ok()?;
            let ppid = ppid_s.parse().ok()?;
            Some(Proc { pid, ppid, comm: comm.trim().to_string() })
        })
        .collect()
}

/// Что из папки `dir` работает прямо сейчас: пары (pid, имя программы), кроме нас
/// самих и наших родителей. Родителей не трогаем (кто запустил мастер, тот
/// ждёт его — сценарий обновления), с одним исключением: сама оболочка
/// `Helene.app`. Она зовёт мастер для тихого обновления и ОБЯЗАНА быть
/// погашена, как `helene.exe` на Windows, иначе «Открыть» после установки
/// активировало бы старый живой экземпляр, а не новую версию.
#[cfg(not(windows))]
fn procs_under_named(dir: &Path, table: &[Proc]) -> Vec<(u32, String)> {
    let prefix = format!("{}/", dir.display()).to_lowercase();
    let shell_prefix = format!("{}/", shell_exe(dir).display()).to_lowercase();
    let me = std::process::id();
    // Цепочка родителей — от нас вверх; на цикле или обрыве останавливаемся.
    let mut ancestors: Vec<u32> = Vec::new();
    let mut cur = me;
    for _ in 0..64 {
        let Some(p) = table.iter().find(|p| p.pid == cur) else { break };
        if p.ppid == 0 || p.ppid == cur || ancestors.contains(&p.ppid) {
            break;
        }
        ancestors.push(p.ppid);
        cur = p.ppid;
    }
    table
        .iter()
        .filter(|p| p.pid != me)
        .filter(|p| {
            let comm = p.comm.to_lowercase();
            comm.starts_with(&prefix) && (!ancestors.contains(&p.pid) || comm.starts_with(&shell_prefix))
        })
        .map(|p| (p.pid, p.comm.rsplit('/').next().unwrap_or(&p.comm).to_string()))
        .collect()
}

/// pid всех процессов, запущенных из этой папки (кроме нас и наших родителей).
/// None — `ps` не нашёлся или не ответил; пустой список — из папки не работает ничего.
#[cfg(not(windows))]
fn pids_under(dir: &Path) -> Option<Vec<u32>> {
    let mut cmd = Command::new("ps");
    cmd.args(["-axo", "pid=,ppid=,comm="]);
    let out = run_hidden_for(&mut cmd, std::time::Duration::from_secs(10)).ok()?;
    if !out.status.success() {
        return None;
    }
    let table = parse_ps(&console_text(&out.stdout));
    Some(procs_under_named(dir, &table).into_iter().map(|(pid, _)| pid).collect())
}

/// Послать сигнал каждому процессу списка. Через `kill(1)`, а не libc: лишняя
/// зависимость ради одного вызова не нужна, а `kill` есть на любой macOS.
#[cfg(not(windows))]
fn signal_all(pids: &[u32], signal: &str) {
    if pids.is_empty() {
        return;
    }
    let mut cmd = Command::new("kill");
    cmd.arg(signal);
    for pid in pids {
        cmd.arg(pid.to_string());
    }
    let _ = run_hidden_for(&mut cmd, std::time::Duration::from_secs(5));
}

/// Остановить всё, что запущено из папки установки. Сначала мягко (TERM):
/// оболочка успевает погасить детей и убрать значок из строки меню; кто через
/// секунду-другую жив — KILL, как `Stop-Process -Force`. Дальше тот же опрос до
/// пустого списка, что и на Windows.
#[cfg(not(windows))]
pub fn stop_running(dir: &Path) -> bool {
    let Some(pids) = pids_under(dir) else { return false };
    if pids.is_empty() {
        return true;
    }
    signal_all(&pids, "-TERM");
    for i in 0..20 {
        std::thread::sleep(std::time::Duration::from_millis(400));
        match pids_under(dir) {
            Some(left) if left.is_empty() => return true,
            None => return false,
            Some(left) => {
                if i == 4 {
                    signal_all(&left, "-KILL");
                }
            }
        }
    }
    false
}

/// На macOS «занятых файлов» нет: работающий бинарь можно перезаписать, и
/// система не скажет об этом. Препятствие здесь — сами живые процессы; их
/// имена и называем, чтобы отказ звучал так же: «часть программы ещё работает».
#[cfg(not(windows))]
fn locked_files(dir: &Path) -> Vec<String> {
    let mut cmd = Command::new("ps");
    cmd.args(["-axo", "pid=,ppid=,comm="]);
    let Ok(out) = run_hidden_for(&mut cmd, std::time::Duration::from_secs(10)) else { return Vec::new() };
    let table = parse_ps(&console_text(&out.stdout));
    let mut names: Vec<String> = Vec::new();
    for (_, name) in procs_under_named(dir, &table) {
        if !name.is_empty() && !names.contains(&name) {
            names.push(name);
        }
    }
    names
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
#[cfg(windows)]
fn port_holder(port: u16) -> String {
    let script = format!(
        "$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1; \
         if ($c) {{ $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue; if ($p) {{ \"$($p.ProcessName).exe\" }} }}"
    );
    powershell(&script)
        .map(|o| console_text(&o.stdout).trim().to_string())
        .unwrap_or_default()
}

/// macOS: `lsof` по слушающему TCP-порту; чужие процессы без прав он не
/// покажет — тогда имени нет, и владелец видит «занят другой программой» без него.
#[cfg(not(windows))]
fn port_holder(port: u16) -> String {
    let mut cmd = Command::new("lsof");
    cmd.args(["-nP", &format!("-iTCP:{port}"), "-sTCP:LISTEN", "-Fc"]);
    let Ok(out) = run_hidden_for(&mut cmd, std::time::Duration::from_secs(5)) else { return String::new() };
    // Формат -F: по строке на поле, имя команды — строка с префиксом `c`.
    console_text(&out.stdout)
        .lines()
        .find_map(|l| l.strip_prefix('c'))
        .unwrap_or("")
        .trim()
        .to_string()
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
#[cfg(windows)]
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
/// Решения владельца, вычитанные из УЖЕ УСТАНОВЛЕННОЙ копии, — чтобы
/// обновление не переспрашивало то, что он однажды решил.
///
/// ⚠ Живой случай 20.09.2026, слова владельца: «блин, он мне ставить собрался,
/// а не обновлять». На macOS `install.sh` собирал такие решения сам (его
/// `decisions_json`) и звал мастер тихо; на Windows окно запускало мастер БЕЗ
/// аргументов, и человек, обновляясь, получал полный визард — имя агента,
/// конституцию, модель заново. Теперь обе платформы делают одно и то же, и
/// правила здесь — те же, что в `install.sh`, слово в слово.
///
/// `None` — когда решать нечего или опасно: нет имён, нет конституции. Тогда
/// зовущий открывает визард, и человек отвечает сам (мастер иначе записал бы
/// поверх его выбора свои умолчания).
///
/// Чистая функция: конфиг и конституция приходят готовыми, диск не трогается.
pub fn setup_from_installed(cfg: &serde_json::Value, soul: &str, dir: &str) -> Option<Setup> {
    let text = |v: Option<&serde_json::Value>| {
        v.and_then(|x| x.as_str()).unwrap_or("").trim().to_string()
    };
    let at = |obj: &str, key: &str| text(cfg.get(obj).and_then(|o| o.get(key)));
    let flag = |obj: &str, key: &str| {
        cfg.get(obj).and_then(|o| o.get(key)).and_then(|v| v.as_bool()).unwrap_or(false)
    };
    let agent = at("agent", "name");
    let owner = at("owner", "name");
    if agent.is_empty() || owner.is_empty() || soul.trim().is_empty() {
        return None;
    }
    let base = at("model", "base_url");
    let key = at("model", "key");
    let name = at("model", "model");
    // Тот же порядок разбора, что у `install.sh`: фреймворк сильнее всего,
    // затем реле (или его ключ-признак), затем локальная модель.
    let provider = if at("model", "framework") == "anthropic" {
        "anthropic"
    } else if flag("relay", "enabled") || key.starts_with("sk-frame-") {
        "chatgpt"
    } else if key == "local" {
        "local"
    } else {
        "api"
    };
    Some(Setup {
        agent,
        owner,
        constitution: soul.to_string(),
        accepted: true,
        provider: provider.to_string(),
        chatgpt_model: if provider == "chatgpt" { name.clone() } else { String::new() },
        reasoning_effort: at("model", "reasoning_effort"),
        api: Endpoint { base_url: base.clone(), model: name.clone(), key: key.clone() },
        anthropic: Endpoint { base_url: base.clone(), model: name.clone(), key },
        local: LocalEndpoint { base_url: base, model: name },
        telegram: Telegram {
            bot_token: at("telegram", "bot_token"),
            owner_id: at("telegram", "owner_id"),
        },
        agent_mode: {
            let mode = text(cfg.get("agent_mode"));
            if mode.is_empty() { "sandbox".to_string() } else { mode }
        },
        // ⚠ Служба, нулевая сессия и брандмауэр — решения ВЛАДЕЛЬЦА, а не
        // умолчания обновления. `installed.service` — след мастера, он же и
        // читается обратно: `false` здесь молча снял бы службу (мастер снимает
        // прежнюю перед копированием и ставит обратно только по этому полю).
        service: flag("installed", "service"),
        session0: flag("service", "session0"),
        force_extensions: false,
        firewall: cfg
            .get("service")
            .and_then(|s| s.get("firewall"))
            .and_then(|v| v.as_bool())
            .unwrap_or(true),
        computer: flag("computer", "enabled"),
        dir: dir.to_string(),
    })
}

/// Решения из установки по её папке: конфиг и конституция читаются с диска,
/// правила — в `setup_from_installed`.
pub fn setup_from_dir(dir: &Path) -> Option<Setup> {
    let cfg = read_json(&dir.join("helene.json"))?;
    let soul = std::fs::read_to_string(dir.join("data").join("soul").join("SOUL.md")).unwrap_or_default();
    setup_from_installed(&cfg, &soul, &dir.display().to_string())
}

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

// Ярлыки, запись в «Приложениях», размер и дата установки — всё это Windows:
// на macOS программа живёт бандлом в ~/Applications, «Приложений» с записью об
// удалении нет, и в расписке этих шагов просто нет (см. `install`).

/// Путь к настоящей папке рабочего стола: на Windows 11 с резервным копированием
/// папок OneDrive это %USERPROFILE%\OneDrive\Рабочий стол, а не %USERPROFILE%\Desktop.
/// Ярлык создавался по известной папке, а удалялся склейкой из USERPROFILE — и
/// переживал удаление программы.
#[cfg(windows)]
fn desktop_dir() -> Option<PathBuf> {
    let out = powershell("[Environment]::GetFolderPath('Desktop')").ok()?;
    let path = console_text(&out.stdout).trim().to_string();
    if path.is_empty() {
        return std::env::var_os("USERPROFILE").map(|p| PathBuf::from(p).join("Desktop"));
    }
    Some(PathBuf::from(path))
}

#[cfg(windows)]
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

#[cfg(windows)]
fn install_date() -> String {
    powershell("(Get-Date).ToString('yyyyMMdd')")
        .ok()
        .map(|o| console_text(&o.stdout).trim().to_string())
        .filter(|s| s.len() == 8)
        .unwrap_or_default()
}

/// Дата установки на macOS — из системных часов, без внешней программы. Записи
/// в «Приложениях» здесь нет, так что строка нужна только расписке и журналу.
#[cfg(not(windows))]
#[allow(dead_code)]
fn install_date() -> String {
    let secs = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    civil_yyyymmdd(secs)
}

/// Секунды от эпохи Unix → `yyyyMMdd` по UTC (алгоритм Хиннанта). Без crate
/// chrono: одна дата в году не стоит зависимости.
#[cfg_attr(windows, allow(dead_code))]
fn civil_yyyymmdd(secs: u64) -> String {
    let days = (secs / 86_400) as i64;
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}{m:02}{d:02}")
}

#[cfg(windows)]
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
#[cfg(windows)]
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

/// macOS: дерево процессов — это группа процессов. Вход запускается своей
/// группой (`process_group(0)` в `relay_login`), и `kill -9 -<pid>` снимает и
/// реле, и помощника, державшего порт 1455, — как `taskkill /T` на Windows.
#[cfg(not(windows))]
pub fn relay_abort() {
    let Ok(mut guard) = LOGIN.lock() else { return };
    if let Some(mut child) = guard.take() {
        if child.try_wait().ok().flatten().is_none() {
            let mut kill = Command::new("kill");
            kill.args(["-KILL", &format!("-{}", child.id())]);
            let _ = run_hidden(&mut kill);
            let _ = child.kill();
            let _ = child.wait();
        }
    }
}

/// Список моделей самого реле: поднять реле из поставки на временном порту, спросить
/// /v1/models, погасить. Без захардкоженного списка — что реле отдаёт, то и выбор.
pub fn relay_models() -> Result<Vec<String>, String> {
    let payload = payload_dir().ok_or("рядом с установщиком нет поставки")?;
    let exe = relay_exe(&payload);
    if !exe.exists() {
        return Err(format!("в этой сборке нет {RELAY_NAME}"));
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
    let python = python_exe(&payload);
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
    let exe = relay_exe(&payload);
    if !exe.exists() {
        return Err(format!("в этой сборке нет {RELAY_NAME}"));
    }
    let home = relay_home();
    std::fs::create_dir_all(&home).map_err(|e| e.to_string())?;
    let mut cmd = Command::new(&exe);
    cmd.arg("login")
        .current_dir(&home)
        .env("RELAY_LOCAL", "1")
        .env("RELAY_LOG_DIR", home.join("logs"));
    let python = python_exe(&payload);
    if python.exists() {
        cmd.env("RELAY_PYTHON", &python);
    }
    #[cfg(windows)]
    cmd.creation_flags(CREATE_NO_WINDOW);
    // Своя группа процессов: отмена входа (`relay_abort`) снимает её целиком —
    // и помощника, которого реле поднимает под браузерный обратный вызов.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
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
#[cfg(windows)]
fn service_op(op: &str, name: &str, script: Option<&Path>) -> Result<(), String> {
    let wrapper = std::env::temp_dir().join("helene-service-op.ps1");
    std::fs::write(&wrapper, SERVICE_OP_PS1).map_err(|e| io_note(&wrapper, &e))?;
    let out = powershell(&service_op_command(&wrapper, op, name, script))?;
    service_op_verdict(out.status.code())
}

/// macOS: снять демон launchd. Ставит его `install_service` (ему нужен ещё и
/// собранный plist), а сюда приходят только снятия — и переустановки, и
/// удаления программы, и «галочку сняли».
///
/// `name` и `script` не значат здесь ничего: поколений продукта под launchd не
/// было, метка одна (`app.helene.svc`), а скрипт снятия — две строки launchctl,
/// и держать их файлом в поставке значило бы завести второй источник правды.
#[cfg(target_os = "macos")]
fn service_op(op: &str, _name: &str, _script: Option<&Path>) -> Result<(), String> {
    if op != "uninstall" && op != "stop" {
        return Err(format!("на macOS команда службы «{op}» не делается этим путём"));
    }
    mac_svc_run_admin(
        &mac_svc_remove_line(),
        &format!("{PRODUCT_UI}: снять службу «Работать без входа в систему»"),
    )
    .map(|_| ())
}

/// На прочих POSIX службы нет по построению. Дойти сюда из интерфейса нельзя:
/// опция службы там не рисуется, а `service_state` отвечает «absent», и все
/// ветки «снять прежнюю» обходятся стороной. Строка — на случай
/// `--install <json>` с чужими решениями.
#[cfg(not(any(windows, target_os = "macos")))]
fn service_op(_op: &str, _name: &str, _script: Option<&Path>) -> Result<(), String> {
    Err("службы на этой системе нет".into())
}

/// Служба: один UAC на машинную часть. Ждём завершения скрипта, потом
/// спрашиваем SCM сами — квитанция о фактическом состоянии, не «запустил».
#[cfg(windows)]
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

/// macOS: поставить демон launchd. Описание собирает САМ `helene-svc`
/// (`plist --config`) — один писатель формы, как на Windows скрипт службы.
/// Мастер кладёт его во временный файл своими правами и просит администратора
/// перенести на место и загрузить; приговор — по состоянию ПОСЛЕ, а не по коду
/// команды.
///
/// Пароль спрашивает система: при тихой установке на машине с беспарольным
/// sudo (раннер CI) диалога не будет вовсе — `mac_svc_run_admin` сам выбирает
/// путь.
#[cfg(target_os = "macos")]
fn install_service(dir: &Path) -> String {
    let svc = dir.join("helene-svc");
    if !svc.is_file() {
        return "missing".into();
    }
    let out = match Command::new(&svc)
        .arg("plist")
        .arg("--config")
        .arg(dir.join("helene.json"))
        .output()
    {
        Ok(out) if out.status.success() => out.stdout,
        Ok(out) => {
            return format!(
                "failed: описание демона не собралось: {}",
                String::from_utf8_lossy(&out.stderr).trim()
            )
        }
        Err(e) => return format!("failed: helene-svc не запустился: {e}"),
    };
    // ⚠ Своя папка 0700 со случайным именем и файл под O_EXCL|0600, а не
    // предсказуемый `temp_dir()/app.helene.svc.plist`: между записью и «Да» в
    // диалоге пароля его успевал подменить любой процесс учётки, и под root
    // копировалось уже чужое описание. Плюс хэш в админ-строку — третья дверь.
    let (tmp_dir, tmp, hash) = match mac_svc_stage_plist(&out) {
        Ok(v) => v,
        Err(e) => return format!("failed: {e}"),
    };
    let said = mac_svc_run_admin(
        &mac_svc_install_line(&tmp, &hash),
        &format!("{PRODUCT_UI}: поставить службу «Работать без входа в систему»"),
    );
    let _ = std::fs::remove_dir_all(&tmp_dir);
    let state = mac_svc_state();
    // Служба — не движок: демон под KeepAlive бывает «running», пока агент в нём
    // падает по кругу. Слова о движке дописываются к расписке, машинное слово
    // состояния не трогаем — по нему живут мастер и карточка.
    let note = mac_svc_engine_note(&state, mac_svc_engine_alive(mac_svc_port(dir)));
    match (said, state.as_str()) {
        (Err(e), "absent") => format!("failed: {e}"),
        (Err(e), st) => format!("{st} (команда ответила отказом: {e})"),
        (Ok(_), st) if !note.is_empty() => format!("{st} ({note})"),
        (Ok(_), st) => st.to_string(),
    }
}

/// Порт канала из `helene.json` установки — для пробы движка. `None` — конфига
/// нет или порт в нём не назван: тогда о движке ничего не говорим.
#[cfg(target_os = "macos")]
fn mac_svc_port(dir: &Path) -> Option<u16> {
    let raw = std::fs::read_to_string(dir.join("helene.json")).ok()?;
    let cfg: serde_json::Value = serde_json::from_str(raw.trim_start_matches('\u{feff}')).ok()?;
    cfg.get("port").and_then(|v| v.as_u64()).map(|n| n as u16)
}

/// На прочих POSIX службы в поставке нет.
#[cfg(not(any(windows, target_os = "macos")))]
fn install_service(_dir: &Path) -> String {
    "missing".into()
}

/// Как снять службу руками — словами ТОЙ системы, на которой мы стоим.
/// Раньше здесь всегда стояло `sc stop`/`sc delete`: на Mac это совет в пустоту.
fn service_hand_removal() -> String {
    if cfg!(windows) {
        format!("Сними вручную: sc stop {PRODUCT} и sc delete {PRODUCT}")
    } else {
        format!(
            "Сними вручную: sudo launchctl bootout system/{MAC_SVC_LABEL} и sudo rm {MAC_SVC_PLIST}"
        )
    }
}

/// Имя исполняемого файла по платформе: суффикс `.exe` только на Windows.
/// Та же функция, что у оболочки и у службы, — файлы поставки зовут трое.
fn exe_name(stem: &str) -> String {
    if cfg!(windows) {
        format!("{stem}.exe")
    } else {
        stem.to_string()
    }
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
#[cfg(windows)]
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

/// macOS: повышение прав не требуется ничему в установке — ставим в
/// ~/Applications, службы нет. Ответ «можно, но не спрашивали» — тот, при
/// котором визард ничего не запирает и ничего не рисует.
#[cfg(not(windows))]
pub fn admin_rights() -> AdminRights {
    AdminRights { can: true, certain: false, elevated: false }
}

#[cfg(windows)]
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

/// macOS: у launchd поколений продукта не было — метка одна, и `name` здесь
/// не значит ничего. Ответ — `running` | `stopped` | `absent` | `unknown`;
/// последнее честнее, чем выдуманное «нет» (см. `mac_svc_state_words`).
#[cfg(target_os = "macos")]
pub fn service_state_of(_name: &str) -> String {
    mac_svc_state()
}

/// На прочих POSIX службы нет вовсе — «absent» всегда. По этому ответу все
/// ветки про прежнюю службу в `install` и `uninstall` обходятся сами.
#[cfg(not(any(windows, target_os = "macos")))]
pub fn service_state_of(_name: &str) -> String {
    "absent".into()
}

/// С дедлайном, как в окне: повисший sc.exe не должен вешать визард
/// (ревью 06.09, §4 п. 13).
#[cfg(windows)]
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
#[cfg(windows)]
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

/// macOS: прежних поколений со службой здесь не бывало — список пуст, и сцена
/// «прежняя версия» в маршрут визарда не встаёт.
#[cfg(not(windows))]
pub fn legacy_services(_home: Option<&Path>) -> Vec<LegacyService> {
    Vec::new()
}

/// Опрос SCM по известным именам. `home` — папка, которую сейчас ставят или
/// снимают: служба, чей exe лежит в ней, помечается «своей».
#[cfg(windows)]
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
#[cfg(windows)]
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
    // Незаполненный шаблон — ровно два пропуска канона (`resources/SOUL.md`):
    // `{{agent}}` и `{{owner}}`. Любые другие `{{…}}` — текст самой конституции:
    // тихое обновление везёт её из установленного SOUL.md, который агент и
    // владелец правили, и резать установку за скобки в их тексте нельзя.
    if s.constitution.contains("{{agent}}") || s.constitution.contains("{{owner}}") {
        return Err("в конституции остался незаполненный шаблон ({{agent}} / {{owner}})".into());
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
/// 25.09 (K): репетиция расширений владельца под НОВЫМ движком из поставки.
///
/// Зовёт `runner.py --check-extensions --data <папка данных>` питоном ПОСТАВКИ (не
/// установленной версии): манифесты, версия API, импорт кода и регистрация вхолостую —
/// без агента и без замка дерева. Отчёт JSON кладётся в `<dir>/extensions-check.json`
/// (его читает труба, показывает карточка «Расширения»). `Ok(None)` — расширений нет.
fn rehearse_extensions(payload: &Path, dir: &Path) -> Result<Option<(bool, String)>, String> {
    let data = read_json(&dir.join("helene.json"))
        .and_then(|c| c.get("tree").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .map(|t| {
            let p = PathBuf::from(&t);
            if p.is_absolute() { p } else { dir.join(p) }
        })
        .unwrap_or_else(|| dir.join("data"));
    let root = data.join("extensions");
    let has_any = std::fs::read_dir(&root)
        .map(|it| it.flatten().any(|e| e.path().join("extension.json").is_file()))
        .unwrap_or(false);
    if !has_any {
        // 25.09 (ревью V3 F12): расширений нет — прежний отчёт об отказе не должен жить в
        // карточке окна с прошлой датой.
        let _ = std::fs::remove_file(dir.join("extensions-check.json"));
        return Ok(None);
    }
    let python = python_exe(payload);
    let runner = payload.join("app").join("localharness").join("runner.py");
    if !python.is_file() || !runner.is_file() {
        return Err(format!(
            "в поставке нет питона или движка ({}, {})",
            python.display(),
            runner.display()
        ));
    }
    // Версия ПОСТАВКИ — из её паспорта: `requires.helene` расширений сверяется с тем, что
    // ставим, а не с тем, что стоит (ревью 25.09, A5 F1).
    let host_version = read_json(&payload.join("helene-build.json"))
        .and_then(|p| p.get("version").and_then(|v| v.as_str()).map(|s| s.to_string()))
        .unwrap_or_default();
    let mut cmd = Command::new(&python);
    cmd.arg("-X")
        .arg("utf8")
        .arg(&runner)
        .arg("--check-extensions")
        .arg("--data")
        .arg(&data)
        .arg("--host-version")
        .arg(&host_version)
        .current_dir(runner.parent().unwrap_or(payload))
        .env("PYTHONIOENCODING", "utf-8");
    // Предел по времени (A5 F3): код владельца может завести не-daemon поток или ждать
    // сеть — мастер без окна висел бы молча. Истечение — отказ словами, не «идём дальше».
    let out = run_hidden_for(&mut cmd, std::time::Duration::from_secs(120))
        .map_err(|e| format!("репетиция расширений не уложилась или не запустилась: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    let report_path = dir.join("extensions-check.json");
    let report: serde_json::Value = match serde_json::from_str(&text) {
        Ok(v) => v,
        Err(e) => {
            // Не разобрали — это НЕ повод обновляться (A5 F2): отчёт с сырым выводом
            // кладём для карточки и отвечаем отказом; «обновить всё равно» — только флагом.
            let err = String::from_utf8_lossy(&out.stderr);
            let raw = serde_json::json!({
                "ok": false,
                "summary": format!("репетиция не дала отчёта ({e})"),
                "items": [],
                "raw_stdout": text.chars().take(2000).collect::<String>(),
                "raw_stderr": err.chars().take(2000).collect::<String>(),
            });
            let _ = write_atomic(&report_path, &raw.to_string());
            return Ok(Some((false, format!(
                "отчёт репетиции не разобрать ({e}); вывод: {}",
                err.trim().chars().take(300).collect::<String>()
            ))));
        }
    };
    let _ = write_atomic(&report_path, &text);
    let ok = report.get("ok").and_then(|v| v.as_bool()).unwrap_or(false);
    let summary = report
        .get("summary")
        .and_then(|v| v.as_str())
        .unwrap_or("без итога")
        .to_string();
    Ok(Some((ok, summary)))
}

pub fn install(s: &Setup, mut progress: impl FnMut(Progress)) -> Result<Receipt, String> {
    validate_setup(s)?;
    // Имена в отказе — этой системы: на Mac оболочка зовётся `Helene.app`.
    let payload = payload_dir().ok_or_else(|| {
        format!(
            "рядом с установщиком нет поставки ({}, app/, runtime/) — запусти его из папки Hélène",
            PAYLOAD_MARKERS[0]
        )
    })?;
    let dir = if s.dir.trim().is_empty() {
        default_dir().ok_or(NO_DEFAULT_DIR)?
    } else {
        PathBuf::from(s.dir.trim())
    };
    // Поставка и установка не должны пересекаться ни в одну сторону: dst внутри
    // src уводил копирование в бесконечную матрёшку (переполнение стека, гигабайты
    // мусора), а dir == payload гасил живого агента и падал на первом же файле.
    let (np, nd) = (norm_path(&payload), norm_path(&dir));
    if inside_or_same(&nd, &np) || inside_or_same(&np, &nd) {
        // На Mac поставку могли распаковать прямо в ~/Applications/Helene:
        // говорим, как выйти, а не только что нельзя.
        let how = if cfg!(windows) {
            String::new()
        } else {
            " Распакуй архив в другое место (например, в «Загрузки») и запусти Helene Setup оттуда — или поставь через install.sh.".to_string()
        };
        return Err(format!(
            "папка установки ({}) и папка поставки ({}) не должны совпадать или лежать одна в другой. \
             Если {PRODUCT_UI} уже установлена здесь, настройки меняются в окне программы, а не установщиком.{how}",
            dir.display(),
            payload.display()
        ));
    }

    let service_before = service_state();
    let mut pre_steps: Vec<Step> = Vec::new();
    // 25.09 (K): репетиция расширений владельца под НОВЫМ движком — ДО снятия службы и
    // подмены папок. Не грузятся и не сказано «--force-extensions» — отказ словами,
    // старая версия остаётся живой; отчёт лежит в корне установки для карточки окна.
    if dir.exists() {
        match rehearse_extensions(&payload, &dir) {
            Ok(None) => {}
            Ok(Some((ok, summary))) => {
                pre_steps.push(Step {
                    label: "Расширения".into(),
                    ok,
                    note: Some(summary.clone()),
                });
                if !ok && !s.force_extensions {
                    return Err(format!(
                        "расширения владельца не пройдут обновление: {summary}. Отчёт — {}. \
                         Поручи агенту адаптировать (карточка «Расширения» в окне) или обнови без них: \
                         повтори с --force-extensions.",
                        dir.join("extensions-check.json").display()
                    ));
                }
            }
            Err(e) => {
                // 25.09 (ревью V4 F3): истечение 120 с или незапуск питона поставки — это
                // тот же отказ, что «не грузятся»: без --force-extensions обновление не идёт,
                // старая версия остаётся живой. Раньше здесь был красный шаг и «продолжаем».
                pre_steps.push(Step {
                    label: "Расширения".into(),
                    ok: false,
                    note: Some(format!("репетиция не удалась: {e}")),
                });
                if !s.force_extensions {
                    return Err(format!(
                        "репетиция расширений владельца не удалась: {e}. Отчёт — {}. \
                         Поручи агенту адаптировать (карточка «Расширения» в окне) или обнови без них: \
                         повтори с --force-extensions.",
                        dir.join("extensions-check.json").display()
                    ));
                }
            }
        }
    }
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
                        // ⚠ Слова — ТОЙ системы, на которой мы стоим: «служба
                        // Windows… sc stop» на Mac это совет в пустоту, а совет
                        // в пустоту хуже молчания (судьи 19.09).
                        return Err(if cfg!(windows) {
                            format!(
                                "служба Windows «{PRODUCT}» продолжает работать и держит файлы программы ({e}). \
                                 Останови её (sc stop {PRODUCT}) и повтори установку."
                            )
                        } else {
                            format!(
                                "служба продолжает работать и держит файлы программы ({e}). {}",
                                service_hand_removal()
                            )
                        });
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
                     (полностью, включая {TRAY_WORD}) и повтори установку.",
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
    // На macOS шагов три: ярлыков и записи в «Приложениях» там нет — и в
    // расписке их нет тоже, а не «пропущено».
    let base_steps = if cfg!(windows) { 5 } else { 3 };
    let total = if s.wants_service() || service_before != "absent" { base_steps + 1 } else { base_steps };
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
    // macOS: карантин Gatekeeper снимаем со всей папки сразу после копирования,
    // до первого запуска. Отдельного шага нет — строка примечания уезжает в
    // install.log вместе с распиской; отказ не роняет установку (файлы уже на
    // месте), но и не молчит.
    #[cfg(not(windows))]
    let files_note = match strip_quarantine(&dir) {
        Ok(()) => format!("{copied} файлов; карантин Gatekeeper снят"),
        Err(e) => format!("{copied} файлов; карантин Gatekeeper НЕ снят: {e} — сними вручную: xattr -dr com.apple.quarantine {}", dir.display()),
    };
    #[cfg(windows)]
    let files_note = format!("{copied} файлов");
    steps.push(Step { label: "Файлы программы".into(), ok: true, note: Some(files_note) });
    steps.push(Step {
        label: "Интерфейс окна".into(),
        ok: true,
        note: Some(match plan {
            StaticPlan::Fresh => "положен из поставки".to_string(),
            StaticPlan::Keep => format!("выпуск его не менял — оставлен твой ({STATIC_REL} не тронута)"),
            StaticPlan::Replace => format!("обновлён; прежняя версия лежит рядом — {STATIC_REL}.prev"),
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
    // Свой движок владельца оставлен — сказать об этом словами: молчаливое
    // «прежние решения сохранены» не отличает случай, когда решение как раз
    // могло не сохраниться.
    if let Some(runner) = merged.get("runner").and_then(|v| v.as_str()) {
        if runner.trim() != DEFAULT_RUNNER_REL {
            cfg_note = Some(match cfg_note {
                Some(n) => format!("{n}; движок оставлен твой ({runner})"),
                None => format!("движок оставлен твой ({runner})"),
            });
        }
    }
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
            // Входа в этом запуске не было. Обновление поверх: прежний вход лежит
            // в `data/relay/local_auth` и цел — журнал не должен объявлять «вход
            // не выполнен» там, где он выполнен.
            None if dir.join("data").join("relay").join("local_auth").join("auth.json").is_file() => steps.push(Step {
                label: "Подписка ChatGPT".into(),
                ok: true,
                note: Some("вход в ChatGPT уже выполнен в этой установке — оставлен как есть".into()),
            }),
            None => steps.push(Step {
                label: "Подписка ChatGPT".into(),
                ok: false,
                note: Some("вход не выполнен — агент не сможет обратиться к модели, пока не войдёшь в настройках".into()),
            }),
        }
    }

    let exe = shell_exe(&dir);
    // Ярлыки и запись в «Приложениях» — Windows. На macOS программа — бандл в
    // ~/Applications, Finder и Launchpad видят его сами; шагов нет вовсе.
    #[cfg(windows)]
    {
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
    }

    let mut warning: Option<String> = None;
    let service = if s.wants_service() {
        // ⚠ «Система спросит пароль» — только когда путь действительно через
        // диалог. На раннере CI и при `HELENE_ADMIN_NOPROMPT=1` мы идём через
        // sudo без пароля (`mac_svc_noprompt_allowed`), и обещание диалога было
        // бы неправдой в тихой установке (судьи 19.09).
        tick(
            if cfg!(windows) {
                "Ставлю службу Windows (появится окно прав администратора)"
            } else if mac_svc_noprompt_allowed(
                std::env::var("GITHUB_ACTIONS").ok().as_deref(),
                std::env::var("HELENE_ADMIN_NOPROMPT").ok().as_deref(),
            ) {
                "Ставлю службу (права администратора — без диалога: так настроена эта машина)"
            } else {
                "Ставлю службу (система спросит пароль администратора)"
            },
            &mut progress,
        );
        let state = install_service(&dir);
        let note = match state.as_str() {
            "missing" => format!("в этой сборке нет службы ({})", exe_name("helene-svc")),
            "absent" => "служба не установлена: права администратора не были даны".to_string(),
            "unknown" => "служба поставлена; спросить систему о её состоянии не вышло — \
                          что происходит, видно в data/service.log"
                .to_string(),
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
        // ⚠ `service_before` снят ДО установки (пре-шаг выше), и на macOS
        // снимает он не «скрипт», а `launchctl bootout` под диалогом пароля.
        // Повторное снятие по устаревшему значению показывало владельцу ВТОРОЙ
        // диалог пароля ради демона, которого уже нет, — и «отмена» в нём
        // рисовала в расписке «служба осталась на машине» о снятой службе
        // (судьи 19.09). Спрашиваем состояние СЕЙЧАС.
        let service_now = if service_before == "absent" { "absent".to_string() } else { service_state() };
        if service_now != "absent" {
            // Галку сняли, а служба осталась бы жить от LocalSystem, и helene.json
            // при этом писал бы service:false — конфиг врал бы о состоянии машины.
            tick(
                if cfg!(windows) { "Снимаю прежнюю службу Windows" } else { "Снимаю прежнюю службу" },
                &mut progress,
            );
            match service_op("uninstall", PRODUCT, Some(&dir.join("uninstall-service.ps1"))) {
                Ok(()) => steps.push(Step { label: "Прежняя служба снята".into(), ok: true, note: None }),
                Err(e) => steps.push(Step {
                    label: "Прежняя служба".into(),
                    ok: false,
                    note: Some(format!("осталась на машине: {e}. {}", service_hand_removal())),
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
#[cfg(windows)]
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

/// Слово в одинарных кавычках для /bin/sh: апостроф внутри — `'\''`. Пути
/// в ~/Applications пробелы содержат («Helene Setup.app»), и склеивать команду
/// без кавычек нельзя.
#[cfg(not(windows))]
fn sh_quote(path: &Path) -> String {
    format!("'{}'", path.display().to_string().replace('\'', "'\\''"))
}

/// То же на macOS. Бандл установщика Unix позволяет удалить и из-под него
/// самого, но окно ещё открыто и WebKit держит свой кэш — поэтому тот же
/// отложенный хвост: `/bin/sh -c 'sleep; rm -rf …'`, отвязанный от нас.
/// Кэши WebKit и Caches обоих бандлов — аналог профилей WebView2 на Windows.
#[cfg(not(windows))]
pub fn uninstall_finish() {
    let dir = exe_dir();
    let mut parts = vec!["sleep 2".to_string()];
    if let Some(home) = home_dir() {
        for id in [AUMID, SETUP_AUMID] {
            for sub in ["Library/WebKit", "Library/Caches"] {
                parts.push(format!("rm -rf {}", sh_quote(&home.join(sub).join(id))));
            }
        }
    }
    if !KEEP_SETUP_EXE.load(Ordering::Relaxed) {
        parts.push(format!("rm -rf {}", sh_quote(&dir.join(SETUP_APP))));
        // Только пустую: если что-то не удалилось, папка остаётся с уликами.
        parts.push(format!("rmdir {} 2>/dev/null", sh_quote(&dir)));
    }
    let mut cmd = Command::new("/bin/sh");
    cmd.arg("-c")
        .arg(parts.join("; "))
        .current_dir(std::env::temp_dir())
        .stdin(std::process::Stdio::null())
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null());
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

// AppContainer — ограда Windows. На macOS ограда — seatbelt (профиль на
// процесс, без следов в системе): снимать при удалении нечего, и весь блок
// ниже остаётся за `cfg(windows)`.

/// Префиксы профилей AppContainer, которые заводит этот продукт: имя считается
/// из пути установки (`fence.container_name`), поэтому каждая новая папка и
/// каждое переименование продукта добавляли ещё один профиль — и ни один не
/// удалялся. На машине автора их накопилось двенадцать, включая `vera.shell.*`
/// от версии 0.1.0: профили пережили и снятие продукта, и его переименование.
#[cfg(windows)]
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

/// Снять права песочницы и удалить её профили — ДО удаления файлов, пока рядом
/// ещё лежат `runtime/python.exe` и `app/localharness/fence.py`.
///
/// `fence.revoke()` написан и проверен инженером харнесса, но снятие продукта
/// его не звало ни разу: профили копились, а ACE мёртвых контейнеров оставались
/// на папке `data`, которую снятие оставляет владельцу. Заодно передаём имена
/// профилей прежних поколений (`vera.shell.*`) — они уже накоплены.
/// Папку `data` обходит сам `fence.revoke` (root и root/data), поэтому
/// отдельного прохода для `--purge` не нужно.
#[cfg(windows)]
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
#[cfg(windows)]
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
            problems.push(format!("служба не снялась: {e}"));
        }
        // Верим системе, а не коду возврата: живая служба с автозапуском — та
        // самая мина, из-за которой следующая установка получала чужого агента.
        // На macOS то же самое: оставленный `app.helene.svc` поднимал бы движок
        // из удалённой папки при каждой загрузке.
        if service_state() != "absent" {
            service_left = true;
            if !problems.iter().any(|p| p.starts_with("служба")) {
                problems.push(format!(
                    "служба осталась зарегистрированной. {}",
                    service_hand_removal()
                ));
            }
        }
    }

    if !stop_running(&dir) && !locked_files(&dir).is_empty() {
        problems.push(format!("часть программы ещё работает: {}", locked_files(&dir).join(", ")));
    }

    // Песочница — ДО удаления файлов: fence.py и рантайм, которым его звать,
    // лежат в этой же папке и через минуту их не станет. Только Windows: у
    // seatbelt на macOS профилей в системе нет, снимать нечего.
    #[cfg(windows)]
    {
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
    #[cfg(windows)]
    {
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
    }
    // macOS: автозапуск — LaunchAgent, который заводит оболочка
    // (`~/Library/LaunchAgents/app.helene.desk.plist`, «Запускать при входе»).
    // Без снятия launchd при каждом входе пытался бы поднять удалённый бандл.
    // Плюс staging install.sh — распакованный архив в кэше, он больше не нужен.
    #[cfg(not(windows))]
    {
        let _ = port; // имя правила брандмауэра здесь не нужно
        macos_remove_autostart();
        if let Some(home) = home_dir() {
            // Кэш самого бандла (Library/Caches/<AUMID>) сносится выше вместе с
            // WebKit; staging install.sh живёт под своим именем, чтобы чистка
            // кэшей программы не унесла распакованный архив посреди установки.
            let _ = std::fs::remove_dir_all(home.join("Library").join("Caches").join("app.helene.install"));
        }
    }

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
        if name == SETUP_ENTRY {
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
                 ## Доснять\n\nЗапусти рядом `{SETUP_CMD} --uninstall --purge --quiet` или просто удали эту папку.\n",
                dir.display()
            ),
        );
    }
    Ok(text)
}

/// Снять LaunchAgent автозапуска: `launchctl bootout gui/<uid>/<метка>` и сам
/// plist. Отказы глотаем: агента могло не быть вовсе — это не препятствие.
#[cfg(not(windows))]
fn macos_remove_autostart() {
    let mut who = Command::new("id");
    who.arg("-u");
    if let Ok(out) = run_hidden_for(&mut who, std::time::Duration::from_secs(5)) {
        let uid = console_text(&out.stdout);
        if !uid.is_empty() {
            let mut cmd = Command::new("launchctl");
            cmd.args(["bootout", &format!("gui/{uid}/{AUMID}")]);
            let _ = run_hidden_for(&mut cmd, std::time::Duration::from_secs(15));
        }
    }
    if let Some(home) = home_dir() {
        let _ = std::fs::remove_file(
            home.join("Library").join("LaunchAgents").join(format!("{AUMID}.plist")),
        );
    }
}

/// Окно с сообщением без Tauri — для безоконных путей (`--install`, `--export`).
/// На macOS это `osascript` с `display dialog`: кавычки и обратные косые в
/// тексте экранируются по правилам AppleScript, иначе путь с кавычкой рвал бы
/// скрипт. Отказ osascript глотаем: сообщать о нём уже некому.
#[cfg(not(windows))]
pub fn message_box(text: &str) {
    let esc = |s: &str| s.replace('\\', "\\\\").replace('"', "\\\"");
    let script = format!(
        "display dialog \"{}\" with title \"{}\" buttons {{\"OK\"}} default button 1",
        esc(text),
        esc(PRODUCT_UI)
    );
    let mut cmd = Command::new("osascript");
    cmd.args(["-e", &script]);
    let _ = run_hidden_for(&mut cmd, std::time::Duration::from_secs(600));
}

/// Открыть установленную программу: бандл — через `open`, он же поднимает её
/// на передний план сам, держать процесс ради этого не нужно (Windows держит
/// установщик десять секунд — там передний план отдают только живому родителю).
#[cfg(not(windows))]
pub fn launch_installed(app: &Path) -> Result<(), String> {
    if !app.exists() {
        return Err(format!("нет {}", app.display()));
    }
    let mut cmd = Command::new("open");
    cmd.arg(app);
    let out = run_hidden_for(&mut cmd, std::time::Duration::from_secs(30))?;
    if out.status.success() {
        Ok(())
    } else {
        Err(console_text(&out.stderr))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// ⚠ «Блин, он мне ставить собрался, а не обновлять» (20.09.2026). Решения
    /// владельца читаются из его же установки: имена, конституция, модель,
    /// режим, служба, «Управление компьютером». Правила — те же, что у
    /// `install.sh` на macOS.
    #[test]
    fn decisions_come_from_the_installed_copy() {
        let cfg = serde_json::json!({
            "agent": {"name": "Мира"},
            "owner": {"name": "Егор"},
            "agent_mode": "sandbox",
            "model": {"framework": "anthropic", "base_url": "https://api.z.ai/api/anthropic",
                      "model": "glm-5.3", "key": "секрет", "reasoning_effort": "low"},
            "telegram": {"bot_token": "", "owner_id": ""},
            "computer": {"enabled": true},
            "service": {"firewall": true, "session0": false},
            "installed": {"service": false, "version": "0.8.2"}
        });
        let s = setup_from_installed(&cfg, "конституция", r"C:\Helene").expect("решения есть");
        assert_eq!((s.agent.as_str(), s.owner.as_str()), ("Мира", "Егор"));
        assert_eq!(s.constitution, "конституция");
        assert!(s.accepted);
        assert_eq!(s.provider, "anthropic");
        assert_eq!(s.chatgpt_model, "");
        assert_eq!(s.reasoning_effort, "low");
        assert_eq!((s.api.model.as_str(), s.api.key.as_str()), ("glm-5.3", "секрет"));
        assert_eq!(s.anthropic.base_url, "https://api.z.ai/api/anthropic");
        assert_eq!(s.local.model, "glm-5.3");
        assert_eq!(s.agent_mode, "sandbox");
        assert!(s.computer, "«Управление компьютером» было включено — обновление не гасит его");
        assert!(!s.service, "службы не было — обновление её не ставит");
        assert!(s.firewall, "решение о брандмауэре — владельца");
        assert!(!s.session0);
        assert_eq!(s.dir, r"C:\Helene");
    }

    /// Провайдер узнаётся тем же порядком, что в `install.sh`; служба и режим
    /// не выдумываются; пустой режим — песочница.
    #[test]
    fn provider_and_service_follow_the_config() {
        let with = |patch: serde_json::Value| -> Setup {
            let mut cfg = serde_json::json!({"agent": {"name": "А"}, "owner": {"name": "Б"}});
            for (k, v) in patch.as_object().unwrap() {
                cfg[k] = v.clone();
            }
            setup_from_installed(&cfg, "с", "d").expect("решения есть")
        };
        assert_eq!(with(serde_json::json!({"relay": {"enabled": true}})).provider, "chatgpt");
        assert_eq!(with(serde_json::json!({"model": {"key": "sk-frame-x"}})).provider, "chatgpt");
        assert_eq!(with(serde_json::json!({"model": {"key": "local"}})).provider, "local");
        assert_eq!(with(serde_json::json!({"model": {"key": "sk-real"}})).provider, "api");
        // Фреймворк сильнее реле: у неё anthropic-адрес и ключ реле одновременно.
        assert_eq!(
            with(serde_json::json!({"model": {"framework": "anthropic"}, "relay": {"enabled": true}})).provider,
            "anthropic"
        );
        // Режим не назван — песочница, а не пустая строка (её мастер не поймёт).
        assert_eq!(with(serde_json::json!({})).agent_mode, "sandbox");
        // Служба стояла — обновление ставит её обратно.
        assert!(with(serde_json::json!({"installed": {"service": true}})).service);
        // Брандмауэр не назван — умолчание `default_firewall`, то есть true.
        assert!(with(serde_json::json!({})).firewall);
    }

    /// Без имён или без конституции решать нечего: зовущий откроет визард, а
    /// умолчания поверх выбора владельца не поедут.
    #[test]
    fn no_decisions_without_names_or_constitution() {
        let full = serde_json::json!({"agent": {"name": "А"}, "owner": {"name": "Б"}});
        assert!(setup_from_installed(&full, "с", "d").is_some());
        assert!(setup_from_installed(&full, "   \n", "d").is_none(), "пустая конституция");
        assert!(setup_from_installed(&serde_json::json!({"owner": {"name": "Б"}}), "с", "d").is_none());
        assert!(setup_from_installed(&serde_json::json!({"agent": {"name": "А"}}), "с", "d").is_none());
        assert!(setup_from_installed(&serde_json::json!({}), "с", "d").is_none());
        // Имя из пробелов — то же, что отсутствие имени.
        let blank = serde_json::json!({"agent": {"name": "  "}, "owner": {"name": "Б"}});
        assert!(setup_from_installed(&blank, "с", "d").is_none());
    }

    /// ⚠⚠ Обновление возвращало штатный движок поверх своего.
    ///
    /// Живой случай 20.09.2026 (Mac, Сергей): его агент жил на собственном
    /// `data/extensions/runner.py`, а установка 0.8.3 поверх 0.8.1 вернула
    /// штатный путь — при том что документы обещают слияние конфига. Ключ
    /// `runner` был в списке визарда и перезаписывался безусловно.
    #[test]
    fn a_custom_runner_survives_the_update() {
        let here = |_: &str| true;
        let gone = |_: &str| false;
        // Свой движок на месте — остаётся.
        assert_eq!(
            keep_own_runner(Some("data/extensions/runner.py"), DEFAULT_RUNNER_REL, here).as_deref(),
            Some("data/extensions/runner.py")
        );
        // Свой движок исчез — чиним штатным, иначе установка останется без движка.
        assert_eq!(keep_own_runner(Some("data/extensions/runner.py"), DEFAULT_RUNNER_REL, gone), None);
        // Штатный, пустой и пробельный — решать нечего.
        assert_eq!(keep_own_runner(Some(DEFAULT_RUNNER_REL), DEFAULT_RUNNER_REL, here), None);
        assert_eq!(keep_own_runner(Some("  "), DEFAULT_RUNNER_REL, here), None);
        assert_eq!(keep_own_runner(None, DEFAULT_RUNNER_REL, here), None);
        // Тот же путь с лишними пробелами — не «чужой».
        assert_eq!(keep_own_runner(Some(" app/localharness/runner.py "), DEFAULT_RUNNER_REL, here), None);

        // И то же через сам merge_config: свой путь целого конфига переживает.
        let s = setup_for("api");
        let existing = serde_json::json!({
            "runner": "data/extensions/runner.py",
            "port": 9999,
            "sandbox": {"network": false}
        });
        let fresh = serde_json::json!({
            "runner": DEFAULT_RUNNER_REL,
            "python": PYTHON_REL,
            "agent": {"name": "А"}
        });
        let merged = merge_config(Some(existing), fresh, &s);
        // Своего движка в поставке нет, поэтому здесь путь чинится штатным:
        // проверяем, что решение приняла именно проверка существования файла.
        assert_eq!(merged["runner"], DEFAULT_RUNNER_REL);
        // Всё прочее владельца на месте.
        assert_eq!(merged["port"], 9999);
        assert_eq!(merged["sandbox"]["network"], false);
    }

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
            force_extensions: false,
            firewall: default_firewall(),
            computer: false,
            dir: String::new(),
        }
    }

    /// Тело руки `computer`: выключено по умолчанию, блок в конфиге есть
    /// всегда (с портом и всеми четырьмя правами), включение — только словом
    /// визарда; переустановка не стирает ни сужённые права, ни порт владельца.
    // Тело есть на Windows и macOS; где его нет, `wants_computer` отвечает «нет»
    // по построению (свой стенд the_service_and_body_follow_the_platform).
    #[test]
    #[cfg(any(windows, target_os = "macos"))]
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

    /// Оболочка в поставке — как её узнаёт эта система: exe рядом или бандл.
    const SHELL_FILE: &str = if cfg!(windows) { "helene.exe" } else { "Helene.app/Contents/MacOS/helene" };

    fn payload_with(root: &Path, digest: &str, build: &str) {
        put(root, SHELL_FILE, "exe");
        put(root, "app/static/index.html", "<html>new</html>");
        put(root, "app/static/assets/a.js", "new js");
        put(root, &format!("app/static/{STATIC_MANIFEST}"), &format!("{{\"v\":1,\"digest\":\"{digest}\",\"files\":{{}}}}"));
        put(root, "app/deskapp.py", "py");
        put(root, PYTHON_REL, "py-exe");
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
        put(&payload, PYTHON_REL, "py-exe-2");
        put(&payload, "app/deskapp.py", "py-2");
        copy_dir_skip(&payload, &dir, &SKIP_FROM_PAYLOAD, &["runtime"]).unwrap();
        assert_eq!(std::fs::read_to_string(python_exe(&dir)).unwrap(), "py-exe");
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
        // Система, под которую собран установщик, объявлена в контракте: по ней
        // окно и визард прячут то, чего здесь нет. `defaults()` отдаёт её тем же словом.
        let platforms = c["platforms"].as_array().expect("contract.json: platforms");
        assert!(platforms.iter().any(|p| p == std::env::consts::OS), "{platforms:?} без {}", std::env::consts::OS);
        assert_eq!(defaults().platform, std::env::consts::OS);
    }

    /// Порт на macOS: пути к оболочке, питону и реле — свои на каждой системе,
    /// и конфиг зовёт питон тем же путём, каким его ищет установщик.
    #[test]
    fn platform_paths_agree_with_config() {
        let root = Path::new("R");
        assert_eq!(config_json(&setup_for("api"), None, RELAY_PORT)["python"], PYTHON_REL);
        assert!(python_exe(root).ends_with(PYTHON_REL.split('/').collect::<PathBuf>()));
        assert_eq!(relay_exe(root), root.join(RELAY_NAME));
        assert_eq!(shell_exe(root), root.join(PAYLOAD_MARKERS[0]));
        assert_eq!(PAYLOAD_MARKERS[0].ends_with(".exe"), cfg!(windows));
        assert_eq!(RELAY_NAME.ends_with(".exe"), cfg!(windows));
    }

    /// Служба и тело есть на Windows и на macOS (порт 19.09: тело —
    /// Accessibility, служба — демон launchd); нулевой сессии на Mac нет как
    /// механизма — демон и так идёт от имени владельца. Где чего нет, решение
    /// из JSON (тихое обновление везёт прежние) в конфиг не проходит:
    /// `installed.service`, `service.session0` и `computer.enabled` остаются
    /// false, а не обещают то, чего нет.
    #[test]
    fn the_service_and_body_follow_the_platform() {
        let here = cfg!(any(windows, target_os = "macos"));
        let mut s = setup_for("api");
        s.service = true;
        s.session0 = true;
        s.computer = true;
        assert_eq!(s.wants_service(), here);
        assert_eq!(s.wants_session0(), cfg!(windows), "нулевая сессия — только Windows");
        assert_eq!(s.wants_computer(), here);
        let cfg = config_json(&s, None, RELAY_PORT);
        assert_eq!(cfg["installed"]["service"], here);
        assert_eq!(cfg["service"]["session0"], cfg!(windows));
        assert_eq!(cfg["computer"]["enabled"], here);
        assert_eq!(cfg["sandbox"]["enabled"], true, "ограда от системы не зависит");
        let out = merge_config(Some(serde_json::json!({ "computer": { "enabled": true } })), config_json(&s, None, RELAY_PORT), &s);
        assert_eq!(out["computer"]["enabled"], here);
        // Выключатель — слово визарда на любой системе: `false` проходит везде.
        s.computer = false;
        assert!(!s.wants_computer());
        assert_eq!(config_json(&s, None, RELAY_PORT)["computer"]["enabled"], false);
    }

    /// Совет «сними вручную» обязан быть про ТУ систему, на которой стоим:
    /// `sc delete` на Mac — совет в пустоту, а `launchctl bootout` на Windows.
    #[test]
    fn hand_removal_speaks_the_systems_own_words() {
        let said = service_hand_removal();
        if cfg!(windows) {
            assert!(said.contains("sc delete"), "{said}");
        } else {
            assert!(said.contains("launchctl bootout system/app.helene.svc"), "{said}");
            assert!(said.contains("/Library/LaunchDaemons/app.helene.svc.plist"), "{said}");
        }
    }

    /// Дата установки без внешней программы: границы года и високосный день.
    #[test]
    fn civil_date_from_epoch() {
        assert_eq!(civil_yyyymmdd(0), "19700101");
        assert_eq!(civil_yyyymmdd(951_782_400), "20000229");
        assert_eq!(civil_yyyymmdd(1_758_240_000), "20250919");
        assert_eq!(civil_yyyymmdd(1_767_225_599), "20251231");
        assert_eq!(civil_yyyymmdd(1_767_225_600), "20260101");
    }

    /// Кавычки для /bin/sh: путь с пробелом и с апострофом.
    #[test]
    #[cfg(not(windows))]
    fn sh_quote_keeps_spaces_and_apostrophes() {
        assert_eq!(sh_quote(Path::new("/Users/x/Helene Setup.app")), "'/Users/x/Helene Setup.app'");
        assert_eq!(sh_quote(Path::new("/a'b")), "'/a'\\''b'");
    }

    /// Живые процессы папки — по пути ИСПОЛНЯЕМОГО файла, не по командной
    /// строке: `sh …/install.sh`, `tail …/helene.log` и редактор с helene.json
    /// в список не попадают; свои родители тоже — кроме самой оболочки, которая
    /// зовёт мастер для обновления и обязана быть погашена.
    #[test]
    #[cfg(not(windows))]
    fn processes_are_matched_by_executable_not_by_arguments() {
        let dir = Path::new("/Users/yegor/Applications/Helene");
        let me = std::process::id();
        let ps = format!(
            "  1     0 /sbin/launchd\n\
             100     1 /Applications/Utilities/Terminal.app/Contents/MacOS/Terminal\n\
             200   100 sh\n\
             {me}   200 /Users/yegor/Applications/Helene/Helene Setup.app/Contents/MacOS/helene-setup\n\
             300     1 /Users/yegor/Applications/Helene/Helene.app/Contents/MacOS/helene\n\
             301   300 /Users/yegor/Applications/Helene/runtime/bin/python3.14\n\
             302   300 /Users/yegor/Applications/Helene/helene-relay\n\
             400   100 tail\n\
             500     1 /Applications/TextEdit.app/Contents/MacOS/TextEdit\n\
             600     1 /Users/yegor/Applications/Helene-old/helene-relay\n"
        );
        let table = parse_ps(&ps);
        assert_eq!(table.len(), 10);
        assert_eq!(table[3].comm, "/Users/yegor/Applications/Helene/Helene Setup.app/Contents/MacOS/helene-setup", "путь с пробелом цел");
        let found = procs_under_named(dir, &table);
        let pids: Vec<u32> = found.iter().map(|(p, _)| *p).collect();
        assert_eq!(pids, vec![300, 301, 302], "{found:?}");
        assert_eq!(found[0].1, "helene", "имя — базовое, для отказа владельцу");
        // Мастер запущен САМОЙ оболочкой (тихое обновление): оболочка — наш
        // родитель, но гасится всё равно, как helene.exe на Windows. А вот
        // sh/Terminal над ней (сценарий обновления) не трогаем.
        let ps2 = format!(
            "  1     0 /sbin/launchd\n\
             200     1 sh\n\
             300   200 /Users/yegor/Applications/Helene/Helene.app/Contents/MacOS/helene\n\
             {me}   300 /Users/yegor/Applications/Helene/Helene Setup.app/Contents/MacOS/helene-setup\n\
             301   300 /Users/yegor/Applications/Helene/runtime/bin/python3.14\n"
        );
        let pids: Vec<u32> = procs_under_named(dir, &parse_ps(&ps2)).iter().map(|(p, _)| *p).collect();
        assert_eq!(pids, vec![300, 301]);
        // Родитель из ТОЙ ЖЕ папки, но не оболочка (например, установщик,
        // запущенный установщиком) — щадим: он ждёт нас.
        let ps3 = format!(
            "  1     0 /sbin/launchd\n\
             250     1 /Users/yegor/Applications/Helene/runtime/bin/python3.14\n\
             {me}   250 /Users/yegor/Applications/Helene/Helene Setup.app/Contents/MacOS/helene-setup\n"
        );
        assert!(procs_under_named(dir, &parse_ps(&ps3)).is_empty());
    }

    /// Корень на macOS — вверх до паспорта сборки, не дальше пяти уровней; без
    /// паспорта (снятие уже унесло его) — над бандлом; иначе сама папка exe.
    #[test]
    #[cfg(not(windows))]
    fn root_is_found_above_the_bundle() {
        let root = temp_dir("root");
        put(&root, "helene-build.json", "{}");
        let exe_dir = root.join("Helene Setup.app").join("Contents").join("MacOS");
        std::fs::create_dir_all(&exe_dir).unwrap();
        assert_eq!(root_above(&exe_dir).unwrap(), root);
        assert_eq!(root_from_exe_dir(&exe_dir), root);
        let deep = root.join("a").join("b").join("c").join("d").join("e").join("f");
        std::fs::create_dir_all(&deep).unwrap();
        assert_eq!(root_above(&deep), None, "шесть уровней — слишком глубоко");
        assert_eq!(root_from_exe_dir(&deep), deep, "не бандл и без паспорта — сама папка");
        // Паспорта нет (после снятия с «оставить данные»): корень — над бандлом,
        // а не папка MacOS внутри него.
        std::fs::remove_file(root.join("helene-build.json")).unwrap();
        assert_eq!(root_above(&exe_dir), None);
        assert_eq!(bundle_root(&exe_dir).unwrap(), root);
        assert_eq!(root_from_exe_dir(&exe_dir), root);
        assert_eq!(bundle_root(Path::new("/x/Foo.app/Contents/Resources")), None);
        assert_eq!(bundle_root(Path::new("/x/Foo/Contents/MacOS")), None);
        let _ = std::fs::remove_dir_all(&root);
    }

    /// Копия поверх существующего файла — НОВЫЙ inode: перезаписанный на месте
    /// Mach-O, который уже исполнялся, macOS убивает при следующем запуске.
    #[test]
    #[cfg(unix)]
    fn copy_replaces_files_with_a_new_inode() {
        use std::os::unix::fs::MetadataExt;
        let src = temp_dir("ino-src");
        let dst = temp_dir("ino-dst");
        put(&src, "bin/helene", "v1");
        copy_dir_skip(&src, &dst, &[], &[]).unwrap();
        let before = std::fs::metadata(dst.join("bin/helene")).unwrap().ino();
        put(&src, "bin/helene", "v2");
        copy_dir_skip(&src, &dst, &[], &[]).unwrap();
        let after = std::fs::metadata(dst.join("bin/helene")).unwrap();
        assert_ne!(after.ino(), before, "файл переписан в тот же inode");
        assert_eq!(std::fs::read_to_string(dst.join("bin/helene")).unwrap(), "v2");
        let _ = std::fs::remove_dir_all(&src);
        let _ = std::fs::remove_dir_all(&dst);
    }

    /// Скобки в тексте конституции — не шаблон; шаблон — ровно `{{agent}}` и
    /// `{{owner}}`. Тихое обновление везёт конституцию из установленного
    /// SOUL.md, и резать его за `{{…}}` в тексте агента нельзя.
    #[test]
    fn validate_keeps_braces_that_are_not_placeholders() {
        let mut s = setup_for("api");
        s.constitution = "Шаблон записи: {{дата}} — {{что случилось}}".into();
        assert!(validate_setup(&s).is_ok());
        s.constitution = "Меня зовут {{owner}}".into();
        assert!(validate_setup(&s).is_err());
    }

    /// Символические ссылки рантайма переезжают ссылками, а не копиями цели.
    #[test]
    #[cfg(unix)]
    fn copy_keeps_symlinks() {
        let src = temp_dir("ln-src");
        let dst = temp_dir("ln-dst");
        put(&src, "runtime/bin/python3.14", "bin");
        std::os::unix::fs::symlink("python3.14", src.join("runtime/bin/python3")).unwrap();
        copy_dir_skip(&src, &dst, &[], &[]).unwrap();
        let link = dst.join("runtime/bin/python3");
        assert!(std::fs::symlink_metadata(&link).unwrap().file_type().is_symlink());
        assert_eq!(std::fs::read_link(&link).unwrap(), PathBuf::from("python3.14"));
        // Повторная копия поверх живой ссылки — не отказ.
        copy_dir_skip(&src, &dst, &[], &[]).unwrap();
        let _ = std::fs::remove_dir_all(&src);
        let _ = std::fs::remove_dir_all(&dst);
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
            assert_eq!(cfg["mode"], "local", "местожительство кода агента трогать нельзя");
            assert_eq!(cfg["sandbox"]["enabled"], sandbox);
        }
    }

    /// ⚠⚠ P0. Служба — не ограда: она ставится ПОВЕРХ любой из двух и ни одну
    /// не снимает. Пока их держали одним списком, владелец, выбравший службу с
    /// песочницей, получал `sandbox.enabled = false` — ограду снимали молча.
    // Семантика службы — Windows: на macOS wants_service отвечает «нет» по
    // построению (свой стенд the_service_is_windows_only_the_body_is_not).
    #[test]
    #[cfg(windows)]
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
    // Семантика службы — Windows: на macOS wants_service отвечает «нет» по
    // построению (свой стенд the_service_is_windows_only_the_body_is_not).
    #[test]
    #[cfg(windows)]
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
    // Семантика службы — Windows: на macOS wants_service отвечает «нет» по
    // построению (свой стенд the_service_is_windows_only_the_body_is_not).
    #[test]
    #[cfg(windows)]
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
    // Семантика службы — Windows: на macOS wants_service отвечает «нет» по
    // построению (свой стенд the_service_is_windows_only_the_body_is_not).
    #[test]
    #[cfg(windows)]
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
    // Семантика службы — Windows: на macOS wants_service отвечает «нет» по
    // построению (свой стенд the_service_is_windows_only_the_body_is_not).
    #[test]
    #[cfg(windows)]
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
    #[cfg(windows)]
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
    #[cfg(windows)]
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

    /// Вывод консоли читается в кодовой странице СИСТЕМЫ, а не в вшитой cp866.
    ///
    /// ⚠ ЖИВОЙ СЛУЧАЙ 17.09. Таблица cp866 была вшита в код всех трёх программ.
    /// На русской Windows она права; на английской, немецкой и китайской — нет,
    /// и человек видел кириллическую абракадабру вместо причины, по которой не
    /// встала служба. Стенд спрашивает страницы ПОИМЁННО: иначе он зеленел бы
    /// на машине собирающего и краснел у того, для кого всё это чинилось.
    #[test]
    #[cfg(windows)]
    fn console_text_reads_the_page_the_system_speaks() {
        // cp866, русская Windows: «рядом нет».
        let ru = [0xe0, 0xef, 0xa4, 0xae, 0xac, 0x20, 0xad, 0xa5, 0xe2];
        assert_eq!(decode_codepage(&ru, 866), "рядом нет");
        // cp437, английская Windows: те же байты — совсем другой текст, и
        // именно его человек с такой системой обязан увидеть.
        assert_ne!(decode_codepage(&ru, 437), "рядом нет");
        // cp1252, немецкая: «Dienst gestört» — кириллицы здесь нет вовсе.
        let de = [0x44, 0x69, 0x65, 0x6e, 0x73, 0x74, 0x20, 0x67, 0x65, 0x73, 0x74, 0xf6, 0x72, 0x74];
        assert_eq!(decode_codepage(&de, 1252), "Dienst gestört");
        // UTF-8 и ASCII проходят до всякой кодовой страницы.
        assert_eq!(console_text("plain ascii".as_bytes()), "plain ascii");
        assert_eq!(console_text("ошибка службы".as_bytes()), "ошибка службы");
        // Хвост перевода строки от sc.exe в подпись человеку не едет.
        assert_eq!(console_text(b"FAILED 1053\r\n"), "FAILED 1053");
    }

    #[test]
    fn inside_or_same_catches_nesting() {
        // Разделитель — этой системы: `\` на Windows, `/` на macOS.
        let p = |s: &str| s.replace('/', &SEP.to_string());
        assert!(inside_or_same(&p("c:/a/b"), &p("c:/a")));
        assert!(inside_or_same(&p("c:/a"), &p("c:/a")));
        assert!(!inside_or_same(&p("c:/ab"), &p("c:/a")));
        assert!(!inside_or_same(&p("c:/a"), &p("c:/a/b")));
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
