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
        put("data/memory/.state/retention.json", "{\"schema\":\"praxis.retention.v1\"}");
        put("data/workspace/inbox/pending.md", "необработанный ввод владельца");
        put("data/workspace/notes.md", "заметки");
        put("data/runner.log", "журнал");
        put("data/relay/local_auth/auth.json", "секрет");
        put("data/voice/model.bin", "тяжёлое");
        put("data/models/whisper/model.bin", "скачанная модель");
        put("data/models/piper/voice.onnx", "скачанный голос");
        put("data/models/custom/weights.bin", "модель владельца");
        put("data/workspace/project/node_modules/module.js", "зависимость");
        put("data/workspace/project/.venv/pyvenv.cfg", "окружение");
        put("data/workspace/project/__pycache__/module.pyc", "кэш");
        put("data/workspace/project/.git/objects/object", "история");
        put("data/workspace/project/build/design.md", "рабочие материалы");
        put("agents/mira/helene.json", "{}");
        put("agents/mira/data/soul/SOUL.md", "душа соседа");
        put("agents/mira/data/runner.log", "журнал соседа");
        put("agents/mira/data/models/whisper/model.bin", "модель соседа");
        put("agents/mira/data/workspace/node_modules/pkg/index.js", "зависимость соседа");
        put("agents/mira/data/workspace/project/main.rs", "проект соседа");
        let never = AtomicBool::new(false);
        let (path, n) = snapshot(&r, &r.join("backups"), "before-1.2.0", &never).unwrap();
        assert!(path.file_name().unwrap().to_string_lossy().ends_with("-before-1.2.0.zip"));
        let file = std::fs::File::open(&path).unwrap();
        let mut z = zip::ZipArchive::new(file).unwrap();
        let names: Vec<String> = (0..z.len()).map(|i| z.by_index(i).unwrap().name().to_string()).collect();
        // Худой состав (01.10): память, душа, конфиги, манифест леджера и
        // необработанный inbox. Рабочие проекты — материалы леджера, их байты
        // и .git в снимок не ездят.
        for want in ["helene.json", "data/soul/SOUL.md", "data/memory/2026-09-27.md",
                     "data/memory/.state/retention.json", "data/workspace/inbox/pending.md",
                     "agents/mira/helene.json", "agents/mira/data/soul/SOUL.md",
                     "data/models/custom/weights.bin"] {
            assert!(names.iter().any(|n| n == want), "нет {want}: {names:?}");
        }
        for never_in in ["data/runner.log", "data/relay/local_auth/auth.json", "data/voice/model.bin",
                         "agents/mira/data/runner.log", "data/models/whisper/model.bin", "data/models/piper/voice.onnx",
                         "data/workspace/notes.md",
                         "data/workspace/project/node_modules/module.js", "data/workspace/project/.venv/pyvenv.cfg",
                         "data/workspace/project/__pycache__/module.pyc", "data/workspace/project/.git/objects/object",
                         "data/workspace/project/build/design.md",
                         "agents/mira/data/models/whisper/model.bin",
                         "agents/mira/data/workspace/node_modules/pkg/index.js",
                         "agents/mira/data/workspace/project/main.rs"] {
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

    fn fixture(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("helene-backup-{name}-{}", std::process::id()));
        std::fs::create_dir_all(path.join("data").join("memory")).unwrap();
        path
    }

    #[test]
    fn cancellation_inside_a_large_file_removes_the_partial_snapshot() {
        let r = fixture("cancel-stream");
        let input = r.join("data/memory/large.bin");
        let file = std::fs::File::create(&input).unwrap();
        file.set_len((BACKUP_CHUNK * 128) as u64).unwrap();
        drop(file);
        let cancel = AtomicBool::new(false);
        let mut statuses = Vec::new();
        let started = std::time::Instant::now();
        let result = snapshot_with_progress(&r, &r.join("backups"), "manual", &cancel, &mut |p| {
            statuses.push(p);
            if p.bytes >= BACKUP_CHUNK as u64 { cancel.store(true, Ordering::Relaxed); }
        });
        assert_eq!(result.unwrap_err(), "отменено");
        assert_eq!(statuses.last().unwrap().bytes, BACKUP_CHUNK as u64, "cancel is checked inside the file");
        assert_eq!(statuses.last().unwrap().files, 0);
        assert!(started.elapsed() < std::time::Duration::from_secs(5));
        assert_eq!(std::fs::read_dir(r.join("backups")).unwrap().count(), 0, "no zip or tmp is left");
        assert!(input.is_file());
        std::fs::remove_dir_all(r).unwrap();
    }

    #[test]
    fn progress_counts_only_selected_data_and_finishes_monotonically() {
        let r = fixture("progress");
        std::fs::write(r.join("data/memory/a.txt"), b"hello").unwrap();
        std::fs::write(r.join("helene.json"), b"{}").unwrap();
        let cache = r.join("data/models/whisper");
        std::fs::create_dir_all(&cache).unwrap();
        std::fs::write(cache.join("model.bin"), b"cache excluded").unwrap();
        let mut statuses = Vec::new();
        let (path, count) = snapshot_with_progress(&r, &r.join("backups"), "manual", &AtomicBool::new(false), &mut |p| statuses.push(p)).unwrap();
        assert!(statuses.iter().all(|p| p.total_files == 2 && p.total_bytes == 7));
        assert!(statuses.windows(2).all(|p| p[0].bytes <= p[1].bytes && p[0].files <= p[1].files));
        let last = statuses.last().unwrap();
        assert_eq!((last.files, last.bytes), (2, 7));
        assert_eq!(count, 2);
        let mut zip = zip::ZipArchive::new(std::fs::File::open(path).unwrap()).unwrap();
        let mut text = String::new();
        zip.by_name("data/memory/a.txt").unwrap().read_to_string(&mut text).unwrap();
        assert_eq!(text, "hello");
        drop(zip);
        std::fs::remove_dir_all(r).unwrap();
    }

    #[test]
    fn cancellation_after_the_last_file_still_does_not_publish() {
        let r = fixture("cancel-last");
        std::fs::write(r.join("data/memory/a.txt"), b"hello").unwrap();
        let cancel = AtomicBool::new(false);
        let result = snapshot_with_progress(&r, &r.join("backups"), "manual", &cancel, &mut |p| {
            if p.total_files > 0 && p.files == p.total_files { cancel.store(true, Ordering::Relaxed); }
        });
        assert_eq!(result.unwrap_err(), "отменено");
        assert_eq!(std::fs::read_dir(r.join("backups")).unwrap().count(), 0);
        std::fs::remove_dir_all(r).unwrap();
    }

    #[test]
    fn failed_read_cannot_publish_an_incomplete_success() {
        let r = fixture("failed-read");
        let file = r.join("data/memory/a.txt");
        std::fs::write(&file, b"hello").unwrap();
        let result = snapshot_with_progress(&r, &r.join("backups"), "manual", &AtomicBool::new(false), &mut |p| {
            if p.bytes == 0 && file.exists() { std::fs::remove_file(&file).unwrap(); }
        });
        assert!(result.is_err());
        assert_eq!(std::fs::read_dir(r.join("backups")).unwrap().count(), 0);
        std::fs::remove_dir_all(r).unwrap();
    }

    #[test]
    fn pre_cancelled_snapshot_never_creates_an_archive() {
        let r = fixture("pre-cancel");
        assert_eq!(snapshot(&r, &r.join("backups"), "manual", &AtomicBool::new(true)).unwrap_err(), "отменено");
        assert!(!r.join("backups").exists());
        std::fs::remove_dir_all(r).unwrap();
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
