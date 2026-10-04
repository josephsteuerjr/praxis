// Служба Hélène на Linux (порт 28.09) — один рецепт на все головы: сама служба (`svc`,
// режимы `daemon`, `home`, `unit`, `service`, `service-root`), окно (кнопки карточки
// «Режим», каким бы движком оно ни рисовалось) и сборка пакета (`build_linux.py` кладёт в
// .deb тот же текст юнита, что печатает `helene-svc unit`). Включается через `include!`,
// поэтому имена с приставкой `linux_svc_`.
//
// УСТРОЙСТВО. Пакет ставит программу в /opt/helene (root, только чтение) и ШАБЛОН юнита
// `helene@.service`. Экземпляр шаблона — владелец: `helene@egor.service` поднимает агента
// Егора от ЕГО имени (`User=%i`), без входа в систему, и переживает выход из сеанса. Файл
// юнита принадлежит пакету, а не пишется в рантайме — поэтому гонки «подменили описание
// между записью и паролем» (Mac, судьи 19.09) здесь нет по построению: поставить службу —
// это только `systemctl enable --now` существующего шаблона.
//
// Дом владельца — `~/.local/share/helene`: `helene.json`, `data/` и ССЫЛКИ на части
// программы в /opt/helene (`runtime`, `app`, `tree`, `helene-relay`…). Так весь код, что
// ищет реле, питон и тело рядом с конфигом (служба, окно, движок), работает без переделок,
// а обновление пакета подменяет программу под ссылками на месте.
//
// ⚠ ЧТО СЛУЖБА ДАЁТ И ЧЕГО НЕТ (одними словами везде): агент живёт без входа в систему,
// Telegram и телефон отвечают, когда окна нет. Экрана, окон и мыши у службы нет — процесс
// вне графической сессии не видит ни X-сервер, ни шину доступности. Тело поднимает окно,
// когда его откроют; служба держит для него мост (как на Mac).

/// Имя шаблона юнита и путь, куда его кладёт пакет. `/lib/systemd/system` — путь пакетов
/// Debian (на системах с объединённым /usr это тот же каталог, что /usr/lib/systemd/system).
#[allow(dead_code)]
const LINUX_SVC_TEMPLATE: &str = "helene@.service";
#[allow(dead_code)]
const LINUX_SVC_UNIT_PATH: &str = "/lib/systemd/system/helene@.service";
/// Корень программы из пакета. Юнит и политика polkit называют бинарь ИМЕННО по этому пути.
#[allow(dead_code)]
const LINUX_SVC_PROGRAM_ROOT: &str = "/opt/helene";
/// Дом владельца относительно его домашней папки. Не `$XDG_DATA_HOME`: служба стартует без
/// сеанса и переменных сеанса не видит, а дом у окна и у службы обязан быть одним.
#[allow(dead_code)]
const LINUX_SVC_HOME_REL: &str = ".local/share/helene";
/// Действие polkit из пакета (`/usr/share/polkit-1/actions/app.helene.policy`).
#[allow(dead_code)]
const LINUX_SVC_POLKIT_ACTION: &str = "app.helene.service";
/// Что в корне программы — ссылками в доме владельца. `helene.json` и `data/` — НЕ
/// ссылки: это его, и пакет их не трогает.
#[allow(dead_code)]
const LINUX_SVC_LINKED: &[&str] = &[
    "runtime", "app", "tree", "server", "licenses",
    "helene-relay", "helene-svc", "helene-body", "helene-bridge",
    "helene-build.json", "ПЕРВЫЙ-ЗАПУСК.md", "ОБНОВЛЕНИЕ.md", "КАК-УСТРОЕН-HELENE.md",
    "РАСШИРЕНИЯ.md", "ЛИЦЕНЗИЯ.md", "ЛИЦЕНЗИИ-ТРЕТЬИХ-СТОРОН.md", "NOTICE", "requirements.txt",
];

/// Текст шаблона юнита. Чистая функция — её держит стенд и её же кладёт в пакет сборка.
///
/// * `User=%i` — агент идёт от имени владельца, не root (тот же выбор, что `UserName` на
///   Mac и сессия владельца на Windows); systemd сам ставит ему HOME, USER, SHELL;
/// * `--owner %i` — служба сверяет имя с тем, от чьего имени её подняли, и находит дом;
/// * `HELENE_SERVICE=1` — движок узнаёт, что поднят службой: тело не поднимает, только мост;
/// * `Restart=always` — упавшую службу поднимает systemd; упавших детей — сама служба;
/// * вывод — в журнал systemd (`journalctl -u helene@<имя>`) и в `data/service.log`.
#[allow(dead_code)]
fn linux_svc_unit_text(program_root: &str) -> String {
    format!(
        "[Unit]\n\
         Description=Hélène — агент %i без входа в систему\n\
         Documentation=file://{root}/ПЕРВЫЙ-ЗАПУСК.md\n\
         After=network-online.target\n\
         Wants=network-online.target\n\
         \n\
         [Service]\n\
         Type=simple\n\
         User=%i\n\
         ExecStart={root}/helene-svc daemon --owner %i\n\
         Environment=HELENE_SERVICE=1\n\
         Restart=always\n\
         RestartSec=10\n\
         TimeoutStopSec=40\n\
         \n\
         [Install]\n\
         WantedBy=multi-user.target\n",
        root = program_root,
    )
}

/// Имя пользователя, годное в экземпляр юнита БЕЗ экранирования: буквы, цифры, `_`, `.`,
/// `-` (Debian/adduser допускает ровно такие; `-` в имени экземпляра systemd не трогает,
/// пока его не читают через `%I`). Отказ — словами, а не молча испорченное имя.
#[allow(dead_code)]
fn linux_svc_owner_ok(user: &str, uid: Option<u32>) -> Result<(), String> {
    let name = user.trim();
    if name.is_empty() {
        return Err("не понял, от чьего имени ставить службу: имя владельца пустое".into());
    }
    if name == "root" || uid == Some(0) {
        return Err("служба ставится от имени владельца, не root: она поднимает движок и реле, и \
                    правами системы их не наделяют. Запусти от своего имени — пароль \
                    администратора система спросит сама"
            .into());
    }
    let first_ok = name.chars().next().is_some_and(|c| c.is_ascii_alphanumeric() || c == '_');
    if !first_ok
        || !name.chars().all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
        || name.len() > 64
    {
        return Err(format!(
            "имя владельца {name:?} не годится в имя службы systemd (нужны латинские буквы, \
             цифры, `_`, `-`, `.`)"
        ));
    }
    Ok(())
}

#[allow(dead_code)]
fn linux_svc_instance(user: &str) -> String {
    format!("helene@{}.service", user.trim())
}

/// Состояние службы словами: `running` | `stopped` | `absent` | `unknown` — те же четыре
/// слова, что у Mac (`mac_svc_state_words`), по ним живут карточка и стенды.
///
/// `text` — вывод `systemctl show <экземпляр> -p LoadState,ActiveState,UnitFileState`.
/// «Поставлена» = включена (`UnitFileState` enabled/enabled-runtime/linked). Шаблона нет
/// (`LoadState=not-found`) — службы нет вовсе: пакет без юнита. Спросить не вышло — это
/// `unknown`, а не выдуманное «нет».
#[allow(dead_code)]
fn linux_svc_state_words(asked: bool, code: Option<i32>, text: &str) -> &'static str {
    if !asked || code != Some(0) {
        return "unknown";
    }
    let field = |key: &str| -> String {
        text.lines()
            .find_map(|line| line.trim().strip_prefix(key).and_then(|rest| rest.strip_prefix('=')))
            .unwrap_or("")
            .trim()
            .to_string()
    };
    let load = field("LoadState");
    let active = field("ActiveState");
    let file = field("UnitFileState");
    if load.is_empty() && active.is_empty() {
        return "unknown";
    }
    if load == "not-found" {
        return "absent";
    }
    let enabled = matches!(file.as_str(), "enabled" | "enabled-runtime" | "linked" | "linked-runtime");
    let running = matches!(active.as_str(), "active" | "activating" | "reloading");
    match (enabled, running) {
        (_, true) => "running",
        (true, false) => "stopped",
        (false, false) => "absent",
    }
}

/// Беспарольный путь — только там, где человека нет (раннер CI, `HELENE_ADMIN_NOPROMPT=1`),
/// тот же закон, что у Mac (`mac_svc_noprompt_allowed`, судьи 19.09): на машине владельца
/// диалог пароля показывается ВСЕГДА, и слова «система спросит пароль» — правда.
#[allow(dead_code)]
fn linux_svc_noprompt_allowed(ci: Option<&str>, forced: Option<&str>) -> bool {
    matches!(ci, Some(v) if v.eq_ignore_ascii_case("true")) || matches!(forced, Some("1"))
}

/// Командная строка под администратором: `pkexec <программа> <аргументы>` (диалог пароля
/// polkit — подпись владельца) или `sudo -n …` там, где человека нет. Чистая функция.
#[allow(dead_code)]
fn linux_svc_admin_argv(quiet: bool, argv: &[String]) -> Vec<String> {
    let mut out = if quiet {
        vec!["/usr/bin/sudo".to_string(), "-n".to_string()]
    } else {
        vec!["/usr/bin/pkexec".to_string()]
    };
    out.extend(argv.iter().cloned());
    out
}

/// Слова по коду `pkexec`: 126 — владелец закрыл диалог или ему не дали права; 127 — нет
/// агента polkit в сеансе (нечем спросить пароль) или не прошла проверка.
#[allow(dead_code)]
fn linux_svc_pkexec_words(code: Option<i32>) -> Option<&'static str> {
    match code {
        Some(126) => Some("права администратора не были даны: диалог пароля закрыт или отказал"),
        Some(127) => Some(
            "пароль спросить нечем: в этом сеансе нет агента polkit (графического окна пароля) \
             или pkexec не прошёл проверку",
        ),
        _ => None,
    }
}

/// Сколько ждём операцию под администратором: между «Поставить» и systemctl стоит человек с
/// диалогом пароля (тот же срок, что у Mac).
#[allow(dead_code)]
const LINUX_SVC_DEADLINE_SEC: u64 = 300;

/// Выполнить под администратором. -> вывод команды или отказ словами (включая закрытый
/// диалог и истёкший срок).
#[allow(dead_code)]
#[cfg(target_os = "linux")]
fn linux_svc_run_admin(argv: &[String]) -> Result<String, String> {
    linux_svc_run_admin_for(argv, LINUX_SVC_DEADLINE_SEC)
}

#[cfg(target_os = "linux")]
fn linux_svc_run_admin_for(argv: &[String], wait_sec: u64) -> Result<String, String> {
    use std::process::{Command, Stdio};
    let ci = std::env::var("GITHUB_ACTIONS").ok();
    let forced = std::env::var("HELENE_ADMIN_NOPROMPT").ok();
    let quiet = linux_svc_noprompt_allowed(ci.as_deref(), forced.as_deref())
        && Command::new("/usr/bin/sudo")
            .args(["-n", "true"])
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false);
    let full = linux_svc_admin_argv(quiet, argv);
    if !quiet && !std::path::Path::new(&full[0]).exists() {
        return Err("нет pkexec (пакет policykit-1 / pkexec): спросить пароль администратора нечем".into());
    }
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    // Вывод — в файл, а не в канал: ребёнок, напечатавший больше буфера канала, встал бы на
    // записи, пока мы ждём его конца (та же мина, что у брокера Windows).
    let out_path = std::env::temp_dir().join(format!("helene-svc-op-{}-{stamp}.out", std::process::id()));
    let out_file = std::fs::File::create(&out_path)
        .map_err(|e| format!("не завёлся файл под вывод {}: {e}", out_path.display()))?;
    let err_file = out_file.try_clone().map_err(|e| format!("не завёлся файл под ошибки: {e}"))?;
    let mut child = Command::new(&full[0])
        .args(&full[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::from(out_file))
        .stderr(Stdio::from(err_file))
        .spawn()
        .map_err(|e| {
            let _ = std::fs::remove_file(&out_path);
            format!("не запустилось {}: {e}", full[0])
        })?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(wait_sec);
    let code = loop {
        match child.try_wait() {
            Ok(Some(status)) => break status.code(),
            Ok(None) => {}
            Err(_) => break None,
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            let _ = std::fs::remove_file(&out_path);
            return Err(format!(
                "не дождался за {wait_sec} с — подтверждение или команда не завершились"
            ));
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    };
    let said = std::fs::read_to_string(&out_path).unwrap_or_default();
    let _ = std::fs::remove_file(&out_path);
    if code == Some(0) {
        return Ok(said.trim().to_string());
    }
    if !quiet {
        if let Some(words) = linux_svc_pkexec_words(code) {
            return Err(words.to_string());
        }
    }
    let text = said.trim().to_string();
    Err(if text.is_empty() {
        match code {
            Some(c) => format!("команда ответила кодом {c}"),
            None => "команда завершилась сигналом".to_string(),
        }
    } else {
        text
    })
}

/// Спросить systemd о службе владельца. Прав не требует: `systemctl show` — чтение.
#[allow(dead_code)]
#[cfg(target_os = "linux")]
fn linux_svc_state(user: &str) -> String {
    let out = std::process::Command::new("/bin/systemctl")
        .args(["show", &linux_svc_instance(user), "-p", "LoadState,ActiveState,UnitFileState"])
        .output();
    match out {
        Ok(out) => linux_svc_state_words(true, out.status.code(), &String::from_utf8_lossy(&out.stdout))
            .to_string(),
        Err(_) => linux_svc_state_words(false, None, "").to_string(),
    }
}

#[cfg(test)]
mod linux_service_tests {
    use super::*;

    #[test]
    fn the_unit_runs_the_agent_as_its_owner_from_the_package() {
        let text = linux_svc_unit_text("/opt/helene");
        assert!(text.contains("User=%i\n"), "{text}");
        assert!(text.contains("ExecStart=/opt/helene/helene-svc daemon --owner %i\n"), "{text}");
        assert!(text.contains("Environment=HELENE_SERVICE=1\n"), "{text}");
        assert!(text.contains("Restart=always\n"), "{text}");
        assert!(text.contains("WantedBy=multi-user.target\n"), "{text}");
        assert!(!text.contains("User=root"), "{text}");
    }

    #[test]
    fn owners_are_named_plainly_and_root_is_refused_with_words() {
        assert!(linux_svc_owner_ok("egor", Some(1000)).is_ok());
        assert!(linux_svc_owner_ok("ivan.petrov", Some(1001)).is_ok());
        assert!(linux_svc_owner_ok("astra-admin", Some(1002)).is_ok());
        let root = linux_svc_owner_ok("root", Some(0)).unwrap_err();
        assert!(root.contains("не root"), "{root}");
        assert!(linux_svc_owner_ok("egor", Some(0)).is_err());
        assert!(linux_svc_owner_ok("", None).is_err());
        assert!(linux_svc_owner_ok("егор", Some(1000)).is_err());
        assert!(linux_svc_owner_ok("a b", Some(1000)).is_err());
        assert!(linux_svc_owner_ok("-x", Some(1000)).is_err());
        assert_eq!(linux_svc_instance("egor"), "helene@egor.service");
    }

    #[test]
    fn state_words_never_invent_absence() {
        let show = |load: &str, active: &str, file: &str| {
            format!("LoadState={load}\nActiveState={active}\nUnitFileState={file}\n")
        };
        assert_eq!(linux_svc_state_words(true, Some(0), &show("loaded", "active", "enabled")), "running");
        assert_eq!(linux_svc_state_words(true, Some(0), &show("loaded", "inactive", "enabled")), "stopped");
        assert_eq!(linux_svc_state_words(true, Some(0), &show("loaded", "failed", "enabled")), "stopped");
        assert_eq!(linux_svc_state_words(true, Some(0), &show("loaded", "inactive", "disabled")), "absent");
        assert_eq!(linux_svc_state_words(true, Some(0), &show("not-found", "inactive", "")), "absent");
        // Поднята руками без enable — работает, это не «нет».
        assert_eq!(linux_svc_state_words(true, Some(0), &show("loaded", "active", "disabled")), "running");
        // Спросить не вышло — не «нет».
        assert_eq!(linux_svc_state_words(false, None, ""), "unknown");
        assert_eq!(linux_svc_state_words(true, Some(1), "Failed to connect to bus"), "unknown");
        assert_eq!(linux_svc_state_words(true, Some(0), "garbage"), "unknown");
    }

    #[test]
    fn nopassword_path_is_only_for_ci_and_pkexec_codes_have_words() {
        assert!(linux_svc_noprompt_allowed(Some("true"), None));
        assert!(linux_svc_noprompt_allowed(None, Some("1")));
        assert!(!linux_svc_noprompt_allowed(None, None));
        assert!(!linux_svc_noprompt_allowed(Some("false"), Some("0")));
        let argv = vec!["/opt/helene/helene-svc".to_string(), "service-root".into()];
        assert_eq!(linux_svc_admin_argv(false, &argv)[0], "/usr/bin/pkexec");
        assert_eq!(&linux_svc_admin_argv(true, &argv)[..2], ["/usr/bin/sudo", "-n"]);
        assert!(linux_svc_pkexec_words(Some(126)).unwrap().contains("не были даны"));
        assert!(linux_svc_pkexec_words(Some(127)).unwrap().contains("polkit"));
        assert!(linux_svc_pkexec_words(Some(1)).is_none());
    }
}
