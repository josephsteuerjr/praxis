//! Раскладка новой версии транзакцией (1.2, 27.09).
//!
//! Слово Егора: «Отмена на каждом шаге, включая копирование… отмена = откат, прежняя
//! версия цела». До 1.2 мастер (и NSIS) писали новую версию ПОВЕРХ живой установки:
//! падение или отмена посреди оставляли смесь двух версий без отката.
//!
//! Теперь так:
//! 1. новая версия раскладывается рядом — в `<папка>.new`; прежняя всё это время
//!    работает, отмена = удалить `.new`;
//! 2. подмена — два переименования на одном диске (мгновенно): `<папка>` →
//!    `<папка>.old`, потом то, что принадлежит владельцу, переезжает из `.old` в
//!    `.new` (`data/`, `helene.json`, копии, соседние агенты, всё, чего нет в поставке),
//!    потом `.new` → `<папка>`;
//! 3. после настройки — `commit`: `.old` удаляется. Любой отказ до этого — `rollback`:
//!    переезды обратно, `.old` → `<папка>`.
//!
//! Журнал `<папка>.helene-tx.json` пишется на каждом переезде: мастер, убитый
//! посреди, при следующем запуске находит его и возвращает прежнюю версию
//! (`recover`). Что заменяется и что нет — договор `resources/ОБНОВЛЕНИЕ.md`: код
//! (`app/`, `tree/`, `runtime/`, exe, документы) — целиком новой поставкой, `data/` и
//! настройки — никогда.
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

#[derive(Serialize, Deserialize, Clone, Copy, PartialEq, Eq, Debug)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    /// Раскладывается `.new`; прежняя установка не тронута.
    Laying,
    /// Идёт подмена: часть владельческого могла уже переехать.
    Swapping,
    /// Новая версия на месте, прежняя — в `.old`; идёт настройка.
    Swapped,
}

/// Один переезд из `.old` в `.new` (пути относительные, через `/`).
#[derive(Serialize, Deserialize, Clone, PartialEq, Eq, Debug)]
pub struct Move {
    pub from: String,
    pub to: String,
}

#[derive(Serialize, Deserialize, Clone, Debug)]
pub struct Journal {
    pub dir: String,
    pub phase: Phase,
    /// Была ли прежняя установка (первая установка — только `.new`).
    pub had_dir: bool,
    pub moves: Vec<Move>,
    pub version: String,
    pub pid: u32,
    /// 1.2.5, откат испытания: `.new` — не свежая раскладка, а сохранённая прежняя
    /// программа (`backups/program-<версия>`). Отмена возвращает её туда, а не удаляет:
    /// без этого прерванный откат стёр бы единственную копию прежней версии.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub restore_new: Option<String>,
}

/// Как поступить с прежним интерфейсом окна (`app/static`) — решение принимает
/// мастер по манифестам двух поставок (`install::static_plan`).
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum StaticCarry {
    /// Первая установка: нечего переносить.
    None,
    /// Выпуск интерфейс не менял: прежняя папка остаётся (и её `static.prev`).
    Keep,
    /// Выпуск менял: прежняя уезжает в `app/static.prev`.
    ToPrev,
}

/// Что взять из прежней установки, кроме «всего, чего нет в новой поставке».
pub struct Carry<'a> {
    /// Имена верхнего уровня, которые не переносятся никогда: `uninstall.exe`
    /// установщика NSIS (1.1.x), журналы прошлой установки.
    pub drop: &'a [&'a str],
    /// Имена верхнего уровня прежней ПОСТАВКИ (из её описи): их нет в новой — значит,
    /// выпуск их убрал, и переносить их незачем. Пусто — прежняя поставка без описи
    /// (до 1.2): переносится всё незнакомое.
    pub old_payload_top: &'a [String],
    pub static_carry: StaticCarry,
    /// Рантайм не менялся — не раскладывался, прежний переезжает целиком.
    pub keep_runtime: bool,
    /// Отдельные переезды «путь в прежней → путь в новой» после общих (1.2.1: пакеты
    /// голоса из прежнего рантайма — в `voice/site-packages`). Каждый — под журналом,
    /// то есть откат возвращает и их.
    pub extra: &'a [(String, String)],
}

pub struct Tx {
    pub dir: PathBuf,
    pub new: PathBuf,
    pub old: PathBuf,
    journal_path: PathBuf,
    journal: Journal,
    done: bool,
}

fn sibling(dir: &Path, suffix: &str) -> PathBuf {
    let mut name = dir.file_name().map(|n| n.to_os_string()).unwrap_or_default();
    name.push(suffix);
    dir.with_file_name(name)
}

pub fn new_path(dir: &Path) -> PathBuf {
    sibling(dir, ".new")
}

pub fn old_path(dir: &Path) -> PathBuf {
    sibling(dir, ".old")
}

pub fn journal_path(dir: &Path) -> PathBuf {
    sibling(dir, ".helene-tx.json")
}

fn with_trouble(e: String, back: &[String]) -> String {
    if back.is_empty() {
        format!("{e} — прежняя версия на месте")
    } else {
        format!("{e}; и вернулось не всё: {}", back.join("; "))
    }
}

fn join_rel(root: &Path, rel: &str) -> PathBuf {
    let mut p = root.to_path_buf();
    for part in rel.split('/') {
        p.push(part);
    }
    p
}

/// Переименование с повтором: антивирус и индексатор держат только что написанные
/// файлы долю секунды, и первый отказ ещё не значит, что папка занята.
pub fn rename_retry(from: &Path, to: &Path, tries: u32) -> std::io::Result<()> {
    let mut last = None;
    for i in 0..tries.max(1) {
        match std::fs::rename(from, to) {
            Ok(()) => return Ok(()),
            Err(e) => {
                last = Some(e);
                if i + 1 < tries {
                    std::thread::sleep(std::time::Duration::from_millis(300));
                }
            }
        }
    }
    Err(last.unwrap())
}

/// Удалить папку, даже если в ней файлы «только для чтения» (git агента, рантайм).
pub fn remove_tree(p: &Path) -> std::io::Result<()> {
    match std::fs::remove_dir_all(p) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(first) => {
            fn clear_ro(p: &Path) {
                if let Ok(rd) = std::fs::read_dir(p) {
                    for e in rd.flatten() {
                        let path = e.path();
                        if let Ok(meta) = std::fs::symlink_metadata(&path) {
                            if meta.is_dir() {
                                clear_ro(&path);
                            } else {
                                let mut perm = meta.permissions();
                                #[allow(clippy::permissions_set_readonly_false)]
                                if perm.readonly() {
                                    perm.set_readonly(false);
                                    let _ = std::fs::set_permissions(&path, perm);
                                }
                            }
                        }
                    }
                }
            }
            clear_ro(p);
            std::fs::remove_dir_all(p).map_err(|_| first)
        }
    }
}

impl Tx {
    /// Начать: `.new` пустой, журнал «раскладываю». Незаконченную прошлую транзакцию
    /// сюда не приносят — её разбирает `recover` до этого.
    pub fn begin(dir: &Path, version: &str) -> Result<Tx, String> {
        let new = new_path(dir);
        let old = old_path(dir);
        let journal_path = journal_path(dir);
        if journal_path.exists() {
            return Err(format!(
                "прошлая установка в {} не закончена (журнал {}) — перезапусти установщик, он вернёт прежнюю версию",
                dir.display(),
                journal_path.display()
            ));
        }
        // `.new` и `.old` без журнала — хвосты законченной или отменённой транзакции,
        // которые не успели удалиться. Владельческого в них нет: оно переезжает только
        // под журналом.
        // Хвост снятия (`.removing`) установке не мешает: его в фоне дочищает отложенная
        // команда, и сразу после снятия он ещё удаляется — «отказано в доступе» здесь не
        // повод отказать в установке (живая проба 27.09). Только по возможности.
        let _ = remove_tree(&sibling(dir, ".removing"));
        for stale in [&new, &old] {
            if stale.exists() {
                remove_tree(stale).map_err(|e| format!("не убирается прежний хвост {}: {e}", stale.display()))?;
            }
        }
        if let Some(parent) = dir.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
        std::fs::create_dir_all(&new).map_err(|e| format!("{}: {e}", new.display()))?;
        let mut tx = Tx {
            dir: dir.to_path_buf(),
            new,
            old,
            journal_path,
            journal: Journal {
                dir: dir.display().to_string(),
                phase: Phase::Laying,
                had_dir: dir.exists(),
                moves: Vec::new(),
                version: version.to_string(),
                pid: std::process::id(),
                restore_new: None,
            },
            done: false,
        };
        tx.save()?;
        Ok(tx)
    }

    /// Откат испытания (1.2.5): новой раскладкой становится готовая прежняя программа
    /// `prepared` — она переезжает в `.new` под журналом, и дальше всё как у обновления:
    /// `swap` переносит владельческое из стоящей (отвергнутой) версии и ставит прежнюю на
    /// место. Отмена и `recover` возвращают `prepared` туда, откуда взяли.
    pub fn begin_from(dir: &Path, version: &str, prepared: &Path) -> Result<Tx, String> {
        if !prepared.is_dir() {
            return Err(format!("сохранённой прежней программы нет: {}", prepared.display()));
        }
        let mut tx = Tx::begin(dir, version)?;
        let _ = std::fs::remove_dir(&tx.new);
        tx.journal.restore_new = Some(prepared.display().to_string());
        tx.save()?;
        if let Err(e) = rename_retry(prepared, &tx.new, 20) {
            let _ = std::fs::create_dir_all(&tx.new);
            tx.journal.restore_new = None;
            let _ = tx.save();
            let _ = tx.rollback_inner();
            tx.done = true;
            return Err(format!("прежняя программа не встаёт на подмену ({}): {e}", prepared.display()));
        }
        Ok(tx)
    }

    #[cfg_attr(not(test), allow(dead_code))]
    pub fn phase(&self) -> Phase {
        self.journal.phase
    }

    fn save(&mut self) -> Result<(), String> {
        let text = serde_json::to_string_pretty(&self.journal).map_err(|e| e.to_string())?;
        let tmp = sibling(&self.journal_path, ".tmp");
        std::fs::write(&tmp, text).map_err(|e| format!("{}: {e}", tmp.display()))?;
        rename_retry(&tmp, &self.journal_path, 5).map_err(|e| format!("{}: {e}", self.journal_path.display()))
    }

    fn mv(&mut self, from_rel: &str, to_rel: &str) -> Result<(), String> {
        let from = join_rel(&self.old, from_rel);
        let to = join_rel(&self.new, to_rel);
        if let Some(parent) = to.parent() {
            std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        }
        // Сначала в журнал, потом переезд: убитый посреди мастер вернёт и этот.
        self.journal.moves.push(Move { from: from_rel.to_string(), to: to_rel.to_string() });
        self.save()?;
        if let Err(e) = rename_retry(&from, &to, 10) {
            self.journal.moves.pop();
            let _ = self.save();
            return Err(format!("{} не переезжает: {e}", from.display()));
        }
        Ok(())
    }

    /// Подмена. `before_swap` зовётся, когда прежняя установка уже остановлена и
    /// остаётся только переименовать (журнал «подмена»).
    pub fn swap(&mut self, carry: &Carry) -> Result<(), String> {
        self.journal.phase = Phase::Swapping;
        self.save()?;
        if self.journal.had_dir {
            if let Err(e) = rename_retry(&self.dir, &self.old, 20) {
                self.journal.phase = Phase::Laying;
                let _ = self.save();
                return Err(format!(
                    "папка {} занята — её держит другая программа (окно Проводника или терминал в ней, антивирус): {e}",
                    self.dir.display()
                ));
            }
            if let Err(e) = self.carry(carry) {
                let back = self.undo_swap();
                return Err(with_trouble(e, &back));
            }
        }
        if let Err(e) = rename_retry(&self.new, &self.dir, 20) {
            let back = self.undo_swap();
            return Err(with_trouble(format!("новая версия не встала на место {}: {e}", self.dir.display()), &back));
        }
        self.journal.phase = Phase::Swapped;
        self.save()?;
        Ok(())
    }

    fn carry(&mut self, carry: &Carry) -> Result<(), String> {
        // Интерфейс окна — по решению о статике.
        match carry.static_carry {
            StaticCarry::Keep => {
                for rel in ["app/static", "app/static.prev"] {
                    if join_rel(&self.old, rel).exists() && !join_rel(&self.new, rel).exists() {
                        self.mv(rel, rel)?;
                    }
                }
            }
            StaticCarry::ToPrev => {
                if join_rel(&self.old, "app/static").exists() && !join_rel(&self.new, "app/static.prev").exists() {
                    self.mv("app/static", "app/static.prev")?;
                }
            }
            StaticCarry::None => {}
        }
        if carry.keep_runtime && self.old.join("runtime").exists() && !self.new.join("runtime").exists() {
            self.mv("runtime", "runtime")?;
        }
        // Всё верхнего уровня, чего нет в новой поставке, — владельца.
        let mut names: Vec<String> = std::fs::read_dir(&self.old)
            .map_err(|e| format!("{}: {e}", self.old.display()))?
            .flatten()
            .filter_map(|e| e.file_name().to_str().map(str::to_string))
            .collect();
        names.sort();
        for name in names {
            if carry.drop.iter().any(|d| d.eq_ignore_ascii_case(&name)) {
                continue;
            }
            if self.new.join(&name).exists() {
                continue;
            }
            if carry.old_payload_top.iter().any(|t| t == &name) {
                continue;
            }
            self.mv(&name, &name)?;
        }
        for (from, to) in carry.extra {
            if join_rel(&self.old, from).exists() && !join_rel(&self.new, to).exists() {
                self.mv(from, to)?;
            }
        }
        Ok(())
    }

    /// Вернуть переехавшее в `.old` и `.old` на место. Ошибки собираются: лучше
    /// вернуть большую часть и сказать, чего не вышло, чем бросить на первом.
    fn undo_swap(&mut self) -> Vec<String> {
        let mut trouble = Vec::new();
        // Новая версия уже на месте — отодвинуть её обратно в `.new`. Узнаём по тому,
        // что `.new` уже нет, а папка есть: либо журнал успел сказать «подменено», либо
        // рядом лежит `.old` (мастер убит между переименованиями), либо прежней
        // установки не было вовсе — тогда папка целиком наша.
        let new_in_place = !self.new.exists()
            && self.dir.exists()
            && (self.journal.phase == Phase::Swapped || self.old.exists() || !self.journal.had_dir);
        if new_in_place {
            if let Err(e) = rename_retry(&self.dir, &self.new, 20) {
                trouble.push(format!("новая версия не отодвигается: {e}"));
                return trouble;
            }
        }
        while let Some(m) = self.journal.moves.pop() {
            let from = join_rel(&self.new, &m.to);
            let to = join_rel(&self.old, &m.from);
            if !from.exists() {
                continue;
            }
            if let Err(e) = rename_retry(&from, &to, 10) {
                trouble.push(format!("{} не вернулся: {e}", m.from));
                self.journal.moves.push(m);
                break;
            }
            let _ = self.save();
        }
        if trouble.is_empty() && self.journal.had_dir && self.old.exists() && !self.dir.exists() {
            if let Err(e) = rename_retry(&self.old, &self.dir, 20) {
                trouble.push(format!("прежняя версия не вернулась на место: {e}"));
            }
        }
        self.journal.phase = Phase::Laying;
        let _ = self.save();
        trouble
    }

    /// Откат из любого места до `commit`. -> что не удалось (пусто — всё вернулось).
    pub fn rollback(mut self) -> Vec<String> {
        self.done = true;
        self.rollback_inner()
    }

    fn rollback_inner(&mut self) -> Vec<String> {
        let mut trouble = Vec::new();
        if self.journal.phase != Phase::Laying || !self.journal.moves.is_empty() {
            trouble = self.undo_swap();
        }
        if trouble.is_empty() {
            match self.journal.restore_new.clone() {
                Some(back) if self.new.exists() => {
                    if let Err(e) = rename_retry(&self.new, Path::new(&back), 20) {
                        trouble.push(format!("прежняя программа не вернулась в {back}: {e} (она в {})", self.new.display()));
                    }
                }
                Some(_) => {}
                None => {
                    if let Err(e) = remove_tree(&self.new) {
                        trouble.push(format!("{} не удалилась: {e}", self.new.display()));
                    }
                }
            }
            if trouble.is_empty() {
                let _ = std::fs::remove_file(&self.journal_path);
            }
        }
        trouble
    }

    /// Успех обновления с испытанием (1.2.5): прежняя программа не удаляется, а уезжает в
    /// `keep_at` (обычно `<папка>/backups/program-<версия>`) — на неё откатывает испытание.
    /// Тот же том — одно переименование. Не вышло — прежняя удаляется, как при `commit`, и
    /// ответ говорит почему: испытание тогда идёт без отката. -> Ok(куда легла).
    pub fn commit_keep(mut self, keep_at: &Path) -> Result<PathBuf, (String, std::thread::JoinHandle<()>)> {
        self.done = true;
        let moved = (|| -> Result<(), String> {
            if !self.old.exists() {
                return Err("прежней программы нет (первая установка)".into());
            }
            if let Some(parent) = keep_at.parent() {
                std::fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
            }
            if keep_at.exists() {
                remove_tree(keep_at).map_err(|e| format!("{} не убирается: {e}", keep_at.display()))?;
            }
            rename_retry(&self.old, keep_at, 20)
                .map_err(|e| format!("{} не переезжает в {}: {e}", self.old.display(), keep_at.display()))
        })();
        let _ = std::fs::remove_file(&self.journal_path);
        match moved {
            Ok(()) => Ok(keep_at.to_path_buf()),
            Err(e) => {
                let old = self.old.clone();
                Err((e, std::thread::spawn(move || {
                    let _ = remove_tree(&old);
                })))
            }
        }
    }

    /// Успех: журнал прочь, `.old` удаляется (в фоне — 15 тысяч файлов это секунды;
    /// мастер ждёт поток на выходе). -> поток уборки.
    pub fn commit(mut self) -> std::thread::JoinHandle<()> {
        self.done = true;
        let _ = std::fs::remove_file(&self.journal_path);
        let old = self.old.clone();
        std::thread::spawn(move || {
            let _ = remove_tree(&old);
        })
    }
}

impl Drop for Tx {
    /// Паника посреди установки (или забытый `commit`) — откат, а не смесь версий.
    fn drop(&mut self) {
        if !self.done {
            let _ = self.rollback_inner();
        }
    }
}

/// Прерванная прошлая установка: журнал есть — вернуть прежнюю версию. -> слово для
/// экрана и журнала; None — возвращать нечего.
pub fn recover(dir: &Path) -> Option<String> {
    let jp = journal_path(dir);
    let raw = std::fs::read(&jp).ok()?;
    let journal: Journal = match serde_json::from_slice(&raw) {
        Ok(j) => j,
        Err(_) => {
            // Журнал нечитаем — `.new` без него не трогаем вслепую, только сам журнал.
            let _ = std::fs::remove_file(&jp);
            return Some(format!("журнал прерванной установки {} не читался — убран", jp.display()));
        }
    };
    let mut tx = Tx {
        dir: dir.to_path_buf(),
        new: new_path(dir),
        old: old_path(dir),
        journal_path: jp,
        journal,
        done: true,
    };
    let phase = tx.journal.phase;
    let trouble = tx.rollback_inner();
    Some(if trouble.is_empty() {
        match phase {
            Phase::Laying => "прошлая установка прервалась на раскладке — её остатки убраны, прежняя версия цела".to_string(),
            _ => "прошлая установка прервалась посреди подмены — прежняя версия возвращена на место".to_string(),
        }
    } else {
        format!("прошлая установка прервалась, и вернулось не всё: {}", trouble.join("; "))
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn root(tag: &str) -> PathBuf {
        let r = std::env::temp_dir().join(format!("helene-tx-{tag}-{}", std::process::id()));
        let _ = remove_tree(&r);
        std::fs::create_dir_all(&r).unwrap();
        r
    }

    fn put(p: &Path, text: &str) {
        std::fs::create_dir_all(p.parent().unwrap()).unwrap();
        std::fs::write(p, text).unwrap();
    }

    fn read(p: &Path) -> String {
        std::fs::read_to_string(p).unwrap()
    }

    /// Прежняя установка: код, данные, настройки, соседний агент, записка NSIS.
    fn old_install(dir: &Path) {
        put(&dir.join("helene.exe"), "old-exe");
        put(&dir.join("app").join("deskapp.py"), "old-app");
        put(&dir.join("app").join("static").join("index.html"), "old-ui");
        put(&dir.join("runtime").join("python.exe"), "old-py");
        put(&dir.join("data").join("soul").join("SOUL.md"), "душа");
        put(&dir.join("helene.json"), "{\"setup_complete\":true}");
        put(&dir.join("agents").join("mira").join("helene.json"), "{}");
        put(&dir.join("uninstall.exe"), "nsis");
        put(&dir.join("ПЕРВЫЙ-ЗАПУСК.md"), "old-doc");
    }

    fn lay_new(new: &Path) {
        put(&new.join("helene.exe"), "new-exe");
        put(&new.join("app").join("deskapp.py"), "new-app");
    }

    #[test]
    fn swap_keeps_owner_things_and_replaces_code() {
        let r = root("swap");
        let dir = r.join("Helene");
        old_install(&dir);
        let mut tx = Tx::begin(&dir, "1.2.0").unwrap();
        lay_new(&tx.new);
        let carry = Carry {
            drop: &["uninstall.exe", "install.log"],
            old_payload_top: &["ПЕРВЫЙ-ЗАПУСК.md".to_string()],
            static_carry: StaticCarry::Keep,
            keep_runtime: true,
            extra: &[],
        };
        tx.swap(&carry).unwrap();
        assert_eq!(tx.phase(), Phase::Swapped);
        assert_eq!(read(&dir.join("helene.exe")), "new-exe");
        assert_eq!(read(&dir.join("app").join("deskapp.py")), "new-app");
        assert_eq!(read(&dir.join("app").join("static").join("index.html")), "old-ui");
        assert_eq!(read(&dir.join("runtime").join("python.exe")), "old-py");
        assert_eq!(read(&dir.join("data").join("soul").join("SOUL.md")), "душа");
        assert!(dir.join("helene.json").is_file());
        assert!(dir.join("agents").join("mira").join("helene.json").is_file());
        assert!(!dir.join("uninstall.exe").exists(), "uninstall.exe NSIS не переезжает");
        assert!(!dir.join("ПЕРВЫЙ-ЗАПУСК.md").exists(), "убранный выпуском документ не переезжает");
        let cleanup = tx.commit();
        cleanup.join().unwrap();
        assert!(!old_path(&dir).exists());
        assert!(!journal_path(&dir).exists());
        let _ = remove_tree(&r);
    }

    #[test]
    fn replaced_ui_goes_to_static_prev() {
        let r = root("prev");
        let dir = r.join("Helene");
        old_install(&dir);
        let mut tx = Tx::begin(&dir, "1.2.0").unwrap();
        lay_new(&tx.new);
        put(&tx.new.join("app").join("static").join("index.html"), "new-ui");
        put(&tx.new.join("runtime").join("python.exe"), "new-py");
        let carry = Carry { drop: &[], old_payload_top: &[], static_carry: StaticCarry::ToPrev, keep_runtime: false, extra: &[] };
        tx.swap(&carry).unwrap();
        assert_eq!(read(&dir.join("app").join("static").join("index.html")), "new-ui");
        assert_eq!(read(&dir.join("app").join("static.prev").join("index.html")), "old-ui");
        assert_eq!(read(&dir.join("runtime").join("python.exe")), "new-py", "рантайм сменился — стоит новый");
        tx.commit().join().unwrap();
        let _ = remove_tree(&r);
    }

    #[test]
    fn rollback_after_swap_returns_the_old_version_whole() {
        let r = root("back");
        let dir = r.join("Helene");
        old_install(&dir);
        let mut tx = Tx::begin(&dir, "1.2.0").unwrap();
        lay_new(&tx.new);
        let carry = Carry { drop: &["uninstall.exe"], old_payload_top: &[], static_carry: StaticCarry::Keep, keep_runtime: true, extra: &[] };
        tx.swap(&carry).unwrap();
        let trouble = tx.rollback();
        assert!(trouble.is_empty(), "{trouble:?}");
        assert_eq!(read(&dir.join("helene.exe")), "old-exe");
        assert_eq!(read(&dir.join("app").join("static").join("index.html")), "old-ui");
        assert_eq!(read(&dir.join("runtime").join("python.exe")), "old-py");
        assert_eq!(read(&dir.join("data").join("soul").join("SOUL.md")), "душа");
        assert!(dir.join("uninstall.exe").is_file(), "откат возвращает и то, что обновление выбросило бы");
        assert!(!new_path(&dir).exists());
        assert!(!old_path(&dir).exists());
        assert!(!journal_path(&dir).exists());
        let _ = remove_tree(&r);
    }

    #[test]
    fn cancel_while_laying_leaves_the_old_install_untouched() {
        let r = root("cancel");
        let dir = r.join("Helene");
        old_install(&dir);
        let tx = Tx::begin(&dir, "1.2.0").unwrap();
        lay_new(&tx.new);
        assert!(tx.rollback().is_empty());
        assert_eq!(read(&dir.join("helene.exe")), "old-exe");
        assert!(!new_path(&dir).exists());
        let _ = remove_tree(&r);
    }

    #[test]
    fn first_install_is_just_a_rename() {
        let r = root("first");
        let dir = r.join("Programs").join("Helene");
        let mut tx = Tx::begin(&dir, "1.2.0").unwrap();
        lay_new(&tx.new);
        let carry = Carry { drop: &[], old_payload_top: &[], static_carry: StaticCarry::None, keep_runtime: false, extra: &[] };
        tx.swap(&carry).unwrap();
        assert_eq!(read(&dir.join("helene.exe")), "new-exe");
        tx.commit().join().unwrap();
        // Откат первой установки убирает её целиком.
        let dir2 = r.join("Programs").join("Helene2");
        let mut tx = Tx::begin(&dir2, "1.2.0").unwrap();
        lay_new(&tx.new);
        tx.swap(&carry).unwrap();
        assert!(tx.rollback().is_empty());
        assert!(!dir2.exists());
        let _ = remove_tree(&r);
    }

    /// Мастер убит посреди подмены: журнал записан, часть владельческого уже в `.new`.
    #[test]
    fn recover_after_a_kill_mid_swap() {
        let r = root("kill");
        let dir = r.join("Helene");
        old_install(&dir);
        let mut tx = Tx::begin(&dir, "1.2.0").unwrap();
        lay_new(&tx.new);
        tx.journal.phase = Phase::Swapping;
        tx.save().unwrap();
        rename_retry(&dir, &tx.old, 3).unwrap();
        tx.mv("data", "data").unwrap();
        // Процесс умер: Drop не отработал.
        std::mem::forget(tx);
        assert!(!dir.exists());
        let said = recover(&dir).expect("журнал найден");
        assert!(said.contains("возвращена"), "{said}");
        assert_eq!(read(&dir.join("helene.exe")), "old-exe");
        assert_eq!(read(&dir.join("data").join("soul").join("SOUL.md")), "душа");
        assert!(!new_path(&dir).exists());
        assert!(!journal_path(&dir).exists());
        assert!(recover(&dir).is_none(), "второй раз возвращать нечего");
        let _ = remove_tree(&r);
    }

    #[test]
    fn begin_refuses_over_an_unfinished_journal_and_clears_stale_tails() {
        let r = root("stale");
        let dir = r.join("Helene");
        old_install(&dir);
        std::fs::create_dir_all(new_path(&dir)).unwrap();
        put(&old_path(&dir).join("x"), "x");
        let tx = Tx::begin(&dir, "1.2.0").unwrap();
        assert!(tx.new.exists() && !tx.old.exists());
        std::mem::forget(tx);
        assert!(Tx::begin(&dir, "1.2.0").is_err(), "незаконченная транзакция не перезаписывается");
        assert!(recover(&dir).is_some());
        let _ = remove_tree(&r);
    }

    #[test]
    fn a_panic_inside_rolls_back() {
        let r = root("panic");
        let dir = r.join("Helene");
        old_install(&dir);
        let d2 = dir.clone();
        let res = std::panic::catch_unwind(move || {
            let mut tx = Tx::begin(&d2, "1.2.0").unwrap();
            lay_new(&tx.new);
            let carry = Carry { drop: &[], old_payload_top: &[], static_carry: StaticCarry::Keep, keep_runtime: true, extra: &[] };
            tx.swap(&carry).unwrap();
            panic!("посреди настройки");
        });
        assert!(res.is_err());
        assert_eq!(read(&dir.join("helene.exe")), "old-exe");
        assert!(!journal_path(&dir).exists());
        let _ = remove_tree(&r);
    }

    /// 1.2.5: обновление с испытанием оставляет прежнюю программу в `backups/`, а откат
    /// ставит её обратно той же транзакцией — с владельческим, которое нажилось за испытание.
    #[test]
    fn kept_program_comes_back_by_the_same_transaction() {
        let r = root("keep");
        let dir = r.join("Helene");
        old_install(&dir);
        let mut tx = Tx::begin(&dir, "1.2.5").unwrap();
        lay_new(&tx.new);
        put(&tx.new.join("runtime").join("python.exe"), "new-py");
        let carry = Carry { drop: &["uninstall.exe"], old_payload_top: &[], static_carry: StaticCarry::ToPrev, keep_runtime: false, extra: &[] };
        tx.swap(&carry).unwrap();
        let kept = dir.join("backups").join("program-1.2.4");
        assert_eq!(tx.commit_keep(&kept).unwrap(), kept);
        assert_eq!(read(&kept.join("helene.exe")), "old-exe");
        assert!(!old_path(&dir).exists() && !journal_path(&dir).exists());
        // за испытание агент кое-что нажил в данных
        put(&dir.join("data").join("memory").join("new.md"), "за испытание");
        // откат: прежняя программа — новой раскладкой
        let mut back = Tx::begin_from(&dir, "1.2.4", &kept).unwrap();
        assert!(!kept.exists());
        let top = vec!["helene.exe".to_string(), "app".to_string(), "runtime".to_string()];
        let extra = vec![("app/static.prev".to_string(), "app/static".to_string())];
        let carry = Carry { drop: &[], old_payload_top: &top, static_carry: StaticCarry::None, keep_runtime: false, extra: &extra };
        back.swap(&carry).unwrap();
        back.commit().join().unwrap();
        assert_eq!(read(&dir.join("helene.exe")), "old-exe");
        assert_eq!(read(&dir.join("runtime").join("python.exe")), "old-py");
        assert_eq!(read(&dir.join("app").join("static").join("index.html")), "old-ui");
        assert_eq!(read(&dir.join("data").join("memory").join("new.md")), "за испытание");
        assert_eq!(read(&dir.join("data").join("soul").join("SOUL.md")), "душа");
        assert!(!dir.join("backups").join("program-1.2.4").exists());
        assert!(!old_path(&dir).exists() && !journal_path(&dir).exists());
        let _ = remove_tree(&r);
    }

    /// Откат прервали посреди: прежняя программа не удаляется, а возвращается на место.
    #[test]
    fn interrupted_rollback_returns_the_kept_program() {
        let r = root("keep-kill");
        let dir = r.join("Helene");
        old_install(&dir);
        let kept = r.join("kept");
        put(&kept.join("helene.exe"), "prev-exe");
        let back = Tx::begin_from(&dir, "1.2.4", &kept).unwrap();
        assert!(!kept.exists());
        std::mem::forget(back);
        let said = recover(&dir).expect("журнал найден");
        assert!(said.contains("раскладке"), "{said}");
        assert_eq!(read(&kept.join("helene.exe")), "prev-exe", "прежняя программа не стёрта");
        assert_eq!(read(&dir.join("helene.exe")), "old-exe");
        assert!(!new_path(&dir).exists() && !journal_path(&dir).exists());
        let _ = remove_tree(&r);
    }

    /// 1.2.1: пакеты голоса уезжают из прежнего рантайма в `voice/` под журналом — и
    /// откат возвращает их на место, а не оставляет в удаляемой `.new`.
    #[test]
    fn extra_moves_travel_under_the_journal_and_come_back() {
        let r = root("extra");
        let dir = r.join("Helene");
        old_install(&dir);
        put(&dir.join("runtime/Lib/site-packages/numpy/__init__.py"), "np");
        put(&dir.join("runtime/Lib/site-packages/numpy-2.3.4.dist-info/METADATA"), "Name: numpy");
        let mut tx = Tx::begin(&dir, "1.2.1").unwrap();
        lay_new(&tx.new);
        put(&tx.new.join("runtime/python.exe"), "new-py");
        let extra = vec![
            ("runtime/Lib/site-packages/numpy".to_string(), "voice/site-packages/numpy".to_string()),
            (
                "runtime/Lib/site-packages/numpy-2.3.4.dist-info".to_string(),
                "voice/site-packages/numpy-2.3.4.dist-info".to_string(),
            ),
        ];
        let carry = Carry { drop: &[], old_payload_top: &[], static_carry: StaticCarry::None, keep_runtime: false, extra: &extra };
        tx.swap(&carry).unwrap();
        assert_eq!(read(&dir.join("voice/site-packages/numpy/__init__.py")), "np");
        assert!(dir.join("voice/site-packages/numpy-2.3.4.dist-info/METADATA").is_file());
        assert_eq!(read(&dir.join("runtime/python.exe")), "new-py", "рантайм — новый");
        let trouble = tx.rollback();
        assert!(trouble.is_empty(), "{trouble:?}");
        assert_eq!(read(&dir.join("runtime/Lib/site-packages/numpy/__init__.py")), "np", "пакет вернулся");
        assert_eq!(read(&dir.join("runtime/python.exe")), "old-py");
        assert!(!dir.join("voice").exists(), "откат не оставил voice/");
        let _ = remove_tree(&r);
    }
}
