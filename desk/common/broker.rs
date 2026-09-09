// Брокер прав Hélène — ОДИН протокол на обе стороны трубы.
//
// Файл включается через `include!` и в службу (`svc/src/main.rs`, сервер), и в
// оболочку (`shell/src/main.rs`, клиент) — тем же приёмом, что и
// `common/firewall_rule.rs`. Так — потому что протокол здесь не «формат
// сообщения», а ГРАНИЦА ПРАВ: разъедься проверки на двух сторонах, и слабая
// сторона станет дырой. Пример из истории продукта: имя правила брандмауэра
// жило в двух местах, и служба молча расширяла суженное правило окна.
//
// Внутри — только `std` и `serde_json`. Никакого FFI: у оболочки другой набор
// фич `windows-sys`, и первая же строчка Win32 сломала бы ей сборку. Транспорт
// на стороне клиента — обычный `std::fs::File` на пути `\\.\pipe\…`: труба
// сделана БАЙТОВОЙ ровно для этого (message-mode потребовал бы
// SetNamedPipeHandleState, то есть Win32 на клиенте).
//
// ЧЕГО ЗДЕСЬ НЕТ И ПОЧЕМУ. Брокер не закрыт от самого агента и закрыт быть не
// может: агент — законный клиент, его код исполняется внутри процесса-клиента.
// Замки здесь — против ЧУЖИХ программ и чужих учётных записей. От собственного
// агента защищает только галочка нулевой сессии, выключенная по умолчанию.

/// Версия протокола. Растёт, когда меняется смысл полей, а не их набор.
const BROKER_V: u64 = 1;

/// Потолок запроса. Запрос — это команда и объяснение, мегабайта хватит с
/// запасом; всё, что больше, — не запрос, а попытка занять память службы.
#[allow(dead_code)]
const BROKER_MAX_ASK: usize = 1024 * 1024;

/// Потолок квитанции на чтении у клиента: два потока по 64 КиБ плюс поля.
const BROKER_MAX_RECEIPT: usize = 4 * 1024 * 1024;

/// Сколько текста каждого потока едет в квитанцию. Остальное отрезается с
/// пометкой — молча обрезанный вывод врёт хуже, чем короткий.
const BROKER_STREAM_CAP: usize = 64 * 1024;

/// Сколько ждём команду, если клиент не сказал. Минута — столько живут
/// `netsh`, `sc`, `reg`, ради которых брокер и заведён.
const BROKER_TIMEOUT_DEFAULT: u64 = 60;

/// Потолок ожидания. Дольше — это уже не «выполни команду», а «живи вместо
/// меня»: для такого есть харнесс.
const BROKER_TIMEOUT_MAX: u64 = 600;

// ─────────────────────────────────────────────────────────── имена и адреса

/// Идентификатор установки — из пути папки установки, без единого файла
/// состояния. Две установки на одной машине не должны драться за имя трубы:
/// ровно эта ловушка у продукта уже была с портом 8094, где второй экземпляр
/// молча приклеивался к чужому харнессу.
///
/// FNV-1a 64 по пути в нижнем регистре: короткая, устойчивая, одинаковая у
/// сервера и клиента без всякого обмена.
fn broker_install_id(root: &std::path::Path) -> String {
    let text = broker_norm_path(root);
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for byte in text.as_bytes() {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    format!("{hash:016x}")
}

/// Путь к нормальному виду: без верватим-префикса `\\?\`, без хвостового
/// слэша, в нижнем регистре. Своя копия, а не `plain_path` из включающего
/// крейта: файл обязан собираться в любом из них.
fn broker_norm_path(p: &std::path::Path) -> String {
    let raw = p.to_string_lossy().replace('/', "\\");
    let raw = match raw.strip_prefix(r"\\?\") {
        Some(rest) if rest.starts_with("UNC\\") => format!(r"\\{}", &rest[4..]),
        Some(rest) => rest.to_string(),
        None => raw,
    };
    raw.trim_end_matches('\\').to_lowercase()
}

/// Имя трубы: `\\.\pipe\helene-broker-<id установки>`.
fn broker_pipe_name(root: &std::path::Path) -> String {
    format!(r"\\.\pipe\helene-broker-{}", broker_install_id(root))
}

/// Токен брокера — СВОЙ файл, не `desk-token`. Разные замки от разных дверей:
/// `desk-token` пускает к трубе харнесса (там роль владельца), этот — к
/// подъёму привилегированного процесса. Один файл на двоих означал бы, что
/// утечка секрета из веб-страницы открывает и брокера.
fn broker_token_path(tree: &std::path::Path) -> std::path::PathBuf {
    tree.join("memory").join(".state").join("broker-token")
}

/// Журнал обращений — рядом с `service.log`, в дереве данных.
#[allow(dead_code)]
fn broker_log_path(tree: &std::path::Path) -> std::path::PathBuf {
    tree.join("broker.log")
}

// ─────────────────────────────────────── просьбы агента: два файла, два писателя
//
// У агента своей двери к брокеру нет и быть не должно: труба брокера пускает по
// токену, а токен закрыт правами «СИСТЕМА, администраторы, владелец» — в
// песочнице агент его вообще не прочитает. Поэтому агент не зовёт брокера, а
// ПРОСИТ; просьбу показывает владельцу оболочка, и она же зовёт брокера своим
// токеном, если владелец сказал «да».
//
// Обмен — два файла в `memory\.state`, рядом с `mounts.json`, и по той же
// причине: у каждого файла РОВНО ОДИН писатель, поэтому замка не нужно.
//
//   broker-asks.json     пишет харнесс, читает оболочка — чего агент просит;
//   broker-answers.json  пишет оболочка, читает харнесс — что решил владелец.
//
// ⚠ ПОЧЕМУ НЕ HTTP К ХАРНЕССУ, хотя окно ходит к нему по трубе. На порту
// харнесса в этом продукте уже бывает ЧУЖАЯ установка (`Verdict::Foreign` в
// оболочке — живой случай, не гипотеза). Спрашивая просьбы по порту, оболочка
// однажды поднесла бы владельцу на подпись команду чужого агента. Файл же лежит
// в СВОЁМ дереве, и перепутать его нельзя.
//
// ЧТО ПИШЕТ ХАРНЕСС в `broker-asks.json` (это и есть контракт руки агента):
//
//   { "v": 1, "requests": [
//       { "id": "b7",                  // [A-Za-z0-9_-], до 64 знаков, свой на просьбу;
//                                      //   по нему харнесс найдёт ответ. Чужие знаки =
//                                      //   просьба пропускается молча: подчищенный id
//                                      //   перестал бы совпадать с ожидаемым
//         "op": "exec",                // exec (правами СИСТЕМЫ, только при включённой
//                                      //   нулевой сессии) | spawn_interactive (правами
//                                      //   владельца, в его сессии) | ping
//         "cmd": "C:\\Windows\\System32\\netsh.exe",   // ТОЛЬКО полный путь
//         "args": ["advfirewall", "…"],// массив строк, никогда одна строка
//         "why": "…",                  // ОБЯЗАТЕЛЬНО, одной строкой: это читает владелец
//         "timeout_sec": 60,           // до 600
//         "at_unix": 1757000000 } ] }  // когда записана; без него возраст берётся по mtime
//
// Токена в просьбе НЕТ и он в ней игнорируется: секрет подставляет оболочка.
// Просьба старше десяти минут отклоняется без вопроса владельцу — за это время
// агент уже ушёл дальше, и подписывать её вслепую опаснее, чем отказать.
//
// ЧТО ПИШЕТ ОБОЛОЧКА в `broker-answers.json`:
//
//   { "v": 1, "updated_at": "[04.09.2026 15:00:00]",
//     "desk": { "watching_since": "…", "pid": 1234 },   // нет поля = спрашивать некому
//     "answers": [ { "id": "b7", "decision": "allowed|refused|failed",
//                    "at": "…", "op": "…", "why": "…", "shown": "команда как есть",
//                    "note": "причина отказа или приписка службы",
//                    "ok": true, "code": 0, "pid": 4242, "out": "…", "err": "…",
//                    "ms": 120 } ] }   // поля квитанции есть только у ответа брокера
//
// Отвеченные просьбы харнесс убирает из своего файла сам — ровно как
// `mounts.forget_answered`.
//
// ⚠ ЧЕГО ЭТИ ФАЙЛЫ НЕ ДАЮТ. Дерево — дом агента, он пишет в него сам: и просьбу
// подделать может (написать «зачем» красивее, чем есть), и ответ себе
// нарисовать. Первое не страшно, потому что решает владелец глазами: он читает
// «зачем» и ВИДИТ саму команду. Второе обманывает только самого агента, прав
// оно не даёт. Настоящая запись о том, что было, живёт не здесь, а в
// `broker.log` и в журнале оболочки рядом с exe.
#[allow(dead_code)]
fn broker_asks_path(tree: &std::path::Path) -> std::path::PathBuf {
    tree.join("memory").join(".state").join("broker-asks.json")
}

/// Ответы владельца и квитанции — обратный файл. Пишет его ТОЛЬКО оболочка.
#[allow(dead_code)]
fn broker_answers_path(tree: &std::path::Path) -> std::path::PathBuf {
    tree.join("memory").join(".state").join("broker-answers.json")
}

/// Похоже ли на строковый SID (`S-1-5-21-…`). Не проверка подлинности, а
/// защита от подстановки: SID уезжает в SDDL, и `)` или `;` внутри него
/// переписали бы дескриптор трубы целиком.
fn broker_sid_ok(sid: &str) -> bool {
    let sid = sid.trim();
    sid.len() >= 3
        && sid.len() <= 184
        && (sid.starts_with("S-") || sid.starts_with("s-"))
        && sid.chars().all(|c| c.is_ascii_digit() || c == '-' || c == 'S' || c == 's')
}

/// Дескриптор трубы словами SDDL: полный доступ СИСТЕМЕ и владельцу
/// установки, больше НИКОМУ.
///
/// * `D:P` — свой список, наследование от родителя отрезано (`P`). Без `P`
///   труба получила бы ACE по умолчанию, в том числе для «Все».
/// * `(A;;GA;;;SY)` — СИСТЕМА: сервер живёт под ней.
/// * `(A;;GA;;;<sid>)` — владелец установки: клиент живёт под ним.
///
/// Администраторов здесь НЕТ намеренно: администратор и так может всё, а
/// лишний ACE — это лишняя дверь в списке.
#[allow(dead_code)]
fn broker_sddl(owner_sid: &str) -> Option<String> {
    if !broker_sid_ok(owner_sid) {
        return None;
    }
    Some(format!("D:P(A;;GA;;;SY)(A;;GA;;;{})", owner_sid.trim()))
}

// ─────────────────────────────────────────────────────────────── что просят

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum BrokerOp {
    /// Живой ли брокер. Ничего не выполняет и в журнал действий не пишется.
    Ping,
    /// Поднять процесс ПРАВАМИ СИСТЕМЫ. Только при включённой галочке нулевой
    /// сессии — иначе отказ.
    Exec,
    /// Поднять процесс в сессии владельца, его правами. Вторая дверь: служба
    /// сидит в нулевой сессии и рабочего стола не видит.
    SpawnInteractive,
    /// Правило брандмауэра для телефона. УЗКАЯ дверь: аргументы netsh собирает
    /// САМА служба из `common/firewall_rule.rs` по номеру порта — от клиента
    /// приходит только порт и «поставить/снять».
    ///
    /// Почему отдельным op, а не через `exec`. Правило брандмауэра — рядовое
    /// действие продукта, ради него незачем открывать нулевую сессию (то есть
    /// право поднимать ЛЮБОЙ процесс правами СИСТЕМЫ). Пока эти два случая
    /// стояли на одной галочке, кнопка «Телефон» под службой была мертва по
    /// умолчанию, а чтобы её оживить, владельцу предлагалось выдать агенту
    /// права системы целиком. Здесь дверь ровно одна и ведёт ровно в одно
    /// место: netsh с аргументами, которых клиент не выбирает.
    Firewall,
}

impl BrokerOp {
    fn as_str(self) -> &'static str {
        match self {
            BrokerOp::Ping => "ping",
            BrokerOp::Exec => "exec",
            BrokerOp::SpawnInteractive => "spawn_interactive",
            BrokerOp::Firewall => "firewall",
        }
    }
    fn parse(raw: &str) -> Option<BrokerOp> {
        match raw {
            "ping" => Some(BrokerOp::Ping),
            "exec" => Some(BrokerOp::Exec),
            "spawn_interactive" => Some(BrokerOp::SpawnInteractive),
            "firewall" => Some(BrokerOp::Firewall),
            _ => None,
        }
    }
    /// Поднимает ли эта просьба процесс. `ping` — нет, и потому не требует ни
    /// команды, ни записи в журнал действий.
    fn runs(self) -> bool {
        !matches!(self, BrokerOp::Ping)
    }
}

#[derive(Clone, Debug)]
struct BrokerAsk {
    id: String,
    token: String,
    op: BrokerOp,
    cmd: String,
    args: Vec<String>,
    why: String,
    timeout_sec: u64,
}

impl BrokerAsk {
    /// Собрать просьбу на стороне клиента.
    #[allow(dead_code)]
    fn new(token: &str, op: BrokerOp, cmd: &str, args: &[String], why: &str) -> BrokerAsk {
        BrokerAsk {
            id: String::new(),
            token: token.to_string(),
            op,
            cmd: cmd.to_string(),
            args: args.to_vec(),
            why: why.to_string(),
            timeout_sec: BROKER_TIMEOUT_DEFAULT,
        }
    }

    fn to_json(&self) -> String {
        serde_json::json!({
            "v": BROKER_V,
            "id": self.id,
            "token": self.token,
            "op": self.op.as_str(),
            "cmd": self.cmd,
            "args": self.args,
            "why": self.why,
            "timeout_sec": self.timeout_sec,
        })
        .to_string()
    }

    /// Разбор и ПРОВЕРКА просьбы. Всё, что здесь запрещено, запрещено на
    /// границе — до того, как служба что-нибудь выполнит.
    #[allow(dead_code)]
    fn parse(raw: &str) -> Result<BrokerAsk, String> {
        let value: serde_json::Value =
            serde_json::from_str(raw).map_err(|e| format!("не разобрал запрос: {e}"))?;

        let v = value.get("v").and_then(|v| v.as_u64()).unwrap_or(0);
        if v != BROKER_V {
            return Err(format!(
                "не тот протокол: жду v={BROKER_V}, пришло v={v}. Обнови ту сторону, которая старее"
            ));
        }

        let id = value.get("id").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if id.len() > 64 {
            return Err("поле id длиннее 64 знаков — это не идентификатор".into());
        }

        let token = value.get("token").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if token.is_empty() {
            return Err("нет токена. Он лежит в memory\\.state\\broker-token".into());
        }

        let op_raw = value.get("op").and_then(|v| v.as_str()).unwrap_or("");
        let Some(op) = BrokerOp::parse(op_raw) else {
            return Err(format!(
                "не знаю такой просьбы: «{op_raw}». Бывают ping, exec, spawn_interactive"
            ));
        };

        // `why` обязательно и человеческими словами — оно идёт в журнал
        // владельцу. Пустое `why` значит «сделай что-то, но не скажу зачем», и
        // это единственная строка журнала, которую владелец действительно
        // читает: без неё журнал превращается в список команд без смысла.
        let why = value.get("why").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if why.is_empty() {
            return Err("не сказано «зачем». Это поле идёт в журнал владельцу — брокер без него не работает".into());
        }
        if why.chars().count() < 3 || !why.chars().any(|c| c.is_alphabetic()) {
            return Err("«зачем» должно быть словами, а не знаком-заглушкой".into());
        }
        if why.chars().count() > 500 {
            return Err("«зачем» длиннее 500 знаков — это уже не объяснение".into());
        }
        // Журнал владельца читается ПОСТРОЧНО. Перевод строки внутри «зачем»
        // подделал бы соседние записи: одна просьба нарисовала бы в журнале
        // десяток чужих строк с любым текстом. Отказ, а не экранирование —
        // объяснение в одну строку это не ограничение, а норма.
        if why.chars().any(|c| c.is_control()) {
            return Err(
                "«зачем» — одной строкой: журнал владельца читается построчно, и перевод \
                 строки в нём подделывает чужие записи"
                    .into(),
            );
        }

        // Массивом, и никогда одной строкой. Склейка командной строки — это
        // целый класс инъекций: `args: "a & del /q *"` в одной строке
        // исполнилось бы как две команды. Отказ здесь честнее, чем попытка
        // «умно» разрезать строку на аргументы.
        let args = match value.get("args") {
            None | Some(serde_json::Value::Null) => Vec::new(),
            Some(serde_json::Value::Array(items)) => {
                let mut out = Vec::with_capacity(items.len());
                for item in items {
                    let Some(text) = item.as_str() else {
                        return Err("в args есть не-строка — аргументы бывают только строками".into());
                    };
                    if text.contains('\0') {
                        return Err("в args есть нулевой байт — Windows обрежет команду на нём".into());
                    }
                    out.push(text.to_string());
                }
                out
            }
            Some(_) => {
                return Err(
                    "args должен быть МАССИВОМ, а не строкой: склейка командной строки — \
                     это дыра, а не удобство"
                        .into(),
                )
            }
        };
        if args.len() > 256 {
            return Err("больше 256 аргументов — это не команда".into());
        }

        let cmd = value.get("cmd").and_then(|v| v.as_str()).unwrap_or("").trim().to_string();
        if op.runs() {
            broker_cmd_ok(&cmd)?;
        }

        let timeout_sec = match value.get("timeout_sec") {
            None | Some(serde_json::Value::Null) => BROKER_TIMEOUT_DEFAULT,
            Some(v) => {
                let Some(n) = v.as_u64() else {
                    return Err("timeout_sec — это число секунд".into());
                };
                if n > BROKER_TIMEOUT_MAX {
                    return Err(format!(
                        "timeout_sec больше {BROKER_TIMEOUT_MAX} с. Столько ждёт не команда, а харнесс"
                    ));
                }
                n
            }
        };

        Ok(BrokerAsk { id, token, op, cmd, args, why, timeout_sec })
    }
}

/// Что за программу разрешено просить.
///
/// ⚠ ПОЧЕМУ ТОЛЬКО ПОЛНЫЙ ПУТЬ. `CreateProcess` ищет голое имя СНАЧАЛА в папке
/// своего процесса, а папка процесса службы — это папка установки, куда пишет
/// обычный пользователь и куда песочница пускает самого агента. Подложенный
/// `netsh.exe` исполнился бы СИСТЕМОЙ. Та же мина уже накрыта в службе
/// отдельно (`sys_exe`), и здесь она обязана быть закрыта явно: брокер — это
/// вход снаружи.
fn broker_cmd_ok(cmd: &str) -> Result<(), String> {
    if cmd.is_empty() {
        return Err("нет команды (поле cmd)".into());
    }
    if cmd.contains('\0') || cmd.contains('\n') || cmd.contains('\r') {
        return Err("в имени программы перевод строки или нулевой байт".into());
    }
    let drive = {
        let b = cmd.as_bytes();
        b.len() > 2 && b[0].is_ascii_alphabetic() && b[1] == b':' && (b[2] == b'\\' || b[2] == b'/')
    };
    let unc = cmd.starts_with(r"\\");
    if !drive && !unc {
        return Err(format!(
            "команду надо называть полным путём, а не «{cmd}»: голое имя Windows ищет сначала \
             в папке программы, а туда пишет обычный пользователь — подменённый файл исполнился \
             бы правами СИСТЕМЫ"
        ));
    }
    if cmd.contains("..") {
        return Err("в пути есть «..» — назови программу прямо".into());
    }
    Ok(())
}

// ─────────────────────────────────────────────────────────────── квитанция

/// Ответ брокера — КВИТАНЦИЯ, а не «успех». Что запустили, что оно сказало,
/// чем кончилось, сколько шло и когда. Без этого владелец видит только
/// «готово» и не может проверить ни одного слова.
#[derive(Clone, Debug)]
#[allow(dead_code)]
struct BrokerReceipt {
    id: String,
    ok: bool,
    op: String,
    /// Код возврата. `None` — процесс не завершился (таймаут или «не ждём»).
    code: Option<i32>,
    pid: Option<u32>,
    out: String,
    err: String,
    ms: u64,
    /// Местное время начала — тем же видом, что в `service.log`.
    at: String,
    why: String,
    /// Человеческая приписка: причина отказа, «убит по таймауту», «ещё
    /// работает». Одно поле на все случаи: два (`error` и `note`) разъезжались
    /// бы, и клиент не знал бы, какое читать.
    note: String,
}

impl BrokerReceipt {
    #[allow(dead_code)]
    fn refused(ask: Option<&BrokerAsk>, at: &str, why_not: &str) -> BrokerReceipt {
        BrokerReceipt {
            id: ask.map(|a| a.id.clone()).unwrap_or_default(),
            ok: false,
            op: ask.map(|a| a.op.as_str().to_string()).unwrap_or_default(),
            code: None,
            pid: None,
            out: String::new(),
            err: String::new(),
            ms: 0,
            at: at.to_string(),
            why: ask.map(|a| a.why.clone()).unwrap_or_default(),
            note: why_not.to_string(),
        }
    }

    #[allow(dead_code)]
    fn to_json(&self) -> String {
        serde_json::json!({
            "v": BROKER_V,
            "id": self.id,
            "ok": self.ok,
            "op": self.op,
            "code": self.code,
            "pid": self.pid,
            "out": self.out,
            "err": self.err,
            "ms": self.ms,
            "at": self.at,
            "why": self.why,
            "note": self.note,
        })
        .to_string()
    }

    #[allow(dead_code)]
    fn parse(raw: &str) -> Result<BrokerReceipt, String> {
        let v: serde_json::Value =
            serde_json::from_str(raw).map_err(|e| format!("квитанция не разобралась: {e}"))?;
        let text = |k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
        Ok(BrokerReceipt {
            id: text("id"),
            ok: v.get("ok").and_then(|x| x.as_bool()).unwrap_or(false),
            op: text("op"),
            code: v.get("code").and_then(|x| x.as_i64()).map(|n| n as i32),
            pid: v.get("pid").and_then(|x| x.as_u64()).map(|n| n as u32),
            out: text("out"),
            err: text("err"),
            ms: v.get("ms").and_then(|x| x.as_u64()).unwrap_or(0),
            at: text("at"),
            why: text("why"),
            note: text("note"),
        })
    }
}

/// Обрезка потока с честной пометкой. Молча урезанный вывод — это враньё в
/// квитанции, а квитанция здесь единственный способ проверить, что произошло.
#[allow(dead_code)]
fn broker_cut(text: &str) -> String {
    if text.len() <= BROKER_STREAM_CAP {
        return text.to_string();
    }
    // Режем по границе символа: середина UTF-8 в квитанции даёт «□».
    let mut end = BROKER_STREAM_CAP;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    format!("{}\n…обрезано, всего {} байт", &text[..end], text.len())
}

/// Команда одной строкой — ТОЛЬКО ДЛЯ ЖУРНАЛА И ГЛАЗ. Ни одна из сторон не
/// исполняет её обратно: программа и аргументы едут раздельно до самого
/// `CreateProcess`.
///
/// Управляющие знаки из аргументов вычищаются здесь же: `why` мы отвергаем с
/// переводом строки, а аргументу перевод строки бывает нужен по делу — значит
/// подделку журнала обязана снимать эта функция.
#[allow(dead_code)]
fn broker_shown_command(cmd: &str, args: &[String]) -> String {
    let flat = |s: &str| -> String {
        s.chars().map(|c| if c.is_control() { '·' } else { c }).collect()
    };
    let mut out = flat(cmd);
    for a in args {
        let a = flat(a);
        out.push(' ');
        if a.is_empty() || a.contains(' ') || a.contains('"') {
            out.push('"');
            out.push_str(&a.replace('"', "\\\""));
            out.push('"');
        } else {
            out.push_str(&a);
        }
    }
    out
}

/// Командная строка для `CreateProcess` — по правилам разбора самой Windows
/// (`CommandLineToArgvW`): кавычки вокруг аргумента с пробелом, удвоение
/// обратных слэшей перед кавычкой, экранирование самой кавычки.
///
/// ⚠ ЗАЧЕМ ЭТО ЗДЕСЬ, а не «просто join через пробел». Аргументы приходят
/// массивом именно для того, чтобы `del /q C:\*` внутри одного аргумента
/// остался ОДНИМ аргументом, а не стал второй командой. Наивная склейка
/// вернула бы ту самую дыру, ради которой массив и заведён, — поэтому сборка
/// строки накрыта тестом.
///
/// Программа при этом всё равно передаётся ОТДЕЛЬНО (`lpApplicationName`), и
/// поиск по путям не выполняется вовсе; здесь она нужна лишь как `argv[0]`,
/// которого ждут почти все программы.
#[allow(dead_code)]
fn broker_command_line(cmd: &str, args: &[String]) -> String {
    let mut out = broker_quote(cmd);
    for a in args {
        out.push(' ');
        out.push_str(&broker_quote(a));
    }
    out
}

#[allow(dead_code)]
fn broker_quote(arg: &str) -> String {
    let needs = arg.is_empty()
        || arg
            .chars()
            .any(|c| c == ' ' || c == '\t' || c == '"' || c == '\n' || c == '\u{b}' || c == '\u{c}');
    if !needs {
        return arg.to_string();
    }
    let mut out = String::from("\"");
    let mut slashes = 0usize;
    for c in arg.chars() {
        match c {
            '\\' => {
                slashes += 1;
                out.push('\\');
            }
            '"' => {
                // Слэши перед кавычкой удваиваются, сама кавычка экранируется.
                for _ in 0..slashes {
                    out.push('\\');
                }
                slashes = 0;
                out.push('\\');
                out.push('"');
            }
            _ => {
                slashes = 0;
                out.push(c);
            }
        }
    }
    // Хвостовые слэши перед закрывающей кавычкой — тоже удваиваются, иначе они
    // съедят её саму.
    for _ in 0..slashes {
        out.push('\\');
    }
    out.push('"');
    out
}

// ─────────────────────────────────────────────────────────────── транспорт

/// Кадр: длина (u32 LE) и тело. Труба байтовая, границы сообщений держит
/// длина, а не тип трубы, — так клиенту не нужен ни Win32, ни message-mode.
fn broker_frame(payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(payload.len() + 4);
    out.extend_from_slice(&(payload.len() as u32).to_le_bytes());
    out.extend_from_slice(payload);
    out
}

/// Прочитать один кадр. `cap` — потолок: без него клиент, назвавший длину
/// 4 ГиБ, заставил бы службу выделить 4 ГиБ.
fn broker_read_frame(source: &mut dyn std::io::Read, cap: usize) -> Result<Vec<u8>, String> {
    let mut head = [0u8; 4];
    source
        .read_exact(&mut head)
        .map_err(|e| format!("канал оборвался на заголовке: {e}"))?;
    let len = u32::from_le_bytes(head) as usize;
    if len == 0 {
        return Err("пустое сообщение".into());
    }
    if len > cap {
        return Err(format!("сообщение больше {cap} байт"));
    }
    let mut body = vec![0u8; len];
    source
        .read_exact(&mut body)
        .map_err(|e| format!("канал оборвался на теле: {e}"))?;
    Ok(body)
}

/// Позвать брокера — сторона КЛИЕНТА (оболочка, руки агента).
///
/// Труба открывается обычным файлом: `\\.\pipe\…` для Windows — путь, и
/// `std::fs` открывает его через тот же `CreateFileW`. Никакого FFI на клиенте
/// поэтому не нужно вовсе.
///
/// ⚠ Ждать ответа нечем, кроме блокирующего чтения: у файла на трубе нет
/// таймаута без перекрытого ввода-вывода. Служба сама держит свои сроки
/// (`timeout_sec`) и всегда отвечает квитанцией, но если процесс службы убит
/// посреди работы, чтение здесь повиснет до разрыва трубы. Зови из отдельного
/// потока, если это важно.
#[allow(dead_code)]
fn broker_call(pipe: &str, ask: &BrokerAsk) -> Result<BrokerReceipt, String> {
    use std::io::{Read, Write};
    // ERROR_PIPE_BUSY (231) — все экземпляры заняты. Это норма при двух
    // запросах подряд, а не отказ: ждём и пробуем снова.
    const BUSY: i32 = 231;
    let mut file = None;
    let started = std::time::Instant::now();
    let mut last = String::new();
    while started.elapsed() < std::time::Duration::from_secs(5) {
        match std::fs::OpenOptions::new().read(true).write(true).open(pipe) {
            Ok(f) => {
                file = Some(f);
                break;
            }
            Err(e) if e.raw_os_error() == Some(BUSY) => {
                last = e.to_string();
                std::thread::sleep(std::time::Duration::from_millis(120));
            }
            Err(e) => {
                return Err(format!(
                    "брокер не отвечает ({pipe}): {e}. Он живёт только при включённой службе"
                ))
            }
        }
    }
    let Some(mut file) = file else {
        return Err(format!("брокер занят дольше пяти секунд ({pipe}): {last}"));
    };
    let body = ask.to_json();
    file.write_all(&broker_frame(body.as_bytes()))
        .map_err(|e| format!("не отправил запрос брокеру: {e}"))?;
    file.flush().map_err(|e| format!("не отправил запрос брокеру: {e}"))?;
    let raw = broker_read_frame(&mut file as &mut dyn Read, BROKER_MAX_RECEIPT)?;
    let text = String::from_utf8(raw).map_err(|_| "квитанция не в UTF-8".to_string())?;
    BrokerReceipt::parse(&text)
}

/// Токен брокера из файла — сторона клиента. Отдельная функция, потому что
/// путь к файлу обязан совпадать у обеих сторон до буквы.
#[allow(dead_code)]
fn broker_token_read(tree: &std::path::Path) -> Option<String> {
    let raw = std::fs::read_to_string(broker_token_path(tree)).ok()?;
    let t = raw.trim();
    (t.len() >= 16 && t.len() <= 128 && t.bytes().all(|b| b.is_ascii_alphanumeric()))
        .then(|| t.to_string())
}

// ────────────────────────────────────────────────────────────────── тесты
//
// Тест едет вместе с файлом в обе подсистемы — как у `firewall_rule.rs`.
// Проверяется здесь не «работает», а «не пускает»: цена ошибки в этих строках
// не «не запустилось», а «запустилось не с теми правами».

#[cfg(test)]
mod broker_protocol_tests {
    use super::*;

    fn ask_json(extra: &str) -> String {
        format!(
            "{{\"v\":1,\"id\":\"a1\",\"token\":\"deadbeefdeadbeef\",\"op\":\"exec\",\
              \"cmd\":\"C:\\\\Windows\\\\System32\\\\netsh.exe\",{extra}}}"
        )
    }

    #[test]
    fn parses_a_whole_request() {
        let raw = ask_json("\"args\":[\"advfirewall\",\"show\"],\"why\":\"правило для телефона\",\"timeout_sec\":30");
        let ask = BrokerAsk::parse(&raw).expect("не разобрался");
        assert_eq!(ask.op, BrokerOp::Exec);
        assert_eq!(ask.cmd, r"C:\Windows\System32\netsh.exe");
        assert_eq!(ask.args, vec!["advfirewall".to_string(), "show".to_string()]);
        assert_eq!(ask.why, "правило для телефона");
        assert_eq!(ask.timeout_sec, 30);
        assert_eq!(ask.id, "a1");
    }

    /// Пустое «зачем» = отказ. Это поле — единственная человеческая строка в
    /// журнале владельца.
    #[test]
    fn refuses_without_why() {
        for extra in [
            "\"args\":[],\"why\":\"\"",
            "\"args\":[]",
            "\"args\":[],\"why\":\"   \"",
            "\"args\":[],\"why\":\"-\"",
            "\"args\":[],\"why\":\"...\"",
        ] {
            let err = BrokerAsk::parse(&ask_json(extra)).expect_err("пропустил без «зачем»");
            assert!(err.contains("зачем"), "{err}");
        }
    }

    /// Аргументы одной строкой — класс инъекций, а не удобство. Отказ, а не
    /// попытка разрезать строку.
    #[test]
    fn refuses_args_as_one_string() {
        let err = BrokerAsk::parse(&ask_json(
            "\"args\":\"advfirewall && del /q C:\\\\*\",\"why\":\"почему бы и нет\"",
        ))
        .expect_err("проглотил строку вместо массива");
        assert!(err.contains("МАССИВОМ"), "{err}");
        let err = BrokerAsk::parse(&ask_json("\"args\":[1,2],\"why\":\"числа\"")).unwrap_err();
        assert!(err.contains("не-строка"), "{err}");
    }

    /// Голое имя программы = подмена в папке установки правами СИСТЕМЫ.
    #[test]
    fn refuses_bare_program_name() {
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"exec\",\"cmd\":\"netsh.exe\",\"args\":[],\"why\":\"правило\"}";
        let err = BrokerAsk::parse(raw).expect_err("пропустил голое имя");
        assert!(err.contains("полным путём"), "{err}");
        // И относительный путь тоже.
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"exec\",\"cmd\":\"..\\\\netsh.exe\",\"args\":[],\"why\":\"правило\"}";
        assert!(BrokerAsk::parse(raw).is_err());
        // UNC — законный полный путь.
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"exec\",\"cmd\":\"\\\\\\\\srv\\\\share\\\\a.exe\",\"args\":[],\"why\":\"правило\"}";
        assert!(BrokerAsk::parse(raw).is_ok());
    }

    #[test]
    fn refuses_without_token_and_on_wrong_version() {
        let raw = "{\"v\":1,\"op\":\"ping\",\"why\":\"проверка связи\"}";
        assert!(BrokerAsk::parse(raw).unwrap_err().contains("токен"));
        let raw = "{\"v\":2,\"token\":\"deadbeefdeadbeef\",\"op\":\"ping\",\"why\":\"проверка связи\"}";
        assert!(BrokerAsk::parse(raw).unwrap_err().contains("протокол"));
    }

    #[test]
    fn refuses_unknown_op_and_long_timeout() {
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"rm -rf\",\"why\":\"шутка\"}";
        assert!(BrokerAsk::parse(raw).unwrap_err().contains("не знаю такой просьбы"));
        let err = BrokerAsk::parse(&ask_json("\"args\":[],\"why\":\"долго\",\"timeout_sec\":100000"))
            .unwrap_err();
        assert!(err.contains("600"), "{err}");
    }

    /// `ping` команды не требует: он ничего не поднимает. Но «зачем» требует и
    /// он — правило одно на все просьбы, чтобы в нём не было исключений,
    /// которые потом расширяют.
    #[test]
    fn ping_needs_no_command_but_needs_why() {
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"ping\",\"why\":\"проверка связи\"}";
        let ask = BrokerAsk::parse(raw).expect("ping не разобрался");
        assert_eq!(ask.op, BrokerOp::Ping);
        assert!(!ask.op.runs());
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"ping\"}";
        assert!(BrokerAsk::parse(raw).is_err());
    }

    /// Круг: собрали у клиента — разобрали у службы. Разъедься эти две
    /// половины, и протокол молча перестал бы существовать.
    #[test]
    fn round_trip_client_to_server() {
        let ask = BrokerAsk::new(
            "deadbeefdeadbeef",
            BrokerOp::SpawnInteractive,
            r"C:\Windows\System32\notepad.exe",
            &[r"C:\путь с пробелом\файл.txt".to_string()],
            "владелец попросил открыть файл",
        );
        let back = BrokerAsk::parse(&ask.to_json()).expect("свой же запрос не разобрался");
        assert_eq!(back.op, BrokerOp::SpawnInteractive);
        assert_eq!(back.args, ask.args);
        assert_eq!(back.why, ask.why);
        assert_eq!(back.timeout_sec, BROKER_TIMEOUT_DEFAULT);
    }

    #[test]
    fn receipt_round_trip_keeps_every_field() {
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
            note: "".into(),
        };
        let back = BrokerReceipt::parse(&receipt.to_json()).expect("квитанция не разобралась");
        assert_eq!(back.code, Some(1));
        assert_eq!(back.pid, Some(4242));
        assert_eq!(back.out, "вывод");
        assert_eq!(back.err, "ошибки");
        assert_eq!(back.ms, 120);
        assert_eq!(back.at, "[04.09.2026 13:00:00]");
        assert!(back.ok);
        // Незавершённый процесс: код именно ОТСУТСТВУЕТ, а не «0».
        let waiting = BrokerReceipt { code: None, ..receipt };
        assert_eq!(BrokerReceipt::parse(&waiting.to_json()).unwrap().code, None);
    }

    #[test]
    fn sddl_lets_in_system_and_owner_only() {
        let sddl = broker_sddl("S-1-5-21-1-2-3-1001").expect("SDDL не собрался");
        assert_eq!(sddl, "D:P(A;;GA;;;SY)(A;;GA;;;S-1-5-21-1-2-3-1001)");
        // `P` — наследование отрезано: без него труба получила бы чужие ACE.
        assert!(sddl.starts_with("D:P"));
        // «Все» (WD), «Прошедшие проверку» (AU), «Пользователи» (BU) — никого.
        for who in ["WD", "AU", "BU", "IU"] {
            assert!(!sddl.contains(&format!(";{who})")), "{sddl}");
        }
    }

    /// SID уезжает в SDDL строкой. Подстановка `)` или `;` переписала бы
    /// дескриптор целиком — отказ, а не экранирование.
    #[test]
    fn sddl_refuses_anything_but_a_sid() {
        for bad in [
            "S-1-5-21-1)(A;;GA;;;WD",
            "S-1-5-21-1;;",
            "ВСЕ",
            "",
            "  ",
            "S-1-5-21-1 ; drop",
        ] {
            assert!(broker_sddl(bad).is_none(), "пропустил «{bad}»");
        }
        assert!(broker_sddl("S-1-5-18").is_some());
    }

    /// Две установки на одной машине не должны драться за имя трубы — ровно
    /// эта ловушка у продукта уже была с портом 8094.
    #[test]
    fn pipe_name_is_per_install() {
        let a = broker_pipe_name(std::path::Path::new(r"C:\Users\Иван\AppData\Local\Programs\Helene"));
        let b = broker_pipe_name(std::path::Path::new(r"D:\Helene"));
        assert_ne!(a, b);
        assert!(a.starts_with(r"\\.\pipe\helene-broker-"), "{a}");
        // Регистр и хвостовой слэш — тот же путь, то же имя: иначе служба и
        // оболочка называли бы разные трубы.
        let c = broker_pipe_name(std::path::Path::new(r"c:\helene\"));
        let d = broker_pipe_name(std::path::Path::new(r"C:\Helene"));
        assert_eq!(c, d);
        // Верватим-префикс из canonicalize() — тот же путь.
        let e = broker_pipe_name(std::path::Path::new(r"\\?\C:\Helene"));
        assert_eq!(d, e);
    }

    #[test]
    fn token_and_log_live_where_promised() {
        let tree = std::path::Path::new(r"C:\Helene\data");
        assert!(broker_token_path(tree).ends_with(r"memory\.state\broker-token"));
        assert!(broker_log_path(tree).ends_with("broker.log"));
        // Не тот же файл, что у трубы харнесса: разные замки от разных дверей.
        assert_ne!(
            broker_token_path(tree).file_name(),
            Some(std::ffi::OsStr::new("desk-token"))
        );
    }

    /// Просьба и ответ — РАЗНЫЕ файлы, и ни один из них не файл секрета. Один
    /// файл на двоих означал бы двух писателей и потерянную просьбу на каждой
    /// одновременной записи; общий с секретом — что просьба агента живёт там же,
    /// где токен, который ему видеть нельзя.
    #[test]
    fn asks_and_answers_are_two_files_beside_the_mounts() {
        let tree = std::path::Path::new(r"C:\Helene\data");
        let asks = broker_asks_path(tree);
        let answers = broker_answers_path(tree);
        assert!(asks.ends_with(r"memory\.state\broker-asks.json"), "{}", asks.display());
        assert!(
            answers.ends_with(r"memory\.state\broker-answers.json"),
            "{}",
            answers.display()
        );
        assert_ne!(asks, answers);
        assert_ne!(asks, broker_token_path(tree));
        assert_ne!(answers, broker_token_path(tree));
        // Рядом с mounts.json — тот же приём и та же папка.
        assert_eq!(asks.parent(), broker_token_path(tree).parent());
    }

    #[test]
    fn frame_carries_its_own_length() {
        let framed = broker_frame(b"abc");
        assert_eq!(framed, vec![3, 0, 0, 0, b'a', b'b', b'c']);
        let mut source: &[u8] = &framed;
        let back = broker_read_frame(&mut source as &mut dyn std::io::Read, 16).unwrap();
        assert_eq!(back, b"abc");
        // Названная длина больше потолка — отказ ДО выделения памяти.
        let mut huge: &[u8] = &[0xff, 0xff, 0xff, 0xff, 1];
        assert!(broker_read_frame(&mut huge as &mut dyn std::io::Read, 1024).is_err());
    }

    #[test]
    fn cut_says_it_cut() {
        let long = "я".repeat(BROKER_STREAM_CAP);
        let cut = broker_cut(&long);
        assert!(cut.contains("обрезано"), "не признался, что обрезал");
        assert!(cut.len() < long.len() + 64);
        assert_eq!(broker_cut("коротко"), "коротко");
    }

    #[test]
    fn shown_command_is_for_eyes_only() {
        let shown = broker_shown_command(
            r"C:\Windows\System32\netsh.exe",
            &["advfirewall".into(), "name=Helene (8094)".into()],
        );
        assert_eq!(
            shown,
            r#"C:\Windows\System32\netsh.exe advfirewall "name=Helene (8094)""#
        );
    }

    /// Подделка журнала переводом строки внутри аргумента: в `why` она
    /// отвергается, в аргументе — вычищается.
    #[test]
    fn shown_command_cannot_forge_a_journal_line() {
        let shown = broker_shown_command("C:\\a.exe", &["раз\nвсё хорошо, ничего не было".into()]);
        assert!(!shown.contains('\n'), "{shown}");
        let raw = "{\"v\":1,\"token\":\"deadbeefdeadbeef\",\"op\":\"ping\",\"why\":\"раз\\nдва\"}";
        assert!(BrokerAsk::parse(raw).unwrap_err().contains("одной строкой"));
    }

    /// Склейка командной строки — это класс инъекций, поэтому она собрана по
    /// правилам самой Windows и накрыта тестом. `del /q C:\*` внутри аргумента
    /// обязан остаться ОДНИМ аргументом.
    #[test]
    fn command_line_keeps_arguments_whole() {
        let line = broker_command_line(
            r"C:\Windows\System32\netsh.exe",
            &["advfirewall".into(), "name=Helene (8094)".into(), "& del /q C:\\*".into()],
        );
        assert_eq!(
            line,
            r#"C:\Windows\System32\netsh.exe advfirewall "name=Helene (8094)" "& del /q C:\*""#
        );
        // Пустой аргумент не должен исчезнуть.
        assert_eq!(broker_command_line(r"C:\a.exe", &[String::new()]), r#"C:\a.exe """#);
    }

    /// Хвостовые слэши и кавычки внутри — те самые места, где наивное
    /// экранирование ломает разбор и склеивает соседние аргументы.
    #[test]
    fn command_line_escapes_slashes_and_quotes() {
        assert_eq!(broker_quote(r"C:\путь с пробелом\"), "\"C:\\путь с пробелом\\\\\"");
        assert_eq!(broker_quote(r#"он сказал "нет""#), "\"он сказал \\\"нет\\\"\"");
        assert_eq!(broker_quote("простой"), "простой");
    }
}
