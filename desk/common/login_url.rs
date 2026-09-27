// Ссылка входа в подписку ChatGPT — из вывода помощника реле.
//
// 27.09 (1.2.3): у Егора помощник входа (питон под `helene-relay login`) поднял свой
// порт 1455, а браузер так и не открыл, и окно висело на «Ждём вход в браузере» без
// единого выхода: ссылку знал только помощник. Он печатает её сам («If your browser did
// not open, navigate to this URL…») в stderr, реле отдаёт его вывод своему родителю.
// Здесь её ловим: окно и мастер показывают ссылку и кнопку «Открыть страницу входа» —
// открывает её уже сам процесс окна, а не третье поколение детей.
//
// Файл включается через `include!` (оболочка и установщик).

/// Ссылка авторизации в строке вывода, если она там есть.
fn login_url_in(line: &str) -> Option<String> {
    line.split_whitespace()
        .find(|w| w.starts_with("https://") && w.contains("/oauth/authorize?"))
        .map(|w| w.trim_end_matches(['.', ',', ';', ')', '"', '\'']).to_string())
}

/// Читать stdout и stderr процесса входа до конца, класть найденную ссылку в `slot`.
/// Трубы надо вычитывать всё время: иначе полный буфер остановил бы помощника.
fn watch_login_output(child: &mut std::process::Child, slot: &'static std::sync::Mutex<Option<String>>) {
    use std::io::BufRead;
    if let Ok(mut known) = slot.lock() {
        *known = None;
    }
    let pipes: Vec<Box<dyn std::io::Read + Send>> = [
        child.stdout.take().map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
        child.stderr.take().map(|p| Box::new(p) as Box<dyn std::io::Read + Send>),
    ]
    .into_iter()
    .flatten()
    .collect();
    for pipe in pipes {
        std::thread::spawn(move || {
            let mut reader = std::io::BufReader::new(pipe);
            let mut buf = Vec::new();
            loop {
                buf.clear();
                match reader.read_until(b'\n', &mut buf) {
                    Ok(0) | Err(_) => break,
                    Ok(_) => {
                        if let Some(url) = login_url_in(&String::from_utf8_lossy(&buf)) {
                            if let Ok(mut known) = slot.lock() {
                                *known = Some(url);
                            }
                        }
                    }
                }
            }
        });
    }
}

#[cfg(test)]
mod login_url_tests {
    use super::login_url_in;

    #[test]
    fn helper_line_gives_the_url() {
        let url = "https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_x&state=abc";
        assert_eq!(login_url_in(url).as_deref(), Some(url));
        assert_eq!(login_url_in(&format!("{url}.")).as_deref(), Some(url));
        assert_eq!(login_url_in("Starting local login server on http://localhost:1455"), None);
        assert_eq!(login_url_in(". If your browser did not open, navigate to this URL to authenticate: "), None);
    }
}
