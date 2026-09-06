// Проба адреса и ключа модели — ОДИН код на окно и установщик.
//
// Включается через `include!` в оболочку (`shell/src/main.rs`) и в установщик
// (`setup/src/install.rs`). До 07.09 у каждого была своя копия, и они
// расходились ровно там, где это видно человеку: на HTTP 200 без списка
// моделей окно отвечало «ok», а установщик — «не ok». Установщик прав: z.ai
// на неверный ключ отвечает 200 с телом {"success":false,"msg":"Authentication
// Failed"}, и «Отвечает» над таким ответом — ложь. Один ключ обязан
// проверяться одинаково в обоих местах продукта (ревью 06.09, §4 п. 12).
//
// Что здесь: гард исходящего адреса (ключ уезжает заголовком на адрес, который
// пришёл из веб-части), разбор списка моделей и сама проба. Сетевой вызов
// блокирующий; оболочка заворачивает его в spawn_blocking сама.

/// Куда позволено ходить с ключом владельца. https — куда угодно (туда и ходят
/// облачные модели), http — только к себе и в локальную сеть (Ollama, LM
/// Studio, соседняя машина в Tailscale). Без этого разбора адрес из веб-части
/// был бы однострочным вывозом ключа наружу.
fn outbound_url_ok(url: &str) -> Result<(), String> {
    let u = url.trim();
    let lower = u.to_lowercase();
    if lower.starts_with("https://") {
        return Ok(());
    }
    let Some(rest) = lower.strip_prefix("http://") else {
        return Err("адрес должен начинаться с https:// или http://".into());
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or("");
    let authority = authority.rsplit('@').next().unwrap_or(authority);
    let host = match authority.strip_prefix('[') {
        Some(v6) => v6.split(']').next().unwrap_or(""),
        None => authority.split(':').next().unwrap_or(""),
    };
    if host_is_local(host) {
        Ok(())
    } else {
        Err(format!(
            "по http ключ уходит только на свою машину или в локальную сеть, а тут {host}; снаружи нужен https://"
        ))
    }
}

/// Свой ли это адрес: петля, частные сети RFC1918, сеть Tailscale, .local.
fn host_is_local(host: &str) -> bool {
    if host == "localhost" || host == "::1" || host.ends_with(".local") || host.ends_with(".localhost") {
        return true;
    }
    let octets: Vec<u8> = host.split('.').filter_map(|p| p.parse::<u8>().ok()).collect();
    if octets.len() != 4 || host.split('.').count() != 4 {
        return false;
    }
    match (octets[0], octets[1]) {
        (127, _) => true,
        (10, _) => true,
        (192, 168) => true,
        (172, b) if (16..=31).contains(&b) => true,
        (100, b) if (64..=127).contains(&b) => true,
        _ => false,
    }
}

/// Идентификаторы моделей из ответа /models (OpenAI-совместимая форма
/// `{"data":[{"id":…}]}`). Без потолка: у прежней копии окна стояло
/// `truncate(80)`, и селектор терял модели провайдеров с длинным списком.
fn model_ids(body: &str) -> Vec<String> {
    let mut ids: Vec<String> = serde_json::from_str::<serde_json::Value>(body)
        .ok()
        .and_then(|v| v.get("data").and_then(|d| d.as_array()).cloned())
        .unwrap_or_default()
        .iter()
        .filter_map(|m| m.get("id").and_then(|i| i.as_str()).map(|s| s.to_string()))
        .collect();
    ids.sort();
    ids.dedup();
    ids
}

/// Живая проба: GET {base}/models (Anthropic-совместимые — /v1/models с
/// x-api-key, остальные — Bearer). -> (подошло ли, фраза владельцу, список).
/// Успех — ТОЛЬКО настоящий список моделей; 200 без списка — не успех.
fn probe_model_blocking(base_url: &str, key: &str, framework: &str) -> (bool, String, Vec<String>) {
    let anthropic = framework.trim().eq_ignore_ascii_case("anthropic");
    let base = base_url.trim().trim_end_matches('/');
    let url = if anthropic {
        if base.ends_with("/v1") { format!("{base}/models") } else { format!("{base}/v1/models") }
    } else {
        format!("{base}/models")
    };
    if let Err(why) = outbound_url_ok(&url) {
        return (false, why, Vec::new());
    }
    // redirects(0) — половина гарда outbound_url_ok, без которой вторая не
    // работает: ureq по умолчанию идёт по переадресациям до пяти хопов и
    // снимает на чужом хосте только Authorization, а НЕ кастомный x-api-key,
    // которым ходит ветка anthropic/z.ai. Разрешённый гардом адрес отвечал бы
    // 302 куда угодно — и ключ уезжал бы туда одним хопом.
    let agent = ureq::AgentBuilder::new()
        .timeout(std::time::Duration::from_secs(12))
        .redirects(0)
        .build();
    let mut req = agent.get(&url);
    if !key.trim().is_empty() {
        if anthropic {
            req = req.set("x-api-key", key.trim()).set("anthropic-version", "2023-06-01");
        } else {
            req = req.set("Authorization", &format!("Bearer {}", key.trim()));
        }
    }
    match req.call() {
        Ok(resp) => {
            // С redirects(0) переадресация возвращается ответом: говорим о ней
            // прямо, а не выдаём пустой список за «странный ответ».
            if (300..400).contains(&resp.status()) {
                let code = resp.status();
                let to: String = resp.header("location").unwrap_or("").chars().take(120).collect();
                return (
                    false,
                    format!("Адрес отвечает переадресацией ({code}) на {to} — ключ туда не отправляю; укажи конечный адрес"),
                    Vec::new(),
                );
            }
            let body = resp.into_string().unwrap_or_default();
            let models = model_ids(&body);
            if models.is_empty() {
                (
                    false,
                    format!(
                        "Ответ пришёл, но это не список моделей: {}",
                        body.trim().chars().take(160).collect::<String>()
                    ),
                    models,
                )
            } else {
                (true, format!("Отвечает: моделей доступно {}", models.len()), models)
            }
        }
        Err(ureq::Error::Status(401, _)) | Err(ureq::Error::Status(403, _)) => (false, "Ключ не подошёл".to_string(), Vec::new()),
        Err(ureq::Error::Status(404, _)) => (false, "По этому адресу нет /models".to_string(), Vec::new()),
        Err(ureq::Error::Status(code, _)) => (false, format!("Ответ {code}"), Vec::new()),
        Err(err) => (false, format!("Нет связи: {}", err.to_string().chars().take(120).collect::<String>()), Vec::new()),
    }
}

#[cfg(test)]
mod model_probe_tests {
    use super::*;

    #[test]
    fn outbound_guard_lets_https_and_local_http_only() {
        assert!(outbound_url_ok("https://api.openai.com/v1/models").is_ok());
        assert!(outbound_url_ok("http://127.0.0.1:11434/v1/models").is_ok());
        assert!(outbound_url_ok("http://localhost:1234/v1/models").is_ok());
        assert!(outbound_url_ok("http://192.168.1.5:1234/v1/models").is_ok());
        assert!(outbound_url_ok("http://100.101.102.103:8094/api").is_ok());
        assert!(outbound_url_ok("http://[::1]:5011/models").is_ok());
        assert!(outbound_url_ok("http://evil.example.com/v1/models").is_err());
        assert!(outbound_url_ok("http://user@evil.example.com/v1/models").is_err());
        assert!(outbound_url_ok("file:///C:/windows/system32").is_err());
        assert!(outbound_url_ok("javascript:alert(1)").is_err());
    }

    #[test]
    fn local_hosts() {
        assert!(host_is_local("127.0.0.1"));
        assert!(host_is_local("10.0.0.7"));
        assert!(host_is_local("172.16.0.1") && !host_is_local("172.32.0.1"));
        assert!(host_is_local("100.64.0.1") && !host_is_local("100.128.0.1"));
        assert!(host_is_local("nas.local"));
        assert!(!host_is_local("8.8.8.8"));
        assert!(!host_is_local("1.2.3"));
    }

    /// Список не режется: селектор моделей показывает всё, что отдал провайдер.
    #[test]
    fn model_ids_are_not_truncated() {
        let rows: Vec<String> = (0..120).map(|i| format!("{{\"id\":\"m{i:03}\"}}")).collect();
        let body = format!("{{\"data\":[{}]}}", rows.join(","));
        assert_eq!(model_ids(&body).len(), 120);
        assert_eq!(model_ids("{\"success\":false,\"msg\":\"Authentication Failed\"}").len(), 0);
        assert_eq!(model_ids("не json"), Vec::<String>::new());
    }

    /// Чужой адрес по http — отказ до сети, ключ никуда не уходит.
    #[test]
    fn probe_refuses_foreign_http_before_any_request() {
        let (ok, note, models) = probe_model_blocking("http://evil.example.com/v1", "sk-secret", "openai");
        assert!(!ok && models.is_empty());
        assert!(note.contains("https://"), "{note}");
    }
}
