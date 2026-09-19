// Демон launchd `app.helene.svc` — один рецепт на три головы: сама служба
// (`svc`, печатает plist и работает демоном), окно (`shell`, кнопки карточки
// «Служба») и мастер (`setup`, установка и снятие программы).
//
// Почему файл общий, как `common/service_op.rs` на Windows: plist, строка
// `launchctl bootstrap` и разбор `launchctl print` — это три места, где
// расхождение стоит не «не собралось», а «демон стоит не тот» или «окно сказало
// владельцу неправду про службу». Включается через `include!`, поэтому имена
// здесь с приставкой `mac_svc_`: в файлы, которые его включают, они попадают в
// одно пространство с их собственными `sh_quote`/`applescript_quote`.
//
// ⚠ ЧТО ЭТОТ ДЕМОН ДАЁТ И ЧЕГО НЕ ДАЁТ (одними словами во всех трёх местах):
// он живёт без входа в систему и переживает выход из учётки — Telegram и
// телефон отвечают, когда окно закрыто. Окон, экрана и мыши у него нет:
// процесс вне графической сессии не видит WindowServer, и TCC ему ничего не
// выдаст. При FileVault до первого входа после перезагрузки диск заперт и не
// идёт ничто, включая демон.

/// Метка демона: имя в launchd, имя файла в /Library/LaunchDaemons и адрес
/// `system/<метка>` в `launchctl`. Меняется только вместе с установщиком —
/// демон прежней установки иначе остался бы висеть под старым именем.
const MAC_SVC_LABEL: &str = "app.helene.svc";

/// Где лежит описание. Системный домен, а не пользовательский LaunchAgent:
/// агент поднимается только при входе владельца, и «работать без входа в
/// систему» на нём не сделать вовсе — в этом вся разница.
const MAC_SVC_PLIST: &str = "/Library/LaunchDaemons/app.helene.svc.plist";

/// Знаки, которые в XML значат разметку. Путь владельца может содержать `&`
/// (`/Users/Tom & Jerry/…`), и неэкранированный он давал бы plist, который
/// launchd отвергает целиком, — то есть «поставили службу», после которого
/// ничего не поднимается.
#[allow(dead_code)]
fn mac_svc_xml(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len());
    for ch in raw.chars() {
        match ch {
            '&' => out.push_str("&amp;"),
            '<' => out.push_str("&lt;"),
            '>' => out.push_str("&gt;"),
            _ => out.push(ch),
        }
    }
    out
}

/// Аргумент для `/bin/sh` в одинарных кавычках — то же правило, что у брокера
/// (`shell::sh_quote`): внутри них sh не разворачивает ничего, а сама кавычка
/// закрывается, экранируется и открывается снова.
#[allow(dead_code)]
fn mac_svc_quote(arg: &str) -> String {
    if !arg.is_empty()
        && arg.bytes().all(|b| b.is_ascii_alphanumeric() || b"/._-+=:,@%".contains(&b))
    {
        return arg.to_string();
    }
    format!("'{}'", arg.replace('\'', "'\\''"))
}

/// Строка для AppleScript: в двойных кавычках, внутри экранируются обратный
/// слеш и сама кавычка.
#[allow(dead_code)]
fn mac_svc_osa_quote(s: &str) -> String {
    format!("\"{}\"", s.replace('\\', "\\\\").replace('"', "\\\""))
}

/// Описание демона для launchd. Чистая функция — её держит стенд, потому что
/// цена ошибки здесь не «не собралось», а «демон работает от имени root» или
/// «работает не тем бинарём».
///
/// * `UserName` — владелец установки, а не root: демон поднимает движок и реле,
///   и правами системы их не наделяют. Это ровно тот же выбор, что на Windows,
///   где харнесс живёт в сессии владельца, а не под LocalSystem.
/// * `GroupName` = `staff` — обычная группа пользователя macOS.
/// * `KeepAlive` — упавший демон поднимает launchd; свой перезапуск детей у
///   демона тоже есть, и одно другому не мешает: launchd следит за демоном,
///   демон — за парой детей.
/// * `HOME` в среде: у процесса launchd его нет вовсе, а рантайм, git и сам
///   движок пишут в `~`. Без этой строки пути уезжали бы в `/var/empty`.
/// * `HELENE_SERVICE=1` — по нему движок узнаёт, что поднят демоном: тело он
///   тогда не поднимает (графической сессии нет), только мост.
/// * Вывод — в тот же `data/service.log`, куда пишет журнал сам демон: у
///   launchd своей консоли нет, и потерянный stderr ребёнка стоил бы причины
///   падения. Записи не перемешиваются: оба конца открыты на дозапись.
///   ⚠ ПРЕДЕЛ, о котором надо знать: журнал усекается на 5 МБ переименованием
///   в `.1` (`open_rolling`), а launchd держит свой поток на СТАРОМ файле и
///   продолжает писать в него до перезапуска демона. То есть после усечения
///   stdout и stderr детей какое-то время уезжают в `service.log.1`, а не в
///   `service.log`. Лечится перезапуском службы; чинить это здесь значило бы
///   отнимать у launchd перенаправление вовсе.
#[allow(dead_code)]
fn mac_svc_plist(exe: &std::path::Path, config: &std::path::Path,
                 root: &std::path::Path, user: &str, home: &str,
                 log: &std::path::Path) -> String {
    format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>{label}</string>
	<key>UserName</key>
	<string>{user}</string>
	<key>GroupName</key>
	<string>staff</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>WorkingDirectory</key>
	<string>{root}</string>
	<key>ProgramArguments</key>
	<array>
		<string>{exe}</string>
		<string>daemon</string>
		<string>--config</string>
		<string>{config}</string>
	</array>
	<key>EnvironmentVariables</key>
	<dict>
		<key>HOME</key>
		<string>{home}</string>
		<key>HELENE_SERVICE</key>
		<string>1</string>
	</dict>
	<key>StandardOutPath</key>
	<string>{log}</string>
	<key>StandardErrorPath</key>
	<string>{log}</string>
</dict>
</plist>
"#,
        label = MAC_SVC_LABEL,
        user = mac_svc_xml(user),
        home = mac_svc_xml(home),
        root = mac_svc_xml(&root.display().to_string()),
        exe = mac_svc_xml(&exe.display().to_string()),
        config = mac_svc_xml(&config.display().to_string()),
        log = mac_svc_xml(&log.display().to_string()),
    )
}

/// Команда установки: положить описание на место, отдать его root и загрузить.
/// Исполняется правами администратора целиком — файл в /Library/LaunchDaemons
/// обязан принадлежать `root:wheel` с правами 644, иначе launchd его молча не
/// возьмёт («Service cannot load in requested session»).
///
/// `bootout` перед `bootstrap` — переустановка поверх живого демона: повторный
/// `bootstrap` на уже загруженную метку отвечает `Input/output error (5)`, и
/// без этой строки обновление выглядело бы как отказ на ровном месте. Отказ
/// самого `bootout` глотаем (`|| true`): демона могло и не быть.
#[allow(dead_code)]
fn mac_svc_install_line(tmp: &std::path::Path) -> String {
    format!(
        "/bin/launchctl bootout system/{label} 2>/dev/null || true; \
         /bin/cp {tmp} {plist} && /usr/sbin/chown root:wheel {plist} && /bin/chmod 644 {plist} && \
         /bin/launchctl bootstrap system {plist}",
        label = MAC_SVC_LABEL,
        tmp = mac_svc_quote(&tmp.display().to_string()),
        plist = mac_svc_quote(MAC_SVC_PLIST),
    )
}

/// Команда снятия: выгрузить и убрать описание. `bootout` на незагруженный
/// демон отвечает отказом — его глотаем, а приговор всё равно выносится по
/// состоянию ПОСЛЕ (`mac_svc_state`), а не по коду этой строки: так же, как
/// на Windows приговор выносит SCM, а не код скрипта.
#[allow(dead_code)]
fn mac_svc_remove_line() -> String {
    format!(
        "/bin/launchctl bootout system/{label} 2>/dev/null || true; /bin/rm -f {plist}",
        label = MAC_SVC_LABEL,
        plist = mac_svc_quote(MAC_SVC_PLIST),
    )
}

/// Скрипт для `osascript -e`: команда правами администратора с системным
/// диалогом пароля. Тот же путь, что у брокера (`shell::mac_admin_script`) —
/// своего окна пароля у продукта нет и быть не должно.
#[allow(dead_code)]
fn mac_svc_admin_script(line: &str, prompt: &str) -> String {
    format!(
        "do shell script {} with prompt {} with administrator privileges",
        mac_svc_osa_quote(line),
        mac_svc_osa_quote(prompt),
    )
}

/// Состояние демона словами: `running` | `stopped` | `absent` | `unknown`.
///
/// Чистая функция и четыре ответа, а не три: «файл демона лежит, а спросить
/// launchd не вышло» — это НЕ «службы нет». На Windows этот случай сливался с
/// «нет» и молчаливо врал владельцу (см. `shell::service_state_blocking`);
/// повторять здесь не будем.
///
/// * `plist_here` — лежит ли `/Library/LaunchDaemons/app.helene.svc.plist`;
/// * `asked` — удалось ли вообще запустить `launchctl print system/<метка>`;
/// * `code`/`text` — его код возврата и вывод.
#[allow(dead_code)]
fn mac_svc_state_words(plist_here: bool, asked: bool, code: Option<i32>, text: &str) -> &'static str {
    if !plist_here {
        // Описания нет — ставить нечего и выгружать нечего. Загруженный демон
        // без файла пережил бы только до перезагрузки, и показывать его как
        // установленную службу значило бы обещать то, чего завтра не будет.
        return "absent";
    }
    if !asked {
        return "unknown";
    }
    let low = text.to_lowercase();
    match code {
        // launchd знает эту метку. `state = running` и строка `pid = N` — он
        // её поднял; без них описание загружено, а процесса сейчас нет.
        Some(0) => {
            if low.contains("state = running") || low.contains("pid = ") {
                "running"
            } else {
                "stopped"
            }
        }
        // «Could not find service … in domain» — описание лежит, но не
        // загружено: обычное состояние после `bootout` без удаления файла.
        Some(_) if low.contains("could not find service") || low.contains("no such process") => "stopped",
        _ => "unknown",
    }
}

/// Спросить launchd о демоне. Прав не требует: `launchctl print` — чтение.
/// Не ответил или ответил незнакомо — `unknown`, а не выдуманное «нет».
#[allow(dead_code)]
#[cfg(target_os = "macos")]
fn mac_svc_state() -> String {
    let plist_here = std::path::Path::new(MAC_SVC_PLIST).exists();
    let out = std::process::Command::new("/bin/launchctl")
        .arg("print")
        .arg(format!("system/{MAC_SVC_LABEL}"))
        .output();
    match out {
        Ok(out) => {
            let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
            text.push_str(&String::from_utf8_lossy(&out.stderr));
            mac_svc_state_words(plist_here, true, out.status.code(), &text).to_string()
        }
        Err(_) => mac_svc_state_words(plist_here, false, None, "").to_string(),
    }
}

/// Сколько ждём операцию со службой. Между «Поставить» и `launchctl` стоит
/// человек с диалогом пароля, поэтому срок большой — но он ЕСТЬ: без него
/// забытый на экране диалог вешал бы поток окна (и шаг мастера) навсегда, и
/// это был бы предел, о котором никто не знает. Тот же порядок, что у `exec`
/// в брокере (`MAC_PASSWORD_GRACE_SEC` плюс сама команда).
#[allow(dead_code)]
const MAC_SVC_DEADLINE_SEC: u64 = 300;

/// Дождаться процесса с дедлайном. `None` в коде — не дождались и убили: ноль
/// здесь был бы враньём. Без канала и без потоков: `output()` мы не зовём,
/// потому что он ждёт вечно, а вывод собираем из временных файлов.
#[allow(dead_code)]
#[cfg(target_os = "macos")]
fn mac_svc_wait(child: &mut std::process::Child, limit: std::time::Duration) -> (Option<i32>, bool) {
    let deadline = std::time::Instant::now() + limit;
    loop {
        match child.try_wait() {
            Ok(Some(st)) => return (st.code(), false),
            Ok(None) => {}
            Err(_) => return (None, false),
        }
        if std::time::Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return (None, true);
        }
        std::thread::sleep(std::time::Duration::from_millis(200));
    }
}

/// Выполнить строку правами администратора. Два пути, и выбор между ними — не
/// удобство, а разные машины:
///   * `sudo -n true` проходит — администратор без пароля (так устроен раннер
///     CI и машины с настроенным sudoers): идём через `sudo`, диалога нет;
///   * иначе — системный диалог пароля через `osascript`, тот же, что у
///     брокера. Своего окна пароля у продукта нет.
///
/// Возвращает вывод команды; отказ — словами, включая отмену диалога и
/// невыбранный за `MAC_SVC_DEADLINE_SEC` пароль.
#[allow(dead_code)]
#[cfg(target_os = "macos")]
fn mac_svc_run_admin(line: &str, prompt: &str) -> Result<String, String> {
    use std::process::Stdio;
    let quiet = std::process::Command::new("/usr/bin/sudo")
        .args(["-n", "true"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    // Вывод — в файлы: канал между родителем и ребёнком это буфер на несколько
    // килобайт, и ребёнок, напечатавший больше, встал бы на записи, пока мы
    // ждём его завершения (та же мина, что у брокера службы на Windows).
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    let out_path = std::env::temp_dir().join(format!("helene-svc-op-{stamp}.out"));
    // Один файл на оба потока, вторым концом — его копия: порознь `osascript`
    // отдаёт причину отказа только в stderr, а расписке нужны оба целиком.
    let Ok(out_file) = std::fs::File::create(&out_path) else {
        return Err(format!("не завёлся файл под вывод: {}", out_path.display()));
    };
    let Ok(err_file) = out_file.try_clone() else {
        let _ = std::fs::remove_file(&out_path);
        return Err(format!("не завёлся файл под ошибки: {}", out_path.display()));
    };
    let mut cmd = if quiet {
        let mut c = std::process::Command::new("/usr/bin/sudo");
        c.args(["-n", "/bin/sh", "-c", line]);
        c
    } else {
        let mut c = std::process::Command::new("/usr/bin/osascript");
        c.arg("-e").arg(mac_svc_admin_script(line, prompt));
        c
    };
    cmd.stdin(Stdio::null())
        .stdout(Stdio::from(out_file))
        .stderr(Stdio::from(err_file));
    let said_all = |path: &std::path::Path| -> String {
        let text = std::fs::read_to_string(path).unwrap_or_default();
        let _ = std::fs::remove_file(path);
        text
    };
    let mut child = match cmd.spawn() {
        Ok(child) => child,
        Err(err) => {
            let _ = std::fs::remove_file(&out_path);
            return Err(format!("не запустилось: {err}"));
        }
    };
    let (code, killed) = mac_svc_wait(&mut child, std::time::Duration::from_secs(MAC_SVC_DEADLINE_SEC));
    let said = said_all(&out_path);
    if killed {
        return Err(format!(
            "не дождался за {MAC_SVC_DEADLINE_SEC} с — диалог пароля так и не закрыли; \n             команда под администратором, если успела начаться, могла остаться работать"
        ));
    }
    if code == Some(0) {
        return Ok(said.trim().to_string());
    }
    // «User canceled (-128)» — владелец закрыл диалог пароля: это его отказ, а
    // не поломка, и называть его «не получилось» нечестно.
    if said.contains("-128") {
        return Err("права администратора не были даны: диалог пароля закрыт".into());
    }
    let text = said.trim().to_string();
    // Код без слов — тоже ответ: молчать о нём хуже, чем сказать число.
    Err(if text.is_empty() {
        match code {
            Some(c) => format!("команда ответила кодом {c}"),
            None => "команда завершилась сигналом".to_string(),
        }
    } else {
        text
    })
}

#[cfg(test)]
mod mac_service_tests {
    use super::*;
    use std::path::Path;

    #[test]
    fn plist_names_owner_program_and_log() {
        let text = mac_svc_plist(
            Path::new("/Users/Tom & Jerry/Applications/Helene/helene-svc"),
            Path::new("/Users/Tom & Jerry/Applications/Helene/helene.json"),
            Path::new("/Users/Tom & Jerry/Applications/Helene"),
            "tom",
            "/Users/Tom & Jerry",
            Path::new("/Users/Tom & Jerry/Applications/Helene/data/service.log"),
        );
        assert!(text.contains("<string>app.helene.svc</string>"), "{text}");
        assert!(text.contains("<key>UserName</key>\n\t<string>tom</string>"), "{text}");
        assert!(text.contains("<string>staff</string>"), "{text}");
        assert!(text.contains("<key>RunAtLoad</key>\n\t<true/>"), "{text}");
        assert!(text.contains("<key>KeepAlive</key>\n\t<true/>"), "{text}");
        assert!(text.contains("<string>daemon</string>"), "{text}");
        assert!(text.contains("<string>--config</string>"), "{text}");
        assert!(text.contains("<key>HELENE_SERVICE</key>\n\t\t<string>1</string>"), "{text}");
        assert!(text.contains("data/service.log"), "{text}");
        // Амперсанд в пути владельца обязан уехать разметкой, иначе launchd
        // отвергает описание целиком.
        assert!(text.contains("Tom &amp; Jerry"), "{text}");
        assert!(!text.contains("Tom & Jerry"), "неэкранированный & в plist: {text}");
    }

    #[test]
    fn install_line_owns_and_loads() {
        let line = mac_svc_install_line(Path::new("/tmp/helene svc.plist"));
        assert!(line.contains("'/tmp/helene svc.plist'"), "{line}");
        assert!(line.contains("/usr/sbin/chown root:wheel"), "{line}");
        assert!(line.contains("/bin/chmod 644"), "{line}");
        assert!(line.contains("bootstrap system"), "{line}");
        // Переустановка поверх живого демона: сначала выгрузить.
        let boot_at = line.find("bootout").expect("нет bootout");
        assert!(boot_at < line.find("bootstrap").expect("нет bootstrap"), "{line}");
    }

    #[test]
    fn remove_line_unloads_and_deletes() {
        let line = mac_svc_remove_line();
        assert!(line.contains("bootout system/app.helene.svc"), "{line}");
        assert!(line.contains("/bin/rm -f /Library/LaunchDaemons/app.helene.svc.plist"), "{line}");
    }

    #[test]
    fn admin_script_escapes_quotes() {
        let script = mac_svc_admin_script(
            "/bin/echo \"a\\b\"",
            "Hélène: поставить службу",
        );
        assert!(script.starts_with("do shell script \""), "{script}");
        assert!(script.contains("with administrator privileges"), "{script}");
        assert!(script.contains("\\\"a\\\\b\\\""), "{script}");
    }

    #[test]
    fn state_words_never_invent_absence() {
        // Файла нет — ставить нечего.
        assert_eq!(mac_svc_state_words(false, true, Some(0), "pid = 5"), "absent");
        // Загружен и поднят.
        assert_eq!(mac_svc_state_words(true, true, Some(0), "state = running\n\tpid = 5"), "running");
        // Загружен, процесса сейчас нет.
        assert_eq!(mac_svc_state_words(true, true, Some(0), "state = not running"), "stopped");
        // Описание лежит, демон выгружен.
        assert_eq!(
            mac_svc_state_words(true, true, Some(113), "Could not find service \"app.helene.svc\""),
            "stopped"
        );
        // Спросить не вышло — это не «нет».
        assert_eq!(mac_svc_state_words(true, false, None, ""), "unknown");
        assert_eq!(mac_svc_state_words(true, true, Some(1), "Operation not permitted"), "unknown");
    }

    #[test]
    fn sh_quote_keeps_apostrophes() {
        assert_eq!(mac_svc_quote("/tmp/x"), "/tmp/x");
        assert_eq!(mac_svc_quote("/tmp/a b"), "'/tmp/a b'");
        assert_eq!(mac_svc_quote("/tmp/O'Brien"), "'/tmp/O'\\''Brien'");
    }
}
