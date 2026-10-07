//! Windows-руки мастера 1.2 (27.09): job-объект на детей, запуск установленной
//! программы вне его, поднятый исполнитель «для всех», снятие песочницы без питона.
//!
//! Требования Егора, ради которых это есть: «уборка за собой при падении — процессы,
//! которые мастер породил, гасятся»; «для всех» — один запрос прав, дальше без
//! вопросов; удаление быстрое.
#![cfg(windows)]

use std::os::windows::ffi::OsStrExt;
use std::os::windows::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::Command;

use windows_sys::Win32::Foundation::{CloseHandle, GetLastError, HANDLE, WAIT_OBJECT_0, WAIT_TIMEOUT};
use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, IsProcessInJob, JobObjectExtendedLimitInformation,
    QueryInformationJobObject, SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_BREAKAWAY_OK, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK,
};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcess, GetExitCodeProcess, OpenProcessToken, WaitForSingleObject, CREATE_BREAKAWAY_FROM_JOB,
};

const CREATE_NO_WINDOW: u32 = 0x0800_0000;

fn wide(s: &std::ffi::OsStr) -> Vec<u16> {
    s.encode_wide().chain(std::iter::once(0)).collect()
}

/// Мастер и всё, что он породит (реле входа в ChatGPT, пробы питона, PowerShell,
/// WebView2), — в одном job-объекте с KILL_ON_JOB_CLOSE: мастер вышел или упал —
/// дети умирают вместе с ним. Установленную программу мастер запускает с
/// CREATE_BREAKAWAY_FROM_JOB (`spawn_outside`), и она живёт дальше.
/// Хендл job намеренно не закрывается: он живёт ровно столько, сколько процесс.
pub fn adopt_self_into_job() -> bool {
    unsafe {
        // Ревью 27.09: мастер родился в чужом job, который детей не отпускает (нет
        // BREAKAWAY_OK). Свой job тогда вложился бы в него, запуск Hélène «вне job»
        // откатился бы в НАШ job с KILL_ON_JOB_CLOSE — и программа умирала бы вместе с
        // мастером. В таком редком запуске уборка при падении уступает живой программе.
        let mut inside = 0;
        if IsProcessInJob(GetCurrentProcess(), std::ptr::null_mut(), &mut inside) != 0 && inside != 0 {
            let mut cur: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            let ok = QueryInformationJobObject(
                std::ptr::null_mut(),
                JobObjectExtendedLimitInformation,
                &mut cur as *mut _ as *mut core::ffi::c_void,
                std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
                std::ptr::null_mut(),
            );
            let lets_go = JOB_OBJECT_LIMIT_BREAKAWAY_OK | JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK;
            if ok == 0 || cur.BasicLimitInformation.LimitFlags & lets_go == 0 {
                return false;
            }
        }
        let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if job.is_null() {
            return false;
        }
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        let ok = SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
        if ok == 0 || AssignProcessToJobObject(job, GetCurrentProcess()) == 0 {
            CloseHandle(job);
            return false;
        }
        OUR_JOB.store(job, std::sync::atomic::Ordering::SeqCst);
        true
    }
}

/// Признак копии, поднятой вне чужого job (`escape_foreign_job`): второй раз она не
/// выходит. Со значением — имя разовой задачи планировщика, которую копия убирает за собой.
pub const ESCAPED_FLAG: &str = "--escaped-job";

/// Папка разовых задач планировщика, которыми мастер выходит из чужого job.
const ESCAPE_TASK_DIR: &str = "Helene";

/// Мастер родился внутри чужого job — выйти из него до первого окна. -> `true`: копия
/// вне job поднята, этому процессу пора выйти.
///
/// 28.09, живой случай. Агент Егора запустил установщик рукой `computer run`
/// (`Start-Process`): тело кладёт каждый прогон в свой job с KILL_ON_JOB_CLOSE и без
/// права выхода, а сам агент живёт в задаче планировщика службы. Владелец нажал
/// «Обновить до 1.2.4», мастер остановил службу — служба сняла свою задачу со всеми
/// процессами, и мастер погиб вместе с программой на полпути: до ручной установки
/// вечером Hélène стояла мёртвой. Мастер, который гасит программу, не может жить в её
/// процессах.
///
/// Путь первый — штатный: копия с CREATE_BREAKAWAY_FROM_JOB (job разрешает выход).
/// Второй — разовая задача планировщика в этой же сессии: её процесс рождается у службы
/// планировщика, вне любых job. Не вышло ни то ни другое — мастер остаётся где был
/// (лучше мастер в чужом job, чем никакого).
pub fn escape_foreign_job(args: &[String]) -> bool {
    if let Some(task) = args.iter().find_map(|a| a.strip_prefix(&format!("{ESCAPED_FLAG}="))) {
        // Копия, поднятая планировщиком: задача своё сделала. Удаление задачи бегущий
        // процесс не трогает (проверено 28.09).
        let _ = schtasks(&["/Delete", "/TN", task, "/F"]);
        return false;
    }
    if args.iter().any(|a| a == ESCAPED_FLAG) {
        return false;
    }
    let mut inside = 0;
    if unsafe { IsProcessInJob(GetCurrentProcess(), std::ptr::null_mut(), &mut inside) } == 0 || inside == 0 {
        return false;
    }
    let Ok(exe) = std::env::current_exe() else {
        return false;
    };
    let rest: Vec<String> = args.iter().skip(1).cloned().collect();
    let mut cmd = Command::new(&exe);
    cmd.args(&rest).arg(ESCAPED_FLAG).creation_flags(CREATE_BREAKAWAY_FROM_JOB);
    if cmd.spawn().is_ok() {
        return true;
    }
    relaunch_by_scheduler(&exe, &rest)
}

/// `schtasks.exe` полным путём, без окна. -> успех.
fn schtasks(args: &[&str]) -> bool {
    let root = std::env::var_os("SystemRoot").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(r"C:\Windows"));
    Command::new(root.join("System32").join("schtasks.exe"))
        .args(args)
        .creation_flags(CREATE_NO_WINDOW)
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Аргумент для строки `/TR`: в кавычках, если в нём пробел или кавычка.
fn tr_quote(arg: &str) -> String {
    if !arg.is_empty() && !arg.contains([' ', '\t', '"']) {
        return arg.to_string();
    }
    format!("\"{}\"", arg.replace('"', "\\\""))
}

/// Командная строка задачи: exe, аргументы, признак копии с именем задачи. Чистая.
fn escape_task_line(exe: &Path, rest: &[String], task: &str) -> String {
    let mut line = tr_quote(&exe.display().to_string());
    for a in rest {
        line.push(' ');
        line.push_str(&tr_quote(a));
    }
    line.push(' ');
    line.push_str(&tr_quote(&format!("{ESCAPED_FLAG}={task}")));
    line
}

/// Разовая задача «только при входе, в этой сессии», поднятая сразу. Права — те же,
/// что у мастера: поднятый мастер ставит задачу с высшими правами, обычный — обычную.
fn relaunch_by_scheduler(exe: &Path, rest: &[String]) -> bool {
    let task = format!(r"{ESCAPE_TASK_DIR}\setup-escape-{}", std::process::id());
    let line = escape_task_line(exe, rest, &task);
    if line.chars().count() > 261 {
        return false; // предел /TR у schtasks: длиннее — задача не встанет
    }
    let mut create = vec!["/Create", "/TN", task.as_str(), "/TR", line.as_str(), "/SC", "ONCE",
                          "/ST", "00:00", "/IT", "/F"];
    if is_elevated() {
        create.extend(["/RL", "HIGHEST"]);
    }
    if !schtasks(&create) {
        return false;
    }
    if schtasks(&["/Run", "/TN", task.as_str()]) {
        return true;
    }
    let _ = schtasks(&["/Delete", "/TN", task.as_str(), "/F"]);
    false
}

#[cfg(test)]
mod escape_tests {
    use super::*;

    #[test]
    fn task_line_quotes_paths_and_carries_the_task_name() {
        let exe = Path::new(r"C:\Users\Егор\Downloads\Helene-1.2.5-setup.exe");
        let line = escape_task_line(exe, &["--update".into(), "--dir".into(), r"C:\Program Files\Helene".into()],
                                    r"Helene\setup-escape-7");
        assert_eq!(
            line,
            r#"C:\Users\Егор\Downloads\Helene-1.2.5-setup.exe --update --dir "C:\Program Files\Helene" --escaped-job=Helene\setup-escape-7"#
        );
        let spaced = escape_task_line(Path::new(r"C:\a b\s.exe"), &[], "T");
        assert!(spaced.starts_with(r#""C:\a b\s.exe" "#), "{spaced}");
    }

    #[test]
    fn escaped_copy_never_escapes_again() {
        assert!(!escape_foreign_job(&["setup.exe".into(), ESCAPED_FLAG.into()]));
    }
}

/// Свой job мастера: `spawn_outside` снимает с него KILL_ON_JOB_CLOSE, если выйти из
/// job всё же не вышло.
static OUR_JOB: std::sync::atomic::AtomicPtr<core::ffi::c_void> = std::sync::atomic::AtomicPtr::new(std::ptr::null_mut());

/// Выход из job отказан (чужой job выше не отпускает): наш job перестаёт гасить детей
/// при закрытии — иначе программа, запущенная «вне мастера», умерла бы вместе с ним.
fn release_our_job() {
    let job = OUR_JOB.load(std::sync::atomic::Ordering::SeqCst);
    if job.is_null() {
        return;
    }
    unsafe {
        let mut info: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        SetInformationJobObject(
            job,
            JobObjectExtendedLimitInformation,
            &info as *const _ as *const core::ffi::c_void,
            std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
        );
    }
}

/// Запуск, который переживает мастер: вне его job-объекта. Если job, в котором
/// родился сам мастер (чужой), выход не разрешает — обычный запуск: лучше живой
/// процесс в чужом job, чем никакого. Наш job при этом перестаёт гасить детей —
/// ребёнок остаётся и в нём (`release_our_job`).
pub fn spawn_outside(cmd: &mut Command) -> std::io::Result<std::process::Child> {
    cmd.creation_flags(CREATE_BREAKAWAY_FROM_JOB);
    match cmd.spawn() {
        Ok(child) => Ok(child),
        Err(e) if e.raw_os_error() == Some(5) => {
            release_our_job();
            cmd.creation_flags(0);
            cmd.spawn()
        }
        Err(e) => Err(e),
    }
}

/// То же для скрытых хвостов (cmd уборки): без окна и вне job.
pub fn spawn_outside_hidden(cmd: &mut Command) -> std::io::Result<std::process::Child> {
    cmd.creation_flags(CREATE_BREAKAWAY_FROM_JOB | CREATE_NO_WINDOW);
    match cmd.spawn() {
        Ok(child) => Ok(child),
        Err(e) if e.raw_os_error() == Some(5) => {
            release_our_job();
            cmd.creation_flags(CREATE_NO_WINDOW);
            cmd.spawn()
        }
        Err(e) => Err(e),
    }
}

/// Процесс поднят (администратор после UAC или UAC выключен вовсе).
pub fn is_elevated() -> bool {
    use windows_sys::Win32::Security::{GetTokenInformation, TokenElevation, TOKEN_ELEVATION, TOKEN_QUERY};
    unsafe {
        let mut token: HANDLE = std::ptr::null_mut();
        if OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut token) == 0 {
            return false;
        }
        let mut el: TOKEN_ELEVATION = std::mem::zeroed();
        let mut len = 0u32;
        let ok = GetTokenInformation(
            token,
            TokenElevation,
            &mut el as *mut _ as *mut core::ffi::c_void,
            std::mem::size_of::<TOKEN_ELEVATION>() as u32,
            &mut len,
        );
        CloseHandle(token);
        ok != 0 && el.TokenIsElevated != 0
    }
}

/// Открыть установленную программу так, чтобы она жила своей жизнью: не в job
/// мастера и — если мастер поднят — не с правами администратора. Поднятый процесс
/// отдаёт запуск Проводнику: тот запускает от имени владельца сеанса, без прав.
pub fn launch_app(exe: &Path) -> std::io::Result<()> {
    if crate::install::owner_stopped_pub() {
        return Err(std::io::Error::new(std::io::ErrorKind::PermissionDenied, "Остановлен владельцем; обновление не возобновляет агента"));
    }
    let cwd = exe.parent().map(Path::to_path_buf).unwrap_or_else(std::env::temp_dir);
    if is_elevated() {
        let explorer = std::env::var_os("SystemRoot")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("C:\\Windows"))
            .join("explorer.exe");
        let mut cmd = Command::new(explorer);
        cmd.arg(exe).current_dir(&cwd);
        spawn_outside(&mut cmd).map(|_| ())
    } else {
        let mut cmd = Command::new(exe);
        cmd.current_dir(&cwd);
        spawn_outside(&mut cmd).map(|_| ())
    }
}

/// Поднятый исполнитель: тот же exe с `runas`. Один запрос прав на всё «для всех».
pub struct Elevated {
    handle: HANDLE,
}

unsafe impl Send for Elevated {}

impl Drop for Elevated {
    fn drop(&mut self) {
        if !self.handle.is_null() {
            unsafe { CloseHandle(self.handle) };
        }
    }
}

impl Elevated {
    /// Запустить `exe params` с правами администратора. Отказ в окне UAC — своими
    /// словами: это решение человека, а не поломка.
    pub fn start(exe: &Path, params: &str) -> Result<Elevated, String> {
        use windows_sys::Win32::UI::Shell::{
            ShellExecuteExW, SEE_MASK_FLAG_NO_UI, SEE_MASK_NOASYNC, SEE_MASK_NOCLOSEPROCESS, SHELLEXECUTEINFOW,
        };
        use windows_sys::Win32::UI::WindowsAndMessaging::SW_HIDE;
        let verb = wide(std::ffi::OsStr::new("runas"));
        let file = wide(exe.as_os_str());
        let params_w = wide(std::ffi::OsStr::new(params));
        let dir = wide(std::env::temp_dir().as_os_str());
        unsafe {
            let mut sei: SHELLEXECUTEINFOW = std::mem::zeroed();
            sei.cbSize = std::mem::size_of::<SHELLEXECUTEINFOW>() as u32;
            sei.fMask = SEE_MASK_NOCLOSEPROCESS | SEE_MASK_NOASYNC | SEE_MASK_FLAG_NO_UI;
            sei.lpVerb = verb.as_ptr();
            sei.lpFile = file.as_ptr();
            sei.lpParameters = params_w.as_ptr();
            sei.lpDirectory = dir.as_ptr();
            sei.nShow = SW_HIDE as i32;
            if ShellExecuteExW(&mut sei) == 0 {
                let err = GetLastError();
                return Err(if err == 1223 {
                    "права администратора не были даны".to_string()
                } else {
                    format!("поднятый исполнитель не запустился (код {err})")
                });
            }
            if sei.hProcess.is_null() {
                return Err("поднятый исполнитель запустился без хендла — ждать нечего".into());
            }
            Ok(Elevated { handle: sei.hProcess })
        }
    }

    /// Ждать до `ms`. Some(код) — вышел; None — ещё работает.
    pub fn wait(&self, ms: u32) -> Option<u32> {
        unsafe {
            match WaitForSingleObject(self.handle, ms) {
                WAIT_OBJECT_0 => {
                    let mut code = 0u32;
                    if GetExitCodeProcess(self.handle, &mut code) != 0 {
                        Some(code)
                    } else {
                        Some(u32::MAX)
                    }
                }
                WAIT_TIMEOUT => None,
                _ => Some(u32::MAX),
            }
        }
    }
}

/// Папки «Пуска» и Рабочего стола: свои или общие («для всех»).
pub fn shell_folder(all_users: bool, desktop: bool) -> Option<PathBuf> {
    if all_users {
        if desktop {
            std::env::var_os("PUBLIC").map(|p| PathBuf::from(p).join("Desktop"))
        } else {
            std::env::var_os("ProgramData")
                .map(|p| PathBuf::from(p).join("Microsoft").join("Windows").join("Start Menu").join("Programs"))
        }
    } else if desktop {
        None // свой рабочий стол — через GetFolderPath (OneDrive), см. install::desktop_dir
    } else {
        std::env::var_os("APPDATA")
            .map(|p| PathBuf::from(p).join("Microsoft").join("Windows").join("Start Menu").join("Programs"))
    }
}

/// Program Files этой системы (64-битный вид).
pub fn program_files() -> PathBuf {
    std::env::var_os("ProgramW6432")
        .or_else(|| std::env::var_os("ProgramFiles"))
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("C:\\Program Files"))
}

// --------------------------------------------------------- песочница без питона

/// Имя контейнера AppContainer установки — тем же правилом, что `fence.container_name`:
/// `helene.shell.` + sha1(путь.lower())[:12], путь — как отдаёт `Path.resolve()` (без `\\?\`).
pub fn container_name(root: &Path) -> String {
    use sha1::Digest;
    let resolved = std::fs::canonicalize(root).unwrap_or_else(|_| root.to_path_buf());
    let text = resolved.display().to_string();
    let text = text.strip_prefix(r"\\?\").unwrap_or(&text).to_lowercase();
    let digest = sha1::Sha1::digest(text.as_bytes());
    let hex: String = digest.iter().map(|b| format!("{b:02x}")).collect();
    format!("helene.shell.{}", &hex[..12])
}

/// SID контейнера строкой (`S-1-15-2-…`). None — имя системе незнакомо.
fn container_sid(name: &str) -> Option<String> {
    use windows_sys::Win32::Security::Authorization::ConvertSidToStringSidW;
    use windows_sys::Win32::Security::FreeSid;
    use windows_sys::Win32::Security::Isolation::DeriveAppContainerSidFromAppContainerName;
    let w = wide(std::ffi::OsStr::new(name));
    unsafe {
        let mut sid: windows_sys::Win32::Security::PSID = std::ptr::null_mut();
        if DeriveAppContainerSidFromAppContainerName(w.as_ptr(), &mut sid) != 0 || sid.is_null() {
            return None;
        }
        let mut text: windows_sys::core::PWSTR = std::ptr::null_mut();
        let ok = ConvertSidToStringSidW(sid, &mut text);
        FreeSid(sid);
        if ok == 0 || text.is_null() {
            return None;
        }
        let mut len = 0usize;
        while *text.add(len) != 0 {
            len += 1;
        }
        let s = String::from_utf16_lossy(std::slice::from_raw_parts(text, len));
        windows_sys::Win32::Foundation::LocalFree(text as _);
        Some(s)
    }
}

fn delete_profile(name: &str) -> bool {
    use windows_sys::Win32::Security::Isolation::DeleteAppContainerProfile;
    let w = wide(std::ffi::OsStr::new(name));
    unsafe { DeleteAppContainerProfile(w.as_ptr()) == 0 }
}

/// Папки, которым контейнер выдавал права снаружи установки (подключённые папки
/// владельца) — по записи самой ограды `data/workspace/.fence/mounts-<sid>.json`.
fn granted_mounts(root: &Path, sid: &str) -> Vec<PathBuf> {
    let record = root.join("data").join("workspace").join(".fence").join(format!("mounts-{sid}.json"));
    let Ok(raw) = std::fs::read(&record) else { return Vec::new() };
    let Ok(v) = serde_json::from_slice::<serde_json::Value>(&raw) else { return Vec::new() };
    let Some(map) = v.as_object() else { return Vec::new() };
    map.iter()
        .filter_map(|(key, row)| {
            let raw = row.get("path").and_then(|p| p.as_str()).unwrap_or(key);
            let p = PathBuf::from(raw.trim());
            (p.is_absolute() && p.exists()).then_some(p)
        })
        .collect()
}

/// Снять ограду при удалении — быстро. До 1.2 это делал `fence.py --revoke <папка>`
/// с `icacls /T` по ВСЕЙ установке (15 тысяч файлов, ~18 с на проход): папка через
/// секунду удаляется целиком, и ACE на её файлах снимать незачем. Здесь права
/// снимаются только там, что ОСТАЁТСЯ: `data/`, если её оставляют, и подключённые
/// папки владельца; профили контейнеров (свой и прежних поколений) удаляются всегда.
/// -> (профилей снято, строки отчёта).
pub fn fence_revoke_fast(root: &Path, keep_data: bool, legacy: &[String]) -> (usize, Vec<String>) {
    let mut names = vec![container_name(root)];
    for n in legacy {
        if !names.contains(n) {
            names.push(n.clone());
        }
    }
    let mut removed = 0usize;
    let mut report = Vec::new();
    for name in &names {
        if let Some(sid) = container_sid(name) {
            let mut targets = granted_mounts(root, &sid);
            if keep_data && root.join("data").exists() {
                targets.push(root.join("data"));
            }
            for path in targets {
                let mut cmd = Command::new(crate::install::sys_exe("icacls.exe"));
                cmd.arg(&path)
                    .args(["/remove:g", &format!("*{sid}"), "/remove:d", &format!("*{sid}"), "/T", "/C", "/Q"])
                    .creation_flags(CREATE_NO_WINDOW);
                match cmd.output() {
                    Ok(o) => report.push(format!("права {name} сняты с {}: код {}", path.display(), o.status.code().unwrap_or(-1))),
                    Err(e) => report.push(format!("icacls не запустился для {}: {e}", path.display())),
                }
            }
        }
        if delete_profile(name) {
            removed += 1;
        }
    }
    (removed, report)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Та же строка, что у `fence.container_name` (питон): sha1 от пути в нижнем регистре.
    #[test]
    fn container_name_matches_the_python_rule() {
        let dir = std::env::temp_dir().join("helene-fence-name-probe");
        std::fs::create_dir_all(&dir).unwrap();
        let name = container_name(&dir);
        assert!(name.starts_with("helene.shell."));
        assert_eq!(name.len(), "helene.shell.".len() + 12);
        // Регистр пути не меняет имени.
        let upper = PathBuf::from(dir.display().to_string().to_uppercase());
        assert_eq!(container_name(&upper), name);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn shell_folders_point_where_windows_keeps_them() {
        let pf = program_files();
        assert!(pf.is_absolute());
        if let Some(start) = shell_folder(true, false) {
            assert!(start.ends_with("Start Menu\\Programs"));
        }
    }
}
