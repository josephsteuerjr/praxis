// Кто живёт в этой установке — то же правило, что в `localharness/agents.py`.
//
// Один и тот же список читают трое: оболочка (кого поднимать, что в трее, куда
// смотрит окно), служба (кого поднимать без окна) и питон (раннер, канал, окно
// настроек). Правило записано словами в питоновском модуле; здесь его вторая
// голова, а сверяет их общий набор случаев `tests/roster-cases.json` — тест в
// самом низу этого файла и `tests/t_agents.py` читают ОДИН файл.
//
//     <установка>/helene.json              — первый агент, id `main`
//     <установка>/agents/<id>/helene.json  — каждый следующий, id = имя папки
//
// ⚠ Почему не «оболочка спрашивает питон». Список нужен ДО подъёма детей: на
// нём строится и трей, и адрес вебвью, и решение «этот порт наш или чужой».
// Спрашивать его у процесса, которого ещё нет, — значит поставить окно в
// зависимость от того, что оно само запускает.

/// Один агент установки: где его конфиг, где его дом, на каком он порту.
#[derive(Clone, Debug)]
struct AgentEntry {
    id: String,
    name: String,
    dir: PathBuf,
    config: PathBuf,
    tree: PathBuf,
    port: u16,
    enabled: bool,
    /// Корневой агент установки (`helene.json` рядом с программой).
    base: bool,
    /// Порт занят другим агентом ЭТОЙ ЖЕ установки — не поднимаем, но показываем.
    conflict: String,
}

impl AgentEntry {
    /// Нужен окну (оболочка); служба тем же файлом только поднимает детей.
    #[allow(dead_code)]
    fn as_json(&self) -> serde_json::Value {
        serde_json::json!({
            "id": self.id,
            "name": self.name,
            "dir": self.dir.to_string_lossy(),
            "config": self.config.to_string_lossy(),
            "tree": self.tree.to_string_lossy(),
            "port": self.port,
            "enabled": self.enabled,
            "base": self.base,
            "conflict": self.conflict,
        })
    }
}

const ROSTER_DIR: &str = "agents";      // как в ui-kit/contract.json
const BASE_AGENT_ID: &str = "main";     // id корневого агента — там же
/// Порт канала по умолчанию — там же. Живёт здесь, а не в каждом из двух
/// приложений: у службы он был литералом `8094`, и «сменить порт по умолчанию»
/// означало найти оба места.
const DESK_PORT: u16 = 8094;

/// Имя папки годится в id: строчная латиница, цифры и дефис, до 32 знаков.
/// ⚠ `Mira` не годится намеренно: id уезжает в командную строку ярлыка и в
/// адрес окна, и «тот же id в другом регистре» — это второй агент, которого нет.
fn agent_id_ok(name: &str) -> bool {
    let bytes = name.as_bytes();
    if bytes.is_empty() || bytes.len() > 32 {
        return false;
    }
    if !(bytes[0].is_ascii_lowercase() || bytes[0].is_ascii_digit()) {
        return false;
    }
    bytes
        .iter()
        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || *b == b'-')
}

/// Конфиг агента; нечитаемый или битый — пустой объект, а не отказ.
/// Один лишний запятой у соседа не имеет права спрятать из окна всех.
fn agent_config(path: &Path) -> serde_json::Value {
    let empty = serde_json::json!({});
    let Ok(bytes) = std::fs::read(path) else {
        return empty;
    };
    let Ok(text) = decode_config(&bytes) else {
        return empty;
    };
    match serde_json::from_str::<serde_json::Value>(&text) {
        Ok(v) if v.is_object() => v,
        _ => empty,
    }
}

/// Имя агента из его конфига: `agent.name`, потом старое `telegram.agent_name`.
fn agent_title(cfg: &serde_json::Value, fallback: &str) -> String {
    let pick = |v: Option<&serde_json::Value>| {
        v.and_then(|s| s.as_str())
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(String::from)
    };
    pick(cfg.get("agent").and_then(|a| a.get("name")))
        .or_else(|| pick(cfg.get("telegram").and_then(|t| t.get("agent_name"))))
        .unwrap_or_else(|| fallback.to_string())
}

fn agent_at(dir: &Path, id: &str, base: bool, index: u16) -> AgentEntry {
    let config = dir.join(CONFIG_NAME);
    let cfg = agent_config(&config);
    let port = cfg
        .get("port")
        .and_then(|v| v.as_u64())
        .filter(|p| *p >= 1 && *p <= u16::MAX as u64)
        .map(|p| p as u16)
        .unwrap_or_else(|| DESK_PORT.saturating_add(index));
    let tree_raw = cfg
        .get("tree")
        .and_then(|v| v.as_str())
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .unwrap_or("data");
    let tree = {
        let p = Path::new(tree_raw);
        if p.is_absolute() {
            p.to_path_buf()
        } else {
            dir.join(p)
        }
    };
    AgentEntry {
        id: id.to_string(),
        name: agent_title(&cfg, if base { "Агент" } else { id }),
        dir: dir.to_path_buf(),
        config,
        tree,
        port,
        enabled: cfg.get("enabled").and_then(|v| v.as_bool()).unwrap_or(true),
        base,
        conflict: String::new(),
    }
}

/// Все агенты установки: корневой первым, соседи по алфавиту.
///
/// Корневой есть ВСЕГДА, даже когда `helene.json` ещё не написан: пустой список
/// в окне значил бы «агентов нет», а на первом запуске их не нет — их ещё не
/// настроили, и это разные вещи.
fn roster(base_dir: &Path) -> Vec<AgentEntry> {
    let mut out = vec![agent_at(base_dir, BASE_AGENT_ID, true, 0)];
    let mut taken: Vec<(u16, String)> = vec![(out[0].port, out[0].id.clone())];
    let mut kids: Vec<PathBuf> = match std::fs::read_dir(base_dir.join(ROSTER_DIR)) {
        Ok(entries) => entries
            .filter_map(|e| e.ok())
            .map(|e| e.path())
            .filter(|p| p.is_dir())
            .collect(),
        Err(_) => Vec::new(),
    };
    kids.sort_by_key(|p| {
        p.file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("")
            .to_lowercase()
    });
    let mut index: u16 = 0;
    for kid in kids {
        let Some(name) = kid.file_name().and_then(|n| n.to_str()) else {
            continue;
        };
        if !agent_id_ok(name) || name == BASE_AGENT_ID || !kid.join(CONFIG_NAME).is_file() {
            continue;
        }
        index += 1;
        let mut got = agent_at(&kid, name, false, index);
        if let Some((_, holder)) = taken.iter().find(|(port, _)| *port == got.port) {
            got.conflict = holder.clone();
        } else {
            taken.push((got.port, got.id.clone()));
        }
        out.push(got);
    }
    out
}

/// Агент по id. Неизвестный — None: ярлык на удалённого агента обязан сказать
/// об этом, а не открыть молча окно чужого.
#[allow(dead_code)]              // зовёт оболочка (--agent, переключатель), служба — нет
fn find_agent(base_dir: &Path, id: &str) -> Option<AgentEntry> {
    let wanted = id.trim().to_lowercase();
    if wanted.is_empty() {
        return None;
    }
    roster(base_dir).into_iter().find(|a| a.id == wanted)
}

/// Кого поднимать: включённые и без спора за порт.
fn raisable(base_dir: &Path) -> Vec<AgentEntry> {
    roster(base_dir)
        .into_iter()
        .filter(|a| a.enabled && a.conflict.is_empty())
        .collect()
}

#[cfg(test)]
mod roster_tests {
    use super::*;

    /// Те же случаи, что читает `tests/t_agents.py`. Файл один — разъезд двух
    /// голов ловится тем, что падает разошедшаяся, а не обе молчат.
    #[test]
    fn cases_from_the_shared_file() {
        let cases: serde_json::Value =
            serde_json::from_str(include_str!("../tests/roster-cases.json")).unwrap();
        for (n, case) in cases["cases"].as_array().unwrap().iter().enumerate() {
            let name = case["name"].as_str().unwrap();
            let root = std::env::temp_dir()
                .join(format!("helene-roster-{}-{n}", std::process::id()));
            let _ = std::fs::remove_dir_all(&root);
            for (rel, body) in case["files"].as_object().unwrap() {
                let path = root.join(rel);
                std::fs::create_dir_all(path.parent().unwrap()).unwrap();
                let text = match body {
                    serde_json::Value::String(s) => s.clone(),
                    other => serde_json::to_string_pretty(other).unwrap(),
                };
                std::fs::write(&path, text).unwrap();
            }
            let got = roster(&root);
            let want = case["expect"].as_array().unwrap();
            assert_eq!(got.len(), want.len(), "{name}: длина списка");
            for (mine, wish) in got.iter().zip(want) {
                assert_eq!(mine.id, wish["id"].as_str().unwrap(), "{name}: id");
                assert_eq!(mine.name, wish["name"].as_str().unwrap(), "{name}: имя");
                assert_eq!(mine.port as u64, wish["port"].as_u64().unwrap(), "{name}: порт");
                assert_eq!(mine.enabled, wish["enabled"].as_bool().unwrap(), "{name}: включён");
                assert_eq!(mine.base, wish["base"].as_bool().unwrap(), "{name}: корневой");
                assert_eq!(mine.conflict, wish["conflict"].as_str().unwrap(), "{name}: спор");
                let rel = mine
                    .tree
                    .strip_prefix(&root)
                    .unwrap()
                    .to_string_lossy()
                    .replace('\\', "/");
                assert_eq!(rel, wish["tree"].as_str().unwrap(), "{name}: дом");
            }
            let _ = std::fs::remove_dir_all(&root);
        }
    }

    #[test]
    fn raisable_skips_disabled_and_conflicts() {
        let root = std::env::temp_dir().join(format!("helene-roster-raise-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(root.join("agents/off")).unwrap();
        std::fs::create_dir_all(root.join("agents/clash")).unwrap();
        std::fs::write(root.join(CONFIG_NAME), r#"{"port": 8094}"#).unwrap();
        std::fs::write(root.join("agents/off").join(CONFIG_NAME), r#"{"enabled": false}"#).unwrap();
        std::fs::write(root.join("agents/clash").join(CONFIG_NAME), r#"{"port": 8094}"#).unwrap();
        let ids: Vec<String> = raisable(&root).into_iter().map(|a| a.id).collect();
        assert_eq!(ids, vec!["main".to_string()]);
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn unknown_id_is_not_silently_the_base_one() {
        let root = std::env::temp_dir().join(format!("helene-roster-find-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        std::fs::write(root.join(CONFIG_NAME), r#"{"agent": {"name": "Hélène"}}"#).unwrap();
        assert!(find_agent(&root, "mira").is_none());
        assert!(find_agent(&root, "").is_none());
        assert_eq!(find_agent(&root, "MAIN").unwrap().name, "Hélène");
        let _ = std::fs::remove_dir_all(&root);
    }
}
