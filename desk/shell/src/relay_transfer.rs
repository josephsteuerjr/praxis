//! Native-only export: the renderer sees a receipt, never OAuth tokens.
use std::{io::Read, path::Path, time::Duration};
use serde_json::{json, Value};

pub fn target(base: &str) -> Result<url::Url, String> {
    let mut u = url::Url::parse(base).map_err(|_| "Некорректный адрес сервера")?;
    let loopback = matches!(u.host_str(), Some("localhost" | "127.0.0.1" | "[::1]"));
    if !(u.scheme() == "https" || (u.scheme() == "http" && loopback))
        || u.host_str().is_none() || !u.username().is_empty() || u.password().is_some()
        || u.query().is_some() || u.fragment().is_some() {
        return Err("Для передачи входа нужен HTTPS сервера либо локальный защищённый туннель".into());
    }
    let path = format!("{}/api/relay/auth/import", u.path().trim_end_matches('/'));
    u.set_path(&path);
    Ok(u)
}

pub fn transfer(home: &Path, base: &str, key: &str, replace: bool) -> Result<Value, String> {
    let url = target(base)?;
    if key.is_empty() { return Err("В настройках подключения нет ключа владельца сервера".into()); }
    let primary = home.join("local_auth/accounts/primary/auth.json");
    let path = if primary.is_file() { primary } else { home.join("local_auth/auth.json") };
    let file = std::fs::File::open(path).map_err(|_| "Сначала войди в ChatGPT в этом приложении")?;
    let mut bytes = Vec::new();
    file.take(128 * 1024 + 1).read_to_end(&mut bytes).map_err(|_| "Не удалось прочитать локальный вход")?;
    if bytes.len() > 128 * 1024 { return Err("Файл входа слишком велик".into()); }
    let auth: Value = serde_json::from_slice(&bytes).map_err(|_| "Локальный файл входа повреждён")?;
    if auth.get("OPENAI_API_KEY").is_some_and(|v| v.as_str().is_some_and(|s| !s.is_empty()))
        || ["access_token", "refresh_token", "id_token"].iter().any(|k|
            auth.get("tokens").and_then(|t| t.get(k)).and_then(Value::as_str).is_none_or(|v| v.is_empty())) {
        return Err("Нужен полный вход ChatGPT по подписке".into());
    }
    let payload = json!({"auth": auth, "replace": replace});
    // A redirect must never forward either the server key or subscription tokens.
    let agent = ureq::AgentBuilder::new().redirects(0).timeout(Duration::from_secs(25)).build();
    let response = agent.post(url.as_str()).set("Authorization", &format!("Bearer {key}"))
        .set("Content-Type", "application/json").send_string(&payload.to_string())
        .map_err(|err| match err {
            ureq::Error::Status(code, _) => format!("Сервер отказал в приёме входа (HTTP {code}); проверь ключ владельца и версию сервера"),
            _ => "Не удалось передать вход: проверь связь и сертификат сервера".into(),
        })?;
    if response.status() != 200 { return Err("Сервер не подтвердил приём входа".into()); }
    let mut raw = String::new();
    response.into_reader().take(8192).read_to_string(&mut raw).map_err(|_| "Нет квитанции сервера")?;
    let said: Value = serde_json::from_str(&raw).map_err(|_| "Сервер вернул некорректную квитанцию")?;
    let id = said.get("id").and_then(Value::as_str).filter(|id| id.len() == 32 && id.bytes().all(|c| c.is_ascii_hexdigit()))
        .ok_or("В квитанции нет номера запроса")?;
    // Never echo an arbitrary response, even from the selected server.
    Ok(json!({"id": id, "phase": "queued", "note": "Вход передан выбранному серверу; ждём применения"}))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validates_destination_before_credentials() {
        for base in ["http://public.example", "https://key@public.example", "https://a/?key=x", "https://a/#fragment", "file:///tmp"] {
            assert!(target(base).is_err(), "{base}");
        }
        assert_eq!(target("https://a/helene/").unwrap().as_str(), "https://a/helene/api/relay/auth/import");
        assert!(target("http://127.0.0.1:8000").is_ok());
    }

    #[test]
    fn transfers_synthetic_auth_and_does_not_follow_redirects() {
        use std::io::Write;
        let home = std::env::temp_dir().join(format!("helene-auth-test-{}-{}", std::process::id(),
            std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().as_nanos()));
        std::fs::create_dir_all(home.join("local_auth")).unwrap();
        std::fs::write(home.join("local_auth/auth.json"), json!({"tokens": {
            "access_token": "fake-access", "refresh_token": "fake-refresh", "id_token": "fake-id"}}).to_string()).unwrap();
        for status in [200, 302] {
            let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
            let base = format!("http://{}", listener.local_addr().unwrap());
            let server = std::thread::spawn(move || {
                let (mut socket, _) = listener.accept().unwrap();
                socket.set_read_timeout(Some(Duration::from_secs(5))).unwrap();
                let mut request = Vec::new();
                let boundary = loop {
                    let mut chunk = [0; 1024];
                    let got = socket.read(&mut chunk).unwrap();
                    assert!(got > 0);
                    request.extend_from_slice(&chunk[..got]);
                    if let Some(pos) = request.windows(4).position(|s| s == b"\r\n\r\n") { break pos + 4; }
                };
                let headers = String::from_utf8_lossy(&request[..boundary]).to_lowercase();
                assert!(headers.starts_with("post /api/relay/auth/import "));
                assert!(headers.contains("authorization: bearer fake-owner"));
                let size: usize = headers.lines().find_map(|l| l.strip_prefix("content-length: ")).unwrap().trim().parse().unwrap();
                while request.len() < boundary + size {
                    let mut chunk = [0; 1024];
                    let got = socket.read(&mut chunk).unwrap();
                    assert!(got > 0); request.extend_from_slice(&chunk[..got]);
                }
                let payload: Value = serde_json::from_slice(&request[boundary..boundary + size]).unwrap();
                assert_eq!(payload["auth"]["tokens"]["access_token"], "fake-access");
                let body = json!({"id": "a".repeat(32), "unexpected_secret": "fake-access"}).to_string();
                write!(socket, "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nLocation: https://example.invalid/leak\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            });
            let result = transfer(&home, &base, "fake-owner", false);
            if status == 200 { assert!(!result.unwrap().to_string().contains("fake-access")); }
            else { assert!(result.is_err()); }
            server.join().unwrap();
        }
        std::fs::remove_dir_all(home).unwrap();
    }
}
