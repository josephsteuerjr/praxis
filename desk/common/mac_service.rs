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

/// SHA-256 своими руками — шестьдесят строк вместо зависимости, которой нет у
/// двух из трёх крейтов, включающих этот файл (`setup` и `svc`). Считает то же,
/// что `shasum -a 256`, и накрыт стендом на известных векторах: расхождение
/// здесь значило бы «проверка под root никогда не сходится», то есть служба,
/// которую нельзя поставить вовсе.
#[allow(dead_code)]
fn mac_svc_sha256(data: &[u8]) -> String {
    const K: [u32; 64] = [
        0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
        0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
        0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
        0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
        0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
        0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
        0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
        0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
    ];
    let mut h: [u32; 8] = [
        0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19,
    ];
    let mut msg = data.to_vec();
    let bits = (data.len() as u64).wrapping_mul(8);
    msg.push(0x80);
    while msg.len() % 64 != 56 {
        msg.push(0);
    }
    msg.extend_from_slice(&bits.to_be_bytes());
    for chunk in msg.chunks(64) {
        let mut w = [0u32; 64];
        for (i, word) in chunk.chunks(4).enumerate() {
            w[i] = u32::from_be_bytes([word[0], word[1], word[2], word[3]]);
        }
        for i in 16..64 {
            let s0 = w[i - 15].rotate_right(7) ^ w[i - 15].rotate_right(18) ^ (w[i - 15] >> 3);
            let s1 = w[i - 2].rotate_right(17) ^ w[i - 2].rotate_right(19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16].wrapping_add(s0).wrapping_add(w[i - 7]).wrapping_add(s1);
        }
        let (mut a, mut b, mut c, mut d, mut e, mut f, mut g, mut x) =
            (h[0], h[1], h[2], h[3], h[4], h[5], h[6], h[7]);
        for i in 0..64 {
            let s1 = e.rotate_right(6) ^ e.rotate_right(11) ^ e.rotate_right(25);
            let ch = (e & f) ^ ((!e) & g);
            let t1 = x.wrapping_add(s1).wrapping_add(ch).wrapping_add(K[i]).wrapping_add(w[i]);
            let s0 = a.rotate_right(2) ^ a.rotate_right(13) ^ a.rotate_right(22);
            let maj = (a & b) ^ (a & c) ^ (b & c);
            let t2 = s0.wrapping_add(maj);
            x = g;
            g = f;
            f = e;
            e = d.wrapping_add(t1);
            d = c;
            c = b;
            b = a;
            a = t1.wrapping_add(t2);
        }
        for (slot, add) in h.iter_mut().zip([a, b, c, d, e, f, g, x]) {
            *slot = slot.wrapping_add(add);
        }
    }
    h.iter().map(|x| format!("{x:08x}")).collect()
}

/// Положить описание демона во временный файл, который НЕЛЬЗЯ подменить между
/// записью и словом владельца. Отдаёт (папка, файл, sha256 содержимого).
///
/// ⚠⚠ ЧТО ЭТО ЛЕЧИТ (находка судей 19.09). Раньше и окно, и мастер писали
/// описание в `temp_dir()/app.helene.svc.plist` — предсказуемое имя в папке,
/// куда пишет любой процесс учётки. Между записью и нажатием «Да» в диалоге
/// пароля (а это секунды, если не минуты) файл успевал подменить кто угодно, и
/// дальше ПОД ROOT исполнялось `cp` уже чужого содержимого: демон, поднимающий
/// чужой бинарь при каждом старте машины.
///
/// Две двери, и закрыты обе:
///   * папка своя, свежая, со случайным именем и правами 0700. Её заводит
///     `create_dir` — он ОТКАЗЫВАЕТ, если имя занято, значит подложить туда
///     символьную ссылку заранее нельзя; права сужаются до того, как внутри
///     что-то появится;
///   * файл открыт с `create_new` (это O_EXCL|O_CREAT) и правами 0600 — поверх
///     существующего файла или ссылки он не пишет, а отвечает отказом.
/// Третья дверь — в админ-строке (`mac_svc_install_line`): хэш содержимого
/// сверяется под root перед `cp`.
#[allow(dead_code)]
#[cfg(target_os = "macos")]
fn mac_svc_stage_plist(
    text: &[u8],
) -> Result<(std::path::PathBuf, std::path::PathBuf, String), String> {
    use std::io::{Read, Write};
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    // Случайное имя — из системного генератора. `random_hex` здесь не зовём:
    // этот файл включается и в `svc/src/daemon.rs`, где его нет в области
    // видимости, а трём копиям расходиться нельзя.
    let mut seed = [0u8; 16];
    std::fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut seed))
        .map_err(|e| format!("системный генератор молчит: {e}"))?;
    let tag: String = seed.iter().map(|b| format!("{b:02x}")).collect();
    let dir = std::env::temp_dir().join(format!("helene-svc-{tag}"));
    std::fs::create_dir(&dir)
        .map_err(|e| format!("временная папка не завелась ({}): {e}", dir.display()))?;
    if let Err(e) = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700)) {
        let _ = std::fs::remove_dir_all(&dir);
        return Err(format!("права временной папки не сузились ({}): {e}", dir.display()));
    }
    let file = dir.join("app.helene.svc.plist");
    let written = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(&file)
        .and_then(|mut f| f.write_all(text));
    if let Err(e) = written {
        let _ = std::fs::remove_dir_all(&dir);
        return Err(format!("описание демона не записалось в {}: {e}", file.display()));
    }
    Ok((dir, file, mac_svc_sha256(text)))
}

/// От чьего имени можно ставить демон. ⚠ НАХОДКА СУДЕЙ 19.09: `UserName` =
/// `root` (или пустое имя, которое launchd читает как root) означал бы демон с
/// правами СИСТЕМЫ, поднимающий движок и реле из папки, куда пишет обычный
/// пользователь. Это ровно тот путь к root из обычного хода агента, ради
/// закрытия которого на Windows сужались права папки установки.
///
/// Отказ СЛОВАМИ, а не «поставим и промолчим»: под `sudo sh install.sh` или
/// `sudo helene-svc plist` владелец получает не сломанную установку, а
/// объяснение, что делать (выйти из-под sudo).
#[allow(dead_code)]
fn mac_svc_owner_ok(user: &str, uid: Option<u32>) -> Result<(), String> {
    let name = user.trim();
    if name.is_empty() {
        return Err("не понял, от чьего имени ставить службу: имя владельца пустое, \
                    а пустое имя launchd читает как root"
            .into());
    }
    if name == "root" || uid == Some(0) {
        return Err("служба ставится от имени владельца, не root: демон поднимает движок и реле \
                    из папки, куда пишет обычный пользователь, и правами системы их не наделяют. \
                    Выйди из-под sudo и повтори — пароль система спросит сама, когда понадобится"
            .into());
    }
    Ok(())
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
///
/// ⚠ `hash` — sha256 того, что мы записали (`mac_svc_stage_plist`). Сверка идёт
/// ПОД ROOT и ДО `cp`: между записью файла и нажатием «Да» в диалоге пароля
/// проходят секунды, если не минуты, и без неё подменённое за это время
/// содержимое уехало бы в /Library/LaunchDaemons как наше. Несовпадение — отказ
/// СЛОВАМИ (код 90), а не тихо поставленный чужой демон.
#[allow(dead_code)]
fn mac_svc_install_line(tmp: &std::path::Path, hash: &str) -> String {
    format!(
        "/bin/launchctl bootout system/{label} 2>/dev/null || true; \
         if [ \"$(/usr/bin/shasum -a 256 {tmp} | /usr/bin/cut -d' ' -f1)\" != {hash} ]; then \
         echo 'описание демона подменили между записью и паролем — ничего не ставлю' >&2; \
         exit 90; fi; \
         /bin/cp {tmp} {plist} && /usr/sbin/chown root:wheel {plist} && /bin/chmod 644 {plist} && \
         /bin/launchctl bootstrap system {plist}",
        label = MAC_SVC_LABEL,
        tmp = mac_svc_quote(&tmp.display().to_string()),
        hash = mac_svc_quote(hash),
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

/// Слова о службе И О ДВИЖКЕ — разные вопросы, и склеивать их нельзя.
///
/// ⚠ НАХОДКА СУДЕЙ 19.09: `mac_svc_state` отвечает про ДЕМОН (что знает о нём
/// launchd), а владельцу и мастеру нужно знать про АГЕНТА. Демон под `KeepAlive`
/// бывает «running», пока движок в нём падает по кругу, — и карточка честно
/// говорила «Служба работает» о машине, где не отвечает никто. А «absent» при
/// живом движке на порту — это мина 8094: службы нет, порт держит чужой
/// процесс, и «поставить службу» упрётся в занятый порт без единого слова о том,
/// почему.
///
/// `engine`: `Some(true)` — канал на порту ответил нам; `Some(false)` — порт
/// молчит или отвечает не наш; `None` — спросить не вышло (порт неизвестен), и
/// тогда ничего не придумываем.
///
/// Машинные слова (`running`/`stopped`/`absent`/`unknown`) эта функция НЕ
/// трогает: по ним живут карточка, мастер и стенды.
#[allow(dead_code)]
fn mac_svc_engine_note(state: &str, engine: Option<bool>) -> &'static str {
    match (state, engine) {
        ("running", Some(true)) => "служба работает, движок отвечает",
        ("running", Some(false)) => "служба работает, движок не отвечает (перезапускается)",
        ("absent", Some(true)) => "службы нет, но порт держит другой процесс",
        ("stopped", Some(true)) => "служба поставлена и не запущена, а порт держит другой процесс",
        _ => "",
    }
}

/// Жив ли движок на порту — анонимно и не отдавая ключа. Тот же опознавательный
/// маршрут, что у окна при старте (`/api/who`, см. `shell::who_probe`): он
/// отдаёт только неcекретное. `None` — порт неизвестен или спросить не вышло.
#[allow(dead_code)]
#[cfg(target_os = "macos")]
fn mac_svc_engine_alive(port: Option<u16>) -> Option<bool> {
    let port = port?;
    let out = std::process::Command::new("/usr/bin/curl")
        .args(["-s", "-m", "2", "-o", "/dev/null", "-w", "%{http_code}"])
        .arg(format!("http://127.0.0.1:{port}/api/who"))
        .output()
        .ok()?;
    let code = String::from_utf8_lossy(&out.stdout).trim().to_string();
    // 200 — ответил наш опознавательный маршрут. 401/403 — на порту кто-то есть,
    // но это не наш канал: для владельца это тоже «порт занят», и врать «движок
    // отвечает» нельзя. 000 — не ответил никто.
    Some(code == "200")
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

/// Можно ли обойтись без диалога пароля. ⚠⚠ НАХОДКА СУДЕЙ 19.09: раньше здесь
/// стоял один `sudo -n true`, и на машине человека с настроенным sudoers (а это
/// обычная настройка у разработчиков и у всех, кто раз сделал «не спрашивать
/// пароль») служба ставилась БЕЗ ЕДИНОГО ВОПРОСА. При этом мастер и карточка
/// обещали владельцу «система спросит пароль администратора» — то есть врали, и
/// врали ровно там, где обещание было единственной подписью.
///
/// Теперь беспарольный путь — только там, где человека нет вовсе: раннер CI
/// (`GITHUB_ACTIONS=true`) и явно выставленный `HELENE_ADMIN_NOPROMPT=1`. На
/// машине владельца диалог показывается ВСЕГДА, и слова мастера снова правда.
/// Чистая функция — по ней и стенд.
#[allow(dead_code)]
fn mac_svc_noprompt_allowed(ci: Option<&str>, forced: Option<&str>) -> bool {
    matches!(ci, Some(v) if v.eq_ignore_ascii_case("true")) || matches!(forced, Some("1"))
}

/// Выполнить строку правами администратора. Два пути, и выбор между ними — не
/// удобство, а «есть ли здесь человек»:
///   * машина без человека (раннер CI, `HELENE_ADMIN_NOPROMPT=1`) и sudo без
///     пароля — идём через `sudo`, спрашивать некого;
///   * во всех остальных случаях — системный диалог пароля через `osascript`,
///     тот же, что у брокера. Своего окна пароля у продукта нет.
///
/// Возвращает вывод команды; отказ — словами, включая отмену диалога и
/// невыбранный за `MAC_SVC_DEADLINE_SEC` пароль.
#[allow(dead_code)]
#[cfg(target_os = "macos")]
fn mac_svc_run_admin(line: &str, prompt: &str) -> Result<String, String> {
    use std::process::Stdio;
    let ci = std::env::var("GITHUB_ACTIONS").ok();
    let forced = std::env::var("HELENE_ADMIN_NOPROMPT").ok();
    let quiet = mac_svc_noprompt_allowed(ci.as_deref(), forced.as_deref())
        && std::process::Command::new("/usr/bin/sudo")
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
        let line = mac_svc_install_line(Path::new("/tmp/helene svc.plist"), "abc123");
        assert!(line.contains("'/tmp/helene svc.plist'"), "{line}");
        assert!(line.contains("/usr/sbin/chown root:wheel"), "{line}");
        assert!(line.contains("/bin/chmod 644"), "{line}");
        assert!(line.contains("bootstrap system"), "{line}");
        // Переустановка поверх живого демона: сначала выгрузить.
        let boot_at = line.find("bootout").expect("нет bootout");
        assert!(boot_at < line.find("bootstrap").expect("нет bootstrap"), "{line}");
    }

    /// ⚠ Гонка на plist (судьи 19.09): в админ-строке обязан стоять хэш того,
    /// что мы записали, и сверка ДО `cp`. Без неё подмена файла между записью и
    /// «Да» в диалоге пароля ставила бы чужой демон.
    #[test]
    fn install_line_checks_the_hash_before_copying() {
        let hash = mac_svc_sha256(b"<plist/>");
        let line = mac_svc_install_line(Path::new("/tmp/helene-svc-ab/app.helene.svc.plist"), &hash);
        assert!(line.contains(&hash), "в строке нет хэша: {line}");
        assert!(line.contains("shasum -a 256"), "{line}");
        let check_at = line.find("shasum").expect("нет сверки");
        let copy_at = line.find("/bin/cp").expect("нет cp");
        assert!(check_at < copy_at, "сверка обязана стоять ДО cp: {line}");
        assert!(line.contains("подменили"), "отказ обязан быть словами: {line}");
        assert!(line.contains("exit 90"), "{line}");
    }

    /// Свой sha256 обязан совпадать с `shasum -a 256` знак в знак: разъедься
    /// они — служба перестала бы ставиться вовсе.
    #[test]
    fn sha256_matches_the_known_vectors() {
        assert_eq!(
            mac_svc_sha256(b""),
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        );
        assert_eq!(
            mac_svc_sha256(b"abc"),
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        );
        // Длиннее одного блока (64 байта) — чтобы ловился разбор хвоста.
        assert_eq!(
            mac_svc_sha256(b"abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq"),
            "248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1"
        );
    }

    /// `sudo -n` — только там, где человека нет. У владельца всегда диалог,
    /// иначе слова мастера «система спросит пароль» были бы неправдой.
    #[test]
    fn nopassword_path_is_only_for_ci() {
        assert!(mac_svc_noprompt_allowed(Some("true"), None));
        assert!(mac_svc_noprompt_allowed(Some("TRUE"), None));
        assert!(mac_svc_noprompt_allowed(None, Some("1")));
        assert!(!mac_svc_noprompt_allowed(None, None));
        assert!(!mac_svc_noprompt_allowed(Some("false"), Some("0")));
        assert!(!mac_svc_noprompt_allowed(Some(""), None));
    }

    /// Служба — не движок. Демон под `KeepAlive` бывает «running», пока агент в
    /// нём падает по кругу; «absent» при живом порту — мина 8094.
    #[test]
    fn engine_note_separates_the_daemon_from_the_agent() {
        assert_eq!(mac_svc_engine_note("running", Some(true)), "служба работает, движок отвечает");
        assert_eq!(
            mac_svc_engine_note("running", Some(false)),
            "служба работает, движок не отвечает (перезапускается)"
        );
        assert_eq!(
            mac_svc_engine_note("absent", Some(true)),
            "службы нет, но порт держит другой процесс"
        );
        // Спросить не вышло — ничего не придумываем.
        assert_eq!(mac_svc_engine_note("running", None), "");
        assert_eq!(mac_svc_engine_note("absent", Some(false)), "");
        assert_eq!(mac_svc_engine_note("unknown", Some(true)), "");
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

    /// Демон от имени root — отказ словами, а не молча поставленная служба с
    /// правами системы (судьи 19.09).
    #[test]
    fn owner_root_is_refused_with_words() {
        assert!(mac_svc_owner_ok("tom", Some(501)).is_ok());
        let said = mac_svc_owner_ok("root", Some(0)).expect_err("root обязан быть отказом");
        assert!(said.contains("не root"), "{said}");
        // uid 0 под чужим именем — тот же случай (`sudo -u`, `su`).
        assert!(mac_svc_owner_ok("tom", Some(0)).is_err());
        // Пустое имя launchd читает как root — значит это тоже отказ.
        let empty = mac_svc_owner_ok("  ", None).expect_err("пустое имя обязано быть отказом");
        assert!(empty.contains("root"), "{empty}");
    }

    #[test]
    fn sh_quote_keeps_apostrophes() {
        assert_eq!(mac_svc_quote("/tmp/x"), "/tmp/x");
        assert_eq!(mac_svc_quote("/tmp/a b"), "'/tmp/a b'");
        assert_eq!(mac_svc_quote("/tmp/O'Brien"), "'/tmp/O'\\''Brien'");
    }
}
