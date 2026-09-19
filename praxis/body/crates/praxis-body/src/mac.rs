//! Общие обёртки macOS для тела: окна и экраны (CoreGraphics), сессия, разрешения TCC,
//! сторож родителя. Здесь и только здесь — `extern "C"` к CoreGraphics и HIServices,
//! которых нет в крейтах; всё остальное берётся из `core-graphics` / `core-foundation`.
//! Модули `desktop` (экран, ввод, окна) и `ax` (дерево окна через Accessibility) зовут
//! ЭТИ функции, а не дублируют их.
//!
//! Правила координат тела на macOS — одни на все глаголы:
//! * всё считается в ПУНКТАХ экрана в глобальной системе CoreGraphics: начало в левом
//!   верхнем углу главного дисплея, ось Y вниз. Так считают CGWindowList, CGEvent и
//!   Accessibility. AppKit (NSScreen, начало внизу) сюда не пускаем;
//! * снимок экрана по умолчанию уменьшается до пунктов, то есть 1:1 с координатами
//!   клика; масштаб дисплея (`scale`) и размер исходника в пикселях едут в ответе.
//!
//! ⚠ TCC — разрешения системы, которых у процесса может не быть, и без них система
//! НЕ ошибается, а врёт: снимок без «Записи экрана» — это обои без чужих окон, ввод без
//! «Универсального доступа» молча глотается, заголовки чужих окон без «Записи экрана»
//! пусты. Поэтому каждый глагол ОБЯЗАН спросить `tcc()` до дела и отказать словами, а
//! не отдать ложь.
#![cfg(target_os = "macos")]

use std::ffi::c_void;

use anyhow::{Context, Result};
use core_foundation::array::CFArray;
use core_foundation::base::{CFType, TCFType};
use core_foundation::boolean::CFBoolean;
use core_foundation::dictionary::{CFDictionary, CFDictionaryRef};
use core_foundation::number::CFNumber;
use core_foundation::string::{CFString, CFStringRef};
use core_graphics::display::CGDisplay;
use core_graphics::geometry::CGPoint;
use core_graphics::window::{
    CGWindowID, copy_window_info, kCGNullWindowID, kCGWindowAlpha, kCGWindowBounds,
    kCGWindowIsOnscreen, kCGWindowLayer, kCGWindowListExcludeDesktopElements,
    kCGWindowListOptionAll, kCGWindowListOptionOnScreenOnly, kCGWindowName, kCGWindowNumber,
    kCGWindowOwnerName, kCGWindowOwnerPID,
};
use serde::Serialize;

#[link(name = "CoreGraphics", kind = "framework")]
unsafe extern "C" {
    fn CGSessionCopyCurrentDictionary() -> CFDictionaryRef;
    fn CGPreflightScreenCaptureAccess() -> bool;
    fn CGRequestScreenCaptureAccess() -> bool;
}

#[link(name = "ApplicationServices", kind = "framework")]
unsafe extern "C" {
    static kAXTrustedCheckOptionPrompt: CFStringRef;
    fn AXIsProcessTrustedWithOptions(options: CFDictionaryRef) -> u8;
}

/// Ключ словаря сессии: сидит ли пользователь за консолью (есть ли WindowServer).
const SESSION_ON_CONSOLE: &str = "kCGSSessionOnConsoleKey";

// ─── окна ────────────────────────────────────────────────────────────────────────────

/// Одно окно из списка WindowServer. Координаты — пункты, глобально (см. шапку).
#[derive(Debug, Clone, Serialize)]
pub struct WindowInfo {
    /// CGWindowID — в JSON тела едет как `hwnd` (число или `0x…`), как на Windows.
    pub id: CGWindowID,
    pub pid: i32,
    /// Имя приложения-владельца (`kCGWindowOwnerName`).
    pub owner: String,
    /// Заголовок. `None` — его нет ИЛИ система его скрыла: без «Записи экрана» заголовки
    /// чужих окон не выдаются. Различить это можно только по `tcc().screen_recording`.
    pub title: Option<String>,
    /// Слой: 0 — обычные окна; строка меню, док, оверлеи — другие.
    pub layer: i32,
    pub alpha: f64,
    pub on_screen: bool,
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    /// 0 — самое верхнее в списке (WindowServer отдаёт сверху вниз).
    pub z_order: usize,
}

impl WindowInfo {
    /// Обычное окно приложения: слой 0, не прозрачное, с размером.
    pub fn is_ordinary(&self) -> bool {
        self.layer == 0 && self.alpha > 0.0 && self.width >= 1.0 && self.height >= 1.0
    }
}

fn key(raw: CFStringRef) -> CFString {
    unsafe { CFString::wrap_under_get_rule(raw) }
}

fn number(dict: &CFDictionary<CFString, CFType>, raw: CFStringRef) -> Option<CFNumber> {
    dict.find(&key(raw)).and_then(|value| value.downcast::<CFNumber>())
}

fn text(dict: &CFDictionary<CFString, CFType>, raw: CFStringRef) -> Option<String> {
    dict.find(&key(raw))
        .and_then(|value| value.downcast::<CFString>())
        .map(|value| value.to_string())
}

fn boolean(dict: &CFDictionary<CFString, CFType>, raw: CFStringRef) -> Option<bool> {
    dict.find(&key(raw))
        .and_then(|value| value.downcast::<CFBoolean>())
        .map(bool::from)
}

fn bounds(dict: &CFDictionary<CFString, CFType>) -> Option<(f64, f64, f64, f64)> {
    let raw = dict
        .find(&key(unsafe { kCGWindowBounds }))
        .and_then(|value| value.downcast::<CFDictionary<*const c_void, *const c_void>>())?;
    let rect: CFDictionary<CFString, CFType> =
        unsafe { CFDictionary::wrap_under_get_rule(raw.as_concrete_TypeRef()) };
    let get = |name: &'static str| -> Option<f64> {
        rect.find(&CFString::from_static_string(name))
            .and_then(|value| value.downcast::<CFNumber>())
            .and_then(|value| value.to_f64())
    };
    Some((get("X")?, get("Y")?, get("Width")?, get("Height")?))
}

/// Окна WindowServer сверху вниз. `on_screen_only` — только видимые сейчас; иначе и
/// свёрнутые/спрятанные. Элементы рабочего стола (обои, иконки) исключены всегда.
/// Работает БЕЗ разрешений; без «Записи экрана» пусты только заголовки чужих окон.
pub fn window_list(on_screen_only: bool) -> Result<Vec<WindowInfo>> {
    let option = if on_screen_only {
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements
    } else {
        kCGWindowListOptionAll | kCGWindowListExcludeDesktopElements
    };
    let array: CFArray =
        copy_window_info(option, kCGNullWindowID).context("CGWindowListCopyWindowInfo вернул NULL")?;
    let mut rows = Vec::with_capacity(array.len() as usize);
    for (z_order, item) in array.iter().enumerate() {
        let dict: CFDictionary<CFString, CFType> =
            unsafe { CFDictionary::wrap_under_get_rule(*item as CFDictionaryRef) };
        let (Some(id), Some(pid)) = (
            number(&dict, unsafe { kCGWindowNumber }).and_then(|n| n.to_i64()),
            number(&dict, unsafe { kCGWindowOwnerPID }).and_then(|n| n.to_i64()),
        ) else {
            continue;
        };
        let (x, y, width, height) = bounds(&dict).unwrap_or((0.0, 0.0, 0.0, 0.0));
        rows.push(WindowInfo {
            id: id as CGWindowID,
            pid: pid as i32,
            owner: text(&dict, unsafe { kCGWindowOwnerName }).unwrap_or_default(),
            title: text(&dict, unsafe { kCGWindowName }).filter(|t| !t.is_empty()),
            layer: number(&dict, unsafe { kCGWindowLayer })
                .and_then(|n| n.to_i64())
                .unwrap_or(0) as i32,
            alpha: number(&dict, unsafe { kCGWindowAlpha })
                .and_then(|n| n.to_f64())
                .unwrap_or(1.0),
            on_screen: boolean(&dict, unsafe { kCGWindowIsOnscreen }).unwrap_or(on_screen_only),
            x,
            y,
            width,
            height,
            z_order,
        });
    }
    Ok(rows)
}

/// Окно по CGWindowID — среди всех, не только видимых.
pub fn window_by_id(id: CGWindowID) -> Result<Option<WindowInfo>> {
    Ok(window_list(false)?.into_iter().find(|w| w.id == id))
}

/// Самое верхнее обычное окно на экране — то, что на Windows зовётся foreground.
/// Без разрешений: порядок списка WindowServer и есть z-порядок.
pub fn frontmost() -> Result<Option<WindowInfo>> {
    Ok(window_list(true)?.into_iter().find(WindowInfo::is_ordinary))
}

// ─── экраны ──────────────────────────────────────────────────────────────────────────

/// Масштаб дисплея (пикселей на пункт): 2.0 на Retina, 1.0 на внешнем без масштаба.
pub fn display_scale(display: &CGDisplay) -> f64 {
    let width = display.bounds().size.width;
    if width > 0.0 {
        display.pixels_wide() as f64 / width
    } else {
        1.0
    }
}

pub fn main_scale() -> f64 {
    display_scale(&CGDisplay::main())
}

/// Масштаб дисплея, на котором лежит точка (пункты). Точка вне всех дисплеев — главный.
pub fn scale_at(x: f64, y: f64) -> f64 {
    match CGDisplay::displays_with_point(CGPoint::new(x, y), 1) {
        Ok((ids, count)) if count > 0 && !ids.is_empty() => display_scale(&CGDisplay::new(ids[0])),
        _ => main_scale(),
    }
}

/// Прямоугольник, накрывающий все активные дисплеи (пункты, глобально) — аналог
/// виртуального экрана Windows: `left`, `top`, `width`, `height`.
#[derive(Debug, Clone, Copy, PartialEq, Serialize)]
pub struct VirtualScreen {
    pub left: f64,
    pub top: f64,
    pub width: f64,
    pub height: f64,
    pub displays: usize,
}

pub fn virtual_screen() -> VirtualScreen {
    let ids = CGDisplay::active_displays().unwrap_or_default();
    let mut left = f64::MAX;
    let mut top = f64::MAX;
    let mut right = f64::MIN;
    let mut bottom = f64::MIN;
    let mut displays = 0usize;
    for id in ids {
        let b = CGDisplay::new(id).bounds();
        left = left.min(b.origin.x);
        top = top.min(b.origin.y);
        right = right.max(b.origin.x + b.size.width);
        bottom = bottom.max(b.origin.y + b.size.height);
        displays += 1;
    }
    if displays == 0 {
        let b = CGDisplay::main().bounds();
        return VirtualScreen {
            left: b.origin.x,
            top: b.origin.y,
            width: b.size.width,
            height: b.size.height,
            displays: 1,
        };
    }
    VirtualScreen { left, top, width: right - left, height: bottom - top, displays }
}

// ─── сессия и разрешения ─────────────────────────────────────────────────────────────

/// Есть ли у процесса графическая сессия (WindowServer, пользователь за консолью).
/// Под LaunchDaemon или по ssh без входа — нет, и тогда тело честно «не интерактивно».
pub fn gui_session() -> bool {
    let raw = unsafe { CGSessionCopyCurrentDictionary() };
    if raw.is_null() {
        return false;
    }
    let dict: CFDictionary<CFString, CFType> = unsafe { CFDictionary::wrap_under_create_rule(raw) };
    dict.find(&CFString::from_static_string(SESSION_ON_CONSOLE))
        .and_then(|value| value.downcast::<CFBoolean>())
        .map(bool::from)
        .unwrap_or(true)
}

/// Два разрешения TCC, от которых зависит тело. Проверка не показывает диалогов.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Tcc {
    /// «Запись экрана»: снимки и заголовки чужих окон.
    pub screen_recording: bool,
    /// «Универсальный доступ»: ввод (мышь, клавиатура) и дерево окон.
    pub accessibility: bool,
}

impl Tcc {
    /// Слова для владельца — куда идти. Одна строка на каждое отсутствующее разрешение.
    pub fn hints(&self) -> Vec<&'static str> {
        let mut out = Vec::new();
        if !self.screen_recording {
            out.push(
                "нет разрешения «Запись экрана»: Системные настройки → Конфиденциальность и \
                 безопасность → Запись экрана и системного звука → включить Helene (после \
                 обновления программы — убрать из списка и добавить снова)",
            );
        }
        if !self.accessibility {
            out.push(
                "нет разрешения «Универсальный доступ»: Системные настройки → Конфиденциальность \
                 и безопасность → Универсальный доступ → включить Helene (после обновления \
                 программы — убрать из списка и добавить снова)",
            );
        }
        out
    }
}

pub fn tcc() -> Tcc {
    Tcc { screen_recording: unsafe { CGPreflightScreenCaptureAccess() }, accessibility: ax_trusted(false) }
}

/// Доверен ли процесс «Универсальному доступу». `prompt` — показать системный диалог с
/// просьбой (один раз на процесс имеет смысл; система сама больше не спрашивает).
pub fn ax_trusted(prompt: bool) -> bool {
    let key = unsafe { CFString::wrap_under_get_rule(kAXTrustedCheckOptionPrompt) };
    let options = CFDictionary::from_CFType_pairs(&[(key, CFBoolean::from(prompt))]);
    unsafe { AXIsProcessTrustedWithOptions(options.as_concrete_TypeRef()) != 0 }
}

/// Попросить «Запись экрана»: система показывает диалог и добавляет программу в список
/// (выключенной, если пользователь отказал). Возвращает, есть ли разрешение СЕЙЧАС.
/// Пока не зовётся: просьба — дело кнопки окна (§2 плана), глаголы тела не просят сами.
#[allow(dead_code)]
pub fn request_screen_capture() -> bool {
    unsafe { CGRequestScreenCaptureAccess() }
}

// ─── процессы ────────────────────────────────────────────────────────────────────────

/// Время старта процесса — Unix-секунды (`proc_pidinfo`, `PROC_PIDTBSDINFO`). Нужно
/// отпечатку окна (`fingerprint`): номера процессов система переиспользует, и один pid
/// без времени старта выдал бы новый процесс за прежний. Чужой процесс, на который нет
/// прав, и мёртвый pid — `None`, и тогда в отпечатке стоит `0`, а в строке `null`: «не
/// узнали» не то же самое, что «ноль секунд».
pub fn process_started(pid: i32) -> Option<u64> {
    if pid <= 0 {
        return None;
    }
    // SAFETY: proc_pidinfo пишет не больше size байт в наш буфер известного размера и
    // возвращает, сколько написал; нули — законное начальное состояние структуры.
    let mut info: libc::proc_bsdinfo = unsafe { std::mem::zeroed() };
    let size = std::mem::size_of::<libc::proc_bsdinfo>() as libc::c_int;
    let written = unsafe {
        libc::proc_pidinfo(
            pid,
            libc::PROC_PIDTBSDINFO,
            0,
            std::ptr::from_mut(&mut info).cast::<c_void>(),
            size,
        )
    };
    (written == size).then_some(info.pbi_start_tvsec)
}

// ─── сторож родителя ─────────────────────────────────────────────────────────────────

/// Что сделать, если родитель умер, — ДО `process::exit`. Держит ввод: тело, уходящее с
/// зажатой ⌘ или левой кнопкой, оставляет стол сломанным, а `Drop` сюда не успевает —
/// `process::exit` стек не разматывает. Кто зажимает, тот и кладёт сюда отпускание
/// (`desktop.rs` при первой пачке ввода).
static ON_PARENT_DEATH: std::sync::Mutex<Vec<Box<dyn Fn() + Send + Sync>>> =
    std::sync::Mutex::new(Vec::new());

pub fn on_parent_death(hook: Box<dyn Fn() + Send + Sync>) {
    match ON_PARENT_DEATH.lock() {
        Ok(mut hooks) => hooks.push(hook),
        Err(poisoned) => poisoned.into_inner().push(hook),
    }
}

/// Позвать всех перед выходом. Отравленный замок не повод уйти с зажатой кнопкой.
fn run_parent_death_hooks() {
    let hooks = match ON_PARENT_DEATH.lock() {
        Ok(hooks) => hooks,
        Err(poisoned) => poisoned.into_inner(),
    };
    for hook in hooks.iter() {
        hook();
    }
}

/// Умер родитель — уходим. На macOS осиротевший процесс переезжает под launchd
/// (ppid становится 1): это и есть сигнал. Замена job-объекту Windows, под которым
/// мост и тело живут у движка Hélène. Родителя нет (мы под launchd сами) — сторож не
/// нужен и не ставится.
pub fn watch_parent(name: &'static str) {
    let parent = unsafe { libc::getppid() };
    if parent <= 1 {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name(format!("{name}-parent-watch"))
        .spawn(move || {
            loop {
                std::thread::sleep(std::time::Duration::from_secs(1));
                let now = unsafe { libc::getppid() };
                if now != parent {
                    tracing::warn!(
                        "{name}: родитель {parent} исчез (теперь ppid {now}) — завершаюсь вместе с ним"
                    );
                    // Сначала отпустить зажатое, потом уходить: иначе стол остаётся с
                    // прижатой кнопкой или модификатором, и виноватым выглядит человек.
                    run_parent_death_hooks();
                    std::process::exit(0);
                }
            }
        });
    if let Err(error) = spawned {
        tracing::warn!("{name}: сторож родителя не поднялся: {error}");
    }
}
