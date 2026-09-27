//! Снимок памяти агента перед обновлением (1.2, 27.09) — общий текст с окном и
//! службой (`common/backup.rs`): там же расписание («раз в неделю») и уборка.
#![allow(dead_code)]

include!("../../common/backup.rs");

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshot_takes_memory_and_settings_but_not_logs_or_login() {
        let r = std::env::temp_dir().join(format!("helene-backup-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&r);
        let put = |rel: &str, text: &str| {
            let p = r.join(rel);
            std::fs::create_dir_all(p.parent().unwrap()).unwrap();
            std::fs::write(p, text).unwrap();
        };
        put("helene.json", "{}");
        put("data/soul/SOUL.md", "душа");
        put("data/memory/2026-09-27.md", "день");
        put("data/workspace/notes.md", "заметки");
        put("data/runner.log", "журнал");
        put("data/relay/local_auth/auth.json", "секрет");
        put("data/voice/model.bin", "тяжёлое");
        put("agents/mira/helene.json", "{}");
        put("agents/mira/data/soul/SOUL.md", "душа соседа");
        put("agents/mira/data/runner.log", "журнал соседа");
        let never = AtomicBool::new(false);
        let (path, n) = snapshot(&r, &r.join("backups"), "before-1.2.0", &never).unwrap();
        assert!(path.file_name().unwrap().to_string_lossy().ends_with("-before-1.2.0.zip"));
        let file = std::fs::File::open(&path).unwrap();
        let mut z = zip::ZipArchive::new(file).unwrap();
        let names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().to_string()).collect();
        for want in ["helene.json", "data/soul/SOUL.md", "data/memory/2026-09-27.md", "data/workspace/notes.md",
                     "agents/mira/helene.json", "agents/mira/data/soul/SOUL.md"] {
            assert!(names.iter().any(|n| n == want), "нет {want}: {names:?}");
        }
        for never_in in ["data/runner.log", "data/relay/local_auth/auth.json", "data/voice/model.bin",
                         "agents/mira/data/runner.log"] {
            assert!(!names.iter().any(|n| n == never_in), "лишнее {never_in}: {names:?}");
        }
        assert_eq!(n as usize, names.len());
        // Второй снимок в ту же минуту — рядом, а не поверх.
        let (path2, _) = snapshot(&r, &r.join("backups"), "before-1.2.0", &never).unwrap();
        assert_ne!(path, path2);
        assert!(path.is_file() && path2.is_file());
        let gone = prune(&r.join("backups"), "-before-", 1);
        assert_eq!(gone.len(), 1);
        assert_eq!(list(&r.join("backups")).len(), 1);
        let _ = std::fs::remove_dir_all(&r);
    }

    #[test]
    fn schedule_is_weekly_by_default_and_can_be_off() {
        let root = Path::new("C:/x/Helene");
        let p = policy(root, None);
        assert_eq!(p.every_days, DEFAULT_EVERY_DAYS);
        assert_eq!(p.keep, DEFAULT_KEEP);
        assert!(p.dir.ends_with("backups"));
        // Абсолютная папка — своя на каждой системе: на macOS `D:/…` не абсолютна, и
        // прогон macos.yml 27.09 честно пристегнул её к папке программы — врал стенд.
        let abs = if cfg!(windows) { "D:/копии" } else { "/Volumes/копии" };
        let cfg = serde_json::json!({"backup": {"every_days": 0, "keep": 3, "dir": abs}});
        let p = policy(root, Some(&cfg));
        assert_eq!(p.every_days, 0);
        assert_eq!(p.keep, 3);
        assert_eq!(p.dir, PathBuf::from(abs));
        // Относительная — от папки программы.
        let cfg = serde_json::json!({"backup": {"dir": "мои-копии"}});
        assert_eq!(policy(root, Some(&cfg)).dir, root.join("мои-копии"));
        let week = 7 * 86_400;
        assert!(due(7, None, 1_000_000), "снимков ещё не было — пора");
        assert!(!due(7, Some(1_000_000), 1_000_000 + week - 1));
        assert!(due(7, Some(1_000_000), 1_000_000 + week));
        assert!(!due(0, None, 1_000_000), "выключено — никогда");
        assert_eq!(stamp_at(0), "1970-01-01_0000");
        assert_eq!(stamp_at(1_790_470_000), "2026-09-27_0046");
    }
}
