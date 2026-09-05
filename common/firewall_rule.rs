// Правило брандмауэра для трубы Hélène — ОДИН текст на обе подсистемы.
//
// Файл включается через `include!` и в оболочку (`shell/src/main.rs`), и в
// службу (`svc/src/main.rs`). Так — потому что имя правила у них совпадает до
// буквы («Helene (<порт>)»), а netsh требует уникальности имени: кто ставит
// вторым, тот переписывает правило первого. Пока эти строки жили в двух
// местах, служба при каждой загрузке машины меняла суженное правило окна на
// своё — без `profile=` (все профили, включая «Общедоступная») и без
// `remoteip=` (весь свет), а расписка окна продолжала обещать «только своя
// подсеть и Tailscale».
//
// Значит: сужение правится ЗДЕСЬ и сразу у обеих подсистем — вместе с текстом,
// которым продукт отчитывается о нём владельцу.

/// Без `profile=` умолчание netsh — ВСЕ профили, включая «Общедоступная»:
/// одно нажатие дома открывало бы порт и в кафе.
const FIREWALL_PROFILE: &str = "profile=domain,private";

/// Без `remoteip=` — весь свет. `LocalSubnet` — своя подсеть,
/// `100.64.0.0/10` — CGNAT-диапазон, в котором живут адреса Tailscale.
const FIREWALL_REMOTEIP: &str = "remoteip=LocalSubnet,100.64.0.0/10";

/// То же самое словами — для расписки владельцу. Расписка обязана совпадать
/// с правилом, поэтому строка лежит рядом с ним, а не в тексте интерфейса.
const FIREWALL_SCOPE_HUMAN: &str = "домашняя и рабочая сеть, только своя подсеть и Tailscale";

/// Имя правила: одно на продукт и порт — по нему же правило и снимают.
fn firewall_rule_title(product: &str, port: u16) -> String {
    format!("{product} ({port})")
}

/// Аргументы `netsh advfirewall firewall add rule`.
///
/// `program = None` — только когда путь программы неизвестен (питон найден по
/// PATH): сузить правило по программе тогда не по чему, но профиль и адреса
/// остаются суженными в любом случае.
fn firewall_add_args(title: &str, port: u16, program: Option<&str>) -> Vec<String> {
    let mut args: Vec<String> = vec![
        "advfirewall".into(),
        "firewall".into(),
        "add".into(),
        "rule".into(),
        format!("name={title}"),
        "dir=in".into(),
        "action=allow".into(),
        "protocol=TCP".into(),
        format!("localport={port}"),
    ];
    if let Some(program) = program {
        args.push(format!("program={program}"));
    }
    args.push(FIREWALL_PROFILE.into());
    args.push(FIREWALL_REMOTEIP.into());
    args
}

/// Сужение — единственное, что отделяет «телефон дома» от «порт открыт в кафе»,
/// и оно уже однажды разъехалось между окном и службой. Поэтому оно накрыто
/// тестом здесь же: тест едет вместе с файлом в обе подсистемы.
#[cfg(test)]
mod firewall_rule_tests {
    use super::*;

    #[test]
    fn add_rule_is_narrow() {
        let args = firewall_add_args("Helene (8765)", 8765, Some(r"C:\Helene\runtime\python.exe"));
        assert!(args.contains(&"profile=domain,private".to_string()), "{args:?}");
        assert!(
            args.contains(&"remoteip=LocalSubnet,100.64.0.0/10".to_string()),
            "{args:?}"
        );
        assert!(args.contains(&"name=Helene (8765)".to_string()), "{args:?}");
        assert!(args.contains(&"localport=8765".to_string()), "{args:?}");
        assert!(
            args.contains(&r"program=C:\Helene\runtime\python.exe".to_string()),
            "{args:?}"
        );
    }

    /// Питон найден по PATH — программу в правило не пишем, но профиль и адреса
    /// остаются суженными: иначе «не знаю программу» означало бы «открой всем».
    #[test]
    fn without_program_still_narrow() {
        let args = firewall_add_args("Helene (8765)", 8765, None);
        assert!(!args.iter().any(|a| a.starts_with("program=")), "{args:?}");
        assert!(args.contains(&"profile=domain,private".to_string()), "{args:?}");
        assert!(
            args.contains(&"remoteip=LocalSubnet,100.64.0.0/10".to_string()),
            "{args:?}"
        );
    }

    /// Имя правила у окна и у службы должно совпадать до буквы: netsh требует
    /// уникальности имени, и по нему же правило снимают.
    #[test]
    fn title_is_product_and_port() {
        assert_eq!(firewall_rule_title("Helene", 8765), "Helene (8765)");
    }
}
