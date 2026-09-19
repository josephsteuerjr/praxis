//! `helene-svc daemon` — служба без входа в систему на macOS (launchd).
//!
//! ЗАЧЕМ. На Windows окно и служба — два супервизора одной пары детей (канал и
//! движок). На macOS до 0.8.0 супервизор был один — окно, и агент умирал вместе
//! с ним: Telegram и телефон молчали, пока владелец не откроет Hélène. Демон
//! launchd — вторая голова: та же пара детей, те же аргументы и та же среда,
//! только запускает её launchd, а не человек.
//!
//! ЧТО ЭТОТ РЕЖИМ ДАЁТ: агент живёт без входа в систему и переживает выход из
//! учётной записи; упавший демон поднимает launchd (`KeepAlive`), упавших детей
//! — сам демон, с растущей паузой.
//!
//! ЧЕГО НЕ ДАЁТ, и врать об этом нельзя: окон, экрана и мыши у процесса вне
//! графической сессии нет — WindowServer его не видит, TCC ему ничего не
//! выдаст. Поэтому под демоном движок поднимает только МОСТ тела, а само тело
//! поднимает окно, когда его откроют (`localharness/body.py`, ветка
//! `HELENE_SERVICE=1`). При включённом FileVault до первого входа в систему
//! после перезагрузки диск заперт, и не идёт ничего — включая демон.
//!
//! ЧЕГО ЗДЕСЬ НЕТ ПО ПОСТРОЕНИЮ: сервис-контрола (launchd сам держит процесс и
//! сам перезапускает), задачи планировщика (демон уже идёт от имени владельца —
//! выходить из нулевой сессии неоткуда и незачем), выравнивания прав папки
//! (это `icacls` и модель Windows) и трубы брокера (на macOS брокер — сама
//! оболочка: `shell::mac_broker_run`, системный диалог пароля).
//!
//! CLI:
//!   helene-svc daemon --config <корень>/helene.json   (зовёт launchd)
//!   helene-svc plist  --config <корень>/helene.json   (печатает описание демона)

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

use super::{arg_after, load_plan, plan_usable, Log};

// Метка демона, путь к описанию, сборка plist и строки launchctl — общие с
// оболочкой и мастером: расхождение здесь означало бы «поставили не тот демон».
include!("../../common/mac_service.rs");

/// Просьба остановиться от launchd (SIGTERM) или от человека (Ctrl+C).
/// `static`, а не канал: обработчик сигнала — это код, который исполняется
/// между инструкциями, и всё, что ему можно, — записать флаг.
static STOP: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_sig: i32) {
    STOP.store(true, Ordering::Relaxed);
}

/// SIGTERM — то, чем launchd просит демон уйти (и чем `launchctl bootout`
/// снимает его). Без обработчика процесс умирал бы на месте, а дети — канал,
/// движок, реле и всё, что они завели, — оставались сиротами на портах.
/// SIGINT — тот же путь для запуска руками.
fn catch_signals() {
    unsafe {
        // Через `*const ()`: прямой перевод функции в число компилятор
        // справедливо считает подозрительным (`function_casts_as_integer`).
        libc::signal(libc::SIGTERM, on_signal as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, on_signal as *const () as libc::sighandler_t);
        // Ребёнок закрыл свой конец журнала — не повод умирать всему демону.
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
}

/// Кто владелец установки — для `UserName` в описании демона. Сначала `USER`
/// (мастер и окно зовут `plist` от имени владельца), потом `id -un`.
/// Пусто — честный отказ: демон с чужим `UserName` поднимал бы агента не тому
/// человеку, а пустой `UserName` launchd читает как root.
fn owner_name() -> Result<String, String> {
    if let Ok(user) = std::env::var("USER") {
        if !user.trim().is_empty() {
            return Ok(user.trim().to_string());
        }
    }
    let out = std::process::Command::new("/usr/bin/id")
        .arg("-un")
        .output()
        .map_err(|e| format!("не спросить имя владельца: {e}"))?;
    let name = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if name.is_empty() {
        return Err("не понял, от чьего имени ставить демон: ни USER, ни `id -un`".into());
    }
    Ok(name)
}

/// Домашняя папка владельца — для `HOME` в среде демона. У процесса launchd её
/// нет вовсе, а рантайм, git и сам движок пишут в `~`: без этой строки они
/// уходили бы в `/var/empty`.
fn owner_home() -> Result<String, String> {
    match std::env::var("HOME") {
        Ok(home) if !home.trim().is_empty() => Ok(home),
        _ => Err("не понял, где дом владельца: переменной HOME нет".into()),
    }
}

/// Где живёт дерево данных по этому конфигу. План может не читаться вовсе
/// (битый helene.json) — тогда `data` рядом с конфигом: ровно туда же смотрит
/// `early_line`, и журнал не расходится.
fn tree_of(config: &Path) -> PathBuf {
    match load_plan(config) {
        Ok(plan) => plan.tree,
        Err(_) => config
            .parent()
            .map(|d| d.join("data"))
            .unwrap_or_else(|| PathBuf::from("data")),
    }
}

/// Сам бинарь: тот, что сейчас исполняется. Демон обязан звать ИМЕННО его —
/// путь в описании переживает и обновление (файл заменяется на месте), и
/// переименование папки не переживает, и это честно: launchd тогда скажет
/// «нет программы», а не поднимет чужую.
fn own_exe(root: &Path) -> PathBuf {
    match std::env::current_exe() {
        Ok(exe) => exe,
        Err(_) => root.join("helene-svc"),
    }
}

/// Описание демона для этой установки.
fn plist_for(config: &Path) -> Result<String, String> {
    let root = config
        .parent()
        .ok_or_else(|| "не понял, где корень установки".to_string())?
        .to_path_buf();
    let user = owner_name()?;
    let home = owner_home()?;
    let log = tree_of(config).join("service.log");
    Ok(mac_svc_plist(&own_exe(&root), config, &root, &user, &home, &log))
}

/// Демон: супервизор пары детей под launchd.
fn run(config: &Path) -> i32 {
    // Детям это скажет, что их поднял демон: движок тогда не поднимает тело
    // (графической сессии нет), а кладёт токен устройства для окна. Обычно
    // переменная приезжает из описания демона; ставим и здесь — запуск руками
    // обязан вести себя так же, а не «почти так же».
    std::env::set_var("HELENE_SERVICE", "1");
    // launchd уже направил stdout и stderr демона в тот же `data/service.log`,
    // куда пишет журнал (`StandardOutPath` в описании). Без этого флага каждая
    // строка ложилась бы в файл дважды — своей записью и эхом stderr.
    super::CONSOLE_GONE.store(true, Ordering::Relaxed);
    catch_signals();
    let stop = Arc::new(AtomicBool::new(false));

    // План может не читаться: битый helene.json, mode=remote, снесённый питон.
    // Выходить с ошибкой нельзя — `KeepAlive` поднимал бы демон каждые десять
    // секунд вечно, и журнал состоял бы из одной строки. Ждём и перечитываем:
    // владелец правит файл, и демон подхватывает правку сам.
    let mut log = Log::open(&tree_of(config));
    let plan = loop {
        if STOP.load(Ordering::Relaxed) {
            return 0;
        }
        match load_plan(config).and_then(|p| plan_usable(&p).map(|_| p)) {
            Ok(plan) => break plan,
            Err(why) => {
                log.line(&format!(
                    "демон: работать пока не с чем — {why}. Перечитаю {} через 30 с",
                    config.display()
                ));
                for _ in 0..30 {
                    if STOP.load(Ordering::Relaxed) {
                        return 0;
                    }
                    std::thread::sleep(Duration::from_secs(1));
                }
            }
        }
    };

    let mut log = Log::open(&plan.tree);
    log.line(&format!(
        "демон launchd поднят: конфиг {} · дерево {} · владелец {} · окна и экрана у него нет \
         (тело поднимет окно Helene, когда его откроют)",
        plan.config.display(),
        plan.tree.display(),
        owner_name().unwrap_or_else(|_| "?".into()),
    ));

    // Мост между сигналом и супервизором: сам `supervise` спрашивает только
    // свой флаг, а обработчик сигнала умеет трогать только статический.
    let stop_watch = stop.clone();
    std::thread::spawn(move || loop {
        if STOP.load(Ordering::Relaxed) {
            stop_watch.store(true, Ordering::Relaxed);
            return;
        }
        std::thread::sleep(Duration::from_millis(200));
    });

    // `firewall = false`: брандмауэр macOS спрашивает сам, у окна, и правил
    // из-под демона никто не ставит — на Windows это работа службы, здесь её
    // нет. Обещать обратное значило бы писать в журнал отказ, который владельцу
    // нечем починить.
    super::supervise(&plan, stop, &mut log, false, &mut || {});
    0
}

/// Разбор командной строки на macOS. Два режима и ни одного Win32.
pub fn main() {
    let mode = std::env::args().nth(1).unwrap_or_default();
    match mode.as_str() {
        "daemon" | "plist" => {}
        _ => {
            eprintln!(
                "helene-svc daemon|plist --config <корень>/helene.json\n\
                 \n\
                 daemon — супервизор канала, движка и реле под launchd (зовёт сам launchd);\n\
                 plist  — описание демона {MAC_SVC_LABEL} для {MAC_SVC_PLIST}.\n\
                 \n\
                 Поставить и снять демон умеют окно (Настройки → «Служба») и мастер:\n\
                 файл кладётся под администратором, launchctl bootstrap/bootout system."
            );
            std::process::exit(2);
        }
    }
    let Some(raw) = arg_after("--config") else {
        eprintln!("{mode} --config <путь к helene.json>");
        std::process::exit(2);
    };
    let config = PathBuf::from(raw);
    if mode == "plist" {
        match plist_for(&config) {
            Ok(text) => print!("{text}"),
            Err(why) => {
                eprintln!("описание демона не собралось: {why}");
                std::process::exit(1);
            }
        }
        return;
    }
    std::process::exit(run(&config));
}

#[cfg(test)]
mod daemon_tests {
    use super::*;

    #[test]
    fn tree_falls_back_to_data_next_to_config() {
        // Битого конфига здесь нет вовсе — файла нет: журнал всё равно обязан
        // лечь рядом с конфигом, а не в текущую папку launchd (`/`).
        let tree = tree_of(Path::new("/nowhere/Helene/helene.json"));
        assert_eq!(tree, PathBuf::from("/nowhere/Helene/data"));
    }

    #[test]
    fn own_exe_falls_back_to_the_root() {
        // current_exe у стенда есть всегда; проверяем, что запасной путь —
        // бинарь в корне поставки, а не пустое имя.
        let root = Path::new("/Users/x/Applications/Helene");
        let exe = own_exe(root);
        assert!(exe.is_absolute(), "{exe:?}");
    }
}
