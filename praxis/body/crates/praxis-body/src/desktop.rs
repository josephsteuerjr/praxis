//! Deterministic native Windows desktop capabilities.
//!
//! This module contains no planning, memory, or autonomous policy. The server
//! chooses a typed operation; the interactive body executes it and returns a
//! structured receipt. Desktop capabilities intentionally refuse to pretend
//! that Session 0 is an interactive desktop.

use std::path::{Path, PathBuf};

#[cfg(any(windows, target_os = "macos", test))]
use std::fs::{self, File};
#[cfg(any(windows, target_os = "macos", test))]
use std::io::{self, BufWriter, Write};
#[cfg(any(windows, target_os = "macos", test))]
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, bail};
use praxis_body_protocol::{AdapterDescriptor, CapabilityDescriptor};
use serde_json::Value;
#[cfg(any(windows, target_os = "macos", test))]
use uuid::Uuid;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NativeDesktopCapability {
    pub name: &'static str,
    pub version: u32,
    pub mutating: bool,
    pub durable: bool,
}

pub const CAPABILITIES: &[NativeDesktopCapability] = &[
    NativeDesktopCapability {
        name: "desktop.status",
        version: 1,
        mutating: false,
        durable: false,
    },
    NativeDesktopCapability {
        name: "os.process.list",
        version: 1,
        mutating: false,
        durable: false,
    },
    NativeDesktopCapability {
        name: "desktop.window.list",
        version: 1,
        mutating: false,
        durable: false,
    },
    NativeDesktopCapability {
        name: "desktop.window.activate",
        version: 1,
        mutating: true,
        durable: true,
    },
    // v2: у мыши появились раздельные нажатие и отпускание плюс составное
    // перетаскивание. До этого пара «нажал+отпустил» была неразрывной, и целый
    // класс действий — потащить файл, ползунок, границу окна, выделить текст
    // мышью — был ей физически недоступен, о чём манифест молчал.
    NativeDesktopCapability {
        name: "desktop.input.perform",
        version: 2,
        mutating: true,
        durable: true,
    },
    NativeDesktopCapability {
        name: "desktop.screen.capture",
        version: 2,
        mutating: false,
        durable: true,
    },
    NativeDesktopCapability {
        name: "desktop.clipboard.read",
        version: 1,
        mutating: false,
        durable: false,
    },
    NativeDesktopCapability {
        name: "desktop.clipboard.write",
        version: 1,
        mutating: true,
        durable: true,
    },
];

/// A provider-owned file result which the shared runtime should publish through
/// the content-addressed artifact transport.  Adding another desktop capture
/// format stays inside this module; transport and journaling do not learn a new
/// capability name.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactOutput {
    pub path: PathBuf,
    pub name: Option<String>,
    pub media_type: String,
    pub presentation: String,
}

pub fn artifact_output(capability: &str, result: &Value) -> Result<Option<ArtifactOutput>> {
    if capability != "desktop.screen.capture" || result.get("ok") != Some(&Value::Bool(true)) {
        return Ok(None);
    }
    let path = PathBuf::from(
        result
            .get("path")
            .and_then(Value::as_str)
            .context("desktop capture result has no artifact path")?,
    );
    let name = path
        .file_name()
        .and_then(|value| value.to_str())
        .map(str::to_string);
    Ok(Some(ArtifactOutput {
        path,
        name,
        media_type: result
            .get("mime")
            .and_then(Value::as_str)
            .unwrap_or("image/png")
            .to_string(),
        presentation: "image".into(),
    }))
}

pub fn descriptors() -> Vec<CapabilityDescriptor> {
    CAPABILITIES
        .iter()
        .map(|capability| CapabilityDescriptor {
            name: capability.name.into(),
            version: capability.version,
            mutating: capability.mutating,
            durable: capability.durable,
        })
        .collect()
}

pub fn adapter_descriptor() -> AdapterDescriptor {
    // Windows-строка не меняется; macOS — свой адаптер с теми же глаголами и формами
    // (порт 19.09), и доступен он ровно там, где собран.
    let (name, version) = if cfg!(target_os = "macos") {
        ("native-macos-desktop", "1")
    } else {
        ("native-win32-desktop", "3")
    };
    AdapterDescriptor {
        name: name.into(),
        version: version.into(),
        capabilities: CAPABILITIES
            .iter()
            .map(|capability| capability.name.to_string())
            .collect(),
        available: cfg!(any(windows, target_os = "macos")),
    }
}

#[cfg(any(windows, target_os = "macos", test))]
const MAX_CAPTURE_PIXELS: u64 = 40_000_000;
#[cfg(any(windows, target_os = "macos", test))]
const MAX_CAPTURE_RAW_BYTES: usize = 128 * 1024 * 1024;
#[cfg(any(windows, target_os = "macos", test))]
const MAX_CAPTURE_PNG_BYTES: usize = 128 * 1024 * 1024;

#[cfg(any(windows, target_os = "macos", test))]
fn capture_allocation(width: i32, height: i32) -> Result<usize> {
    if width <= 0 || height <= 0 {
        bail!("capture rectangle must have positive width and height")
    }
    let pixels = u64::try_from(width)?
        .checked_mul(u64::try_from(height)?)
        .context("capture pixel count overflow")?;
    if pixels > MAX_CAPTURE_PIXELS {
        bail!("capture rectangle is too large: {pixels} pixels (maximum {MAX_CAPTURE_PIXELS})")
    }
    let raw_bytes = usize::try_from(pixels)?
        .checked_mul(4)
        .context("capture raw BGRA size overflow")?;
    if raw_bytes > MAX_CAPTURE_RAW_BYTES {
        bail!("capture requires {raw_bytes} raw BGRA bytes (maximum {MAX_CAPTURE_RAW_BYTES})")
    }
    Ok(raw_bytes)
}

#[cfg(any(windows, target_os = "macos", test))]
struct SizeLimitedWriter<W> {
    inner: W,
    written: usize,
    limit: usize,
}

#[cfg(any(windows, target_os = "macos", test))]
impl<W> SizeLimitedWriter<W> {
    fn new(inner: W, limit: usize) -> Self {
        Self {
            inner,
            written: 0,
            limit,
        }
    }
}

#[cfg(any(windows, target_os = "macos", test))]
impl<W: Write> Write for SizeLimitedWriter<W> {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if buffer.len() > self.limit.saturating_sub(self.written) {
            return Err(io::Error::other(format!(
                "PNG exceeds {} bytes",
                self.limit
            )));
        }
        let count = self.inner.write(buffer)?;
        self.written = self
            .written
            .checked_add(count)
            .ok_or_else(|| io::Error::other("PNG byte count overflow"))?;
        Ok(count)
    }

    fn flush(&mut self) -> io::Result<()> {
        self.inner.flush()
    }
}

#[cfg(any(windows, target_os = "macos", test))]
fn write_png(path: &Path, width: i32, height: i32, bgra: &[u8]) -> Result<()> {
    let expected = capture_allocation(width, height)?;
    if bgra.len() != expected {
        bail!(
            "capture buffer has {} bytes; expected {expected}",
            bgra.len()
        )
    }

    let temporary = path.with_file_name(format!(".praxis-capture-{}.png", Uuid::new_v4()));
    let encoded = (|| -> Result<()> {
        let output = BufWriter::new(File::create(&temporary)?);
        let output = SizeLimitedWriter::new(output, MAX_CAPTURE_PNG_BYTES);
        let mut encoder = png::Encoder::new(output, u32::try_from(width)?, u32::try_from(height)?);
        encoder.set_color(png::ColorType::Rgb);
        encoder.set_depth(png::BitDepth::Eight);
        encoder.set_compression(png::Compression::Fast);
        let mut writer = encoder.write_header().context("write PNG header")?;
        {
            let mut stream = writer
                .stream_writer_with_size(64 * 1024)
                .context("start PNG stream")?;
            let bgra_row_bytes = usize::try_from(width)?
                .checked_mul(4)
                .context("capture BGRA row size overflow")?;
            let rgb_row_bytes = usize::try_from(width)?
                .checked_mul(3)
                .context("capture RGB row size overflow")?;
            let mut rgb_row = vec![0u8; rgb_row_bytes];
            for bgra_row in bgra.chunks_exact(bgra_row_bytes) {
                for (source, target) in bgra_row.chunks_exact(4).zip(rgb_row.chunks_exact_mut(3)) {
                    target.copy_from_slice(&[source[2], source[1], source[0]]);
                }
                stream.write_all(&rgb_row).context("write PNG pixels")?;
            }
            stream.finish().context("finish PNG pixel stream")?;
        }
        writer.finish().context("finish PNG")?;
        Ok(())
    })();
    if let Err(error) = encoded {
        let _ = fs::remove_file(&temporary);
        return Err(error);
    }

    let size = fs::metadata(&temporary)?.len();
    if size > MAX_CAPTURE_PNG_BYTES as u64 {
        let _ = fs::remove_file(&temporary);
        bail!("captured PNG exceeded {MAX_CAPTURE_PNG_BYTES} bytes")
    }
    let committed = crate::fsops::replace_path(&temporary, path);
    if committed.is_err() {
        let _ = fs::remove_file(&temporary);
    }
    committed
}

#[cfg(any(windows, target_os = "macos", test))]
fn capture_name(requested: &str) -> String {
    let mut filtered: String = requested
        .chars()
        .filter(|value| value.is_alphanumeric() || matches!(value, '-' | '_' | '.'))
        .collect();
    for suffix in [".png", ".bmp"] {
        if filtered.to_ascii_lowercase().ends_with(suffix) {
            filtered.truncate(filtered.len() - suffix.len());
            break;
        }
    }
    let mut name = String::new();
    let mut utf16_units = 0usize;
    for value in filtered.trim_matches('.').chars() {
        let units = value.len_utf16();
        if utf16_units + units > 200 {
            break;
        }
        name.push(value);
        utf16_units += units;
    }
    let stem = name
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    let reserved = matches!(stem.as_str(), "CON" | "PRN" | "AUX" | "NUL")
        || stem
            .strip_prefix("COM")
            .and_then(|value| value.parse::<u8>().ok())
            .is_some_and(|value| (1..=9).contains(&value))
        || stem
            .strip_prefix("LPT")
            .and_then(|value| value.parse::<u8>().ok())
            .is_some_and(|value| (1..=9).contains(&value));
    if reserved {
        name.insert_str(0, "capture-");
    }
    if name.is_empty() {
        let stamp = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos();
        name = format!("capture-{}-{stamp}.png", std::process::id());
    } else {
        name.push_str(".png");
    }
    name
}

pub fn dispatch(capability: &str, args: Value, state_dir: &Path) -> Result<Value> {
    let Ok(handle) = tokio::runtime::Handle::try_current() else {
        return platform::dispatch(capability, args, state_dir);
    };
    if handle.runtime_flavor() != tokio::runtime::RuntimeFlavor::MultiThread {
        bail!("native desktop dispatch requires a multi-thread Tokio runtime")
    }
    let capability = capability.to_string();
    let state_dir = PathBuf::from(state_dir);
    tokio::task::block_in_place(|| {
        handle.block_on(async move {
            tokio::task::spawn_blocking(move || platform::dispatch(&capability, args, &state_dir))
                .await
                .context("native desktop worker stopped")?
        })
    })
}

#[cfg(not(any(windows, target_os = "macos")))]
mod platform {
    use std::path::Path;

    use anyhow::{Result, bail};
    use serde_json::Value;

    pub fn dispatch(_capability: &str, _args: Value, _state_dir: &Path) -> Result<Value> {
        bail!("native desktop capabilities require an interactive Windows or macOS session")
    }
}

/// Платформенно-нейтральная часть macOS-ветки: аргументы глаголов, планировщик ввода,
/// таблица клавиш, разбор `ps`, пиксели снимка, формы строк JSON. Ни одного вызова
/// системы — стенды на любой ОС проверяют ровно тот код, который потом исполняется на
/// Mac, а живая часть (`platform` под `target_os = "macos"`) только шлёт готовое в
/// CoreGraphics. Формы JSON — те же, что у Windows-ветки: дерево Праксис и
/// `body_client.py` не должны заметить платформу иначе как по полю `platform`.
#[cfg(any(target_os = "macos", test))]
// Вне macOS модуль живёт только ради стендов: аргументы глаголов и планировщик там никто
// не зовёт, и предупреждать об этом на каждой сборке Windows незачем.
#[cfg_attr(not(target_os = "macos"), allow(dead_code))]
mod mac_pure {
    use anyhow::{Context, Result, bail};
    use serde::Deserialize;
    use serde_json::{Value, json};

    // Пределы — те же числа, что у Windows-ветки, и едут в каждом ответе (`input_limits`).
    pub(super) const DEFAULT_PAGE: usize = 2_048;
    pub(super) const MAX_PAGE: usize = 20_000;
    pub(super) const DEFAULT_CLIPBOARD_CHARS: usize = 1_000_000;
    pub(super) const MAX_CLIPBOARD_CHARS: usize = 1_000_000;
    pub(super) const MAX_INPUT_EVENTS: usize = 512;
    pub(super) const MAX_INPUT_TEXT_UTF16_UNITS: usize = 16_384;
    pub(super) const MAX_HOTKEY_KEYS: usize = 32;
    pub(super) const MAX_CLICK_COUNT: u32 = 64;
    pub(super) const MAX_INPUT_RECORDS: usize = 65_536;
    // body_client ждёт 60 секунд; оставляем запас на сам вызов и ответ.
    pub(super) const MAX_TOTAL_INPUT_DELAY_MS: u64 = 30_000;
    pub(super) const MAX_DRAG_STEPS: u32 = 256;
    pub(super) const DEFAULT_DRAG_STEPS: u32 = 24;
    pub(super) const MAX_DRAG_PAUSE_MS: u64 = 5_000;
    pub(super) const DEFAULT_DRAG_HOLD_MS: u64 = 60;
    pub(super) const DEFAULT_DRAG_STEP_DELAY_MS: u64 = 8;
    pub(super) const DEFAULT_DRAG_SETTLE_MS: u64 = 60;
    pub(super) const MAX_ACTIVATE_TIMEOUT_MS: u64 = 15_000;
    /// Пауза между знаками текста. Урок VK_PACKET на Windows: знаки, отправленные одной
    /// пачкой, терялись, 20 мс между ними лечит. На macOS знак уходит парой событий
    /// (down/up) через CGEventKeyboardSetUnicodeString, и после каждого знака стоит та же
    /// пауза. Она входит в общий бюджет MAX_TOTAL_INPUT_DELAY_MS, поэтому за один вызов
    /// помещается ~1 500 знаков — предел назван в `limits` и в тексте отказа, не спрятан.
    pub(super) const TEXT_UNIT_PAUSE_MS: u64 = 20;
    /// Одна зарубка колеса Windows (delta 120) — три строки: столько по умолчанию
    /// прокручивает Windows, и столько же имеет в виду дерево, когда говорит «steps: 1».
    pub(super) const WHEEL_LINES_PER_NOTCH: i32 = 3;
    pub(super) const WHEEL_NOTCH: i32 = 120;

    // ─── аргументы (формы Windows-ветки) ────────────────────────────────────────────

    /// `hwnd` = CGWindowID: число или строка `0x…`, как на Windows.
    #[derive(Debug, Clone, Deserialize)]
    #[serde(untagged)]
    pub(super) enum HwndArg {
        Number(u64),
        Text(String),
    }

    impl HwndArg {
        pub(super) fn value(&self) -> Result<u32> {
            let value = match self {
                Self::Number(value) => *value,
                Self::Text(value) => {
                    let value = value.trim();
                    if let Some(hex) = value
                        .strip_prefix("0x")
                        .or_else(|| value.strip_prefix("0X"))
                    {
                        u64::from_str_radix(hex, 16)
                            .context("hwnd must be a positive integer or hexadecimal string")?
                    } else {
                        value.parse::<u64>().context(
                            "hwnd must be a positive integer or 0x-prefixed hexadecimal string",
                        )?
                    }
                }
            };
            if value == 0 || value > u64::from(u32::MAX) {
                bail!("invalid hwnd: a CGWindowID is a nonzero 32-bit number")
            }
            Ok(value as u32)
        }
    }

    #[derive(Debug, Default, Deserialize)]
    pub(super) struct PageArgs {
        #[serde(default)]
        pub(super) offset: usize,
        #[serde(default = "default_page")]
        pub(super) limit: usize,
    }

    fn default_page() -> usize {
        DEFAULT_PAGE
    }

    #[derive(Debug, Default, Deserialize)]
    pub(super) struct ProcessListArgs {
        #[serde(flatten)]
        pub(super) page: PageArgs,
        #[serde(default)]
        pub(super) name_contains: String,
        #[serde(default)]
        pub(super) session_id: Option<u32>,
    }

    #[derive(Debug, Deserialize)]
    pub(super) struct WindowListArgs {
        #[serde(flatten)]
        pub(super) page: PageArgs,
        #[serde(default = "yes")]
        pub(super) visible_only: bool,
        #[serde(default)]
        pub(super) pid: Option<u32>,
        #[serde(default)]
        pub(super) title_contains: String,
        /// WindowServer перечисляет и строку меню, док, оверлеи, окна статуса — на
        /// Windows их аналоги не top-level окна. По умолчанию — только обычные окна
        /// (слой 0); `true` — всё, что знает WindowServer. Умолчание названо в ответе.
        #[serde(default)]
        pub(super) all_layers: bool,
    }

    impl Default for WindowListArgs {
        fn default() -> Self {
            Self {
                page: PageArgs::default(),
                visible_only: true,
                pid: None,
                title_contains: String::new(),
                all_layers: false,
            }
        }
    }

    fn yes() -> bool {
        true
    }

    #[derive(Debug, Deserialize)]
    pub(super) struct ActivateArgs {
        pub(super) hwnd: HwndArg,
        #[serde(default)]
        pub(super) expected_pid: Option<u32>,
        #[serde(default = "yes")]
        pub(super) restore: bool,
        #[serde(default = "activate_timeout")]
        pub(super) timeout_ms: u64,
    }

    fn activate_timeout() -> u64 {
        1_500
    }

    #[derive(Debug, Deserialize)]
    pub(super) struct InputArgs {
        #[serde(default)]
        pub(super) expected_foreground: Option<HwndArg>,
        #[serde(default)]
        pub(super) expected_pid: Option<u32>,
        pub(super) events: Vec<InputEvent>,
        #[serde(default)]
        pub(super) inter_event_delay_ms: u64,
    }

    /// Те же шаги, что у Windows-ветки v2 (см. её комментарии к каждому).
    #[derive(Debug, Deserialize)]
    #[serde(tag = "type", rename_all = "snake_case")]
    pub(super) enum InputEvent {
        Text {
            text: String,
        },
        Hotkey {
            keys: Vec<KeyArg>,
        },
        Key {
            key: KeyArg,
            #[serde(default = "press_action")]
            action: String,
        },
        Mouse {
            x: i32,
            y: i32,
            #[serde(default)]
            relative: bool,
        },
        Click {
            #[serde(default = "left_button")]
            button: String,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
            #[serde(default = "one")]
            count: u32,
        },
        Wheel {
            delta: i32,
            #[serde(default)]
            horizontal: bool,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
        },
        MouseDown {
            #[serde(default = "left_button")]
            button: String,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
        },
        MouseUp {
            #[serde(default = "left_button")]
            button: String,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
        },
        Drag {
            #[serde(default = "left_button")]
            button: String,
            x: i32,
            y: i32,
            to_x: i32,
            to_y: i32,
            #[serde(default = "default_drag_steps")]
            steps: u32,
            #[serde(default = "default_drag_hold_ms")]
            hold_ms: u64,
            #[serde(default = "default_drag_step_delay_ms")]
            step_delay_ms: u64,
            #[serde(default = "default_drag_settle_ms")]
            settle_ms: u64,
        },
    }

    /// Имя клавиши из таблицы Windows-ветки или число. ⚠ Число здесь — код macOS
    /// (`kVK_*`), а не Windows VK: таблицы разные, и подменять одно другим молча нельзя.
    /// Об этом сказано в `limits.numeric_keys`.
    #[derive(Debug, Clone, Deserialize)]
    #[serde(untagged)]
    pub(super) enum KeyArg {
        Number(u16),
        Text(String),
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(super) enum MouseButton {
        Left,
        Right,
        Middle,
    }

    impl MouseButton {
        pub(super) fn parse(raw: &str) -> Result<Self> {
            match raw.trim().to_ascii_lowercase().as_str() {
                "left" => Ok(Self::Left),
                "right" => Ok(Self::Right),
                "middle" => Ok(Self::Middle),
                _ => bail!("button must be left, right, or middle"),
            }
        }

        pub(super) fn name(self) -> &'static str {
            match self {
                Self::Left => "left",
                Self::Right => "right",
                Self::Middle => "middle",
            }
        }
    }

    fn press_action() -> String {
        "press".into()
    }

    fn left_button() -> String {
        "left".into()
    }

    fn one() -> u32 {
        1
    }

    fn default_drag_steps() -> u32 {
        DEFAULT_DRAG_STEPS
    }

    fn default_drag_hold_ms() -> u64 {
        DEFAULT_DRAG_HOLD_MS
    }

    fn default_drag_step_delay_ms() -> u64 {
        DEFAULT_DRAG_STEP_DELAY_MS
    }

    fn default_drag_settle_ms() -> u64 {
        DEFAULT_DRAG_SETTLE_MS
    }

    #[derive(Debug, Deserialize)]
    pub(super) struct CaptureArgs {
        #[serde(default = "desktop_target")]
        pub(super) target: String,
        #[serde(default)]
        pub(super) hwnd: Option<HwndArg>,
        #[serde(default)]
        pub(super) x: Option<i32>,
        #[serde(default)]
        pub(super) y: Option<i32>,
        #[serde(default)]
        pub(super) width: Option<i32>,
        #[serde(default)]
        pub(super) height: Option<i32>,
        #[serde(default)]
        pub(super) name: String,
        /// `true` — отдать пиксели как есть (на Retina вдвое больше пунктов);
        /// по умолчанию снимок уменьшается до пунктов, 1:1 с координатами клика.
        #[serde(default)]
        pub(super) native: bool,
    }

    fn desktop_target() -> String {
        "desktop".into()
    }

    #[derive(Debug, Deserialize)]
    pub(super) struct ClipboardReadArgs {
        #[serde(default = "default_clipboard_chars")]
        pub(super) limit_chars: usize,
    }

    fn default_clipboard_chars() -> usize {
        DEFAULT_CLIPBOARD_CHARS
    }

    #[derive(Debug, Deserialize)]
    pub(super) struct ClipboardWriteArgs {
        pub(super) text: String,
    }

    // ─── планировщик ввода ──────────────────────────────────────────────────────────

    /// Одна отправка в CoreGraphics. Координаты — пункты, глобально, уже подтянутые к
    /// краю экрана; тип события (Moved/Dragged, click state) выбирает отправитель по
    /// тому, что реально нажато к этому моменту.
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub(super) enum Record {
        KeyDown(u16),
        KeyUp(u16),
        /// Один знак текста: 1 или 2 единицы UTF-16 (суррогатная пара — одним событием).
        Unicode { units: Vec<u16>, down: bool },
        MoveTo { x: i32, y: i32 },
        /// Сдвиг от живого положения курсора; подтягивается к краю при отправке.
        MoveBy { dx: i32, dy: i32 },
        ButtonDown { button: MouseButton, click_state: u32 },
        ButtonUp { button: MouseButton, click_state: u32 },
        /// Строки. Знак — как на Windows: положительное — вверх / вправо.
        Wheel { vertical: i32, horizontal: i32 },
    }

    /// Отправки одного шага плюс пауза после них (см. Windows-ветку `PreparedChunk`).
    #[derive(Debug)]
    pub(super) struct PreparedChunk {
        pub(super) records: Vec<Record>,
        pub(super) pause_ms: u64,
        pub(super) press: Option<MouseButton>,
        pub(super) release: Option<MouseButton>,
        pub(super) clamped_moves: usize,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(super) struct PlanTotals {
        pub(super) batches: usize,
        pub(super) records: usize,
        pub(super) pause_ms: u64,
        pub(super) clamped_moves: usize,
    }

    /// Виртуальный экран в пунктах (все дисплеи), для подтягивания координат к краю.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(super) struct Screen {
        pub(super) left: i32,
        pub(super) top: i32,
        pub(super) width: i32,
        pub(super) height: i32,
    }

    impl Screen {
        /// Точка внутри экрана и признак «пришлось подтянуть к краю».
        pub(super) fn clamp(&self, x: i32, y: i32) -> (i32, i32, bool) {
            let (cx, pulled_x) = clamp_axis(x, self.left, self.width);
            let (cy, pulled_y) = clamp_axis(y, self.top, self.height);
            (cx, cy, pulled_x || pulled_y)
        }
    }

    fn clamp_axis(value: i32, origin: i32, length: i32) -> (i32, bool) {
        if length <= 1 {
            return (origin, value != origin);
        }
        let last = i64::from(origin) + i64::from(length) - 1;
        let clamped = i64::from(value).clamp(i64::from(origin), last);
        (clamped as i32, clamped != i64::from(value))
    }

    pub(super) fn prepare_input_events(
        events: &[InputEvent],
        inter_event_delay_ms: u64,
        screen: Screen,
    ) -> Result<Vec<PreparedChunk>> {
        let mut total = 0usize;
        let mut chunks: Vec<PreparedChunk> = Vec::with_capacity(events.len());
        for (index, event) in events.iter().enumerate() {
            let produced = event_chunks(event, screen)?;
            for chunk in &produced {
                total = total
                    .checked_add(chunk.records.len())
                    .context("input record count overflow")?;
                if total > MAX_INPUT_RECORDS {
                    bail!("input batch expands to {total} records (maximum {MAX_INPUT_RECORDS})")
                }
            }
            chunks.extend(produced);
            if index + 1 < events.len()
                && inter_event_delay_ms > 0
                && let Some(last) = chunks.last_mut()
            {
                last.pause_ms = last
                    .pause_ms
                    .checked_add(inter_event_delay_ms)
                    .context("input delay overflow")?;
            }
        }
        Ok(chunks)
    }

    /// Всё, что известно о пачке ДО первой отправки — те же числа, что едут в ответ.
    /// Пауза последнего шага не считается: после него ничего не ждут (как на Windows).
    pub(super) fn plan_totals(chunks: &[PreparedChunk]) -> Result<PlanTotals> {
        let mut records = 0usize;
        let mut pause_ms = 0u64;
        let mut clamped_moves = 0usize;
        for (index, chunk) in chunks.iter().enumerate() {
            records = records
                .checked_add(chunk.records.len())
                .context("input record count overflow")?;
            if index + 1 < chunks.len() {
                pause_ms = pause_ms
                    .checked_add(chunk.pause_ms)
                    .context("input delay overflow")?;
            }
            clamped_moves = clamped_moves
                .checked_add(chunk.clamped_moves)
                .context("clamped move count overflow")?;
        }
        Ok(PlanTotals {
            batches: chunks.len(),
            records,
            pause_ms,
            clamped_moves,
        })
    }

    /// Что шаг делает с состоянием кнопок: (что нажал, что отпустил).
    pub(super) fn event_button_effect(
        event: &InputEvent,
    ) -> Result<(Option<MouseButton>, Option<MouseButton>)> {
        Ok(match event {
            InputEvent::Click { button, .. } | InputEvent::Drag { button, .. } => {
                let button = MouseButton::parse(button)?;
                (Some(button), Some(button))
            }
            InputEvent::MouseDown { button, .. } => (Some(MouseButton::parse(button)?), None),
            InputEvent::MouseUp { button, .. } => (None, Some(MouseButton::parse(button)?)),
            _ => (None, None),
        })
    }

    /// Что останется зажатым, если все шаги дойдут до стола целиком. Живой цикл ведёт
    /// ту же ведомость по фактическим отправкам; здесь она считается наперёд для стендов.
    #[cfg(test)]
    pub(super) fn net_held(events: &[InputEvent]) -> Result<Vec<&'static str>> {
        let mut held: Vec<MouseButton> = Vec::new();
        for event in events {
            let (press, release) = event_button_effect(event)?;
            if let Some(button) = press
                && !held.contains(&button)
            {
                held.push(button);
            }
            if let Some(button) = release {
                held.retain(|value| *value != button);
            }
        }
        Ok(held.into_iter().map(MouseButton::name).collect())
    }

    fn event_chunks(event: &InputEvent, screen: Screen) -> Result<Vec<PreparedChunk>> {
        match event {
            InputEvent::Drag {
                button,
                x,
                y,
                to_x,
                to_y,
                steps,
                hold_ms,
                step_delay_ms,
                settle_ms,
            } => drag_chunks(
                MouseButton::parse(button)?,
                (*x, *y),
                (*to_x, *to_y),
                *steps,
                (*hold_ms, *step_delay_ms, *settle_ms),
                screen,
            ),
            InputEvent::Text { text } => text_chunks(text),
            _ => {
                let (press, release) = event_button_effect(event)?;
                let mut clamped_moves = 0usize;
                let records = event_records(event, screen, &mut clamped_moves)?;
                Ok(vec![PreparedChunk {
                    records,
                    pause_ms: 0,
                    press,
                    release,
                    clamped_moves,
                }])
            }
        }
    }

    /// Текст — по одному знаку на отправку с паузой TEXT_UNIT_PAUSE_MS после каждого.
    /// Перевод строки и табуляция — настоящими клавишами: юникодный `\n` многие
    /// программы Mac не считают за Return, а Return считают все.
    fn text_chunks(text: &str) -> Result<Vec<PreparedChunk>> {
        let units = text.encode_utf16().count();
        if units > MAX_INPUT_TEXT_UTF16_UNITS {
            bail!("input text has {units} UTF-16 units (maximum {MAX_INPUT_TEXT_UTF16_UNITS})")
        }
        let mut chunks = Vec::with_capacity(text.chars().count());
        let mut previous = '\0';
        for value in text.chars() {
            if value == '\n' && previous == '\r' {
                previous = value;
                continue;
            }
            previous = value;
            let records = match value {
                '\r' | '\n' => vec![Record::KeyDown(KEY_RETURN), Record::KeyUp(KEY_RETURN)],
                '\t' => vec![Record::KeyDown(KEY_TAB), Record::KeyUp(KEY_TAB)],
                _ => {
                    let mut buffer = [0u16; 2];
                    let encoded = value.encode_utf16(&mut buffer).to_vec();
                    vec![
                        Record::Unicode {
                            units: encoded.clone(),
                            down: true,
                        },
                        Record::Unicode {
                            units: encoded,
                            down: false,
                        },
                    ]
                }
            };
            chunks.push(PreparedChunk {
                records,
                pause_ms: TEXT_UNIT_PAUSE_MS,
                press: None,
                release: None,
                clamped_moves: 0,
            });
        }
        Ok(chunks)
    }

    fn drag_chunks(
        button: MouseButton,
        from: (i32, i32),
        to: (i32, i32),
        steps: u32,
        pauses: (u64, u64, u64),
        screen: Screen,
    ) -> Result<Vec<PreparedChunk>> {
        if !(1..=MAX_DRAG_STEPS).contains(&steps) {
            bail!("drag steps must be between 1 and {MAX_DRAG_STEPS}")
        }
        let (hold_ms, step_delay_ms, settle_ms) = pauses;
        for (name, value) in [
            ("hold_ms", hold_ms),
            ("step_delay_ms", step_delay_ms),
            ("settle_ms", settle_ms),
        ] {
            if value > MAX_DRAG_PAUSE_MS {
                bail!("drag {name} is {value}ms (maximum {MAX_DRAG_PAUSE_MS}ms per pause)")
            }
        }
        let mut chunks = Vec::with_capacity(steps as usize + 2);
        let mut clamped_moves = 0usize;
        let press = vec![
            absolute_move(from.0, from.1, screen, &mut clamped_moves),
            Record::ButtonDown {
                button,
                click_state: 1,
            },
        ];
        chunks.push(PreparedChunk {
            records: press,
            pause_ms: hold_ms,
            press: Some(button),
            release: None,
            clamped_moves,
        });
        for step in 1..=steps {
            let point = |start: i32, end: i32| -> i32 {
                let span = i64::from(end) - i64::from(start);
                (i64::from(start) + span * i64::from(step) / i64::from(steps)) as i32
            };
            let mut clamped_moves = 0usize;
            let record = absolute_move(
                point(from.0, to.0),
                point(from.1, to.1),
                screen,
                &mut clamped_moves,
            );
            chunks.push(PreparedChunk {
                records: vec![record],
                pause_ms: if step == steps { settle_ms } else { step_delay_ms },
                press: None,
                release: None,
                clamped_moves,
            });
        }
        chunks.push(PreparedChunk {
            records: vec![Record::ButtonUp {
                button,
                click_state: 1,
            }],
            pause_ms: 0,
            press: None,
            release: Some(button),
            clamped_moves: 0,
        });
        Ok(chunks)
    }

    fn event_records(
        event: &InputEvent,
        screen: Screen,
        clamped_moves: &mut usize,
    ) -> Result<Vec<Record>> {
        match event {
            InputEvent::Hotkey { keys } => hotkey_records(keys),
            InputEvent::Key { key, action } => key_action_records(key, action),
            InputEvent::Mouse { x, y, relative } => {
                if *relative {
                    Ok(vec![Record::MoveBy { dx: *x, dy: *y }])
                } else {
                    Ok(vec![absolute_move(*x, *y, screen, clamped_moves)])
                }
            }
            InputEvent::Click {
                button,
                x,
                y,
                count,
            } => click_records(button, *x, *y, *count, screen, clamped_moves),
            InputEvent::Wheel {
                delta,
                horizontal,
                x,
                y,
            } => wheel_records(*delta, *horizontal, *x, *y, screen, clamped_moves),
            InputEvent::MouseDown { button, x, y } => {
                button_edge_records(button, *x, *y, true, screen, clamped_moves)
            }
            InputEvent::MouseUp { button, x, y } => {
                button_edge_records(button, *x, *y, false, screen, clamped_moves)
            }
            InputEvent::Text { .. } | InputEvent::Drag { .. } => {
                bail!("text and drag are expanded into batches by event_chunks")
            }
        }
    }

    fn button_edge_records(
        button: &str,
        x: Option<i32>,
        y: Option<i32>,
        press: bool,
        screen: Screen,
        clamped_moves: &mut usize,
    ) -> Result<Vec<Record>> {
        if x.is_some() != y.is_some() {
            bail!("mouse button x and y must be supplied together")
        }
        let button = MouseButton::parse(button)?;
        let mut records = Vec::with_capacity(2);
        if let (Some(x), Some(y)) = (x, y) {
            records.push(absolute_move(x, y, screen, clamped_moves));
        }
        records.push(if press {
            Record::ButtonDown {
                button,
                click_state: 1,
            }
        } else {
            Record::ButtonUp {
                button,
                click_state: 1,
            }
        });
        Ok(records)
    }

    fn hotkey_records(keys: &[KeyArg]) -> Result<Vec<Record>> {
        if keys.is_empty() {
            bail!("hotkey keys must not be empty")
        }
        if keys.len() > MAX_HOTKEY_KEYS {
            bail!("hotkey has {} keys (maximum {MAX_HOTKEY_KEYS})", keys.len())
        }
        let codes = keys.iter().map(key_code).collect::<Result<Vec<_>>>()?;
        let mut records = Vec::with_capacity(codes.len() * 2);
        records.extend(codes.iter().map(|code| Record::KeyDown(*code)));
        records.extend(codes.iter().rev().map(|code| Record::KeyUp(*code)));
        Ok(records)
    }

    fn key_action_records(key: &KeyArg, action: &str) -> Result<Vec<Record>> {
        let code = key_code(key)?;
        match action.trim().to_ascii_lowercase().as_str() {
            "down" => Ok(vec![Record::KeyDown(code)]),
            "up" => Ok(vec![Record::KeyUp(code)]),
            "press" => Ok(vec![Record::KeyDown(code), Record::KeyUp(code)]),
            _ => bail!("key action must be press, down, or up"),
        }
    }

    /// Двойной и тройной щелчок на Mac — это click state 2 и 3 у самих событий, а не
    /// просто два нажатия подряд: без него программы видят два одиночных щелчка.
    fn click_records(
        button: &str,
        x: Option<i32>,
        y: Option<i32>,
        count: u32,
        screen: Screen,
        clamped_moves: &mut usize,
    ) -> Result<Vec<Record>> {
        if x.is_some() != y.is_some() {
            bail!("click x and y must be supplied together")
        }
        let button = MouseButton::parse(button)?;
        if !(1..=MAX_CLICK_COUNT).contains(&count) {
            bail!("click count must be between 1 and {MAX_CLICK_COUNT}")
        }
        let mut records = Vec::with_capacity(count as usize * 2 + usize::from(x.is_some()));
        if let (Some(x), Some(y)) = (x, y) {
            records.push(absolute_move(x, y, screen, clamped_moves));
        }
        for click_state in 1..=count {
            records.push(Record::ButtonDown {
                button,
                click_state,
            });
            records.push(Record::ButtonUp {
                button,
                click_state,
            });
        }
        Ok(records)
    }

    fn wheel_records(
        delta: i32,
        horizontal: bool,
        x: Option<i32>,
        y: Option<i32>,
        screen: Screen,
        clamped_moves: &mut usize,
    ) -> Result<Vec<Record>> {
        if x.is_some() != y.is_some() {
            bail!("wheel x and y must be supplied together")
        }
        let mut records = Vec::with_capacity(2);
        if let (Some(x), Some(y)) = (x, y) {
            records.push(absolute_move(x, y, screen, clamped_moves));
        }
        let lines = wheel_lines(delta);
        records.push(if horizontal {
            Record::Wheel {
                vertical: 0,
                horizontal: lines,
            }
        } else {
            Record::Wheel {
                vertical: lines,
                horizontal: 0,
            }
        });
        Ok(records)
    }

    /// Строки прокрутки из Windows-дельты: 120 — одна зарубка — три строки; знак
    /// сохраняется, и ненулевая дельта меньше трети зарубки всё равно даёт одну строку,
    /// а не молчаливый ноль.
    pub(super) fn wheel_lines(delta: i32) -> i32 {
        if delta == 0 {
            return 0;
        }
        let lines = i64::from(delta) * i64::from(WHEEL_LINES_PER_NOTCH) / i64::from(WHEEL_NOTCH);
        if lines == 0 {
            delta.signum()
        } else {
            lines.clamp(i64::from(i32::MIN), i64::from(i32::MAX)) as i32
        }
    }

    fn absolute_move(x: i32, y: i32, screen: Screen, clamped_moves: &mut usize) -> Record {
        let (cx, cy, pulled) = screen.clamp(x, y);
        if pulled {
            // Координата за пределами экрана подтягивается к краю — и это считается,
            // а не делается молча (см. Windows-ветку `absolute_move`).
            *clamped_moves = clamped_moves.saturating_add(1);
        }
        Record::MoveTo { x: cx, y: cy }
    }

    // ─── клавиши ────────────────────────────────────────────────────────────────────

    pub(super) const KEY_RETURN: u16 = 0x24;
    pub(super) const KEY_TAB: u16 = 0x30;

    pub(super) fn key_code(key: &KeyArg) -> Result<u16> {
        match key {
            KeyArg::Number(0) => bail!("virtual key must be nonzero"),
            KeyArg::Number(value) => Ok(*value),
            KeyArg::Text(raw) => key_code_by_name(raw),
        }
    }

    /// Имена — те же, что у Windows-ветки (`key_value`), плюс родные синонимы Mac.
    /// Коды — `kVK_*` из Carbon Events.h; буквы и цифры — по физическим клавишам
    /// раскладки ANSI (для сочетаний это и нужно: ⌘C — это клавиша C, какая бы раскладка
    /// ни стояла; сам текст идёт юникодом и от раскладки не зависит).
    pub(super) fn key_code_by_name(raw: &str) -> Result<u16> {
        let key = raw.trim().to_ascii_lowercase();
        let value = match key.as_str() {
            "backspace" => 0x33,
            "tab" => KEY_TAB,
            "enter" | "return" => KEY_RETURN,
            "shift" | "left_shift" => 0x38,
            "right_shift" => 0x3C,
            "ctrl" | "control" | "left_ctrl" => 0x3B,
            "right_ctrl" | "right_control" => 0x3E,
            "alt" | "option" | "left_alt" => 0x3A,
            "right_alt" | "right_option" => 0x3D,
            "win" | "meta" | "left_win" | "cmd" | "command" | "left_cmd" => 0x37,
            "right_win" | "right_cmd" | "right_command" => 0x36,
            "caps_lock" | "capslock" => 0x39,
            "escape" | "esc" => 0x35,
            "space" => 0x31,
            "page_up" | "pageup" => 0x74,
            "page_down" | "pagedown" => 0x79,
            "end" => 0x77,
            "home" => 0x73,
            "left" => 0x7B,
            "up" => 0x7E,
            "right" => 0x7C,
            "down" => 0x7D,
            // Клавиша Insert внешней PC-клавиатуры приходит в macOS как Help (0x72);
            // своей клавиши Insert у Mac нет.
            "insert" => 0x72,
            "delete" | "del" => 0x75,
            "fn" => 0x3F,
            "pause" | "print_screen" | "printscreen" => {
                bail!("key {raw:?} does not exist on a Mac keyboard")
            }
            _ if key.len() == 1 => {
                let byte = key.as_bytes()[0];
                match byte {
                    b'a' => 0x00,
                    b's' => 0x01,
                    b'd' => 0x02,
                    b'f' => 0x03,
                    b'h' => 0x04,
                    b'g' => 0x05,
                    b'z' => 0x06,
                    b'x' => 0x07,
                    b'c' => 0x08,
                    b'v' => 0x09,
                    b'b' => 0x0B,
                    b'q' => 0x0C,
                    b'w' => 0x0D,
                    b'e' => 0x0E,
                    b'r' => 0x0F,
                    b'y' => 0x10,
                    b't' => 0x11,
                    b'1' => 0x12,
                    b'2' => 0x13,
                    b'3' => 0x14,
                    b'4' => 0x15,
                    b'6' => 0x16,
                    b'5' => 0x17,
                    b'9' => 0x19,
                    b'7' => 0x1A,
                    b'8' => 0x1C,
                    b'0' => 0x1D,
                    b'o' => 0x1F,
                    b'u' => 0x20,
                    b'i' => 0x22,
                    b'p' => 0x23,
                    b'l' => 0x25,
                    b'j' => 0x26,
                    b'k' => 0x28,
                    b'n' => 0x2D,
                    b'm' => 0x2E,
                    _ => bail!("unsupported named key {raw:?}; pass a numeric macOS key code (kVK)"),
                }
            }
            _ if key.starts_with('f') => {
                let number = key[1..].parse::<u16>().unwrap_or_default();
                match number {
                    1 => 0x7A,
                    2 => 0x78,
                    3 => 0x63,
                    4 => 0x76,
                    5 => 0x60,
                    6 => 0x61,
                    7 => 0x62,
                    8 => 0x64,
                    9 => 0x65,
                    10 => 0x6D,
                    11 => 0x67,
                    12 => 0x6F,
                    13 => 0x69,
                    14 => 0x6B,
                    15 => 0x71,
                    16 => 0x6A,
                    17 => 0x40,
                    18 => 0x4F,
                    19 => 0x50,
                    20 => 0x5A,
                    _ => bail!("function key must be f1 through f20 on macOS"),
                }
            }
            _ => bail!("unsupported named key {raw:?}; pass a numeric macOS key code (kVK)"),
        };
        Ok(value)
    }

    /// Пределы ввода едут в КАЖДОМ ответе (см. Windows-ветку `input_limits`).
    pub(super) fn input_limits() -> Value {
        json!({
            "max_events": MAX_INPUT_EVENTS,
            "max_input_records": MAX_INPUT_RECORDS,
            "max_text_utf16_units": MAX_INPUT_TEXT_UTF16_UNITS,
            "max_hotkey_keys": MAX_HOTKEY_KEYS,
            "max_click_count": MAX_CLICK_COUNT,
            "max_total_delay_ms": MAX_TOTAL_INPUT_DELAY_MS,
            "max_drag_steps": MAX_DRAG_STEPS,
            "default_drag_steps": DEFAULT_DRAG_STEPS,
            "max_drag_pause_ms": MAX_DRAG_PAUSE_MS,
            "default_drag_pauses_ms": {
                "hold": DEFAULT_DRAG_HOLD_MS,
                "step_delay": DEFAULT_DRAG_STEP_DELAY_MS,
                "settle": DEFAULT_DRAG_SETTLE_MS,
            },
            "typing_pacing_ms": TEXT_UNIT_PAUSE_MS,
            "max_text_chars_per_call_at_pacing": MAX_TOTAL_INPUT_DELAY_MS / TEXT_UNIT_PAUSE_MS,
            "coordinates": "points in the global CoreGraphics space (origin at the top-left of the main display, Y down); absolute x/y are clamped into virtual_screen; clamped_moves says how many were pulled to the edge",
            "keys": "same names as on Windows; win/cmd/meta = Command, alt/option = Option, ctrl = Control; letters and digits are physical ANSI keys (hotkeys), text goes as Unicode",
            "numeric_keys": "a numeric key is a macOS virtual key code (kVK_*), not a Windows VK code",
            "wheel": format!("delta {WHEEL_NOTCH} = one notch = {WHEEL_LINES_PER_NOTCH} lines; positive = up, or right when horizontal (as on Windows)"),
            "button_hold": "a held mouse button never survives the call: whatever this batch leaves down is released before returning and named in buttons_auto_released",
            "modifiers_scope": "call",
            "modifier_hold": "modifiers_scope is \"call\": shift/ctrl/alt/cmd pressed with `key down` live only until this call returns - whatever is still down is released AFTER the last event of the batch and named in modifiers_auto_released. Hold and use a modifier in ONE batch: a hotkey event, or key down -> key press -> key up together. The body keeps no global HID state between calls",
            "focus_guard": "expected_foreground/expected_pid are re-checked before EVERY batch (each typed character is a batch), so a drag or a text is aborted mid-way if the foreground moves; the held button is released and named in the error",
            "permissions": "input needs the Accessibility permission (TCC); without it macOS drops posted events silently, so the body refuses before the first event",
        })
    }

    // ─── процессы ───────────────────────────────────────────────────────────────────

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub(super) struct PsRow {
        pub(super) pid: u32,
        pub(super) ppid: u32,
        pub(super) uid: u32,
        /// `comm` у macOS `ps` — путь исполняемого файла как его запустили; может
        /// содержать пробелы («…/Google Chrome.app/Contents/MacOS/Google Chrome»).
        pub(super) comm: String,
    }

    /// Разбор `ps -axo pid=,ppid=,uid=,comm=`. ⚠ `ps` выравнивает числа пробелами
    /// слева — строка начинается с пробелов, и наивный `split(' ')` даёт пустые поля
    /// (на раннере так уже получали пустую таблицу). Поэтому — обрезка и
    /// `splitn(4)` по пробельным пробегам, хвост целиком — путь. Нечитаемые строки не
    /// выбрасываются молча: их число возвращается рядом.
    pub(super) fn parse_ps(text: &str) -> (Vec<PsRow>, usize) {
        let mut rows = Vec::new();
        let mut skipped = 0usize;
        for line in text.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let parsed = (|| {
                let (pid, rest) = split_first(line)?;
                let (ppid, rest) = split_first(rest)?;
                let (uid, comm) = split_first(rest)?;
                let comm = comm.trim().to_string();
                if comm.is_empty() {
                    return None;
                }
                Some(PsRow {
                    pid: pid.parse().ok()?,
                    ppid: ppid.parse().ok()?,
                    uid: uid.parse().ok()?,
                    comm,
                })
            })();
            match parsed {
                Some(row) => rows.push(row),
                None => skipped += 1,
            }
        }
        (rows, skipped)
    }

    /// Первое поле и остаток после пробельного пробега (без ведущих пробелов).
    fn split_first(text: &str) -> Option<(&str, &str)> {
        let text = text.trim_start();
        let end = text.find(char::is_whitespace)?;
        Some((&text[..end], text[end..].trim_start()))
    }

    pub(super) fn file_name(path: &str) -> &str {
        path.rsplit('/').next().unwrap_or(path)
    }

    /// Строка процесса — форма Windows-ветки; чего у macOS нет (сессии, число потоков,
    /// время создания в FILETIME), стоит `null`, а не выдумка. Время старта у macOS ЕСТЬ
    /// (`proc_pidinfo`), но в СВОИХ единицах — секунды Unix, и едет оно своим полем
    /// `process_started_unix`; `created_filetime` остаётся `null`, потому что FILETIME
    /// — это 100 нс от 1601 года, и подстановка туда секунд была бы ложью формой.
    pub(super) fn process_row(row: &PsRow, path: Option<&str>, started: Option<u64>) -> Value {
        let path = path.or_else(|| row.comm.starts_with('/').then_some(row.comm.as_str()));
        json!({
            "pid": row.pid,
            "parent_pid": row.ppid,
            "uid": row.uid,
            "threads": null,
            "name": file_name(path.unwrap_or(row.comm.as_str())),
            "path": path,
            "session_id": null,
            "created_filetime": null,
            "process_started_unix": started,
        })
    }

    /// Путь к пакету `.app` по пути исполняемого файла внутри него:
    /// `/Applications/Safari.app/Contents/MacOS/Safari` → `/Applications/Safari.app`.
    pub(super) fn app_bundle(executable: &str) -> Option<String> {
        let index = executable.rfind(".app/")?;
        Some(executable[..index + 4].to_string())
    }

    // ─── окна ───────────────────────────────────────────────────────────────────────

    /// Что известно об окне без единого вызова системы — вход для `window_row`.
    #[derive(Debug, Clone, PartialEq)]
    pub(super) struct WindowFacts {
        pub(super) id: u32,
        pub(super) pid: i32,
        pub(super) owner: String,
        pub(super) title: Option<String>,
        pub(super) layer: i32,
        pub(super) x: f64,
        pub(super) y: f64,
        pub(super) width: f64,
        pub(super) height: f64,
        pub(super) on_screen: bool,
        pub(super) z_order: usize,
    }

    pub(super) fn hwnd_hex(id: u32) -> String {
        format!("0x{id:X}")
    }

    pub(super) fn round(value: f64) -> i64 {
        value.round() as i64
    }

    /// Строка окна — форма Windows-ветки `window_row`. `titles_visible` — есть ли
    /// «Запись экрана»: без неё система не отдаёт заголовки чужих окон, и `title: null`
    /// получает `note`, чтобы «без названия» не читалось как «окно без заголовка».
    ///
    /// `class` — идентификатор пакета (`com.apple.finder`), как у `read_window`
    /// (`uia::window_info`); запасное — имя владельца. Имя владельца при этом остаётся
    /// отдельным полем `owner`: это разные вещи, и раньше `class` молча подменял одно
    /// другим, из-за чего отбор по `class` на Mac и на Windows значил разное.
    ///
    /// `fingerprint` — `hwnd:pid:<время старта процесса>`, как на Windows: номера
    /// процессов система переиспользует, и без времени старта один pid выдавал бы новый
    /// процесс за прежний. Не узнали — в отпечатке `0`, а в строке `process_started_unix:
    /// null`. `process_created_filetime` остаётся `null`: это единицы Windows (100 нс от
    /// 1601 года), и выдавать за них секунды Unix значило бы соврать формой.
    /// Чего у macOS нет (поток, сессия, «свёрнуто» из CGWindowList) — `null`.
    pub(super) fn window_row(
        facts: &WindowFacts,
        process_path: Option<&str>,
        class_name: Option<&str>,
        process_started: Option<u64>,
        titles_visible: bool,
        foreground: Option<bool>,
    ) -> Value {
        let hex = hwnd_hex(facts.id);
        let left = round(facts.x);
        let top = round(facts.y);
        let width = round(facts.width);
        let height = round(facts.height);
        let mut row = json!({
            "hwnd": hex,
            "fingerprint": format!("{hex}:{}:{}", facts.pid, process_started.unwrap_or(0)),
            "pid": facts.pid,
            "thread_id": null,
            "session_id": null,
            "process_path": process_path,
            "process_created_filetime": null,
            "process_started_unix": process_started,
            "title": facts.title,
            "class": class_name.unwrap_or(facts.owner.as_str()),
            "owner": facts.owner,
            "rect": {
                "left": left,
                "top": top,
                "right": left + width,
                "bottom": top + height,
                "width": width,
                "height": height,
            },
            "visible": facts.on_screen,
            "minimized": null,
            "z_order": facts.z_order,
            "layer": facts.layer,
        });
        if let Some(foreground) = foreground {
            row["foreground"] = Value::Bool(foreground);
        }
        if facts.title.is_none() && !titles_visible {
            row["note"] = Value::String(
                "title hidden: without the Screen Recording permission macOS does not \
                 report other apps' window titles"
                    .into(),
            );
        }
        row
    }

    pub(super) fn page(rows: Vec<Value>, args: PageArgs) -> Value {
        let total = rows.len();
        let limit = args.limit.clamp(1, MAX_PAGE);
        let items: Vec<_> = rows.into_iter().skip(args.offset).take(limit).collect();
        json!({
            "ok": true,
            "total": total,
            "offset": args.offset,
            "limit": limit,
            "returned": items.len(),
            "next_offset": (args.offset + items.len() < total).then_some(args.offset + items.len()),
            "items": items,
        })
    }

    // ─── снимок ─────────────────────────────────────────────────────────────────────

    /// Раскладка байтов 32-битного пикселя CGImage в памяти.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(super) enum PixelLayout {
        Bgra,
        Argb,
        Rgba,
        Abgr,
    }

    const CG_ALPHA_INFO_MASK: u32 = 0x1F;
    const CG_BYTE_ORDER_MASK: u32 = 0x7000;
    const CG_BYTE_ORDER_32_LITTLE: u32 = 2 << 12;
    const CG_BYTE_ORDER_32_BIG: u32 = 4 << 12;
    const CG_FLOAT_COMPONENTS: u32 = 1 << 8;

    /// Раскладка по `CGBitmapInfo` снимка. CGWindowListCreateImage на практике отдаёт
    /// `kCGImageAlphaPremultipliedFirst | kCGBitmapByteOrder32Little` (BGRA в памяти),
    /// но это не обещание API, поэтому раскладка читается, а не предполагается; чужой
    /// формат — отказ словами, а не картинка с перепутанными каналами.
    pub(super) fn pixel_layout(
        bitmap_info: u32,
        bits_per_pixel: usize,
        bits_per_component: usize,
    ) -> Result<PixelLayout> {
        if bits_per_pixel != 32 || bits_per_component != 8 {
            bail!(
                "capture image is {bits_per_pixel} bits per pixel / {bits_per_component} per \
                 component; only 32-bit 8-8-8-8 images are handled"
            )
        }
        if bitmap_info & CG_FLOAT_COMPONENTS != 0 {
            bail!("capture image has float components; only 8-bit integer channels are handled")
        }
        // 0 none, 1 premultiplied last, 2 premultiplied first, 3 last, 4 first,
        // 5 none-skip-last, 6 none-skip-first, 7 alpha only.
        let alpha = bitmap_info & CG_ALPHA_INFO_MASK;
        let alpha_first = match alpha {
            2 | 4 | 6 => true,
            0 | 1 | 3 | 5 => false,
            _ => bail!("capture image has alpha info {alpha}, which carries no colour"),
        };
        let little = match bitmap_info & CG_BYTE_ORDER_MASK {
            CG_BYTE_ORDER_32_LITTLE => true,
            0 | CG_BYTE_ORDER_32_BIG => false,
            other => bail!("capture image has byte order {other:#x}, not a 32-bit order"),
        };
        Ok(match (alpha_first, little) {
            (true, true) => PixelLayout::Bgra,
            (true, false) => PixelLayout::Argb,
            (false, true) => PixelLayout::Abgr,
            (false, false) => PixelLayout::Rgba,
        })
    }

    /// Плотный BGRA (то, что ест `write_png`) из строк CGImage любой из четырёх
    /// раскладок; `bytes_per_row` может быть шире `width * 4` — хвост строки выкидывается.
    /// Альфа не используется: снимок экрана непрозрачен, а у окна с прозрачными углами
    /// премультиплицированный цвет — это «поверх чёрного», и так его и видно.
    pub(super) fn to_bgra(
        bytes: &[u8],
        width: usize,
        height: usize,
        bytes_per_row: usize,
        layout: PixelLayout,
    ) -> Result<Vec<u8>> {
        let row_bytes = width.checked_mul(4).context("capture row size overflow")?;
        if bytes_per_row < row_bytes {
            bail!("capture rows are {bytes_per_row} bytes, narrower than {width} pixels")
        }
        let needed = bytes_per_row
            .checked_mul(height.saturating_sub(1))
            .and_then(|value| value.checked_add(row_bytes))
            .context("capture buffer size overflow")?;
        if bytes.len() < needed {
            bail!(
                "capture buffer has {} bytes; {needed} needed for {width}x{height}",
                bytes.len()
            )
        }
        let mut out = Vec::with_capacity(row_bytes * height);
        for row in 0..height {
            let start = row * bytes_per_row;
            for pixel in bytes[start..start + row_bytes].chunks_exact(4) {
                let (b, g, r) = match layout {
                    PixelLayout::Bgra => (pixel[0], pixel[1], pixel[2]),
                    PixelLayout::Argb => (pixel[3], pixel[2], pixel[1]),
                    PixelLayout::Rgba => (pixel[2], pixel[1], pixel[0]),
                    PixelLayout::Abgr => (pixel[1], pixel[2], pixel[3]),
                };
                out.extend_from_slice(&[b, g, r, 255]);
            }
        }
        Ok(out)
    }

    /// Как уменьшать снимок до пунктов.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(super) enum Downscale {
        /// Пиксели и пункты совпадают (или просили `native`).
        None,
        /// Целый масштаб (Retina: 2): усреднение блоков n×n — честнее, чем выбросить три
        /// пикселя из четырёх, и текст остаётся читаемым.
        Box(usize),
        /// Нецелое отношение (снимок через дисплеи с разным масштабом, окно, срезанное
        /// краем): ближайший пиксель. Назван в ответе полем `downscale`.
        Nearest,
    }

    /// Что выйдет из снимка: масштаб, способ уменьшения, размер результата В ПУНКТАХ и
    /// разошлась ли рамка с образом.
    #[derive(Debug, Clone, Copy, PartialEq)]
    pub(super) struct CapturePlan {
        /// Пикселей на пункт — выведен из ШИРИНЫ: `образ / рамка`.
        pub(super) scale: f64,
        pub(super) downscale: Downscale,
        /// Пункты результата — сколько стола в нём на самом деле.
        pub(super) width: usize,
        pub(super) height: usize,
        /// Размер рамки в пунктах, если он разошёлся с образом больше чем на пункт.
        pub(super) frame_mismatch: Option<(usize, usize)>,
    }

    /// План снимка. ⚠ ГЛАВНОЕ: размер результата считается от ФАКТИЧЕСКОГО образа, а от
    /// рамки берётся только левый верхний угол и масштаб по ширине.
    ///
    /// Почему. Рамка окна из CGWindowList и образ из CGWindowListCreateImage совпадают
    /// не всегда: рамка округлена до пунктов, а образ идёт без тени и полей
    /// (`kCGWindowImageBoundsIgnoreFraming`). Раньше целью уменьшения была РАМКА, и
    /// образ 1000×600 при рамке 500×301 уезжал ближайшим пикселем в 500×301 — растяжка
    /// до чужого размера: картинка, которой на экране не было, и каждая координата в ней
    /// смещена. Теперь тот же случай — честное уменьшение блоками в 500×300.
    pub(super) fn plan_capture(
        pixel_width: usize,
        pixel_height: usize,
        frame_width: usize,
        frame_height: usize,
        native: bool,
    ) -> CapturePlan {
        let scale = if frame_width == 0 || pixel_width == 0 {
            1.0
        } else {
            pixel_width as f64 / frame_width as f64
        };
        let scale = if scale.is_finite() && scale > 0.0 { scale } else { 1.0 };
        let width = ((pixel_width as f64) / scale).round().max(1.0) as usize;
        let height = ((pixel_height as f64) / scale).round().max(1.0) as usize;
        let factor = scale as usize;
        let downscale = if native || (width == pixel_width && height == pixel_height) {
            Downscale::None
        } else if scale.fract() == 0.0
            && factor >= 2
            && pixel_width.is_multiple_of(factor)
            && pixel_height.is_multiple_of(factor)
        {
            // Целый масштаб — усреднение блоков: текст остаётся читаемым.
            Downscale::Box(factor)
        } else {
            // Нецелый масштаб (снимок через дисплеи с разным масштабом) или образ с
            // нечётной стороной — ближайший пиксель, но до СВОЕГО размера.
            Downscale::Nearest
        };
        CapturePlan {
            scale,
            downscale,
            width,
            height,
            frame_mismatch: (frame_width.abs_diff(width) > 1 || frame_height.abs_diff(height) > 1)
                .then_some((frame_width, frame_height)),
        }
    }

    /// Усреднение блоков `factor×factor`; размеры обязаны делиться на `factor`.
    pub(super) fn downscale_box(
        bgra: &[u8],
        width: usize,
        height: usize,
        factor: usize,
    ) -> Option<(Vec<u8>, usize, usize)> {
        if factor < 2 || width % factor != 0 || height % factor != 0 || bgra.len() != width * height * 4 {
            return None;
        }
        let (target_w, target_h) = (width / factor, height / factor);
        let area = (factor * factor) as u32;
        let mut out = Vec::with_capacity(target_w * target_h * 4);
        for ty in 0..target_h {
            for tx in 0..target_w {
                let mut sum = [0u32; 3];
                for dy in 0..factor {
                    let row = (ty * factor + dy) * width * 4;
                    for dx in 0..factor {
                        let at = row + (tx * factor + dx) * 4;
                        sum[0] += u32::from(bgra[at]);
                        sum[1] += u32::from(bgra[at + 1]);
                        sum[2] += u32::from(bgra[at + 2]);
                    }
                }
                out.extend_from_slice(&[
                    ((sum[0] + area / 2) / area) as u8,
                    ((sum[1] + area / 2) / area) as u8,
                    ((sum[2] + area / 2) / area) as u8,
                    255,
                ]);
            }
        }
        Some((out, target_w, target_h))
    }

    /// Ближайший пиксель к целевому размеру (для нецелого масштаба).
    pub(super) fn resample_nearest(
        bgra: &[u8],
        width: usize,
        height: usize,
        target_w: usize,
        target_h: usize,
    ) -> Vec<u8> {
        let mut out = Vec::with_capacity(target_w * target_h * 4);
        for ty in 0..target_h {
            let sy = ((ty * height) / target_h.max(1)).min(height.saturating_sub(1));
            for tx in 0..target_w {
                let sx = ((tx * width) / target_w.max(1)).min(width.saturating_sub(1));
                let at = (sy * width + sx) * 4;
                out.extend_from_slice(&bgra[at..at + 4]);
            }
        }
        out
    }
}

/// macOS: экран, ввод, окна, процессы, буфер обмена — через CoreGraphics (события и
/// снимок), CGWindowList (окна, из `mac.rs`), Accessibility (поднять окно), `ps`,
/// `pbpaste`/`pbcopy`. Формы JSON — Windows-ветки; отличия названы полем `platform`
/// и полями `tcc`/`hints`. Правила координат и TCC — в шапке `mac.rs`: всё в пунктах,
/// без разрешения — отказ словами ДО дела.
#[cfg(target_os = "macos")]
mod platform {
    use std::fs;
    use std::io::Write as _;
    use std::path::Path;
    use std::process::{Command, Stdio};
    use std::sync::{Mutex, OnceLock};
    use std::thread;
    use std::time::Duration;

    use anyhow::{Context, Result, anyhow, bail};
    use core_graphics::display::CGDisplay;
    use core_graphics::event::{
        CGEvent, CGEventFlags, CGEventTapLocation, CGEventType, CGMouseButton, EventField,
        ScrollEventUnit,
    };
    use core_graphics::event_source::{CGEventSource, CGEventSourceStateID};
    use core_graphics::geometry::{CGPoint, CGRect, CGSize};
    use core_graphics::window::{
        kCGNullWindowID, kCGWindowImageBestResolution, kCGWindowImageBoundsIgnoreFraming,
        kCGWindowListOptionIncludingWindow, kCGWindowListOptionOnScreenOnly,
    };
    use foreign_types::ForeignTypeRef;
    use serde_json::{Value, json};

    use super::mac_pure::{
        ActivateArgs, CaptureArgs, ClipboardReadArgs, ClipboardWriteArgs, Downscale, HwndArg,
        InputArgs, MAX_ACTIVATE_TIMEOUT_MS, MAX_CLIPBOARD_CHARS, MAX_INPUT_EVENTS,
        MAX_TOTAL_INPUT_DELAY_MS, MouseButton, PreparedChunk, ProcessListArgs, Record, Screen,
        TEXT_UNIT_PAUSE_MS, WindowFacts, WindowListArgs, app_bundle, downscale_box, hwnd_hex,
        input_limits, page, parse_ps, pixel_layout, plan_capture, plan_totals,
        prepare_input_events, process_row, resample_nearest, round, to_bgra, window_row,
    };
    use super::{capture_allocation, capture_name, write_png};
    // Accessibility — только через `ax.rs`: трейт нужен, чтобы звать `set_bool` и
    // `perform` у `AxElement` теми же руками, что и дерево окна.
    use crate::ax::{self, Element as _};
    use crate::mac::{self, Tcc, WindowInfo};

    // ─── FFI, которого нет в крейтах ────────────────────────────────────────────────

    // Accessibility здесь БОЛЬШЕ НЕТ. Поднятие окна ходит через `crate::ax::live`
    // (`AxElement`, `ax_window_for`): там приватная `_AXUIElementGetWindow` берётся
    // через `dlsym`, а если её не станет — работает запасной путь по pid, рамке и
    // заголовку. Второй `extern` к ней был бы вторым способом найти то же окно, и
    // разъехались бы эти два способа молча.

    #[link(name = "CoreGraphics", kind = "framework")]
    unsafe extern "C" {
        fn CGImageGetBitmapInfo(image: *mut core_graphics::sys::CGImage) -> u32;
    }

    // ─── общее ──────────────────────────────────────────────────────────────────────

    pub fn dispatch(capability: &str, args: Value, state_dir: &Path) -> Result<Value> {
        match capability {
            "desktop.status" => desktop_status(),
            "os.process.list" => process_list(serde_json::from_value(args)?),
            "desktop.window.list" => window_list(serde_json::from_value(args)?),
            "desktop.window.activate" => window_activate(serde_json::from_value(args)?),
            "desktop.input.perform" => input_perform(serde_json::from_value(args)?),
            "desktop.screen.capture" => screen_capture(serde_json::from_value(args)?, state_dir),
            "desktop.clipboard.read" => clipboard_read(serde_json::from_value(args)?),
            "desktop.clipboard.write" => clipboard_write(serde_json::from_value(args)?),
            _ => bail!("unknown native desktop capability {capability}"),
        }
    }

    /// Отказ по TCC — ОШИБКА рамки, не результат. ⚠ `body_client.call()` дерева накрывает
    /// `ok` тела рамкой транспорта (`ok=frame.ok`): результат `ok:false` доезжал бы до
    /// модели как `"ok": true` — ложь в сторону успеха. В тексте ошибки — слова
    /// `Tcc::hints()`, чтобы «куда идти» доехало вместе с отказом (решение ведущего 19.09).
    fn refused_by_tcc(what: &str, tcc: Tcc) -> anyhow::Error {
        anyhow!(
            "{what}; {} (tcc: screen_recording={}, accessibility={}; platform=macos)",
            tcc.hints().join("; "),
            tcc.screen_recording,
            tcc.accessibility
        )
    }

    fn facts(window: &WindowInfo, z_order: usize) -> WindowFacts {
        WindowFacts {
            id: window.id,
            pid: window.pid,
            owner: window.owner.clone(),
            title: window.title.clone(),
            layer: window.layer,
            x: window.x,
            y: window.y,
            width: window.width,
            height: window.height,
            on_screen: window.on_screen,
            z_order,
        }
    }

    /// `class` строки окна — идентификатор пакета владельца (`com.apple.finder`), ровно
    /// то же, что кладёт в `class` чтение окна (`uia::window_info`). Не узнали пакет
    /// (голый бинарь, процесс без прав) — `None`, и тогда строка ставит туда имя
    /// владельца, а не пустоту.
    fn window_class(window: &WindowInfo) -> Option<String> {
        ax::live::bundle_identifier(window.pid)
    }

    /// Путь исполняемого файла процесса (`proc_pidpath`); чужие процессы без прав — `None`.
    fn process_path(pid: i32) -> Option<String> {
        let mut buffer = vec![0u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
        let length = unsafe {
            libc::proc_pidpath(pid, buffer.as_mut_ptr().cast(), buffer.len() as u32)
        };
        (length > 0).then(|| String::from_utf8_lossy(&buffer[..length as usize]).into_owned())
    }

    fn screen_points() -> Screen {
        let screen = mac::virtual_screen();
        Screen {
            left: round(screen.left) as i32,
            top: round(screen.top) as i32,
            width: round(screen.width) as i32,
            height: round(screen.height) as i32,
        }
    }

    fn virtual_screen() -> Value {
        let screen = mac::virtual_screen();
        let (left, top) = (round(screen.left), round(screen.top));
        let (width, height) = (round(screen.width), round(screen.height));
        json!({
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "right": left + width,
            "bottom": top + height,
            "displays": screen.displays,
        })
    }

    fn hid_source() -> Result<CGEventSource> {
        CGEventSource::new(CGEventSourceStateID::HIDSystemState)
            .map_err(|_| anyhow!("CGEventSourceCreate(HIDSystemState) failed"))
    }

    fn cursor_location(source: &CGEventSource) -> Option<(f64, f64)> {
        let event = CGEvent::new(source.clone()).ok()?;
        let point = event.location();
        Some((point.x, point.y))
    }

    fn foreground_value(window: Option<&WindowInfo>) -> Value {
        window.map_or(Value::Null, |w| Value::String(hwnd_hex(w.id)))
    }

    // ─── desktop.status ─────────────────────────────────────────────────────────────

    fn desktop_status() -> Result<Value> {
        let tcc = mac::tcc();
        let foreground = mac::frontmost()?;
        let cursor = hid_source()
            .ok()
            .and_then(|source| cursor_location(&source))
            .map(|(x, y)| json!({"x": round(x), "y": round(y)}));
        Ok(json!({
            "ok": true,
            "interactive": mac::gui_session(),
            "session_id": null,
            "foreground": foreground.as_ref().map(|window| {
                window_row(
                    &facts(window, 0),
                    process_path(window.pid).as_deref(),
                    window_class(window).as_deref(),
                    mac::process_started(window.pid),
                    tcc.screen_recording,
                    None,
                )
            }),
            "cursor": cursor,
            "virtual_screen": virtual_screen(),
            "platform": "macos",
            "scale": mac::main_scale(),
            "tcc": tcc,
            "hints": tcc.hints(),
        }))
    }

    // ─── os.process.list ────────────────────────────────────────────────────────────

    fn process_list(args: ProcessListArgs) -> Result<Value> {
        if args.session_id.is_some() {
            bail!("os.process.list: macOS has no session ids; omit session_id")
        }
        let output = Command::new("ps")
            .args(["-axo", "pid=,ppid=,uid=,comm="])
            .stdin(Stdio::null())
            .output()
            .context("run ps")?;
        if !output.status.success() {
            bail!(
                "ps exited with {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            )
        }
        let (rows, skipped) = parse_ps(&String::from_utf8_lossy(&output.stdout));
        let needle = args.name_contains.to_lowercase();
        let mut items: Vec<Value> = rows
            .iter()
            .filter_map(|row| {
                let pid = row.pid as i32;
                let path = process_path(pid);
                let value = process_row(row, path.as_deref(), mac::process_started(pid));
                let matches = needle.is_empty()
                    || value["name"]
                        .as_str()
                        .is_some_and(|name| name.to_lowercase().contains(&needle))
                    || value["path"]
                        .as_str()
                        .is_some_and(|path| path.to_lowercase().contains(&needle));
                matches.then_some(value)
            })
            .collect();
        items.sort_by_key(|row| row["pid"].as_u64().unwrap_or_default());
        let mut result = page(items, args.page);
        result["platform"] = Value::String("macos".into());
        if skipped > 0 {
            result["unparsed_lines"] = Value::from(skipped);
        }
        Ok(result)
    }

    // ─── desktop.window.list ────────────────────────────────────────────────────────

    fn window_list(args: WindowListArgs) -> Result<Value> {
        let tcc = mac::tcc();
        if !args.title_contains.is_empty() && !tcc.screen_recording {
            // Без «Записи экрана» заголовков чужих окон нет — фильтр по ним отдал бы
            // пустой список, который читается как «такого окна нет». Это ложь.
            return Err(refused_by_tcc(
                "title_contains cannot be applied: without the Screen Recording permission \
                 macOS hides other apps' window titles, so the filter would match nothing",
                tcc,
            ));
        }
        let windows = mac::window_list(args.visible_only)?;
        let foreground = mac::frontmost()?.map(|window| window.id);
        let needle = args.title_contains.to_lowercase();
        let mut rows = Vec::new();
        let mut seen: std::collections::HashMap<i32, (Option<String>, Option<String>, Option<u64>)> =
            std::collections::HashMap::new();
        for window in &windows {
            if !args.all_layers && !window.is_ordinary() {
                continue;
            }
            if args.pid.is_some_and(|expected| window.pid as u32 != expected) {
                continue;
            }
            if !needle.is_empty()
                && !window
                    .title
                    .as_deref()
                    .is_some_and(|title| title.to_lowercase().contains(&needle))
            {
                continue;
            }
            let z_order = rows.len();
            // Про один процесс спрашиваем один раз: у приложения десятки окон, а
            // `bundle_identifier` открывает пакет и читает Info.plist с диска.
            let about = seen
                .entry(window.pid)
                .or_insert_with(|| {
                    (
                        process_path(window.pid),
                        window_class(window),
                        mac::process_started(window.pid),
                    )
                })
                .clone();
            rows.push(window_row(
                &facts(window, z_order),
                about.0.as_deref(),
                about.1.as_deref(),
                about.2,
                tcc.screen_recording,
                Some(Some(window.id) == foreground),
            ));
        }
        let mut result = page(rows, args.page);
        result["foreground_hwnd"] = foreground.map_or(Value::Null, |id| Value::String(hwnd_hex(id)));
        result["platform"] = Value::String("macos".into());
        result["tcc"] = json!(tcc);
        result["layers"] = Value::String(if args.all_layers {
            "all WindowServer windows (menu bar, dock, overlays included)".into()
        } else {
            "ordinary windows only (layer 0, opaque, non-empty); pass all_layers: true for the \
             menu bar, dock and overlays"
                .into()
        });
        if !tcc.screen_recording {
            result["note"] = Value::String(
                "titles of other apps' windows are hidden: no Screen Recording permission".into(),
            );
            result["hints"] = json!(tcc.hints());
        }
        Ok(result)
    }

    // ─── desktop.window.activate ────────────────────────────────────────────────────

    /// Поднять окно — и сказать правду о том, поднялось ли оно.
    ///
    /// ⚠ ПОЧЕМУ ЭТО ОШИБКА, А НЕ `ok: false`. Рамка транспорта накрывает `ok` результата
    /// (`runtime.rs` ставит `ok: true` на любой `Ok`, а дерево и движок шьют
    /// `{**рамка, **результат, "ok": рамка.ok}`): ответ `{"ok": false}` доехал бы до
    /// модели как `"ok": true`. Поэтому «не подняли» — `Err` со словами, ровно как отказы
    /// TCC. Успех считается по ФАКТУ: переднее окно системы стало запрошенным.
    ///
    /// С «Универсальным доступом» — по-настоящему: приложение вперёд (`AXFrontmost`),
    /// окно развернуть (`AXMinimized`) и поднять (`AXRaise`). Без него остаётся только
    /// `open <bundle>` — он активирует ПРИЛОЖЕНИЕ, а не окно; голый бинарь без пакета
    /// `.app` поднять нечем — отказ словами.
    fn window_activate(args: ActivateArgs) -> Result<Value> {
        let id = args.hwnd.value()?;
        if args.timeout_ms > MAX_ACTIVATE_TIMEOUT_MS {
            bail!("activation timeout must not exceed {MAX_ACTIVATE_TIMEOUT_MS}ms")
        }
        let window = mac::window_by_id(id)?
            .with_context(|| format!("window {} no longer exists", hwnd_hex(id)))?;
        if let Some(expected) = args.expected_pid
            && window.pid as u32 != expected
        {
            bail!("window pid changed: expected {expected}, actual {}", window.pid)
        }
        let before = mac::frontmost()?;
        let tcc = mac::tcc();
        let mut notes: Vec<String> = Vec::new();
        let mut raised = false;
        let mut restored = false;
        let mut ax_frontmost = false;
        let timeout = Duration::from_millis(args.timeout_ms.clamp(1_000, MAX_ACTIVATE_TIMEOUT_MS));
        let (method, ax_app, ax_window) = if tcc.accessibility {
            let app = ax::live::AxElement::application(window.pid, timeout).with_context(|| {
                format!("AXUIElementCreateApplication returned NULL for pid {}", window.pid)
            })?;
            ax_frontmost = app.set_bool("AXFrontmost", true).is_ok();
            if !ax_frontmost {
                notes.push("the application refused AXFrontmost".into());
            }
            // Та же дорога, что у чтения окна: номер окна через приватную функцию
            // (dlsym), а если её нет — pid, рамка, заголовок. Второго способа искать
            // окно в теле нет.
            let found = match ax::live::ax_window_for(&window, timeout) {
                Ok(target) => {
                    let minimized = matches!(
                        target
                            .copy_attribute("AXMinimized")
                            .ok()
                            .flatten()
                            .map(|value| ax::live::raw_of(&value)),
                        Some(ax::Raw::Bool(true))
                    );
                    if args.restore && minimized {
                        restored = target.set_bool("AXMinimized", false).is_ok();
                        if !restored {
                            notes.push("the window refused to leave the Dock (AXMinimized)".into());
                        }
                    }
                    raised = target.perform("AXRaise").is_ok();
                    if !raised {
                        notes.push("the window refused AXRaise".into());
                    }
                    Some(target)
                }
                Err(words) => {
                    notes.push(format!(
                        "the Accessibility window for this CGWindowID was not found ({words}): at \
                         best the application was brought to front, not this particular window"
                    ));
                    None
                }
            };
            ("accessibility", Some(app), found)
        } else {
            let path = process_path(window.pid).with_context(|| {
                format!(
                    "no Accessibility permission and the executable of pid {} cannot be \
                     resolved: nothing can raise the window; {}",
                    window.pid,
                    tcc.hints().join("; ")
                )
            })?;
            let bundle = app_bundle(&path).with_context(|| {
                format!(
                    "no Accessibility permission and {path} is not inside an .app bundle: \
                     `open` cannot activate it; {}",
                    tcc.hints().join("; ")
                )
            })?;
            let output = Command::new("open")
                .arg(&bundle)
                .stdin(Stdio::null())
                .output()
                .context("run open")?;
            if !output.status.success() {
                bail!(
                    "open {bundle} failed: {}",
                    String::from_utf8_lossy(&output.stderr).trim()
                )
            }
            notes.push(format!(
                "no Accessibility permission: `open {bundle}` was asked to activate the whole \
                 application; this particular window is not raised and a minimized window is \
                 not restored"
            ));
            ("open", None, None)
        };
        let mut waited = 0u64;
        let mut attempts = 1u32;
        while mac::frontmost()?.map(|w| w.id) != Some(id) && waited < args.timeout_ms {
            thread::sleep(Duration::from_millis(25));
            waited += 25;
            if waited.is_multiple_of(500)
                && let (Some(app), Some(target)) = (&ax_app, &ax_window)
            {
                ax_frontmost |= app.set_bool("AXFrontmost", true).is_ok();
                raised |= target.perform("AXRaise").is_ok();
                attempts += 1;
            }
        }
        let actual = mac::frontmost()?;
        // `activated` — только по ФАКТУ: переднее окно стола принадлежит нужному
        // процессу. Литерала здесь быть не может: `open` возвращает ноль и тогда, когда
        // приложение так и не вышло вперёд, а AXFrontmost говорит «принято», не «сделано».
        let activated = actual.as_ref().is_some_and(|w| w.pid == window.pid);
        let won = actual.as_ref().is_some_and(|w| w.id == id);
        let note = (!notes.is_empty()).then(|| notes.join("; "));
        if !won {
            let did = if activated {
                "the application is in front, but THIS window did not come up"
            } else if method == "open" {
                "`open` was accepted, but the application did not come to the front"
            } else {
                "neither the application nor the window came to the front"
            };
            let mut said = format!(
                "window {} was not activated: {did} (method={method}, ax_frontmost={ax_frontmost}, \
                 activated={activated}, raised={raised}, restored={restored}, attempts={attempts}, \
                 waited_ms={waited}, foreground_before={}, foreground_hwnd={})",
                hwnd_hex(id),
                foreground_value(before.as_ref()),
                foreground_value(actual.as_ref()),
            );
            if let Some(note) = &note {
                said.push_str("; ");
                said.push_str(note);
            }
            if !tcc.accessibility {
                // Дело именно в разрешении — «куда идти» едет вместе с отказом.
                said.push_str("; ");
                said.push_str(&tcc.hints().join("; "));
            }
            return Err(anyhow!(said));
        }
        Ok(json!({
            "ok": true,
            "requested_hwnd": hwnd_hex(id),
            "foreground_before": foreground_value(before.as_ref()),
            "foreground_hwnd": foreground_value(actual.as_ref()),
            "method": method,
            "activated": activated,
            "ax_frontmost": ax_frontmost,
            "raised": raised,
            "restored": restored,
            "attempts": attempts,
            "waited_ms": waited,
            "note": note,
            "tcc": tcc,
            "hints": tcc.hints(),
            "platform": "macos",
        }))
    }


    // ─── desktop.input.perform ──────────────────────────────────────────────────────

    /// Отправитель: один источник событий на вызов, живое положение курсора, что
    /// реально нажато (для типа Dragged/Moved) и какие модификаторы зажаты (флаги
    /// каждого события — иначе ⌘C уходит как «C»).
    struct Poster {
        source: CGEventSource,
        cursor: (f64, f64),
        held: Vec<MouseButton>,
        modifiers: CGEventFlags,
        /// Коды модификаторов, которые ЭТА пачка зажала и ещё не отпустила. Флаги
        /// (`modifiers`) говорят «что подмешать в событие», а коды — «кому послать
        /// key up»; одно из другого не выводится (⌘ слева и справа дают один флаг).
        held_modifiers: Vec<u16>,
        screen: Screen,
        clamped_moves: usize,
    }

    impl Poster {
        fn new(screen: Screen) -> Result<Self> {
            let source = hid_source()?;
            let cursor = cursor_location(&source).unwrap_or((0.0, 0.0));
            Ok(Self {
                source,
                cursor,
                held: Vec::new(),
                modifiers: CGEventFlags::empty(),
                held_modifiers: Vec::new(),
                screen,
                clamped_moves: 0,
            })
        }

        /// Отпустить модификаторы, которые пачка оставила зажатыми, — ПОСЛЕ последнего
        /// её события. Состояние модификаторов живёт ровно один вызов: глобального
        /// состояния HID тело не держит, и «зажать сейчас, нажать следующим вызовом» не
        /// работает ни у кого. Уйти же с зажатой ⌘ нельзя — следующий щелчок человека
        /// станет ⌘-щелчком. Возвращает имена отпущенного.
        fn release_modifiers(&mut self) -> Vec<&'static str> {
            if self.held_modifiers.is_empty() {
                return Vec::new();
            }
            let mut names = Vec::new();
            for code in std::mem::take(&mut self.held_modifiers).into_iter().rev() {
                names.push(modifier_name(code));
                if let Some(flag) = modifier_flag(code) {
                    self.modifiers.remove(flag);
                }
                if let Ok(event) = CGEvent::new_keyboard_event(self.source.clone(), code, false) {
                    event.set_flags(self.modifiers);
                    event.post(CGEventTapLocation::HID);
                }
            }
            names.reverse();
            remember_held(&self.held, &self.held_modifiers);
            names
        }

        fn post(&mut self, record: &Record) -> Result<()> {
            match record {
                Record::KeyDown(code) => {
                    if let Some(flag) = modifier_flag(*code) {
                        self.modifiers |= flag;
                        if !self.held_modifiers.contains(code) {
                            self.held_modifiers.push(*code);
                        }
                    }
                    let event = CGEvent::new_keyboard_event(self.source.clone(), *code, true)
                        .map_err(|_| anyhow!("CGEventCreateKeyboardEvent failed"))?;
                    event.set_flags(self.modifiers);
                    event.post(CGEventTapLocation::HID);
                }
                Record::KeyUp(code) => {
                    if let Some(flag) = modifier_flag(*code) {
                        self.modifiers.remove(flag);
                        self.held_modifiers.retain(|held| held != code);
                    }
                    let event = CGEvent::new_keyboard_event(self.source.clone(), *code, false)
                        .map_err(|_| anyhow!("CGEventCreateKeyboardEvent failed"))?;
                    event.set_flags(self.modifiers);
                    event.post(CGEventTapLocation::HID);
                }
                Record::Unicode { units, down } => {
                    // Код клавиши 0 (ANSI A) — формальность: знак берётся из строки
                    // события, а не из кода; так делают все, кто печатает юникодом.
                    let event = CGEvent::new_keyboard_event(self.source.clone(), 0, *down)
                        .map_err(|_| anyhow!("CGEventCreateKeyboardEvent failed"))?;
                    event.set_string_from_utf16_unchecked(units);
                    event.set_flags(self.modifiers);
                    event.post(CGEventTapLocation::HID);
                }
                Record::MoveTo { x, y } => self.move_to(f64::from(*x), f64::from(*y))?,
                Record::MoveBy { dx, dy } => {
                    let x = (self.cursor.0 + f64::from(*dx)).round() as i32;
                    let y = (self.cursor.1 + f64::from(*dy)).round() as i32;
                    let (cx, cy, pulled) = self.screen.clamp(x, y);
                    if pulled {
                        self.clamped_moves += 1;
                    }
                    self.move_to(f64::from(cx), f64::from(cy))?;
                }
                Record::ButtonDown {
                    button,
                    click_state,
                } => {
                    let (kind, cg_button) = match button {
                        MouseButton::Left => (CGEventType::LeftMouseDown, CGMouseButton::Left),
                        MouseButton::Right => (CGEventType::RightMouseDown, CGMouseButton::Right),
                        MouseButton::Middle => (CGEventType::OtherMouseDown, CGMouseButton::Center),
                    };
                    self.mouse(kind, cg_button, i64::from(*click_state))?;
                    if !self.held.contains(button) {
                        self.held.push(*button);
                    }
                }
                Record::ButtonUp {
                    button,
                    click_state,
                } => {
                    let (kind, cg_button) = match button {
                        MouseButton::Left => (CGEventType::LeftMouseUp, CGMouseButton::Left),
                        MouseButton::Right => (CGEventType::RightMouseUp, CGMouseButton::Right),
                        MouseButton::Middle => (CGEventType::OtherMouseUp, CGMouseButton::Center),
                    };
                    self.mouse(kind, cg_button, i64::from(*click_state))?;
                    self.held.retain(|value| value != button);
                }
                Record::Wheel {
                    vertical,
                    horizontal,
                } => {
                    // Единицы — строки. Знак вертикали у Quartz тот же, что на Windows
                    // (плюс — вверх); по горизонтали Quartz считает плюс за «влево»
                    // (WebKit переворачивает знак, отдавая deltaX странице), Windows — за
                    // «вправо», поэтому здесь минус.
                    let event = CGEvent::new_scroll_event(
                        self.source.clone(),
                        ScrollEventUnit::LINE,
                        2,
                        *vertical,
                        -*horizontal,
                        0,
                    )
                    .map_err(|_| anyhow!("CGEventCreateScrollWheelEvent failed"))?;
                    event.set_location(CGPoint::new(self.cursor.0, self.cursor.1));
                    event.set_flags(self.modifiers);
                    event.post(CGEventTapLocation::HID);
                }
            }
            // Сторож родителя живёт на своём потоке и до `Poster` не дотянется:
            // запись на общую доску — единственный способ дать ему отпустить это.
            remember_held(&self.held, &self.held_modifiers);
            Ok(())
        }

        /// Сдвиг курсора. С зажатой кнопкой это Dragged, а не Moved: иначе Finder,
        /// ползунки и выделение текста не видят перетаскивания.
        fn move_to(&mut self, x: f64, y: f64) -> Result<()> {
            let (kind, button) = match self.held.first() {
                Some(MouseButton::Left) => (CGEventType::LeftMouseDragged, CGMouseButton::Left),
                Some(MouseButton::Right) => (CGEventType::RightMouseDragged, CGMouseButton::Right),
                Some(MouseButton::Middle) => (CGEventType::OtherMouseDragged, CGMouseButton::Center),
                None => (CGEventType::MouseMoved, CGMouseButton::Left),
            };
            self.cursor = (x, y);
            self.mouse(kind, button, 0)
        }

        fn mouse(&mut self, kind: CGEventType, button: CGMouseButton, click_state: i64) -> Result<()> {
            let point = CGPoint::new(self.cursor.0, self.cursor.1);
            let event = CGEvent::new_mouse_event(self.source.clone(), kind, point, button)
                .map_err(|_| anyhow!("CGEventCreateMouseEvent failed"))?;
            if click_state > 0 {
                event.set_integer_value_field(EventField::MOUSE_EVENT_CLICK_STATE, click_state);
            }
            event.set_flags(self.modifiers);
            event.post(CGEventTapLocation::HID);
            Ok(())
        }
    }

    /// Имя модификатора по коду — для ответа: «что отпустили» словами человека,
    /// а не кодами kVK.
    fn modifier_name(code: u16) -> &'static str {
        match code {
            0x38 => "shift",
            0x3C => "right_shift",
            0x3B => "ctrl",
            0x3E => "right_ctrl",
            0x3A => "alt",
            0x3D => "right_alt",
            0x37 => "cmd",
            0x36 => "right_cmd",
            0x39 => "capslock",
            0x3F => "fn",
            _ => "modifier",
        }
    }

    fn modifier_flag(code: u16) -> Option<CGEventFlags> {
        Some(match code {
            0x38 | 0x3C => CGEventFlags::CGEventFlagShift,
            0x3B | 0x3E => CGEventFlags::CGEventFlagControl,
            0x3A | 0x3D => CGEventFlags::CGEventFlagAlternate,
            0x37 | 0x36 => CGEventFlags::CGEventFlagCommand,
            0x39 => CGEventFlags::CGEventFlagAlphaShift,
            0x3F => CGEventFlags::CGEventFlagSecondaryFn,
            _ => return None,
        })
    }

    // ─── что зажато прямо сейчас (для сторожа родителя) ─────────────────────────────

    /// Общая доска: какие кнопки и какие модификаторы этот процесс держит нажатыми
    /// ПРЯМО СЕЙЧАС. Нужна одному — сторожу родителя: движок умер, тело уходит следом
    /// через `process::exit`, а `exit` стек НЕ разматывает, и `Drop` у `HeldButtons`
    /// с `Poster` не сработает. Уйти с зажатой левой кнопкой или ⌘ — оставить стол
    /// сломанным: следующее движение мыши станет выделением, следующий щелчок — ⌘-щелчком.
    static HELD_NOW: Mutex<(Vec<MouseButton>, Vec<u16>)> = Mutex::new((Vec::new(), Vec::new()));

    fn remember_held(buttons: &[MouseButton], modifiers: &[u16]) {
        if let Ok(mut held) = HELD_NOW.lock() {
            held.0.clear();
            held.0.extend_from_slice(buttons);
            held.1.clear();
            held.1.extend_from_slice(modifiers);
        }
    }

    /// Поставить сторожу отпускание — один раз на процесс, при первой пачке ввода.
    /// Раньше не за чем: до первого ввода отпускать нечего, а `CGEventSource` под
    /// службой без графической сессии не создастся вовсе.
    fn arm_release_on_parent_death() {
        static ARMED: OnceLock<()> = OnceLock::new();
        ARMED.get_or_init(|| {
            mac::on_parent_death(Box::new(release_everything_held));
        });
    }

    /// Отпустить всё, что записано на доске. Свой источник событий: чужой живёт на
    /// потоке вызова и сюда не переезжает.
    fn release_everything_held() {
        let Ok(held) = HELD_NOW.lock() else { return };
        let (buttons, modifiers) = (held.0.clone(), held.1.clone());
        drop(held);
        if buttons.is_empty() && modifiers.is_empty() {
            return;
        }
        let Ok(source) = hid_source() else { return };
        let (x, y) = cursor_location(&source).unwrap_or((0.0, 0.0));
        for button in buttons.into_iter().rev() {
            let (kind, cg_button) = match button {
                MouseButton::Left => (CGEventType::LeftMouseUp, CGMouseButton::Left),
                MouseButton::Right => (CGEventType::RightMouseUp, CGMouseButton::Right),
                MouseButton::Middle => (CGEventType::OtherMouseUp, CGMouseButton::Center),
            };
            if let Ok(event) =
                CGEvent::new_mouse_event(source.clone(), kind, CGPoint::new(x, y), cg_button)
            {
                event.post(CGEventTapLocation::HID);
            }
        }
        for code in modifiers.into_iter().rev() {
            if let Ok(event) = CGEvent::new_keyboard_event(source.clone(), code, false) {
                event.post(CGEventTapLocation::HID);
            }
        }
    }

    /// Ведомость зажатых кнопок (см. Windows-ветку `HeldButtons`): что этот вызов
    /// оставил зажатым, отпускается на выходе и называется в ответе; `Drop` — последний
    /// рубеж. Отпускание идёт по живому положению курсора.
    struct HeldButtons {
        held: Vec<MouseButton>,
        source: CGEventSource,
    }

    impl HeldButtons {
        fn new(source: CGEventSource) -> Self {
            Self {
                held: Vec::new(),
                source,
            }
        }

        fn press(&mut self, button: MouseButton) {
            if !self.held.contains(&button) {
                self.held.push(button);
            }
        }

        fn release(&mut self, button: MouseButton) {
            self.held.retain(|value| *value != button);
        }

        fn release_all(&mut self) -> Vec<&'static str> {
            if self.held.is_empty() {
                return Vec::new();
            }
            let names: Vec<&'static str> = self.held.iter().map(|button| button.name()).collect();
            let (x, y) = cursor_location(&self.source).unwrap_or((0.0, 0.0));
            for button in std::mem::take(&mut self.held).into_iter().rev() {
                let (kind, cg_button) = match button {
                    MouseButton::Left => (CGEventType::LeftMouseUp, CGMouseButton::Left),
                    MouseButton::Right => (CGEventType::RightMouseUp, CGMouseButton::Right),
                    MouseButton::Middle => (CGEventType::OtherMouseUp, CGMouseButton::Center),
                };
                // Без права на отказ: если событие не создалось, сказать об этом можно
                // только текстом ответа — но не удержанием кнопки.
                if let Ok(event) = CGEvent::new_mouse_event(
                    self.source.clone(),
                    kind,
                    CGPoint::new(x, y),
                    cg_button,
                ) {
                    event.post(CGEventTapLocation::HID);
                }
            }
            names
        }
    }

    impl Drop for HeldButtons {
        fn drop(&mut self) {
            let _ = self.release_all();
        }
    }

    fn ensure_foreground(
        expected: Option<&HwndArg>,
        expected_pid: Option<u32>,
    ) -> Result<Option<WindowInfo>> {
        let actual = mac::frontmost()?;
        if let Some(expected) = expected {
            let id = expected.value()?;
            match &actual {
                Some(window) if window.id == id => {}
                Some(window) => bail!(
                    "foreground changed: expected {}, actual {}",
                    hwnd_hex(id),
                    hwnd_hex(window.id)
                ),
                None => bail!(
                    "foreground changed: expected {}, but no ordinary window is on screen",
                    hwnd_hex(id)
                ),
            }
        }
        if let Some(expected_pid) = expected_pid {
            match &actual {
                Some(window) if window.pid as u32 == expected_pid => {}
                Some(window) => bail!(
                    "foreground pid changed: expected {expected_pid}, actual {}",
                    window.pid
                ),
                None => bail!(
                    "foreground pid changed: expected {expected_pid}, but no ordinary window is \
                     on screen"
                ),
            }
        }
        // Без ожиданий пустой стол — не отказ: строка меню и Spotlight принимают ввод
        // и без единого окна.
        Ok(actual)
    }

    fn input_perform(args: InputArgs) -> Result<Value> {
        if args.events.is_empty() {
            bail!("events must not be empty")
        }
        if args.events.len() > MAX_INPUT_EVENTS {
            bail!(
                "too many input events: {} (maximum {MAX_INPUT_EVENTS})",
                args.events.len()
            )
        }
        let screen = screen_points();
        // Вся пачка раскладывается и проверяется ДО первой отправки — и до вопроса о
        // разрешении: предел бьёт по форме просьбы, разрешение — по столу.
        let chunks = prepare_input_events(&args.events, args.inter_event_delay_ms, screen)?;
        let totals = plan_totals(&chunks)?;
        if totals.pause_ms > MAX_TOTAL_INPUT_DELAY_MS {
            let typed = args
                .events
                .iter()
                .any(|event| matches!(event, super::mac_pure::InputEvent::Text { .. }));
            bail!(
                "total input delay is {}ms (maximum {MAX_TOTAL_INPUT_DELAY_MS}ms){}",
                totals.pause_ms,
                if typed {
                    format!(
                        "; text is paced at {TEXT_UNIT_PAUSE_MS}ms per character on macOS, so \
                         keep one call under {} characters",
                        MAX_TOTAL_INPUT_DELAY_MS / TEXT_UNIT_PAUSE_MS
                    )
                } else {
                    String::new()
                }
            )
        }
        let tcc = mac::tcc();
        if !tcc.accessibility {
            return Err(refused_by_tcc(
                "input refused: no Accessibility permission — macOS would drop the posted \
                 events silently, so nothing was sent",
                tcc,
            ));
        }
        let before = ensure_foreground(args.expected_foreground.as_ref(), args.expected_pid)?;
        arm_release_on_parent_death();
        let mut poster = Poster::new(screen)?;
        let mut held = HeldButtons::new(poster.source.clone());
        let mut paused_ms = 0u64;
        // Сторож фокуса стоит перед КАЖДОЙ пачкой (каждый знак текста — пачка), но
        // только когда есть что сторожить: без ожиданий он лишь читал бы список окон
        // WindowServer полторы тысячи раз подряд — это миллисекунды на знак поверх
        // 20 мс паузы, и на длинном тексте они съедали бы срок ожидания body_client.
        let guarded = args.expected_foreground.is_some() || args.expected_pid.is_some();
        let outcome = (|| -> Result<()> {
            for (index, chunk) in chunks.iter().enumerate() {
                if guarded {
                    ensure_foreground(args.expected_foreground.as_ref(), args.expected_pid)?;
                }
                if let Some(button) = chunk.press {
                    held.press(button);
                }
                post_chunk(&mut poster, chunk)?;
                if let Some(button) = chunk.release {
                    held.release(button);
                }
                if chunk.pause_ms > 0 && index + 1 < chunks.len() {
                    thread::sleep(Duration::from_millis(chunk.pause_ms));
                    paused_ms = paused_ms.saturating_add(chunk.pause_ms);
                }
            }
            Ok(())
        })();
        // Порядок: сначала модификаторы (последним событием пачки), потом кнопки —
        // отпустить ⌘ уже после того, как щелчок с ним ушёл.
        let modifiers_released = poster.release_modifiers();
        let auto_released = held.release_all();
        remember_held(&[], &[]);
        if let Err(error) = outcome {
            if auto_released.is_empty() && modifiers_released.is_empty() {
                return Err(error);
            }
            return Err(anyhow!(
                "{error:#}; released before returning: buttons [{}], modifiers [{}]",
                auto_released.join(", "),
                modifiers_released.join(", ")
            ));
        }
        let after = mac::frontmost()?;
        let mut notes: Vec<String> = Vec::new();
        if !modifiers_released.is_empty() {
            notes.push(format!(
                "modifiers do not survive the call: [{}] stayed down at the end of this batch and                  were released after its last event. Hold and use a modifier in ONE batch — a                  hotkey event, or key down -> key press -> key up together",
                modifiers_released.join(", ")
            ));
        }
        Ok(json!({
            "ok": true,
            "events": args.events.len(),
            "input_batches": totals.batches,
            "input_records": totals.records,
            "planned_pause_ms": totals.pause_ms,
            "paused_ms": paused_ms,
            "clamped_moves": totals.clamped_moves + poster.clamped_moves,
            "buttons_auto_released": auto_released,
            "buttons_held_at_exit": Vec::<&str>::new(),
            "modifiers_auto_released": modifiers_released,
            "modifiers_held_at_exit": Vec::<&str>::new(),
            "notes": notes,
            "foreground_before": foreground_value(before.as_ref()),
            "foreground_after": foreground_value(after.as_ref()),
            "virtual_screen": virtual_screen(),
            "limits": input_limits(),
            "platform": "macos",
            "tcc": tcc,
        }))
    }

    fn post_chunk(poster: &mut Poster, chunk: &PreparedChunk) -> Result<()> {
        for record in &chunk.records {
            poster.post(record)?;
        }
        Ok(())
    }

    // ─── desktop.screen.capture ─────────────────────────────────────────────────────

    #[derive(Debug, Clone, Copy)]
    struct CaptureRegion {
        left: i32,
        top: i32,
        width: i32,
        height: i32,
    }

    fn cg_rect(region: CaptureRegion) -> CGRect {
        CGRect::new(
            &CGPoint::new(f64::from(region.left), f64::from(region.top)),
            &CGSize::new(f64::from(region.width), f64::from(region.height)),
        )
    }

    /// `CGRectNull`: «границы окна» для CGWindowListCreateImage.
    fn cg_rect_null() -> CGRect {
        CGRect::new(
            &CGPoint::new(f64::INFINITY, f64::INFINITY),
            &CGSize::new(0.0, 0.0),
        )
    }

    fn screen_capture(args: CaptureArgs, state_dir: &Path) -> Result<Value> {
        let tcc = mac::tcc();
        if !tcc.screen_recording {
            // Без «Записи экрана» система не ошибается, а отдаёт обои без чужих окон.
            return Err(refused_by_tcc(
                "screen capture refused: no Screen Recording permission — macOS would return \
                 the wallpaper without other apps' windows instead of the desktop",
                tcc,
            ));
        }
        let target = args.target.to_ascii_lowercase();
        let (region, target_hwnd, composition) = match target.as_str() {
            "desktop" => {
                let screen = screen_points();
                (
                    CaptureRegion {
                        left: screen.left,
                        top: screen.top,
                        width: screen.width,
                        height: screen.height,
                    },
                    None,
                    "everything on screen inside the rectangle",
                )
            }
            "region" => (
                CaptureRegion {
                    left: args.x.context("region requires x")?,
                    top: args.y.context("region requires y")?,
                    width: args.width.context("region requires width")?,
                    height: args.height.context("region requires height")?,
                },
                None,
                "everything on screen inside the rectangle",
            ),
            "window" => {
                let id = args.hwnd.context("window capture requires hwnd")?.value()?;
                let window = mac::window_by_id(id)?
                    .with_context(|| format!("window {} no longer exists", hwnd_hex(id)))?;
                (
                    CaptureRegion {
                        left: round(window.x) as i32,
                        top: round(window.y) as i32,
                        width: round(window.width) as i32,
                        height: round(window.height) as i32,
                    },
                    Some(id),
                    "this window only (kCGWindowListOptionIncludingWindow): windows above it \
                     are not in the image, transparent parts come out black",
                )
            }
            _ => bail!("capture target must be desktop, region, or window"),
        };
        if region.width <= 0 || region.height <= 0 {
            bail!("capture rectangle must have positive width and height")
        }
        let image = match target_hwnd {
            Some(id) => CGDisplay::screenshot(
                cg_rect_null(),
                kCGWindowListOptionIncludingWindow,
                id,
                kCGWindowImageBestResolution | kCGWindowImageBoundsIgnoreFraming,
            ),
            None => CGDisplay::screenshot(
                cg_rect(region),
                kCGWindowListOptionOnScreenOnly,
                kCGNullWindowID,
                kCGWindowImageBestResolution,
            ),
        }
        .context(
            "CGWindowListCreateImage returned no image (the rectangle may lie outside every \
             display, or the window has nothing to show)",
        )?;
        let (pixel_width, pixel_height) = (image.width(), image.height());
        if pixel_width == 0 || pixel_height == 0 {
            bail!("capture image is empty ({pixel_width}x{pixel_height} pixels)")
        }
        capture_allocation(
            i32::try_from(pixel_width).context("capture width exceeds i32")?,
            i32::try_from(pixel_height).context("capture height exceeds i32")?,
        )?;
        let layout = pixel_layout(
            unsafe { CGImageGetBitmapInfo(image.as_ptr()) },
            image.bits_per_pixel(),
            image.bits_per_component(),
        )?;
        let data = image.data();
        let bgra = to_bgra(data.bytes(), pixel_width, pixel_height, image.bytes_per_row(), layout)?;
        let plan = plan_capture(
            pixel_width,
            pixel_height,
            region.width as usize,
            region.height as usize,
            args.native,
        );
        let (bgra, saved_width, saved_height, downscale) = match plan.downscale {
            Downscale::None => (bgra, pixel_width, pixel_height, "none"),
            Downscale::Box(factor) => {
                let (scaled, width, height) = downscale_box(&bgra, pixel_width, pixel_height, factor)
                    .context("box downscale refused a non-divisible image")?;
                (scaled, width, height, "box-average")
            }
            Downscale::Nearest => (
                resample_nearest(&bgra, pixel_width, pixel_height, plan.width, plan.height),
                plan.width,
                plan.height,
                "nearest",
            ),
        };
        let directory = state_dir.join("desktop").join("captures");
        fs::create_dir_all(&directory)?;
        let path = directory.join(capture_name(&args.name));
        write_png(
            &path,
            i32::try_from(saved_width)?,
            i32::try_from(saved_height)?,
            &bgra,
        )?;
        let size = fs::metadata(&path)?.len();
        // Угол — из рамки, размер — из образа: рамка говорит, ГДЕ снято, а сколько
        // стола попало в кадр, знает только сам образ.
        let mut notes: Vec<String> = Vec::new();
        if let Some((frame_width, frame_height)) = plan.frame_mismatch {
            notes.push(format!(
                "the window frame from CGWindowList ({frame_width}x{frame_height} pt) and the                  image the system returned ({}x{} pt at scale {}) disagree by more than a point;                  width/height here describe the IMAGE, left/top come from the frame",
                plan.width, plan.height, plan.scale
            ));
        }
        Ok(json!({
            "ok": true,
            "path": path,
            "format": "png",
            "mime": "image/png",
            "size": size,
            "left": region.left,
            "top": region.top,
            "width": plan.width,
            "height": plan.height,
            "frame_width": region.width,
            "frame_height": region.height,
            "pixel_width": saved_width,
            "pixel_height": saved_height,
            "source_pixel_width": pixel_width,
            "source_pixel_height": pixel_height,
            "scale": plan.scale,
            "native": args.native,
            "downscale": downscale,
            "notes": notes,
            "target": target,
            "target_hwnd": target_hwnd.map(hwnd_hex),
            "composition": composition,
            // Окно снимается из его собственного буфера — с перекрытыми частями; это не
            // «то, что видно на столе», и поле обязано это сказать.
            "visible_desktop_capture": target_hwnd.is_none(),
            "platform": "macos",
        }))
    }

    // ─── буфер обмена ───────────────────────────────────────────────────────────────

    /// `pbpaste`/`pbcopy` — без AppKit. Кодировка у них от локали, а под launchd локали
    /// нет вовсе, поэтому UTF-8 задаётся явно детям, не трогая чужое окружение.
    fn pasteboard(tool: &str) -> Command {
        let mut command = Command::new(tool);
        command
            .env("LANG", "en_US.UTF-8")
            .env("LC_CTYPE", "en_US.UTF-8")
            .env("LC_ALL", "en_US.UTF-8");
        command
    }

    fn clipboard_read(args: ClipboardReadArgs) -> Result<Value> {
        let output = pasteboard("pbpaste")
            .args(["-Prefer", "txt"])
            .stdin(Stdio::null())
            .output()
            .context("run pbpaste")?;
        if !output.status.success() {
            bail!(
                "pbpaste exited with {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            )
        }
        let text = String::from_utf8_lossy(&output.stdout);
        let units: Vec<u16> = text.encode_utf16().collect();
        let length = units.len();
        let limit = args.limit_chars.clamp(1, MAX_CLIPBOARD_CHARS).min(length);
        let mut result = json!({
            "ok": true,
            "available": true,
            "format": "unicode_text",
            "text": String::from_utf16_lossy(&units[..limit]),
            "chars": length,
            "truncated": limit < length,
            "platform": "macos",
        });
        if length == 0 {
            // pbpaste молчит одинаково и на пустом тексте, и когда текста в буфере нет
            // (там картинка, файл). Различить без AppKit нельзя — сказано вслух.
            result["note"] = Value::String(
                "pbpaste returned nothing: the pasteboard holds either an empty text or no \
                 text at all (an image or a file), which cannot be told apart here"
                    .into(),
            );
        }
        Ok(result)
    }

    fn clipboard_write(args: ClipboardWriteArgs) -> Result<Value> {
        let units = args.text.encode_utf16().count();
        if units > MAX_CLIPBOARD_CHARS {
            bail!("clipboard text has {units} UTF-16 units (maximum {MAX_CLIPBOARD_CHARS})")
        }
        let mut child = pasteboard("pbcopy")
            .stdin(Stdio::piped())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .spawn()
            .context("run pbcopy")?;
        child
            .stdin
            .take()
            .context("pbcopy has no stdin")?
            .write_all(args.text.as_bytes())
            .context("feed pbcopy")?;
        let output = child.wait_with_output().context("wait for pbcopy")?;
        if !output.status.success() {
            bail!(
                "pbcopy exited with {}: {}",
                output.status,
                String::from_utf8_lossy(&output.stderr).trim()
            )
        }
        Ok(json!({
            "ok": true,
            "format": "unicode_text",
            "chars": units,
            "platform": "macos",
        }))
    }
}

#[cfg(windows)]
mod platform {
    use std::ffi::c_void;
    use std::fs;
    use std::mem::size_of;
    use std::path::Path;
    use std::ptr;
    use std::thread;
    use std::time::Duration;

    use anyhow::{Context, Result, anyhow, bail};
    use serde::Deserialize;
    use serde_json::{Value, json};
    use windows::Win32::Foundation::{
        CloseHandle, GlobalFree, HANDLE, HGLOBAL, HWND, LPARAM, POINT, RECT,
    };
    use windows::Win32::Graphics::Gdi::{
        BI_RGB, BITMAPINFO, BitBlt, CAPTUREBLT, CreateCompatibleBitmap, CreateCompatibleDC,
        DIB_RGB_COLORS, DeleteDC, DeleteObject, GetDC, GetDIBits, HBITMAP, HGDIOBJ, ReleaseDC,
        SRCCOPY, SelectObject,
    };
    use windows::Win32::System::DataExchange::{
        CloseClipboard, EmptyClipboard, GetClipboardData, GetClipboardSequenceNumber,
        IsClipboardFormatAvailable, OpenClipboard, SetClipboardData,
    };
    use windows::Win32::System::Diagnostics::ToolHelp::{
        CreateToolhelp32Snapshot, PROCESSENTRY32W, Process32FirstW, Process32NextW,
        TH32CS_SNAPPROCESS,
    };
    use windows::Win32::System::Memory::{
        GMEM_MOVEABLE, GlobalAlloc, GlobalLock, GlobalSize, GlobalUnlock,
    };
    use windows::Win32::System::RemoteDesktop::ProcessIdToSessionId;
    use windows::Win32::System::Threading::{
        AttachThreadInput, GetCurrentProcessId, GetCurrentThreadId, GetProcessTimes, OpenProcess,
        PROCESS_QUERY_LIMITED_INFORMATION, QueryFullProcessImageNameW,
    };
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        INPUT, INPUT_0, INPUT_KEYBOARD, INPUT_MOUSE, KEYBD_EVENT_FLAGS, KEYBDINPUT,
        KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, MOUSE_EVENT_FLAGS, MOUSEEVENTF_ABSOLUTE,
        MOUSEEVENTF_HWHEEL, MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP, MOUSEEVENTF_MIDDLEDOWN,
        MOUSEEVENTF_MIDDLEUP, MOUSEEVENTF_MOVE, MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP,
        MOUSEEVENTF_VIRTUALDESK, MOUSEEVENTF_WHEEL, MOUSEINPUT, SendInput, VIRTUAL_KEY,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        BringWindowToTop, EnumWindows, GetClassNameW, GetCursorPos, GetForegroundWindow,
        GetSystemMetrics, GetWindowRect, GetWindowTextLengthW, GetWindowTextW,
        GetWindowThreadProcessId, IsIconic, IsWindow, IsWindowVisible, SM_CXVIRTUALSCREEN,
        SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SW_RESTORE, SetForegroundWindow,
        ShowWindowAsync,
    };
    use windows::core::{BOOL, PWSTR};

    use super::{capture_allocation, capture_name, write_png};

    const CF_UNICODETEXT: u32 = 13;
    const DEFAULT_PAGE: usize = 2_048;
    const MAX_PAGE: usize = 20_000;
    const DEFAULT_CLIPBOARD_CHARS: usize = 1_000_000;
    const MAX_CLIPBOARD_CHARS: usize = 1_000_000;
    const MAX_INPUT_EVENTS: usize = 512;
    const MAX_INPUT_TEXT_UTF16_UNITS: usize = 16_384;
    const MAX_HOTKEY_KEYS: usize = 32;
    const MAX_CLICK_COUNT: u32 = 64;
    const MAX_INPUT_RECORDS: usize = 65_536;
    // body_client waits 60 seconds; leave room for the native call and reply.
    const MAX_TOTAL_INPUT_DELAY_MS: u64 = 30_000;
    // Перетаскивание. Проводник, ползунки и выделение текста не считают за drag
    // «телепорт» курсора: им нужны промежуточные MOUSEMOVE и живая пауза после
    // нажатия. Поэтому один шаг drag разворачивается в несколько отправок с
    // паузами — и у каждой паузы есть свой потолок, а их сумма едет в тот же
    // общий бюджет MAX_TOTAL_INPUT_DELAY_MS, а не мимо него.
    const MAX_DRAG_STEPS: u32 = 256;
    const DEFAULT_DRAG_STEPS: u32 = 24;
    const MAX_DRAG_PAUSE_MS: u64 = 5_000;
    const DEFAULT_DRAG_HOLD_MS: u64 = 60;
    const DEFAULT_DRAG_STEP_DELAY_MS: u64 = 8;
    const DEFAULT_DRAG_SETTLE_MS: u64 = 60;

    #[derive(Debug, Clone, Deserialize)]
    #[serde(untagged)]
    enum HwndArg {
        Number(u64),
        Text(String),
    }

    impl HwndArg {
        fn value(&self) -> Result<HWND> {
            let value = match self {
                Self::Number(value) => *value,
                Self::Text(value) => {
                    let value = value.trim();
                    if let Some(hex) = value
                        .strip_prefix("0x")
                        .or_else(|| value.strip_prefix("0X"))
                    {
                        u64::from_str_radix(hex, 16)
                            .context("hwnd must be a positive integer or hexadecimal string")?
                    } else {
                        value.parse::<u64>().context(
                            "hwnd must be a positive integer or 0x-prefixed hexadecimal string",
                        )?
                    }
                }
            };
            if value == 0 || value > usize::MAX as u64 {
                bail!("invalid hwnd")
            }
            Ok(HWND(value as usize as *mut c_void))
        }
    }

    #[derive(Debug, Default, Deserialize)]
    struct PageArgs {
        #[serde(default)]
        offset: usize,
        #[serde(default = "default_page")]
        limit: usize,
    }

    fn default_page() -> usize {
        DEFAULT_PAGE
    }

    #[derive(Debug, Default, Deserialize)]
    struct ProcessListArgs {
        #[serde(flatten)]
        page: PageArgs,
        #[serde(default)]
        name_contains: String,
        #[serde(default)]
        session_id: Option<u32>,
    }

    #[derive(Debug, Deserialize)]
    struct WindowListArgs {
        #[serde(flatten)]
        page: PageArgs,
        #[serde(default = "yes")]
        visible_only: bool,
        #[serde(default)]
        pid: Option<u32>,
        #[serde(default)]
        title_contains: String,
    }

    impl Default for WindowListArgs {
        fn default() -> Self {
            Self {
                page: PageArgs::default(),
                visible_only: true,
                pid: None,
                title_contains: String::new(),
            }
        }
    }

    fn yes() -> bool {
        true
    }

    #[derive(Debug, Deserialize)]
    struct ActivateArgs {
        hwnd: HwndArg,
        #[serde(default)]
        expected_pid: Option<u32>,
        #[serde(default = "yes")]
        restore: bool,
        #[serde(default = "activate_timeout")]
        timeout_ms: u64,
    }

    fn activate_timeout() -> u64 {
        1_500
    }

    #[derive(Debug, Deserialize)]
    struct InputArgs {
        #[serde(default)]
        expected_foreground: Option<HwndArg>,
        #[serde(default)]
        expected_pid: Option<u32>,
        events: Vec<InputEvent>,
        #[serde(default)]
        inter_event_delay_ms: u64,
    }

    #[derive(Debug, Deserialize)]
    #[serde(tag = "type", rename_all = "snake_case")]
    pub(super) enum InputEvent {
        Text {
            text: String,
        },
        Hotkey {
            keys: Vec<KeyArg>,
        },
        Key {
            key: KeyArg,
            #[serde(default = "press_action")]
            action: String,
        },
        Mouse {
            x: i32,
            y: i32,
            #[serde(default)]
            relative: bool,
        },
        Click {
            #[serde(default = "left_button")]
            button: String,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
            #[serde(default = "one")]
            count: u32,
        },
        Wheel {
            delta: i32,
            #[serde(default)]
            horizontal: bool,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
        },
        /// Зажать кнопку и оставить зажатой до следующих шагов ЭТОЙ ЖЕ пачки.
        /// Из вызова зажатие не выходит: см. `HeldButtons`.
        MouseDown {
            #[serde(default = "left_button")]
            button: String,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
        },
        /// Отпустить кнопку. Отпускание кнопки, которая не была зажата, — не ошибка:
        /// это её ручной способ снять залипание, оставшееся от оборванного вызова.
        MouseUp {
            #[serde(default = "left_button")]
            button: String,
            #[serde(default)]
            x: Option<i32>,
            #[serde(default)]
            y: Option<i32>,
        },
        /// «Нажал → провёл → отпустил» одним шагом: файл в проводнике, ползунок,
        /// граница окна, выделение текста по диагонали.
        ///
        /// Начало (`x`,`y`) обязательно и явно: курсор мог быть сдвинут предыдущим
        /// шагом этой же пачки, и «тащить от того места, где он сейчас» означало бы
        /// считать интерполяцию от точки, которой она не видела.
        Drag {
            #[serde(default = "left_button")]
            button: String,
            x: i32,
            y: i32,
            to_x: i32,
            to_y: i32,
            #[serde(default = "default_drag_steps")]
            steps: u32,
            #[serde(default = "default_drag_hold_ms")]
            hold_ms: u64,
            #[serde(default = "default_drag_step_delay_ms")]
            step_delay_ms: u64,
            #[serde(default = "default_drag_settle_ms")]
            settle_ms: u64,
        },
    }

    #[derive(Debug, Clone, Deserialize)]
    #[serde(untagged)]
    pub(super) enum KeyArg {
        Number(u16),
        Text(String),
    }

    /// Кнопка мыши как состояние, а не как неразрывная пара флагов.
    /// Пока нажатие и отпускание жили только внутри `click_inputs`, зажатия
    /// не существовало — а значит, не существовало и перетаскивания.
    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub(super) enum MouseButton {
        Left,
        Right,
        Middle,
    }

    impl MouseButton {
        fn parse(raw: &str) -> Result<Self> {
            match raw.trim().to_ascii_lowercase().as_str() {
                "left" => Ok(Self::Left),
                "right" => Ok(Self::Right),
                "middle" => Ok(Self::Middle),
                _ => bail!("button must be left, right, or middle"),
            }
        }

        fn name(self) -> &'static str {
            match self {
                Self::Left => "left",
                Self::Right => "right",
                Self::Middle => "middle",
            }
        }

        fn down(self) -> MOUSE_EVENT_FLAGS {
            match self {
                Self::Left => MOUSEEVENTF_LEFTDOWN,
                Self::Right => MOUSEEVENTF_RIGHTDOWN,
                Self::Middle => MOUSEEVENTF_MIDDLEDOWN,
            }
        }

        fn up(self) -> MOUSE_EVENT_FLAGS {
            match self {
                Self::Left => MOUSEEVENTF_LEFTUP,
                Self::Right => MOUSEEVENTF_RIGHTUP,
                Self::Middle => MOUSEEVENTF_MIDDLEUP,
            }
        }
    }

    fn press_action() -> String {
        "press".into()
    }

    fn left_button() -> String {
        "left".into()
    }

    fn one() -> u32 {
        1
    }

    fn default_drag_steps() -> u32 {
        DEFAULT_DRAG_STEPS
    }

    fn default_drag_hold_ms() -> u64 {
        DEFAULT_DRAG_HOLD_MS
    }

    fn default_drag_step_delay_ms() -> u64 {
        DEFAULT_DRAG_STEP_DELAY_MS
    }

    fn default_drag_settle_ms() -> u64 {
        DEFAULT_DRAG_SETTLE_MS
    }

    #[derive(Debug, Deserialize)]
    struct CaptureArgs {
        #[serde(default = "desktop_target")]
        target: String,
        #[serde(default)]
        hwnd: Option<HwndArg>,
        #[serde(default)]
        x: Option<i32>,
        #[serde(default)]
        y: Option<i32>,
        #[serde(default)]
        width: Option<i32>,
        #[serde(default)]
        height: Option<i32>,
        #[serde(default)]
        name: String,
    }

    #[derive(Debug, Clone, Copy)]
    struct CaptureRegion {
        left: i32,
        top: i32,
        width: i32,
        height: i32,
    }

    fn desktop_target() -> String {
        "desktop".into()
    }

    #[derive(Debug, Deserialize)]
    struct ClipboardReadArgs {
        #[serde(default = "default_clipboard_chars")]
        limit_chars: usize,
    }

    fn default_clipboard_chars() -> usize {
        DEFAULT_CLIPBOARD_CHARS
    }

    #[derive(Debug, Deserialize)]
    struct ClipboardWriteArgs {
        text: String,
    }

    #[derive(Debug)]
    struct ProcessDetails {
        path: Option<String>,
        created_filetime: Option<u64>,
        session_id: Option<u32>,
    }

    struct HandleGuard(HANDLE);

    impl Drop for HandleGuard {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseHandle(self.0);
            }
        }
    }

    struct ClipboardGuard;

    impl Drop for ClipboardGuard {
        fn drop(&mut self) {
            unsafe {
                let _ = CloseClipboard();
            }
        }
    }

    /// Temporarily join the input queues involved in foreground arbitration.
    ///
    /// A tray/background process is normally forbidden from stealing foreground even when the
    /// owner explicitly asked it to activate a window.  Joining the existing foreground and
    /// target queues is the documented Win32 handshake; it avoids a blind synthetic Alt press
    /// and is always undone before returning.
    struct ThreadInputAttachments {
        current: u32,
        attached: Vec<u32>,
    }

    impl ThreadInputAttachments {
        fn new() -> Self {
            Self {
                current: unsafe { GetCurrentThreadId() },
                attached: Vec::new(),
            }
        }

        fn attach(&mut self, other: u32) -> bool {
            if other == 0 || other == self.current || self.attached.contains(&other) {
                return other == self.current || self.attached.contains(&other);
            }
            if unsafe { AttachThreadInput(self.current, other, true) }.as_bool() {
                self.attached.push(other);
                true
            } else {
                false
            }
        }
    }

    impl Drop for ThreadInputAttachments {
        fn drop(&mut self) {
            for other in self.attached.iter().rev() {
                unsafe {
                    let _ = AttachThreadInput(self.current, *other, false);
                }
            }
        }
    }

    struct WindowCollector {
        foreground: HWND,
        visible_only: bool,
        pid: Option<u32>,
        title_contains: String,
        rows: Vec<Value>,
    }

    pub fn dispatch(capability: &str, args: Value, state_dir: &Path) -> Result<Value> {
        match capability {
            "desktop.status" => desktop_status(),
            "os.process.list" => process_list(serde_json::from_value(args)?),
            "desktop.window.list" => window_list(serde_json::from_value(args)?),
            "desktop.window.activate" => window_activate(serde_json::from_value(args)?),
            "desktop.input.perform" => input_perform(serde_json::from_value(args)?),
            "desktop.screen.capture" => screen_capture(serde_json::from_value(args)?, state_dir),
            "desktop.clipboard.read" => clipboard_read(serde_json::from_value(args)?),
            "desktop.clipboard.write" => clipboard_write(serde_json::from_value(args)?),
            _ => bail!("unknown native desktop capability {capability}"),
        }
    }

    fn desktop_status() -> Result<Value> {
        let foreground = unsafe { GetForegroundWindow() };
        let mut cursor = POINT::default();
        let cursor = unsafe { GetCursorPos(&mut cursor) }
            .ok()
            .map(|_| json!({"x": cursor.x, "y": cursor.y}));
        let session_id = process_session(unsafe { GetCurrentProcessId() });
        Ok(json!({
            "ok": true,
            "interactive": session_id.is_some_and(|value| value != 0),
            "session_id": session_id,
            "foreground": window_row(foreground, 0),
            "cursor": cursor,
            "virtual_screen": virtual_screen(),
            "clipboard_sequence": unsafe { GetClipboardSequenceNumber() },
        }))
    }

    fn process_list(args: ProcessListArgs) -> Result<Value> {
        let snapshot = unsafe { CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)? };
        let _snapshot = HandleGuard(snapshot);
        let mut entry = PROCESSENTRY32W {
            dwSize: size_of::<PROCESSENTRY32W>() as u32,
            ..Default::default()
        };
        let mut rows = Vec::new();
        let mut more = unsafe { Process32FirstW(snapshot, &mut entry) }.is_ok();
        let needle = args.name_contains.to_lowercase();
        while more {
            let name = utf16_z(&entry.szExeFile);
            let details = process_details(entry.th32ProcessID);
            let matches_name = needle.is_empty()
                || name.to_lowercase().contains(&needle)
                || details
                    .path
                    .as_deref()
                    .is_some_and(|value| value.to_lowercase().contains(&needle));
            let matches_session = args
                .session_id
                .is_none_or(|value| details.session_id == Some(value));
            if matches_name && matches_session {
                rows.push(json!({
                    "pid": entry.th32ProcessID,
                    "parent_pid": entry.th32ParentProcessID,
                    "threads": entry.cntThreads,
                    "name": name,
                    "path": details.path,
                    "session_id": details.session_id,
                    "created_filetime": details.created_filetime,
                }));
            }
            more = unsafe { Process32NextW(snapshot, &mut entry) }.is_ok();
        }
        rows.sort_by_key(|row| row["pid"].as_u64().unwrap_or_default());
        Ok(page(rows, args.page))
    }

    fn window_list(args: WindowListArgs) -> Result<Value> {
        let mut collector = WindowCollector {
            foreground: unsafe { GetForegroundWindow() },
            visible_only: args.visible_only,
            pid: args.pid,
            title_contains: args.title_contains.to_lowercase(),
            rows: Vec::new(),
        };
        unsafe {
            EnumWindows(
                Some(enum_window),
                LPARAM(&mut collector as *mut WindowCollector as isize),
            )?;
        }
        let mut result = page(collector.rows, args.page);
        result["foreground_hwnd"] = Value::String(hwnd_hex(collector.foreground));
        Ok(result)
    }

    unsafe extern "system" fn enum_window(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let collector = unsafe { &mut *(lparam.0 as *mut WindowCollector) };
        let visible = unsafe { IsWindowVisible(hwnd) }.as_bool();
        if collector.visible_only && !visible {
            return BOOL(1);
        }
        let mut pid = 0u32;
        unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
        if collector.pid.is_some_and(|expected| expected != pid) {
            return BOOL(1);
        }
        let title = window_text(hwnd);
        if !collector.title_contains.is_empty()
            && !title.to_lowercase().contains(&collector.title_contains)
        {
            return BOOL(1);
        }
        let z_order = collector.rows.len();
        if let Some(mut row) = window_row(hwnd, z_order) {
            row["visible"] = Value::Bool(visible);
            row["foreground"] = Value::Bool(hwnd == collector.foreground);
            collector.rows.push(row);
        }
        BOOL(1)
    }

    fn window_activate(args: ActivateArgs) -> Result<Value> {
        let hwnd = args.hwnd.value()?;
        require_window(hwnd, args.expected_pid)?;
        if args.timeout_ms > 15_000 {
            bail!("activation timeout must not exceed 15000ms")
        }
        let foreground_before = unsafe { GetForegroundWindow() };
        let foreground_thread = if foreground_before.0.is_null() {
            0
        } else {
            unsafe { GetWindowThreadProcessId(foreground_before, None) }
        };
        let target_thread = unsafe { GetWindowThreadProcessId(hwnd, None) };
        let mut attachments = ThreadInputAttachments::new();
        let attached_foreground = attachments.attach(foreground_thread);
        let attached_target = attachments.attach(target_thread);
        if args.restore && unsafe { IsIconic(hwnd) }.as_bool() {
            unsafe {
                let _ = ShowWindowAsync(hwnd, SW_RESTORE);
            }
        }
        let mut raised = unsafe { BringWindowToTop(hwnd) }.is_ok();
        let mut requested = unsafe { SetForegroundWindow(hwnd) }.as_bool();
        let mut attempts = 1u32;
        let timeout = args.timeout_ms;
        let mut waited = 0u64;
        while unsafe { GetForegroundWindow() } != hwnd && waited < timeout {
            thread::sleep(Duration::from_millis(25));
            waited += 25;
            if waited.is_multiple_of(250) {
                raised |= unsafe { BringWindowToTop(hwnd) }.is_ok();
                requested |= unsafe { SetForegroundWindow(hwnd) }.as_bool();
                attempts += 1;
            }
        }
        let actual = unsafe { GetForegroundWindow() };
        Ok(json!({
            "ok": actual == hwnd,
            "requested_hwnd": hwnd_hex(hwnd),
            "foreground_before": hwnd_hex(foreground_before),
            "foreground_hwnd": hwnd_hex(actual),
            "set_foreground_returned": requested,
            "brought_to_top": raised,
            "current_thread": attachments.current,
            "foreground_thread": foreground_thread,
            "target_thread": target_thread,
            "attached_foreground": attached_foreground,
            "attached_target": attached_target,
            "attempts": attempts,
            "waited_ms": waited,
        }))
    }

    /// Одна отправка в `SendInput` плюс пауза после неё.
    ///
    /// Шаг ввода больше не равен одной отправке: перетаскивание разворачивается в
    /// «нажал → n промежуточных сдвигов → отпустил» с живыми паузами внутри шага.
    /// `press`/`release` — то, что эта пачка делает с состоянием кнопок; по ним
    /// ведётся ведомость зажатого.
    struct PreparedChunk {
        inputs: Vec<INPUT>,
        pause_ms: u64,
        press: Option<MouseButton>,
        release: Option<MouseButton>,
        clamped_moves: usize,
    }

    /// Всё, что известно о пачке ДО первой отправки. Живой ход и тест считают это
    /// одной и той же функцией, чтобы числа в ответе не расходились с реальностью.
    pub(super) struct PlanTotals {
        pub(super) batches: usize,
        pub(super) records: usize,
        pub(super) pause_ms: u64,
        pub(super) clamped_moves: usize,
    }

    /// Ведомость зажатых кнопок.
    ///
    /// Зажатие переживает отдельную отправку — в этом весь смысл раздельных
    /// `mouse_down`/`mouse_up`. Но именно поэтому любой обрыв между ними (отказ
    /// `ensure_foreground` на следующем шаге, UIPI на середине пачки, паника)
    /// оставил бы рабочий стол Егора с намертво зажатой кнопкой и неуправляемым.
    /// Ведомость гасит это на выходе из обработчика, а `Drop` — последний рубеж.
    #[derive(Default)]
    struct HeldButtons {
        held: Vec<MouseButton>,
    }

    impl HeldButtons {
        fn press(&mut self, button: MouseButton) {
            if !self.held.contains(&button) {
                self.held.push(button);
            }
        }

        fn release(&mut self, button: MouseButton) {
            self.held.retain(|value| *value != button);
        }

        /// Отпускает всё, что осталось зажатым, и возвращает имена — чтобы ответ
        /// назвал вслух правку состояния мыши, которую сделал не её шаг, а тело.
        fn release_all(&mut self) -> Vec<&'static str> {
            if self.held.is_empty() {
                return Vec::new();
            }
            let names: Vec<&'static str> = self.held.iter().map(|button| button.name()).collect();
            let inputs: Vec<INPUT> = self
                .held
                .iter()
                .rev()
                .map(|button| mouse_input(0, 0, 0, button.up()))
                .collect();
            self.held.clear();
            // Отпускаем без права на отказ: если и это не пустил UIPI, сказать об
            // этом можно только текстом ответа — но не удержанием кнопки.
            let _ = send_inputs(&inputs);
            names
        }
    }

    impl Drop for HeldButtons {
        fn drop(&mut self) {
            let _ = self.release_all();
        }
    }

    fn input_perform(args: InputArgs) -> Result<Value> {
        if args.events.is_empty() {
            bail!("events must not be empty")
        }
        if args.events.len() > MAX_INPUT_EVENTS {
            bail!(
                "too many input events: {} (maximum {MAX_INPUT_EVENTS})",
                args.events.len()
            )
        }
        // Prepare and validate the entire batch before the first mutation.  A
        // malformed late event must not leave a half-applied automation step.
        let chunks = prepare_input_events(&args.events, args.inter_event_delay_ms)?;
        let totals = plan_totals(&chunks)?;
        if totals.pause_ms > MAX_TOTAL_INPUT_DELAY_MS {
            bail!(
                "total input delay is {}ms (maximum {MAX_TOTAL_INPUT_DELAY_MS}ms)",
                totals.pause_ms
            )
        }
        let before = ensure_foreground(args.expected_foreground.as_ref(), args.expected_pid)?;
        let mut held = HeldButtons::default();
        let mut paused_ms = 0u64;
        let outcome = (|| -> Result<()> {
            for (index, chunk) in chunks.iter().enumerate() {
                ensure_foreground(args.expected_foreground.as_ref(), args.expected_pid)?;
                // Помечаем зажатой ДО отправки: `SendInput` умеет вставить нажатие и
                // упереться на отпускании, и тогда кнопка уже зажата, а мы бы о ней
                // не знали.
                if let Some(button) = chunk.press {
                    held.press(button);
                }
                send_inputs(&chunk.inputs)?;
                if let Some(button) = chunk.release {
                    held.release(button);
                }
                if chunk.pause_ms > 0 && index + 1 < chunks.len() {
                    thread::sleep(Duration::from_millis(chunk.pause_ms));
                    paused_ms = paused_ms.saturating_add(chunk.pause_ms);
                }
            }
            Ok(())
        })();
        let auto_released = held.release_all();
        if let Err(error) = outcome {
            if auto_released.is_empty() {
                return Err(error);
            }
            // Ошибку показывают целиком (`{:#}` — вся цепочка), плюс правду о том,
            // что стол оставлен без зажатых кнопок: иначе она бы гадала, чинить ли
            // мышь вручную.
            return Err(anyhow!(
                "{error:#}; released still-held mouse buttons before returning: {}",
                auto_released.join(", ")
            ));
        }
        let after = unsafe { GetForegroundWindow() };
        Ok(json!({
            "ok": true,
            "events": args.events.len(),
            "input_batches": totals.batches,
            "input_records": totals.records,
            "planned_pause_ms": totals.pause_ms,
            "paused_ms": paused_ms,
            "clamped_moves": totals.clamped_moves,
            "buttons_auto_released": auto_released,
            "buttons_held_at_exit": Vec::<&str>::new(),
            "foreground_before": hwnd_hex(before),
            "foreground_after": hwnd_hex(after),
            "virtual_screen": virtual_screen(),
            "limits": input_limits(),
        }))
    }

    /// Пределы ввода едут в КАЖДОМ ответе. Молчаливый предел — та же ложь, просто
    /// отложенная до момента удара: она узнавала о капе из текста ошибки.
    fn input_limits() -> Value {
        json!({
            "max_events": MAX_INPUT_EVENTS,
            "max_input_records": MAX_INPUT_RECORDS,
            "max_text_utf16_units": MAX_INPUT_TEXT_UTF16_UNITS,
            "max_hotkey_keys": MAX_HOTKEY_KEYS,
            "max_click_count": MAX_CLICK_COUNT,
            "max_total_delay_ms": MAX_TOTAL_INPUT_DELAY_MS,
            "max_drag_steps": MAX_DRAG_STEPS,
            "default_drag_steps": DEFAULT_DRAG_STEPS,
            "max_drag_pause_ms": MAX_DRAG_PAUSE_MS,
            "default_drag_pauses_ms": {
                "hold": DEFAULT_DRAG_HOLD_MS,
                "step_delay": DEFAULT_DRAG_STEP_DELAY_MS,
                "settle": DEFAULT_DRAG_SETTLE_MS,
            },
            "coordinates": "absolute x/y are clamped into virtual_screen; clamped_moves says how many were pulled to the edge",
            "button_hold": "a held mouse button never survives the call: whatever this batch leaves down is released before returning and named in buttons_auto_released",
            // Раньше сторож фокуса срабатывал раз на шаг; теперь шаг drag — это 26 отправок,
            // и сторож стоит перед КАЖДОЙ. Само по себе это правильно (стол мог уехать
            // посреди перетаскивания), но пока об этом молчали, отказ «foreground changed»
            // посреди drag выглядел бы необъяснимым: она передала сторожа один раз, а
            // сорвалось на двадцатой отправке.
            "focus_guard": "expected_foreground/expected_pid are re-checked before EVERY batch, so a drag is aborted mid-way if the foreground moves; the held button is released and named in the error",
        })
    }

    fn plan_totals(chunks: &[PreparedChunk]) -> Result<PlanTotals> {
        let mut records = 0usize;
        let mut pause_ms = 0u64;
        let mut clamped_moves = 0usize;
        for chunk in chunks {
            records = records
                .checked_add(chunk.inputs.len())
                .context("input record count overflow")?;
            pause_ms = pause_ms
                .checked_add(chunk.pause_ms)
                .context("input delay overflow")?;
            clamped_moves = clamped_moves
                .checked_add(chunk.clamped_moves)
                .context("clamped move count overflow")?;
        }
        Ok(PlanTotals {
            batches: chunks.len(),
            records,
            pause_ms,
            clamped_moves,
        })
    }

    /// Разложить пачку в отправки, ничего не отправляя. Тесты ходят сюда, чтобы
    /// проверять арифметику пауз и капы, не трогая настоящую мышь Егора.
    #[cfg(test)]
    pub(super) fn plan_summary(
        events: &[InputEvent],
        inter_event_delay_ms: u64,
    ) -> Result<PlanTotals> {
        plan_totals(&prepare_input_events(events, inter_event_delay_ms)?)
    }

    fn prepare_input_events(
        events: &[InputEvent],
        inter_event_delay_ms: u64,
    ) -> Result<Vec<PreparedChunk>> {
        let mut total = 0usize;
        let mut chunks: Vec<PreparedChunk> = Vec::with_capacity(events.len());
        for (index, event) in events.iter().enumerate() {
            let produced = event_chunks(event)?;
            for chunk in &produced {
                total = total
                    .checked_add(chunk.inputs.len())
                    .context("input record count overflow")?;
                if total > MAX_INPUT_RECORDS {
                    bail!("input batch expands to {total} records (maximum {MAX_INPUT_RECORDS})")
                }
            }
            chunks.extend(produced);
            if index + 1 < events.len()
                && inter_event_delay_ms > 0
                && let Some(last) = chunks.last_mut()
            {
                last.pause_ms = last
                    .pause_ms
                    .checked_add(inter_event_delay_ms)
                    .context("input delay overflow")?;
            }
        }
        Ok(chunks)
    }

    /// Что шаг делает с состоянием кнопок: (что нажал, что отпустил).
    /// Один источник правды и для живой ведомости, и для предсказания `net_held`.
    fn event_button_effect(event: &InputEvent) -> Result<(Option<MouseButton>, Option<MouseButton>)> {
        Ok(match event {
            InputEvent::Click { button, .. } | InputEvent::Drag { button, .. } => {
                let button = MouseButton::parse(button)?;
                (Some(button), Some(button))
            }
            InputEvent::MouseDown { button, .. } => (Some(MouseButton::parse(button)?), None),
            InputEvent::MouseUp { button, .. } => (None, Some(MouseButton::parse(button)?)),
            _ => (None, None),
        })
    }

    /// Что останется зажатым, если все шаги дойдут до стола целиком. Живой цикл
    /// ведёт ту же ведомость по фактическим отправкам; здесь она считается наперёд,
    /// чтобы тест мог проверить арифметику без единого касания настоящей мыши.
    #[cfg(test)]
    pub(super) fn net_held(events: &[InputEvent]) -> Result<Vec<&'static str>> {
        let mut held = HeldButtons::default();
        for event in events {
            let (press, release) = event_button_effect(event)?;
            if let Some(button) = press {
                held.press(button);
            }
            if let Some(button) = release {
                held.release(button);
            }
        }
        // Забираем список, не отпуская: `Drop` не должен слать ввод из подсчёта.
        Ok(std::mem::take(&mut held.held)
            .into_iter()
            .map(|button| button.name())
            .collect())
    }

    fn event_chunks(event: &InputEvent) -> Result<Vec<PreparedChunk>> {
        if let InputEvent::Drag {
            button,
            x,
            y,
            to_x,
            to_y,
            steps,
            hold_ms,
            step_delay_ms,
            settle_ms,
        } = event
        {
            return drag_chunks(
                MouseButton::parse(button)?,
                (*x, *y),
                (*to_x, *to_y),
                *steps,
                (*hold_ms, *step_delay_ms, *settle_ms),
            );
        }
        let (press, release) = event_button_effect(event)?;
        let mut clamped_moves = 0usize;
        let inputs = event_inputs(event, &mut clamped_moves)?;
        Ok(vec![PreparedChunk {
            inputs,
            pause_ms: 0,
            press,
            release,
            clamped_moves,
        }])
    }

    fn drag_chunks(
        button: MouseButton,
        from: (i32, i32),
        to: (i32, i32),
        steps: u32,
        pauses: (u64, u64, u64),
    ) -> Result<Vec<PreparedChunk>> {
        if !(1..=MAX_DRAG_STEPS).contains(&steps) {
            bail!("drag steps must be between 1 and {MAX_DRAG_STEPS}")
        }
        let (hold_ms, step_delay_ms, settle_ms) = pauses;
        for (name, value) in [
            ("hold_ms", hold_ms),
            ("step_delay_ms", step_delay_ms),
            ("settle_ms", settle_ms),
        ] {
            if value > MAX_DRAG_PAUSE_MS {
                bail!("drag {name} is {value}ms (maximum {MAX_DRAG_PAUSE_MS}ms per pause)")
            }
        }
        let mut chunks = Vec::with_capacity(steps as usize + 2);
        let mut clamped_moves = 0usize;
        let mut press = vec![absolute_move(from.0, from.1, &mut clamped_moves)?];
        press.push(mouse_input(0, 0, 0, button.down()));
        chunks.push(PreparedChunk {
            inputs: press,
            pause_ms: hold_ms,
            press: Some(button),
            release: None,
            clamped_moves,
        });
        for step in 1..=steps {
            // Линейная интерполяция в i64: экраны бывают с отрицательным началом,
            // а произведение размаха на номер шага легко вылезает из i32.
            let point = |start: i32, end: i32| -> i32 {
                let span = i64::from(end) - i64::from(start);
                (i64::from(start) + span * i64::from(step) / i64::from(steps)) as i32
            };
            let mut clamped_moves = 0usize;
            let input = absolute_move(point(from.0, to.0), point(from.1, to.1), &mut clamped_moves)?;
            chunks.push(PreparedChunk {
                inputs: vec![input],
                pause_ms: if step == steps { settle_ms } else { step_delay_ms },
                press: None,
                release: None,
                clamped_moves,
            });
        }
        chunks.push(PreparedChunk {
            inputs: vec![mouse_input(0, 0, 0, button.up())],
            pause_ms: 0,
            press: None,
            release: Some(button),
            clamped_moves: 0,
        });
        Ok(chunks)
    }

    fn event_inputs(event: &InputEvent, clamped_moves: &mut usize) -> Result<Vec<INPUT>> {
        match event {
            InputEvent::Text { text } => text_inputs(text),
            InputEvent::Hotkey { keys } => hotkey_inputs(keys),
            InputEvent::Key { key, action } => key_action_inputs(key, action),
            InputEvent::Mouse { x, y, relative } => {
                if *relative {
                    Ok(vec![mouse_input(*x, *y, 0, MOUSEEVENTF_MOVE)])
                } else {
                    Ok(vec![absolute_move(*x, *y, clamped_moves)?])
                }
            }
            InputEvent::Click {
                button,
                x,
                y,
                count,
            } => click_inputs(button, *x, *y, *count, clamped_moves),
            InputEvent::Wheel {
                delta,
                horizontal,
                x,
                y,
            } => wheel_inputs(*delta, *horizontal, *x, *y, clamped_moves),
            InputEvent::MouseDown { button, x, y } => {
                button_edge_inputs(button, *x, *y, true, clamped_moves)
            }
            InputEvent::MouseUp { button, x, y } => {
                button_edge_inputs(button, *x, *y, false, clamped_moves)
            }
            // Перетаскивание — единственный шаг, который разворачивается больше чем
            // в одну отправку, поэтому его собирает `event_chunks`, а не эта функция.
            InputEvent::Drag { .. } => bail!("drag is expanded into batches by event_chunks"),
        }
    }

    fn button_edge_inputs(
        button: &str,
        x: Option<i32>,
        y: Option<i32>,
        press: bool,
        clamped_moves: &mut usize,
    ) -> Result<Vec<INPUT>> {
        if x.is_some() != y.is_some() {
            bail!("mouse button x and y must be supplied together")
        }
        let button = MouseButton::parse(button)?;
        let mut inputs = Vec::with_capacity(2);
        if let (Some(x), Some(y)) = (x, y) {
            inputs.push(absolute_move(x, y, clamped_moves)?);
        }
        inputs.push(mouse_input(
            0,
            0,
            0,
            if press { button.down() } else { button.up() },
        ));
        Ok(inputs)
    }

    fn text_inputs(text: &str) -> Result<Vec<INPUT>> {
        let units: Vec<_> = text.encode_utf16().collect();
        if units.len() > MAX_INPUT_TEXT_UTF16_UNITS {
            bail!(
                "input text has {} UTF-16 units (maximum {MAX_INPUT_TEXT_UTF16_UNITS})",
                units.len()
            )
        }
        let capacity = units
            .len()
            .checked_mul(2)
            .context("input text record count overflow")?;
        let mut inputs = Vec::with_capacity(capacity);
        for unit in units {
            inputs.push(keyboard_input(0, unit, KEYEVENTF_UNICODE));
            inputs.push(keyboard_input(0, unit, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
        }
        Ok(inputs)
    }

    fn hotkey_inputs(keys: &[KeyArg]) -> Result<Vec<INPUT>> {
        if keys.is_empty() {
            bail!("hotkey keys must not be empty")
        }
        if keys.len() > MAX_HOTKEY_KEYS {
            bail!("hotkey has {} keys (maximum {MAX_HOTKEY_KEYS})", keys.len())
        }
        let keys = keys.iter().map(key_value).collect::<Result<Vec<_>>>()?;
        let mut inputs = Vec::with_capacity(keys.len() * 2);
        for key in &keys {
            inputs.push(keyboard_input(*key, 0, KEYBD_EVENT_FLAGS(0)));
        }
        for key in keys.iter().rev() {
            inputs.push(keyboard_input(*key, 0, KEYEVENTF_KEYUP));
        }
        Ok(inputs)
    }

    fn key_action_inputs(key: &KeyArg, action: &str) -> Result<Vec<INPUT>> {
        let key = key_value(key)?;
        match action.trim().to_ascii_lowercase().as_str() {
            "down" => Ok(vec![keyboard_input(key, 0, KEYBD_EVENT_FLAGS(0))]),
            "up" => Ok(vec![keyboard_input(key, 0, KEYEVENTF_KEYUP)]),
            "press" => Ok(vec![
                keyboard_input(key, 0, KEYBD_EVENT_FLAGS(0)),
                keyboard_input(key, 0, KEYEVENTF_KEYUP),
            ]),
            _ => bail!("key action must be press, down, or up"),
        }
    }

    fn click_inputs(
        button: &str,
        x: Option<i32>,
        y: Option<i32>,
        count: u32,
        clamped_moves: &mut usize,
    ) -> Result<Vec<INPUT>> {
        if x.is_some() != y.is_some() {
            bail!("click x and y must be supplied together")
        }
        let button = MouseButton::parse(button)?;
        if !(1..=MAX_CLICK_COUNT).contains(&count) {
            bail!("click count must be between 1 and {MAX_CLICK_COUNT}")
        }
        let mut inputs = Vec::with_capacity(count as usize * 2 + usize::from(x.is_some()));
        if let (Some(x), Some(y)) = (x, y) {
            inputs.push(absolute_move(x, y, clamped_moves)?);
        }
        for _ in 0..count {
            inputs.push(mouse_input(0, 0, 0, button.down()));
            inputs.push(mouse_input(0, 0, 0, button.up()));
        }
        Ok(inputs)
    }

    fn wheel_inputs(
        delta: i32,
        horizontal: bool,
        x: Option<i32>,
        y: Option<i32>,
        clamped_moves: &mut usize,
    ) -> Result<Vec<INPUT>> {
        if x.is_some() != y.is_some() {
            bail!("wheel x and y must be supplied together")
        }
        let mut inputs = Vec::with_capacity(2);
        if let (Some(x), Some(y)) = (x, y) {
            inputs.push(absolute_move(x, y, clamped_moves)?);
        }
        inputs.push(mouse_input(
            0,
            0,
            delta as u32,
            if horizontal {
                MOUSEEVENTF_HWHEEL
            } else {
                MOUSEEVENTF_WHEEL
            },
        ));
        Ok(inputs)
    }

    fn send_inputs(inputs: &[INPUT]) -> Result<()> {
        if inputs.is_empty() {
            return Ok(());
        }
        let sent = unsafe { SendInput(inputs, size_of::<INPUT>() as i32) } as usize;
        if sent != inputs.len() {
            bail!(
                "SendInput inserted {sent} of {} records (the target may be blocked by UIPI)",
                inputs.len()
            )
        }
        Ok(())
    }

    fn keyboard_input(vk: u16, scan: u16, flags: KEYBD_EVENT_FLAGS) -> INPUT {
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: VIRTUAL_KEY(vk),
                    wScan: scan,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        }
    }

    fn mouse_input(dx: i32, dy: i32, data: u32, flags: MOUSE_EVENT_FLAGS) -> INPUT {
        INPUT {
            r#type: INPUT_MOUSE,
            Anonymous: INPUT_0 {
                mi: MOUSEINPUT {
                    dx,
                    dy,
                    mouseData: data,
                    dwFlags: flags,
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        }
    }

    fn absolute_move(x: i32, y: i32, clamped_moves: &mut usize) -> Result<INPUT> {
        let (left, top, width, height) = virtual_screen_tuple();
        if width <= 0 || height <= 0 {
            bail!("virtual desktop has invalid dimensions")
        }
        let (nx, pulled_x) = normalize_axis(x, left, width);
        let (ny, pulled_y) = normalize_axis(y, top, height);
        if pulled_x || pulled_y {
            // Координата за пределами виртуального экрана раньше молча подтягивалась
            // к краю: она метила в точку по чужому скриншоту, получала край и не
            // узнавала об этом ниоткуда. Теперь такие сдвиги считаются и едут в ответ.
            *clamped_moves = clamped_moves.saturating_add(1);
        }
        Ok(mouse_input(
            nx,
            ny,
            0,
            MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
        ))
    }

    /// Возвращает нормализованную ось и признак «пришлось подтянуть к краю».
    fn normalize_axis(value: i32, origin: i32, length: i32) -> (i32, bool) {
        if length <= 1 {
            return (0, i64::from(value) != i64::from(origin));
        }
        let relative = i64::from(value).saturating_sub(i64::from(origin));
        let clamped = relative.clamp(0, i64::from(length - 1));
        (
            ((clamped * 65_535) / i64::from(length - 1)) as i32,
            clamped != relative,
        )
    }

    fn key_value(key: &KeyArg) -> Result<u16> {
        if let KeyArg::Number(value) = key {
            if *value == 0 {
                bail!("virtual key must be nonzero")
            }
            return Ok(*value);
        }
        let KeyArg::Text(raw) = key else {
            unreachable!()
        };
        let key = raw.trim().to_ascii_lowercase();
        let value = match key.as_str() {
            "backspace" => 0x08,
            "tab" => 0x09,
            "enter" | "return" => 0x0D,
            "shift" => 0x10,
            "ctrl" | "control" => 0x11,
            "alt" => 0x12,
            "pause" => 0x13,
            "caps_lock" | "capslock" => 0x14,
            "escape" | "esc" => 0x1B,
            "space" => 0x20,
            "page_up" | "pageup" => 0x21,
            "page_down" | "pagedown" => 0x22,
            "end" => 0x23,
            "home" => 0x24,
            "left" => 0x25,
            "up" => 0x26,
            "right" => 0x27,
            "down" => 0x28,
            "print_screen" | "printscreen" => 0x2C,
            "insert" => 0x2D,
            "delete" | "del" => 0x2E,
            "win" | "meta" | "left_win" => 0x5B,
            "right_win" => 0x5C,
            _ if key.len() == 1 => {
                let byte = key.as_bytes()[0];
                if byte.is_ascii_alphanumeric() {
                    byte.to_ascii_uppercase() as u16
                } else {
                    bail!("unsupported named key {raw:?}; pass a numeric virtual-key code")
                }
            }
            _ if key.starts_with('f') => {
                let number = key[1..].parse::<u16>().unwrap_or_default();
                if (1..=24).contains(&number) {
                    0x6F + number
                } else {
                    bail!("function key must be f1 through f24")
                }
            }
            _ => bail!("unsupported named key {raw:?}; pass a numeric virtual-key code"),
        };
        Ok(value)
    }

    fn ensure_foreground(expected: Option<&HwndArg>, expected_pid: Option<u32>) -> Result<HWND> {
        let actual = unsafe { GetForegroundWindow() };
        if actual.0.is_null() {
            bail!("there is no foreground window in the interactive desktop")
        }
        if let Some(expected) = expected {
            let expected = expected.value()?;
            if actual != expected {
                bail!(
                    "foreground changed: expected {}, actual {}",
                    hwnd_hex(expected),
                    hwnd_hex(actual)
                )
            }
        }
        if let Some(expected_pid) = expected_pid {
            let mut actual_pid = 0;
            unsafe { GetWindowThreadProcessId(actual, Some(&mut actual_pid)) };
            if actual_pid != expected_pid {
                bail!("foreground pid changed: expected {expected_pid}, actual {actual_pid}")
            }
        }
        Ok(actual)
    }

    fn screen_capture(args: CaptureArgs, state_dir: &Path) -> Result<Value> {
        let (region, target_hwnd) = match args.target.to_ascii_lowercase().as_str() {
            "desktop" => {
                let (left, top, width, height) = virtual_screen_tuple();
                (
                    CaptureRegion {
                        left,
                        top,
                        width,
                        height,
                    },
                    None,
                )
            }
            "region" => (
                CaptureRegion {
                    left: args.x.context("region requires x")?,
                    top: args.y.context("region requires y")?,
                    width: args.width.context("region requires width")?,
                    height: args.height.context("region requires height")?,
                },
                None,
            ),
            "window" => {
                let hwnd = args.hwnd.context("window capture requires hwnd")?.value()?;
                require_window(hwnd, None)?;
                let mut rect = RECT::default();
                unsafe { GetWindowRect(hwnd, &mut rect)? };
                (
                    CaptureRegion {
                        left: rect.left,
                        top: rect.top,
                        width: rect.right - rect.left,
                        height: rect.bottom - rect.top,
                    },
                    Some(hwnd),
                )
            }
            _ => bail!("capture target must be desktop, region, or window"),
        };
        if region.width <= 0 || region.height <= 0 {
            bail!("capture rectangle must have positive width and height")
        }
        let image_bytes = capture_allocation(region.width, region.height)?;
        let directory = state_dir.join("desktop").join("captures");
        fs::create_dir_all(&directory)?;
        let name = capture_name(&args.name);
        let path = directory.join(name);
        capture_png(region, &path, image_bytes)?;
        let size = fs::metadata(&path)?.len();
        Ok(json!({
            "ok": true,
            "path": path,
            "format": "png",
            "mime": "image/png",
            "size": size,
            "left": region.left,
            "top": region.top,
            "width": region.width,
            "height": region.height,
            "target_hwnd": target_hwnd.map(hwnd_hex),
            "visible_desktop_capture": true,
        }))
    }

    fn capture_png(region: CaptureRegion, path: &Path, image_bytes: usize) -> Result<()> {
        let screen = unsafe { GetDC(None) };
        if screen.0.is_null() {
            bail!("GetDC returned no desktop device context")
        }
        let memory = unsafe { CreateCompatibleDC(Some(screen)) };
        if memory.0.is_null() {
            unsafe {
                ReleaseDC(None, screen);
            }
            bail!("CreateCompatibleDC failed")
        }
        let bitmap = unsafe { CreateCompatibleBitmap(screen, region.width, region.height) };
        if bitmap.0.is_null() {
            unsafe {
                let _ = DeleteDC(memory);
                ReleaseDC(None, screen);
            }
            bail!("CreateCompatibleBitmap failed")
        }
        let old = unsafe { SelectObject(memory, HGDIOBJ(bitmap.0)) };
        let result = capture_selected_png(screen, memory, bitmap, region, path, image_bytes);
        unsafe {
            SelectObject(memory, old);
            let _ = DeleteObject(HGDIOBJ(bitmap.0));
            let _ = DeleteDC(memory);
            ReleaseDC(None, screen);
        }
        result
    }

    fn capture_selected_png(
        screen: windows::Win32::Graphics::Gdi::HDC,
        memory: windows::Win32::Graphics::Gdi::HDC,
        bitmap: HBITMAP,
        region: CaptureRegion,
        path: &Path,
        image_bytes: usize,
    ) -> Result<()> {
        unsafe {
            BitBlt(
                memory,
                0,
                0,
                region.width,
                region.height,
                Some(screen),
                region.left,
                region.top,
                SRCCOPY | CAPTUREBLT,
            )?;
        }
        let mut pixels = vec![0u8; image_bytes];
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = size_of::<windows::Win32::Graphics::Gdi::BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = region.width;
        info.bmiHeader.biHeight = -region.height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB.0;
        info.bmiHeader.biSizeImage = u32::try_from(image_bytes)?;
        let rows = unsafe {
            GetDIBits(
                memory,
                bitmap,
                0,
                region.height as u32,
                Some(pixels.as_mut_ptr().cast()),
                &mut info,
                DIB_RGB_COLORS,
            )
        };
        if rows != region.height {
            bail!("GetDIBits returned {rows} of {} rows", region.height)
        }
        write_png(path, region.width, region.height, &pixels)
    }

    fn clipboard_read(args: ClipboardReadArgs) -> Result<Value> {
        let _clipboard = open_clipboard()?;
        let sequence = unsafe { GetClipboardSequenceNumber() };
        if unsafe { IsClipboardFormatAvailable(CF_UNICODETEXT) }.is_err() {
            return Ok(json!({
                "ok": true,
                "available": false,
                "format": "unicode_text",
                "clipboard_sequence": sequence,
            }));
        }
        let handle = unsafe { GetClipboardData(CF_UNICODETEXT)? };
        let global = HGLOBAL(handle.0);
        let bytes = unsafe { GlobalSize(global) };
        if bytes < 2 {
            return Ok(json!({
                "ok": true,
                "available": true,
                "format": "unicode_text",
                "text": "",
                "chars": 0,
                "truncated": false,
                "clipboard_sequence": sequence,
            }));
        }
        let pointer = unsafe { GlobalLock(global) } as *const u16;
        if pointer.is_null() {
            bail!("GlobalLock failed for clipboard text")
        }
        let units = unsafe { std::slice::from_raw_parts(pointer, bytes / 2) };
        let length = units
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(units.len());
        let limit = args.limit_chars.clamp(1, MAX_CLIPBOARD_CHARS).min(length);
        let text = String::from_utf16_lossy(&units[..limit]);
        unsafe {
            let _ = GlobalUnlock(global);
        }
        Ok(json!({
            "ok": true,
            "available": true,
            "format": "unicode_text",
            "text": text,
            "chars": length,
            "truncated": limit < length,
            "clipboard_sequence": sequence,
        }))
    }

    fn clipboard_write(args: ClipboardWriteArgs) -> Result<Value> {
        let mut units: Vec<u16> = args.text.encode_utf16().collect();
        if units.len() > MAX_CLIPBOARD_CHARS {
            bail!(
                "clipboard text has {} UTF-16 units (maximum {MAX_CLIPBOARD_CHARS})",
                units.len()
            )
        }
        units.push(0);
        let bytes = units
            .len()
            .checked_mul(size_of::<u16>())
            .context("clipboard text is too large")?;
        let global = unsafe { GlobalAlloc(GMEM_MOVEABLE, bytes)? };
        let pointer = unsafe { GlobalLock(global) } as *mut u16;
        if pointer.is_null() {
            unsafe {
                let _ = GlobalFree(Some(global));
            }
            bail!("GlobalLock failed for clipboard allocation")
        }
        unsafe {
            ptr::copy_nonoverlapping(units.as_ptr(), pointer, units.len());
            let _ = GlobalUnlock(global);
        }
        let result = (|| -> Result<()> {
            let _clipboard = open_clipboard()?;
            unsafe {
                EmptyClipboard()?;
                SetClipboardData(CF_UNICODETEXT, Some(HANDLE(global.0)))?;
            }
            Ok(())
        })();
        if let Err(error) = result {
            unsafe {
                let _ = GlobalFree(Some(global));
            }
            return Err(error);
        }
        Ok(json!({
            "ok": true,
            "format": "unicode_text",
            "chars": units.len() - 1,
            "clipboard_sequence": unsafe { GetClipboardSequenceNumber() },
        }))
    }

    fn open_clipboard() -> Result<ClipboardGuard> {
        for _ in 0..40 {
            if unsafe { OpenClipboard(None) }.is_ok() {
                return Ok(ClipboardGuard);
            }
            thread::sleep(Duration::from_millis(25));
        }
        bail!("clipboard remained busy for 1 second")
    }

    fn process_details(pid: u32) -> ProcessDetails {
        let session_id = process_session(pid);
        let Ok(handle) = (unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) })
        else {
            return ProcessDetails {
                path: None,
                created_filetime: None,
                session_id,
            };
        };
        let _handle = HandleGuard(handle);
        let mut path_buffer = vec![0u16; 32_768];
        let mut length = path_buffer.len() as u32;
        let path = unsafe {
            QueryFullProcessImageNameW(
                handle,
                Default::default(),
                PWSTR(path_buffer.as_mut_ptr()),
                &mut length,
            )
        }
        .ok()
        .map(|_| String::from_utf16_lossy(&path_buffer[..length as usize]));
        let mut created = Default::default();
        let mut exited = Default::default();
        let mut kernel = Default::default();
        let mut user = Default::default();
        let created_filetime =
            unsafe { GetProcessTimes(handle, &mut created, &mut exited, &mut kernel, &mut user) }
                .ok()
                .map(|_| {
                    (u64::from(created.dwHighDateTime) << 32) | u64::from(created.dwLowDateTime)
                });
        ProcessDetails {
            path,
            created_filetime,
            session_id,
        }
    }

    fn process_session(pid: u32) -> Option<u32> {
        let mut session = 0;
        unsafe { ProcessIdToSessionId(pid, &mut session) }
            .ok()
            .map(|_| session)
    }

    fn window_row(hwnd: HWND, z_order: usize) -> Option<Value> {
        if hwnd.0.is_null() || !unsafe { IsWindow(Some(hwnd)) }.as_bool() {
            return None;
        }
        let mut pid = 0;
        let thread_id = unsafe { GetWindowThreadProcessId(hwnd, Some(&mut pid)) };
        let details = process_details(pid);
        let mut rect = RECT::default();
        let rect_value = unsafe { GetWindowRect(hwnd, &mut rect) }.ok().map(|_| {
            json!({
                "left": rect.left,
                "top": rect.top,
                "right": rect.right,
                "bottom": rect.bottom,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            })
        });
        let hwnd_text = hwnd_hex(hwnd);
        Some(json!({
            "hwnd": hwnd_text,
            "fingerprint": format!("{}:{pid}:{}", hwnd_hex(hwnd), details.created_filetime.unwrap_or_default()),
            "pid": pid,
            "thread_id": thread_id,
            "session_id": details.session_id,
            "process_path": details.path,
            "process_created_filetime": details.created_filetime,
            "title": window_text(hwnd),
            "class": window_class(hwnd),
            "rect": rect_value,
            "visible": unsafe { IsWindowVisible(hwnd) }.as_bool(),
            "minimized": unsafe { IsIconic(hwnd) }.as_bool(),
            "z_order": z_order,
        }))
    }

    fn window_text(hwnd: HWND) -> String {
        let length = unsafe { GetWindowTextLengthW(hwnd) }.max(0) as usize;
        let mut buffer = vec![0u16; length.saturating_add(1).max(2)];
        let copied = unsafe { GetWindowTextW(hwnd, &mut buffer) }.max(0) as usize;
        String::from_utf16_lossy(&buffer[..copied.min(buffer.len())])
    }

    fn window_class(hwnd: HWND) -> String {
        let mut buffer = vec![0u16; 512];
        let copied = unsafe { GetClassNameW(hwnd, &mut buffer) }.max(0) as usize;
        String::from_utf16_lossy(&buffer[..copied.min(buffer.len())])
    }

    fn require_window(hwnd: HWND, expected_pid: Option<u32>) -> Result<()> {
        if !unsafe { IsWindow(Some(hwnd)) }.as_bool() {
            bail!("window {} no longer exists", hwnd_hex(hwnd))
        }
        if let Some(expected_pid) = expected_pid {
            let mut actual_pid = 0;
            unsafe { GetWindowThreadProcessId(hwnd, Some(&mut actual_pid)) };
            if actual_pid != expected_pid {
                bail!("window pid changed: expected {expected_pid}, actual {actual_pid}")
            }
        }
        Ok(())
    }

    fn page(rows: Vec<Value>, args: PageArgs) -> Value {
        let total = rows.len();
        let limit = args.limit.clamp(1, MAX_PAGE);
        let items: Vec<_> = rows.into_iter().skip(args.offset).take(limit).collect();
        json!({
            "ok": true,
            "total": total,
            "offset": args.offset,
            "limit": limit,
            "returned": items.len(),
            "next_offset": (args.offset + items.len() < total).then_some(args.offset + items.len()),
            "items": items,
        })
    }

    fn virtual_screen() -> Value {
        let (left, top, width, height) = virtual_screen_tuple();
        json!({
            "left": left,
            "top": top,
            "width": width,
            "height": height,
            "right": left + width,
            "bottom": top + height,
        })
    }

    fn virtual_screen_tuple() -> (i32, i32, i32, i32) {
        unsafe {
            (
                GetSystemMetrics(SM_XVIRTUALSCREEN),
                GetSystemMetrics(SM_YVIRTUALSCREEN),
                GetSystemMetrics(SM_CXVIRTUALSCREEN),
                GetSystemMetrics(SM_CYVIRTUALSCREEN),
            )
        }
    }

    fn hwnd_hex(hwnd: HWND) -> String {
        format!("0x{:X}", hwnd.0 as usize)
    }

    fn utf16_z(value: &[u16]) -> String {
        let length = value
            .iter()
            .position(|unit| *unit == 0)
            .unwrap_or(value.len());
        String::from_utf16_lossy(&value[..length])
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn capability_names_are_unique_and_versioned() {
        let mut names: Vec<_> = CAPABILITIES.iter().map(|value| value.name).collect();
        names.sort_unstable();
        names.dedup();
        assert_eq!(names.len(), CAPABILITIES.len());
        for descriptor in descriptors() {
            // input.perform v2 — раздельные нажатие/отпускание мыши и перетаскивание.
            let expected = u32::from(matches!(
                descriptor.name.as_str(),
                "desktop.screen.capture" | "desktop.input.perform"
            )) + 1;
            assert_eq!(descriptor.version, expected, "{}", descriptor.name);
        }
        assert_eq!(
            adapter_descriptor().version,
            if cfg!(target_os = "macos") { "1" } else { "3" }
        );
    }

    #[test]
    fn capture_declares_a_provider_owned_image_artifact() {
        let output = artifact_output(
            "desktop.screen.capture",
            &serde_json::json!({"ok": true, "path": "capture.png", "mime": "image/png"}),
        )
        .unwrap()
        .unwrap();
        assert_eq!(output.path, PathBuf::from("capture.png"));
        assert_eq!(output.media_type, "image/png");
        assert_eq!(output.presentation, "image");
        assert!(
            artifact_output("desktop.status", &serde_json::json!({"ok": true}))
                .unwrap()
                .is_none()
        );
    }

    #[test]
    fn adapter_is_only_advertised_as_available_where_it_runs() {
        let adapter = adapter_descriptor();
        assert_eq!(adapter.available, cfg!(any(windows, target_os = "macos")));
        assert_eq!(
            adapter.name,
            if cfg!(target_os = "macos") { "native-macos-desktop" } else { "native-win32-desktop" }
        );
    }

    #[cfg(windows)]
    #[test]
    fn native_read_surfaces_smoke() {
        let state = std::env::temp_dir().join("praxis-desktop-read-smoke");
        for (capability, args) in [
            ("desktop.status", serde_json::json!({})),
            ("os.process.list", serde_json::json!({"limit": 8})),
            (
                "desktop.window.list",
                serde_json::json!({"limit": 8, "visible_only": false}),
            ),
        ] {
            let result = dispatch(capability, args, &state).unwrap();
            assert_eq!(result["ok"], true, "{capability}: {result}");
        }
    }

    #[cfg(windows)]
    #[test]
    fn input_resource_limits_fail_before_touching_the_desktop() {
        let state = std::env::temp_dir().join("praxis-desktop-bounds");
        let too_many_events = serde_json::json!({
            "events": (0..513).map(|_| serde_json::json!({
                "type": "mouse", "x": 0, "y": 0, "relative": true
            })).collect::<Vec<_>>()
        });
        let error = dispatch("desktop.input.perform", too_many_events, &state).unwrap_err();
        assert!(error.to_string().contains("too many input events"));

        let oversized_text = serde_json::json!({
            "events": [{"type": "text", "text": "x".repeat(16_385)}]
        });
        let error = dispatch("desktop.input.perform", oversized_text, &state).unwrap_err();
        assert!(error.to_string().contains("UTF-16 units"));

        let too_many_records = serde_json::json!({
            "events": (0..3).map(|_| serde_json::json!({
                "type": "text", "text": "x".repeat(16_384)
            })).collect::<Vec<_>>()
        });
        let error = dispatch("desktop.input.perform", too_many_records, &state).unwrap_err();
        assert!(error.to_string().contains("input batch expands"));

        let excessive_delay = serde_json::json!({
            "inter_event_delay_ms": 30_001,
            "events": [
                {"type": "key", "key": "a"},
                {"type": "key", "key": "b"}
            ]
        });
        let error = dispatch("desktop.input.perform", excessive_delay, &state).unwrap_err();
        assert!(error.to_string().contains("total input delay"));

        let excessive_clicks = serde_json::json!({
            "events": [{"type": "click", "button": "left", "count": 65}]
        });
        let error = dispatch("desktop.input.perform", excessive_clicks, &state).unwrap_err();
        assert!(error.to_string().contains("click count"));
    }

    #[cfg(windows)]
    fn input_events(value: serde_json::Value) -> Vec<platform::InputEvent> {
        serde_json::from_value(value).expect("input events must parse")
    }

    /// Ведомость зажатого. Это тот самый счёт, по которому обработчик отпускает
    /// кнопку на выходе: если он ошибётся, мышь Егора останется зажатой.
    #[cfg(windows)]
    #[test]
    fn a_held_mouse_button_is_counted_until_something_releases_it() {
        let held = |value| platform::net_held(&input_events(value)).unwrap();

        // Одинокое нажатие — ровно тот случай, ради которого есть авто-отпускание.
        assert_eq!(
            held(serde_json::json!([{"type": "mouse_down", "button": "left"}])),
            vec!["left"]
        );
        assert!(
            held(serde_json::json!([
                {"type": "mouse_down", "button": "left"},
                {"type": "mouse", "x": 10, "y": 10},
                {"type": "mouse_up", "button": "left"}
            ]))
            .is_empty()
        );
        // Разные кнопки живут независимо: отпустили левую — правая всё ещё зажата.
        assert_eq!(
            held(serde_json::json!([
                {"type": "mouse_down", "button": "left"},
                {"type": "mouse_down", "button": "right"},
                {"type": "mouse_up", "button": "left"}
            ])),
            vec!["right"]
        );
        // Составные шаги замкнуты сами на себя.
        assert!(held(serde_json::json!([{"type": "click", "count": 3}])).is_empty());
        assert!(
            held(serde_json::json!([
                {"type": "drag", "x": 0, "y": 0, "to_x": 20, "to_y": 20}
            ]))
            .is_empty()
        );
        // Отпустить незажатое — не ошибка: это её способ снять залипание,
        // оставшееся от вызова, который оборвался раньше.
        assert!(held(serde_json::json!([{"type": "mouse_up", "button": "middle"}])).is_empty());
    }

    /// Перетаскивание разворачивается в отправки с паузами, и эти паузы едут в тот
    /// же общий бюджет задержки, а не мимо него.
    #[cfg(windows)]
    #[test]
    fn drag_expands_into_batches_whose_pauses_land_in_the_declared_delay_budget() {
        let plan = |value, delay| platform::plan_summary(&input_events(value), delay).unwrap();

        let single = plan(
            serde_json::json!([{
                "type": "drag", "x": 0, "y": 0, "to_x": 100, "to_y": 50,
                "steps": 10, "hold_ms": 50, "step_delay_ms": 7, "settle_ms": 30
            }]),
            0,
        );
        // нажатие + 10 сдвигов + отпускание
        assert_eq!(single.batches, 12);
        // 50 (после нажатия) + 9x7 (между сдвигами) + 30 (перед отпусканием)
        assert_eq!(single.pause_ms, 143);

        // Пауза между шагами добавляется ровно один раз на стык, поверх внутренних.
        let paired = plan(
            serde_json::json!([
                {"type": "drag", "x": 0, "y": 0, "to_x": 100, "to_y": 50,
                 "steps": 10, "hold_ms": 50, "step_delay_ms": 7, "settle_ms": 30},
                {"type": "key", "key": "a"}
            ]),
            200,
        );
        assert_eq!(paired.pause_ms, 343);
        assert_eq!(paired.batches, 13);

        // Умолчания названы в ответе и должны совпадать с тем, что реально считается.
        let defaults = plan(
            serde_json::json!([{"type": "drag", "x": 0, "y": 0, "to_x": 10, "to_y": 10}]),
            0,
        );
        assert_eq!(defaults.batches, 26);
        assert_eq!(defaults.pause_ms, 60 + 23 * 8 + 60);
    }

    /// Границы новых капов — с обеих сторон, но без единого касания настоящей мыши:
    /// принятая сторона доказывается тем, что подготовка спотыкается уже о ДРУГОЙ,
    /// заведомо более поздний предел.
    #[cfg(windows)]
    #[test]
    fn drag_limits_are_checked_at_the_boundary_before_the_desktop_is_touched() {
        let state = std::env::temp_dir().join("praxis-desktop-drag-bounds");
        let drag = |extra: serde_json::Value| {
            let mut step = serde_json::json!({
                "type": "drag", "x": 0, "y": 0, "to_x": 10, "to_y": 10,
                "steps": 1, "hold_ms": 0, "step_delay_ms": 0, "settle_ms": 0
            });
            let object = step.as_object_mut().unwrap();
            for (key, value) in extra.as_object().unwrap() {
                object.insert(key.clone(), value.clone());
            }
            step
        };
        // Второй шаг заведомо непроходим, поэтому «принято» видно по тому, КАКАЯ
        // жалоба пришла, и ни одна мышь при этом не двигается.
        let wall = serde_json::json!({"type": "text", "text": "x".repeat(16_385)});
        let refused = |first: serde_json::Value| {
            dispatch(
                "desktop.input.perform",
                serde_json::json!({"events": [first, wall.clone()]}),
                &state,
            )
            .unwrap_err()
            .to_string()
        };

        assert!(refused(drag(serde_json::json!({"steps": 256}))).contains("UTF-16 units"));
        assert!(
            refused(drag(serde_json::json!({"steps": 257})))
                .contains("drag steps must be between 1 and 256")
        );
        assert!(
            refused(drag(serde_json::json!({"steps": 0})))
                .contains("drag steps must be between 1 and 256")
        );
        assert!(refused(drag(serde_json::json!({"hold_ms": 5_000}))).contains("UTF-16 units"));
        assert!(
            refused(drag(serde_json::json!({"hold_ms": 5_001})))
                .contains("drag hold_ms is 5001ms")
        );
        assert!(
            refused(drag(serde_json::json!({"settle_ms": 5_001})))
                .contains("drag settle_ms is 5001ms")
        );
        assert!(
            refused(drag(serde_json::json!({"step_delay_ms": 5_001})))
                .contains("drag step_delay_ms is 5001ms")
        );

        // Сумма внутренних пауз упирается в общий бюджет: 200x150 = 30000 проходит,
        // 201-я миллисекунда на шаге — уже нет.
        let events = input_events(serde_json::json!([drag(
            serde_json::json!({"steps": 200, "step_delay_ms": 150, "settle_ms": 150})
        )]));
        assert_eq!(platform::plan_summary(&events, 0).unwrap().pause_ms, 30_000);
        let over = dispatch(
            "desktop.input.perform",
            serde_json::json!({"events": [drag(
                serde_json::json!({"steps": 200, "step_delay_ms": 151, "settle_ms": 151})
            )]}),
            &state,
        )
        .unwrap_err()
        .to_string();
        assert!(over.contains("total input delay is 30200ms"), "{over}");
    }

    /// Подтягивание координаты к краю виртуального экрана было молчаливым.
    #[cfg(windows)]
    #[test]
    fn out_of_screen_coordinates_are_counted_instead_of_being_pulled_in_silence() {
        let state = std::env::temp_dir().join("praxis-desktop-clamp");
        let status = dispatch("desktop.status", serde_json::json!({}), &state).unwrap();
        let screen = &status["virtual_screen"];
        let (left, top) = (
            screen["left"].as_i64().unwrap() as i32,
            screen["top"].as_i64().unwrap() as i32,
        );

        let inside = platform::plan_summary(
            &input_events(serde_json::json!([
                {"type": "mouse_down", "x": left, "y": top},
                {"type": "mouse_up", "x": left, "y": top}
            ])),
            0,
        )
        .unwrap();
        assert_eq!(inside.clamped_moves, 0);

        let outside = platform::plan_summary(
            &input_events(serde_json::json!([
                {"type": "mouse_down", "x": left - 1, "y": top},
                {"type": "mouse", "x": left, "y": top - 1},
                {"type": "mouse_up", "x": left, "y": top}
            ])),
            0,
        )
        .unwrap();
        assert_eq!(outside.clamped_moves, 2);
    }

    #[test]
    fn capture_has_independent_pixel_and_raw_byte_limits() {
        let error = capture_allocation(10_000, 4_001).unwrap_err();
        assert!(error.to_string().contains("pixels"));

        let error = capture_allocation(9_000, 4_000).unwrap_err();
        assert!(error.to_string().contains("raw BGRA bytes"));
    }

    #[test]
    fn capture_name_is_windows_component_safe() {
        let name = capture_name(&("𐐀".repeat(300) + ".bmp"));
        assert!(name.encode_utf16().count() <= 204);
        assert!(name.ends_with(".png"));
        assert_eq!(capture_name("Praxis.Screen.BMP"), "Praxis.Screen.png");
        assert_eq!(capture_name("Praxis.Screen.png"), "Praxis.Screen.png");
        assert_ne!(capture_name("CON"), "CON.png");
    }

    #[test]
    fn png_encoder_preserves_bgra_colors_as_lossless_rgb() {
        let directory = std::env::temp_dir().join(format!("praxis-png-test-{}", Uuid::new_v4()));
        fs::create_dir(&directory).unwrap();
        let path = directory.join("capture.png");
        fs::write(&path, b"old capture").unwrap();
        let bgra = [
            0, 0, 255, 0, // red; the unused GDI alpha byte must not make it transparent
            255, 128, 0, 37, // blue with a green component
        ];
        write_png(&path, 2, 1, &bgra).unwrap();

        let bytes = fs::read(&path).unwrap();
        assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n");
        let decoder = png::Decoder::new(std::io::BufReader::new(File::open(&path).unwrap()));
        let mut reader = decoder.read_info().unwrap();
        let mut decoded = vec![0; reader.output_buffer_size().unwrap()];
        let info = reader.next_frame(&mut decoded).unwrap();
        assert_eq!((info.width, info.height), (2, 1));
        assert_eq!(info.color_type, png::ColorType::Rgb);
        assert_eq!(&decoded[..info.buffer_size()], &[255, 0, 0, 0, 128, 255]);
        assert_eq!(fs::read_dir(&directory).unwrap().count(), 1);
        fs::remove_dir_all(directory).unwrap();
    }

    #[test]
    fn png_output_writer_enforces_its_byte_limit_before_overflow() {
        let mut output = SizeLimitedWriter::new(Vec::new(), 3);
        output.write_all(b"abc").unwrap();
        let error = output.write_all(b"d").unwrap_err();
        assert!(error.to_string().contains("PNG exceeds 3 bytes"));
        assert_eq!(output.inner, b"abc");
    }

    #[tokio::test(flavor = "current_thread")]
    async fn desktop_dispatch_refuses_to_block_a_current_thread_runtime() {
        let state = std::env::temp_dir().join("praxis-desktop-current-thread");
        let error = dispatch("desktop.status", serde_json::json!({}), &state).unwrap_err();
        assert!(error.to_string().contains("multi-thread Tokio runtime"));
    }

    #[cfg(windows)]
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn desktop_dispatch_runs_from_tokio_via_the_blocking_pool() {
        let state = std::env::temp_dir().join("praxis-desktop-blocking-pool");
        let result = dispatch("desktop.status", serde_json::json!({}), &state).unwrap();
        assert_eq!(result["ok"], true);
    }

    // ─── macOS: чистые стенды — любая ОС, ни одного вызова системы ──────────────────

    fn mac_events(value: serde_json::Value) -> Vec<mac_pure::InputEvent> {
        serde_json::from_value(value).expect("input events must parse")
    }

    const MAC_SCREEN: mac_pure::Screen = mac_pure::Screen {
        left: -1920,
        top: 0,
        width: 3360,
        height: 900,
    };

    #[test]
    fn mac_key_table_maps_windows_names_to_kvk() {
        for (name, code) in [
            ("enter", 0x24),
            ("return", 0x24),
            ("tab", 0x30),
            ("esc", 0x35),
            ("space", 0x31),
            ("backspace", 0x33),
            ("delete", 0x75),
            ("del", 0x75),
            ("insert", 0x72),
            ("home", 0x73),
            ("end", 0x77),
            ("pageup", 0x74),
            ("page_down", 0x79),
            ("left", 0x7B),
            ("up", 0x7E),
            ("right", 0x7C),
            ("down", 0x7D),
            ("ctrl", 0x3B),
            ("control", 0x3B),
            ("shift", 0x38),
            ("alt", 0x3A),
            ("option", 0x3A),
            ("win", 0x37),
            ("cmd", 0x37),
            ("meta", 0x37),
            ("right_win", 0x36),
            ("capslock", 0x39),
            ("a", 0x00),
            ("Z", 0x06),
            ("5", 0x17),
            ("0", 0x1D),
            ("f1", 0x7A),
            ("F12", 0x6F),
            ("f20", 0x5A),
        ] {
            assert_eq!(
                mac_pure::key_code_by_name(name).unwrap(),
                code,
                "{name}"
            );
        }
        for absent in ["f21", "f24", "pause", "print_screen", "", "ё", "-"] {
            assert!(mac_pure::key_code_by_name(absent).is_err(), "{absent:?}");
        }
        assert_eq!(mac_pure::key_code(&mac_pure::KeyArg::Number(0x24)).unwrap(), 0x24);
        assert!(mac_pure::key_code(&mac_pure::KeyArg::Number(0)).is_err());
    }

    /// `ps` выравнивает числа пробелами слева, а путь может содержать пробелы:
    /// на раннере наивный разбор уже давал пустую таблицу.
    #[test]
    fn mac_ps_parser_survives_leading_spaces_and_paths_with_spaces() {
        let sample = "    1     0     0 /sbin/launchd\n\
                      12345   1   501 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome\n\
                      \n\
                      garbage line here\n\
                      777 1 501 helene-body\n";
        let (rows, skipped) = mac_pure::parse_ps(sample);
        assert_eq!(skipped, 1);
        assert_eq!(rows.len(), 3);
        assert_eq!(
            rows[1],
            mac_pure::PsRow {
                pid: 12345,
                ppid: 1,
                uid: 501,
                comm: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome".into(),
            }
        );
        assert_eq!(rows[2].comm, "helene-body");

        let row = mac_pure::process_row(&rows[1], None, Some(1_726_700_000));
        assert_eq!(row["pid"], 12345);
        assert_eq!(row["parent_pid"], 1);
        assert_eq!(row["name"], "Google Chrome");
        assert_eq!(
            row["path"],
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        );
        assert!(row["session_id"].is_null());
        // FILETIME — единицы Windows: на macOS его нет, и секунды Unix едут своим полем.
        assert!(row["created_filetime"].is_null());
        assert_eq!(row["process_started_unix"], 1_726_700_000u64);
        assert!(row["threads"].is_null());
        // Относительный comm без proc_pidpath — путь неизвестен, а не выдуман; время
        // старта не узнали — `null`, а не ноль.
        let bare = mac_pure::process_row(&rows[2], None, None);
        assert_eq!(bare["name"], "helene-body");
        assert!(bare["path"].is_null());
        assert!(bare["process_started_unix"].is_null());
        let resolved = mac_pure::process_row(&rows[2], Some("/opt/helene/helene-body"), None);
        assert_eq!(resolved["path"], "/opt/helene/helene-body");
    }

    #[test]
    fn mac_window_row_has_the_windows_shape_and_names_hidden_titles() {
        let facts = mac_pure::WindowFacts {
            id: 31,
            pid: 4242,
            owner: "Finder".into(),
            title: None,
            layer: 0,
            x: 100.0,
            y: 50.0,
            width: 640.0,
            height: 480.0,
            on_screen: true,
            z_order: 2,
        };
        let hidden = mac_pure::window_row(
            &facts,
            Some("/System/Finder"),
            Some("com.apple.finder"),
            Some(1_726_700_000),
            false,
            Some(true),
        );
        assert_eq!(hidden["hwnd"], "0x1F");
        // Отпечаток — hwnd:pid:время старта, как на Windows: pid система переиспользует.
        assert_eq!(hidden["fingerprint"], "0x1F:4242:1726700000");
        assert_eq!(hidden["pid"], 4242);
        // `class` — идентификатор пакета (как у read_window), имя владельца — своим полем.
        assert_eq!(hidden["class"], "com.apple.finder");
        assert_eq!(hidden["owner"], "Finder");
        assert_eq!(hidden["process_started_unix"], 1_726_700_000u64);
        assert!(hidden["process_created_filetime"].is_null());
        assert_eq!(hidden["process_path"], "/System/Finder");
        assert_eq!(hidden["rect"]["left"], 100);
        assert_eq!(hidden["rect"]["right"], 740);
        assert_eq!(hidden["rect"]["bottom"], 530);
        assert_eq!(hidden["rect"]["width"], 640);
        assert_eq!(hidden["visible"], true);
        assert_eq!(hidden["foreground"], true);
        assert_eq!(hidden["z_order"], 2);
        assert!(hidden["title"].is_null());
        assert!(hidden["session_id"].is_null() && hidden["minimized"].is_null());
        assert!(hidden["note"].as_str().unwrap().contains("Screen Recording"));

        let titled = mac_pure::window_row(
            &mac_pure::WindowFacts {
                title: Some("Documents".into()),
                ..facts.clone()
            },
            None,
            None,
            None,
            true,
            None,
        );
        assert_eq!(titled["title"], "Documents");
        assert!(titled.get("note").is_none());
        assert!(titled.get("foreground").is_none());
        // Пакет не узнали — `class` падает на имя владельца, а не на пустоту; время
        // старта неизвестно — в отпечатке ноль, в поле `null`.
        assert_eq!(titled["class"], "Finder");
        assert_eq!(titled["owner"], "Finder");
        assert_eq!(titled["fingerprint"], "0x1F:4242:0");
        assert!(titled["process_started_unix"].is_null());
        // Заголовка нет, но разрешение есть: это честное «без заголовка», без заметки.
        let untitled = mac_pure::window_row(&facts, None, None, None, true, None);
        assert!(untitled["title"].is_null() && untitled.get("note").is_none());
    }

    #[test]
    fn mac_hwnd_parses_like_windows() {
        let parse = |value: serde_json::Value| {
            serde_json::from_value::<mac_pure::HwndArg>(value)
                .unwrap()
                .value()
        };
        assert_eq!(parse(serde_json::json!("0x1A")).unwrap(), 26);
        assert_eq!(parse(serde_json::json!("26")).unwrap(), 26);
        assert_eq!(parse(serde_json::json!(26)).unwrap(), 26);
        assert!(parse(serde_json::json!(0)).is_err());
        assert!(parse(serde_json::json!("0x")).is_err());
        assert!(parse(serde_json::json!(1u64 << 40)).is_err());
        assert_eq!(mac_pure::hwnd_hex(255), "0xFF");
    }

    #[test]
    fn mac_wheel_delta_maps_to_lines_without_silent_zero() {
        assert_eq!(mac_pure::wheel_lines(120), 3);
        assert_eq!(mac_pure::wheel_lines(-240), -6);
        assert_eq!(mac_pure::wheel_lines(60), 1);
        assert_eq!(mac_pure::wheel_lines(-10), -1);
        assert_eq!(mac_pure::wheel_lines(0), 0);
    }

    /// Текст — по знаку на отправку с паузой 20 мс; перевод строки — клавишей Return;
    /// перетаскивание — те же пачки и паузы, что на Windows.
    #[test]
    fn mac_input_plan_paces_text_and_counts_drag_pauses() {
        let plan = |value, delay| {
            let events = mac_events(value);
            let chunks = mac_pure::prepare_input_events(&events, delay, MAC_SCREEN).unwrap();
            (mac_pure::plan_totals(&chunks).unwrap(), chunks)
        };
        let (totals, chunks) = plan(serde_json::json!([{"type": "text", "text": "ab\r\n🙂"}]), 0);
        // a, b, Return (одна клавиша на CRLF), 🙂 — четыре пачки, пауза после трёх.
        assert_eq!(totals.batches, 4);
        assert_eq!(totals.pause_ms, 3 * mac_pure::TEXT_UNIT_PAUSE_MS);
        assert_eq!(totals.records, 8);
        assert_eq!(
            chunks[2].records,
            vec![
                mac_pure::Record::KeyDown(mac_pure::KEY_RETURN),
                mac_pure::Record::KeyUp(mac_pure::KEY_RETURN)
            ]
        );
        assert!(matches!(
            &chunks[3].records[0],
            mac_pure::Record::Unicode { units, down: true } if units.len() == 2
        ));

        let (drag, _) = plan(
            serde_json::json!([{
                "type": "drag", "x": 0, "y": 0, "to_x": 100, "to_y": 50,
                "steps": 10, "hold_ms": 50, "step_delay_ms": 7, "settle_ms": 30
            }]),
            0,
        );
        assert_eq!(drag.batches, 12);
        assert_eq!(drag.pause_ms, 143);

        let (paired, _) = plan(
            serde_json::json!([
                {"type": "drag", "x": 0, "y": 0, "to_x": 100, "to_y": 50,
                 "steps": 10, "hold_ms": 50, "step_delay_ms": 7, "settle_ms": 30},
                {"type": "key", "key": "a"}
            ]),
            200,
        );
        assert_eq!(paired.pause_ms, 343);
        assert_eq!(paired.batches, 13);

        let (defaults, _) = plan(
            serde_json::json!([{"type": "drag", "x": 0, "y": 0, "to_x": 10, "to_y": 10}]),
            0,
        );
        assert_eq!(defaults.batches, 26);
        assert_eq!(defaults.pause_ms, 60 + 23 * 8 + 60);

        // Тройной щелчок — click state 1, 2, 3, а не три одиночных.
        let (_, click) = plan(
            serde_json::json!([{"type": "click", "count": 3, "x": 5, "y": 5}]),
            0,
        );
        let states: Vec<u32> = click[0]
            .records
            .iter()
            .filter_map(|record| match record {
                mac_pure::Record::ButtonDown { click_state, .. } => Some(*click_state),
                _ => None,
            })
            .collect();
        assert_eq!(states, vec![1, 2, 3]);

        // Сочетание: модификаторы вниз по порядку, вверх — в обратном.
        let (_, hotkey) = plan(
            serde_json::json!([{"type": "hotkey", "keys": ["cmd", "shift", "s"]}]),
            0,
        );
        assert_eq!(
            hotkey[0].records,
            vec![
                mac_pure::Record::KeyDown(0x37),
                mac_pure::Record::KeyDown(0x38),
                mac_pure::Record::KeyDown(0x01),
                mac_pure::Record::KeyUp(0x01),
                mac_pure::Record::KeyUp(0x38),
                mac_pure::Record::KeyUp(0x37),
            ]
        );

        // Колесо: вниз — минус строки; с точкой — сначала сдвиг курсора.
        let (_, wheel) = plan(
            serde_json::json!([{"type": "wheel", "delta": -240, "x": 10, "y": 10}]),
            0,
        );
        assert_eq!(
            wheel[0].records,
            vec![
                mac_pure::Record::MoveTo { x: 10, y: 10 },
                mac_pure::Record::Wheel { vertical: -6, horizontal: 0 }
            ]
        );

        // Ведомость зажатого — та же арифметика, что на Windows.
        let held = |value| mac_pure::net_held(&mac_events(value)).unwrap();
        assert_eq!(
            held(serde_json::json!([{"type": "mouse_down", "button": "left"}])),
            vec!["left"]
        );
        assert_eq!(
            held(serde_json::json!([
                {"type": "mouse_down", "button": "left"},
                {"type": "mouse_down", "button": "right"},
                {"type": "mouse_up", "button": "left"}
            ])),
            vec!["right"]
        );
        assert!(held(serde_json::json!([{"type": "click", "count": 3}])).is_empty());
        assert!(held(serde_json::json!([{"type": "mouse_up", "button": "middle"}])).is_empty());
    }

    #[test]
    fn mac_input_plan_refuses_the_same_limits_as_windows_and_counts_clamps() {
        let refused = |value| {
            mac_pure::prepare_input_events(&mac_events(value), 0, MAC_SCREEN)
                .unwrap_err()
                .to_string()
        };
        assert!(refused(serde_json::json!([{"type": "text", "text": "x".repeat(16_385)}]))
            .contains("UTF-16 units"));
        assert!(refused(serde_json::json!([{"type": "click", "count": 65}])).contains("click count"));
        assert!(refused(serde_json::json!([
            {"type": "drag", "x": 0, "y": 0, "to_x": 1, "to_y": 1, "steps": 257}
        ]))
        .contains("drag steps must be between 1 and 256"));
        assert!(refused(serde_json::json!([
            {"type": "drag", "x": 0, "y": 0, "to_x": 1, "to_y": 1, "hold_ms": 5001}
        ]))
        .contains("drag hold_ms is 5001ms"));
        assert!(refused(serde_json::json!([{"type": "click", "x": 1}])).contains("together"));
        assert!(refused(serde_json::json!([{"type": "key", "key": "a", "action": "hold"}]))
            .contains("press, down, or up"));
        assert!(refused(serde_json::json!([{"type": "hotkey", "keys": []}])).contains("not be empty"));

        // Точка за экраном подтягивается к краю и СЧИТАЕТСЯ.
        let events = mac_events(serde_json::json!([
            {"type": "mouse_down", "x": -1920, "y": 0},
            {"type": "mouse", "x": -1921, "y": 0},
            {"type": "mouse", "x": 0, "y": 900},
            {"type": "mouse_up", "x": 1439, "y": 899}
        ]));
        let chunks = mac_pure::prepare_input_events(&events, 0, MAC_SCREEN).unwrap();
        let totals = mac_pure::plan_totals(&chunks).unwrap();
        assert_eq!(totals.clamped_moves, 2);
        assert_eq!(chunks[1].records, vec![mac_pure::Record::MoveTo { x: -1920, y: 0 }]);
        assert_eq!(chunks[2].records, vec![mac_pure::Record::MoveTo { x: 0, y: 899 }]);

        // Бюджет пауз: 200 шагов по 150 мс — ровно потолок; текст в 1 501 знак — уже нет.
        let budget = |value| {
            let events = mac_events(value);
            mac_pure::plan_totals(&mac_pure::prepare_input_events(&events, 0, MAC_SCREEN).unwrap())
                .unwrap()
                .pause_ms
        };
        assert_eq!(
            budget(serde_json::json!([{
                "type": "drag", "x": 0, "y": 0, "to_x": 1, "to_y": 1,
                "steps": 200, "hold_ms": 0, "step_delay_ms": 150, "settle_ms": 150
            }])),
            30_000
        );
        assert_eq!(budget(serde_json::json!([{"type": "text", "text": "x".repeat(1_501)}])), 30_000);
        assert_eq!(budget(serde_json::json!([{"type": "text", "text": "x".repeat(1_502)}])), 30_020);
        let limits = mac_pure::input_limits();
        assert_eq!(limits["typing_pacing_ms"], 20);
        assert_eq!(limits["max_text_chars_per_call_at_pacing"], 1_500);
        assert!(limits["permissions"].as_str().unwrap().contains("Accessibility"));
        // Состояние модификаторов живёт ровно один вызов, и предел говорит это словом.
        assert_eq!(limits["modifiers_scope"], "call");
        assert!(limits["modifier_hold"].as_str().unwrap().contains("ONE batch"));
    }

    /// Раскладка байтов CGImage читается из CGBitmapInfo, а не предполагается.
    #[test]
    fn mac_pixel_layout_reads_bitmap_info_and_to_bgra_handles_padding() {
        use mac_pure::{PixelLayout, pixel_layout, to_bgra};
        const LITTLE: u32 = 2 << 12;
        const BIG: u32 = 4 << 12;
        assert_eq!(pixel_layout(2 | LITTLE, 32, 8).unwrap(), PixelLayout::Bgra);
        assert_eq!(pixel_layout(6 | LITTLE, 32, 8).unwrap(), PixelLayout::Bgra);
        assert_eq!(pixel_layout(2 | BIG, 32, 8).unwrap(), PixelLayout::Argb);
        assert_eq!(pixel_layout(2, 32, 8).unwrap(), PixelLayout::Argb);
        assert_eq!(pixel_layout(1 | BIG, 32, 8).unwrap(), PixelLayout::Rgba);
        assert_eq!(pixel_layout(5, 32, 8).unwrap(), PixelLayout::Rgba);
        assert_eq!(pixel_layout(1 | LITTLE, 32, 8).unwrap(), PixelLayout::Abgr);
        assert!(pixel_layout(2 | LITTLE, 16, 5).is_err());
        assert!(pixel_layout(2 | (1 << 8), 32, 8).is_err());
        assert!(pixel_layout(7, 32, 8).is_err());

        // Две строки по одному пикселю, строка с хвостом в 4 байта мусора.
        let red_then_blue = |layout: PixelLayout| -> Vec<u8> {
            let (red, blue): ([u8; 4], [u8; 4]) = match layout {
                PixelLayout::Bgra => ([0, 0, 255, 255], [255, 0, 0, 255]),
                PixelLayout::Argb => ([255, 255, 0, 0], [255, 0, 0, 255]),
                PixelLayout::Rgba => ([255, 0, 0, 255], [0, 0, 255, 255]),
                PixelLayout::Abgr => ([255, 0, 0, 255], [255, 255, 0, 0]),
            };
            [red.as_slice(), &[9, 9, 9, 9], blue.as_slice(), &[9, 9, 9, 9]].concat()
        };
        for layout in [PixelLayout::Bgra, PixelLayout::Argb, PixelLayout::Rgba, PixelLayout::Abgr] {
            let bgra = to_bgra(&red_then_blue(layout), 1, 2, 8, layout).unwrap();
            assert_eq!(bgra, vec![0, 0, 255, 255, 255, 0, 0, 255], "{layout:?}");
        }
        assert!(to_bgra(&[0u8; 8], 2, 2, 8, PixelLayout::Bgra).is_err());
        assert!(to_bgra(&[0u8; 16], 3, 1, 8, PixelLayout::Bgra).is_err());
    }

    #[test]
    fn mac_downscale_box_averages_and_nearest_resamples() {
        use mac_pure::{Downscale, downscale_box, plan_capture, resample_nearest};
        // 4×2 → 2×1: левый блок — четыре разных серых (среднее 10), правый — 200.
        let mut bgra = Vec::new();
        for row in [[4u8, 8, 200, 200], [12, 16, 200, 200]] {
            for value in row {
                bgra.extend_from_slice(&[value, value, value, 255]);
            }
        }
        let (scaled, width, height) = downscale_box(&bgra, 4, 2, 2).unwrap();
        assert_eq!((width, height), (2, 1));
        assert_eq!(scaled, vec![10, 10, 10, 255, 200, 200, 200, 255]);
        assert!(downscale_box(&bgra, 4, 2, 3).is_none());
        assert!(downscale_box(&bgra, 4, 2, 1).is_none());

        let nearest = resample_nearest(&bgra, 4, 2, 2, 2);
        assert_eq!(nearest.len(), 16);
        assert_eq!(&nearest[..4], &[4, 4, 4, 255]);
        assert_eq!(&nearest[4..8], &[200, 200, 200, 255]);

        let plan = |pw, ph, fw, fh, native| plan_capture(pw, ph, fw, fh, native);
        assert_eq!(plan(2880, 1800, 1440, 900, false).downscale, Downscale::Box(2));
        assert_eq!(plan(4320, 2700, 1440, 900, false).downscale, Downscale::Box(3));
        assert_eq!(plan(1440, 900, 1440, 900, false).downscale, Downscale::None);
        assert_eq!(plan(2880, 1800, 1440, 900, true).downscale, Downscale::None);
        // Нецелый масштаб (снимок через дисплеи с разным масштабом) — ближайший пиксель.
        assert_eq!(plan(2160, 1350, 1440, 900, false).downscale, Downscale::Nearest);
        assert_eq!(plan(2880, 1801, 1440, 900, false).downscale, Downscale::Nearest);
    }

    /// ⚠ Размер результата — от ОБРАЗА, не от рамки. Образ 1000×600 при рамке 500×301
    /// (рамка CGWindowList округлена, образ идёт без полей) раньше уезжал ближайшим
    /// пикселем в 500×301 — растяжка до чужого размера. Теперь это честное усреднение
    /// блоками в 500×300 при целом масштабе 2.
    #[test]
    fn mac_capture_plan_measures_the_image_not_the_frame() {
        use mac_pure::{Downscale, plan_capture};
        let plan = plan_capture(1000, 600, 500, 301, false);
        assert_eq!(plan.scale, 2.0);
        assert_eq!(plan.downscale, Downscale::Box(2));
        assert_eq!((plan.width, plan.height), (500, 300));
        // Разошлись ровно на пункт — это округление рамки, а не повод для заметки.
        assert_eq!(plan.frame_mismatch, None);

        // Разошлись сильно — обе цифры называются вслух.
        let off = plan_capture(1000, 600, 500, 380, false);
        assert_eq!((off.width, off.height), (500, 300));
        assert_eq!(off.frame_mismatch, Some((500, 380)));

        // Масштаб 1: уменьшать нечего, растягивать до рамки — тем более.
        let plain = plan_capture(800, 613, 800, 614, false);
        assert_eq!(plain.downscale, Downscale::None);
        assert_eq!((plain.width, plain.height), (800, 613));

        // `native`: образ как есть, но пункты в ответе всё равно считаны от образа.
        let native = plan_capture(1000, 600, 500, 301, true);
        assert_eq!(native.downscale, Downscale::None);
        assert_eq!((native.width, native.height), (500, 300));

        // Нечётная сторона образа при целом масштабе — блоками нельзя, но цель всё
        // равно СВОЯ (500×301), а не рамка.
        let odd = plan_capture(1000, 601, 500, 300, false);
        assert_eq!(odd.downscale, Downscale::Nearest);
        assert_eq!((odd.width, odd.height), (500, 301));
    }

    #[test]
    fn mac_app_bundle_is_cut_from_the_executable_path() {
        assert_eq!(
            mac_pure::app_bundle("/Applications/Safari.app/Contents/MacOS/Safari").as_deref(),
            Some("/Applications/Safari.app")
        );
        assert_eq!(
            mac_pure::app_bundle("/Applications/Xcode.app/Contents/Applications/Simulator.app/Contents/MacOS/Simulator")
                .as_deref(),
            Some("/Applications/Xcode.app/Contents/Applications/Simulator.app")
        );
        assert!(mac_pure::app_bundle("/opt/helene/helene-body").is_none());
        assert!(mac_pure::app_bundle("/Applications/Safari.app").is_none());
    }

    #[test]
    fn mac_page_and_process_filters_share_the_windows_shape() {
        let rows = (1..=5).map(|pid| serde_json::json!({"pid": pid})).collect();
        let page = mac_pure::page(rows, mac_pure::PageArgs { offset: 3, limit: 1 });
        assert_eq!(page["total"], 5);
        assert_eq!(page["returned"], 1);
        assert_eq!(page["next_offset"], 4);
        assert_eq!(page["items"][0]["pid"], 4);
        let args: mac_pure::WindowListArgs = serde_json::from_value(serde_json::json!({
            "limit": 8, "visible_only": false, "title_contains": "Finder"
        }))
        .unwrap();
        assert!(!args.visible_only && !args.all_layers);
        assert_eq!(args.page.limit, 8);
        let capture: mac_pure::CaptureArgs =
            serde_json::from_value(serde_json::json!({"target": "window", "hwnd": "0x2A"})).unwrap();
        assert!(!capture.native);
        assert_eq!(capture.hwnd.unwrap().value().unwrap(), 42);
    }

    // ─── macOS: живые стенды — только на Mac; без TCC — честный отказ, не пропуск ───

    #[cfg(target_os = "macos")]
    fn mac_state() -> PathBuf {
        std::env::temp_dir().join(format!("praxis-desktop-mac-{}", Uuid::new_v4()))
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_live_status_answers_with_platform_and_tcc() {
        let status = dispatch("desktop.status", serde_json::json!({}), &mac_state()).unwrap();
        assert_eq!(status["ok"], true, "{status}");
        assert_eq!(status["platform"], "macos");
        assert!(status["session_id"].is_null());
        assert!(status["interactive"].is_boolean());
        assert!(status["tcc"]["screen_recording"].is_boolean());
        assert!(status["tcc"]["accessibility"].is_boolean());
        assert!(status["hints"].is_array());
        assert!(status["scale"].as_f64().unwrap() >= 1.0);
        let screen = &status["virtual_screen"];
        assert!(screen["width"].as_i64().unwrap() > 0 && screen["height"].as_i64().unwrap() > 0);
        assert_eq!(status["hints"].as_array().unwrap().len(), usize::from(!status["tcc"]["screen_recording"].as_bool().unwrap()) + usize::from(!status["tcc"]["accessibility"].as_bool().unwrap()));
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_live_window_list_and_process_list_answer_honestly() {
        let state = mac_state();
        let all = dispatch(
            "desktop.window.list",
            serde_json::json!({"visible_only": false, "all_layers": true, "limit": 50}),
            &state,
        )
        .unwrap();
        assert_eq!(all["ok"], true, "{all}");
        assert_eq!(all["platform"], "macos");
        assert!(all["layers"].as_str().unwrap().contains("all"));
        if crate::mac::gui_session() {
            assert!(all["total"].as_u64().unwrap() >= 1, "{all}");
        }
        let ordinary = dispatch("desktop.window.list", serde_json::json!({}), &state).unwrap();
        assert_eq!(ordinary["ok"], true, "{ordinary}");
        assert!(ordinary["layers"].as_str().unwrap().contains("ordinary"));
        for row in ordinary["items"].as_array().unwrap() {
            assert!(row["hwnd"].as_str().unwrap().starts_with("0x"));
            assert!(row["rect"]["width"].as_i64().is_some());
            assert!(row["visible"].is_boolean());
        }
        let tcc = crate::mac::tcc();
        let filtered = dispatch(
            "desktop.window.list",
            serde_json::json!({"title_contains": "praxis-no-such-window"}),
            &state,
        );
        if tcc.screen_recording {
            let filtered = filtered.unwrap();
            assert_eq!(filtered["ok"], true);
            assert_eq!(filtered["total"], 0);
        } else {
            // Отказ — ошибка рамки со словами подсказки, а не результат `ok:false`.
            let error = filtered.unwrap_err().to_string();
            assert!(error.contains("title_contains cannot be applied"), "{error}");
            assert!(error.contains("Запись экрана"), "{error}");
            assert!(error.contains("screen_recording=false"), "{error}");
        }

        let processes = dispatch("os.process.list", serde_json::json!({"limit": 20_000}), &state).unwrap();
        assert_eq!(processes["ok"], true, "{processes}");
        let me = std::process::id();
        assert!(
            processes["items"].as_array().unwrap().iter().any(|row| row["pid"] == me),
            "own pid {me} missing: {processes}"
        );
        let error = dispatch("os.process.list", serde_json::json!({"session_id": 1}), &state).unwrap_err();
        assert!(error.to_string().contains("session ids"), "{error}");
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_live_capture_gives_a_png_or_refuses_with_hints() {
        let state = mac_state();
        let tcc = crate::mac::tcc();
        let result = dispatch("desktop.screen.capture", serde_json::json!({}), &state);
        if tcc.screen_recording {
            let result = result.unwrap();
            assert_eq!(result["ok"], true, "{result}");
            let path = PathBuf::from(result["path"].as_str().unwrap());
            let bytes = fs::read(&path).unwrap();
            assert!(bytes.len() > 10 * 1024, "PNG is only {} bytes", bytes.len());
            assert_eq!(&bytes[..8], b"\x89PNG\r\n\x1a\n");
            let scale = result["scale"].as_f64().unwrap();
            assert!(scale >= 1.0, "{result}");
            assert_eq!(result["downscale"], if scale > 1.0 { "box-average" } else { "none" });
            assert_eq!(result["pixel_width"], result["width"]);
            let native = dispatch("desktop.screen.capture", serde_json::json!({"native": true}), &state).unwrap();
            assert_eq!(native["ok"], true, "{native}");
            assert_eq!(native["pixel_width"], native["source_pixel_width"]);
            assert!(artifact_output("desktop.screen.capture", &result).unwrap().is_some());
            let _ = fs::remove_dir_all(&state);
        } else {
            // Отказ — ошибка рамки: слова «куда идти» в тексте, на диске ничего.
            let error = result.unwrap_err().to_string();
            assert!(error.contains("Screen Recording"), "{error}");
            assert!(error.contains("Запись экрана"), "{error}");
            assert!(error.contains("platform=macos"), "{error}");
            assert!(!state.join("desktop").exists(), "nothing may be written on refusal");
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_live_input_and_activation_refuse_without_accessibility() {
        let state = mac_state();
        let tcc = crate::mac::tcc();
        // Сдвиг на (0, 0) — единственный безвредный ввод на чужом столе.
        let input = dispatch(
            "desktop.input.perform",
            serde_json::json!({"events": [{"type": "mouse", "x": 0, "y": 0, "relative": true}]}),
            &state,
        );
        if tcc.accessibility {
            let input = input.unwrap();
            assert_eq!(input["ok"], true, "{input}");
            assert_eq!(input["input_batches"], 1);
            assert_eq!(input["limits"]["typing_pacing_ms"], 20);
        } else {
            // Отказ — ошибка рамки со словами подсказки.
            let error = input.unwrap_err().to_string();
            assert!(error.contains("Accessibility"), "{error}");
            assert!(error.contains("Универсальный доступ"), "{error}");
            assert!(error.contains("accessibility=false"), "{error}");
        }
        // Пределы бьют раньше разрешений и раньше стола — как на Windows.
        let error = dispatch(
            "desktop.input.perform",
            serde_json::json!({"events": [{"type": "text", "text": "x".repeat(16_385)}]}),
            &state,
        )
        .unwrap_err();
        assert!(error.to_string().contains("UTF-16 units"), "{error}");
        let error = dispatch(
            "desktop.input.perform",
            serde_json::json!({"events": [{"type": "text", "text": "x".repeat(2_000)}]}),
            &state,
        )
        .unwrap_err();
        assert!(error.to_string().contains("paced"), "{error}");

        let missing = dispatch(
            "desktop.window.activate",
            serde_json::json!({"hwnd": "0xFFFFFFF"}),
            &state,
        )
        .unwrap_err();
        assert!(missing.to_string().contains("no longer exists"), "{missing}");

        // ⚠ Поднятие окна: «не подняли» обязано быть ОШИБКОЙ — рамка транспорта шьёт
        // `ok` результата своим, и `{"ok": false}` доехал бы до модели как `true`.
        // Поднимаем ПЕРЕДНЕЕ окно: оно уже впереди, так что стол не дёргается.
        if let Some(front) = crate::mac::frontmost().unwrap() {
            let hwnd = format!("0x{:X}", front.id);
            let result = dispatch(
                "desktop.window.activate",
                serde_json::json!({"hwnd": hwnd, "timeout_ms": 1500}),
                &state,
            );
            match result {
                Ok(value) => {
                    // Успех считается по ФАКТУ, а не по возврату `open`/AXFrontmost.
                    assert_eq!(value["ok"], true, "{value}");
                    assert_eq!(value["foreground_hwnd"], hwnd, "ok без переднего окна: {value}");
                    assert_eq!(value["requested_hwnd"], hwnd, "{value}");
                    assert!(value["activated"].is_boolean(), "{value}");
                    assert!(value["waited_ms"].is_u64(), "{value}");
                }
                Err(error) => {
                    let said = error.to_string();
                    assert!(said.contains("was not activated"), "{said}");
                    assert!(said.contains("foreground_hwnd"), "{said}");
                    assert!(said.contains("waited_ms"), "{said}");
                    if !tcc.accessibility {
                        assert!(said.contains("Универсальный доступ"), "{said}");
                    }
                }
            }
        }
    }

    /// Пачка, кончившаяся зажатым модификатором, отпускает его САМА и говорит об этом:
    /// состояние модификаторов живёт один вызов, и молчать об этом значит обещать модели
    /// «зажми сейчас, нажми потом», чего тело не умеет.
    #[cfg(target_os = "macos")]
    #[test]
    fn mac_live_a_lone_modifier_down_is_released_and_named() {
        let state = mac_state();
        let tcc = crate::mac::tcc();
        let result = dispatch(
            "desktop.input.perform",
            serde_json::json!({"events": [{"type": "key", "key": "cmd", "action": "down"}]}),
            &state,
        );
        if tcc.accessibility {
            let result = result.unwrap();
            assert_eq!(result["ok"], true, "{result}");
            assert_eq!(result["modifiers_auto_released"], serde_json::json!(["cmd"]), "{result}");
            assert_eq!(result["modifiers_held_at_exit"], serde_json::json!([]), "{result}");
            let notes = result["notes"].as_array().unwrap();
            assert!(
                notes.iter().any(|note| note.as_str().unwrap_or("").contains("modifiers do not survive")),
                "{result}"
            );
        } else {
            let error = result.unwrap_err().to_string();
            assert!(error.contains("Универсальный доступ"), "{error}");
        }
    }

    #[cfg(target_os = "macos")]
    #[test]
    fn mac_live_clipboard_round_trip() {
        if !crate::mac::gui_session() {
            eprintln!("нет графической сессии — pbcopy/pbpaste не проверить");
            return;
        }
        let state = mac_state();
        let text = "Привет, Mac 🙂";
        let written = dispatch("desktop.clipboard.write", serde_json::json!({"text": text}), &state).unwrap();
        assert_eq!(written["ok"], true, "{written}");
        assert_eq!(written["chars"], text.encode_utf16().count());
        let read = dispatch("desktop.clipboard.read", serde_json::json!({}), &state).unwrap();
        assert_eq!(read["ok"], true, "{read}");
        assert_eq!(read["text"], text);
        assert_eq!(read["truncated"], false);
        let short = dispatch("desktop.clipboard.read", serde_json::json!({"limit_chars": 6}), &state).unwrap();
        assert_eq!(short["text"], "Привет");
        assert_eq!(short["truncated"], true);
    }

    #[cfg(target_os = "macos")]
    #[tokio::test(flavor = "multi_thread", worker_threads = 2)]
    async fn mac_live_dispatch_runs_from_tokio_via_the_blocking_pool() {
        let result = dispatch("desktop.status", serde_json::json!({}), &mac_state()).unwrap();
        assert_eq!(result["ok"], true);
        assert_eq!(result["platform"], "macos");
    }
}
