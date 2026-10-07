//! Общие обёртки Linux для тела: X-сервер (окна, ввод, снимок, мониторы), сессия, процессы
//! из `/proc`, сторож родителя. Двойник `mac.rs`: модули `desktop` (экран, ввод, окна) и
//! `atspi` (дерево окна) зовут ЭТИ функции, а не держат свои соединения и свои разборы.
//!
//! Правила координат тела на Linux — одни на все глаголы:
//! * всё считается в ПИКСЕЛЯХ корневого окна X-сервера: начало в левом верхнем углу
//!   виртуального экрана (все мониторы — один корень), ось Y вниз. Так считают EWMH, XTest,
//!   GetImage и AT-SPI (`GetExtents` в координатах экрана). Снимок и клик — 1:1;
//! * прямоугольник окна — С РАМКОЙ оконного менеджера (`_NET_FRAME_EXTENTS`), как
//!   `GetWindowRect` на Windows и границы CGWindowList на Mac: окно — то, что видно на
//!   экране, а не только клиентская часть.
//!
//! ⚠ WAYLAND. По замыслу Wayland глобального ввода, чужих снимков и списка окон нет: всё
//! это здесь делается через X-сервер, и под Wayland-сессией (GNOME в Debian 12 и Ubuntu
//! 22.04+ по умолчанию) X-сервер — это XWayland, который видит ТОЛЬКО X-программы. Тело
//! обязано сказать об этом словами (`Session::hints`), а не отдать неполный список окон
//! как полный. Дерево элементов (AT-SPI) от этого не зависит — оно ходит по D-Bus.
//!
//! Соединение с X-сервером — одно на вызов глагола (как CGEventSource у Mac): у глаголов
//! свои потоки, и общий сокет пришлось бы делить замком без всякой выгоды — открыть
//! соединение по локальному сокету стоит сотни микросекунд.
#![cfg(target_os = "linux")]

use std::time::Duration;

use anyhow::{Context, Result, anyhow, bail};
use serde::Serialize;
use x11rb::connection::{Connection, RequestConnection};
use x11rb::protocol::randr::ConnectionExt as _;
use x11rb::protocol::xkb::{self, ConnectionExt as _};
use x11rb::protocol::res::{ClientIdMask, ClientIdSpec, ConnectionExt as _};
use x11rb::protocol::xproto::{
    Atom, AtomEnum, ClientMessageEvent, ConnectionExt as _, EventMask, ImageFormat, ImageOrder,
    MapState, Window,
};
use x11rb::protocol::xtest::ConnectionExt as _;
use x11rb::rust_connection::RustConnection;

// ─── сессия ──────────────────────────────────────────────────────────────────────────────

/// Что за графическая сессия у процесса — по его окружению (так её видит и сама программа:
/// другого честного источника у процесса нет). `x11_reachable` — удалось ли на самом деле
/// открыть X-сервер по `DISPLAY`.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Session {
    /// `x11` | `wayland` | `tty` | `unknown` — `XDG_SESSION_TYPE`, а без него — по тому,
    /// какие дисплеи названы.
    pub kind: String,
    pub display: Option<String>,
    pub wayland_display: Option<String>,
    /// `XDG_CURRENT_DESKTOP`: GNOME, KDE, fly (Astra), XFCE…
    pub desktop: Option<String>,
    /// X-сервер под Wayland-сессией — это XWayland: он видит только X-программы.
    pub xwayland: bool,
    pub x11_reachable: bool,
    /// Зачем X-сервер не открылся, если не открылся.
    pub x11_error: Option<String>,
}

fn env_nonempty(name: &str) -> Option<String> {
    std::env::var(name).ok().map(|v| v.trim().to_string()).filter(|v| !v.is_empty())
}

/// Разбор окружения — чистая функция, её держат стенды.
pub fn classify_session(
    session_type: Option<&str>,
    display: Option<&str>,
    wayland_display: Option<&str>,
) -> (String, bool) {
    let said = session_type.map(|s| s.trim().to_ascii_lowercase()).filter(|s| !s.is_empty());
    let kind = match said.as_deref() {
        Some("x11") => "x11".to_string(),
        Some("wayland") => "wayland".to_string(),
        Some("tty") => "tty".to_string(),
        // Нет слова от logind (ssh с пробросом X, Xvfb, запуск руками): судим по дисплеям.
        _ => match (wayland_display.is_some(), display.is_some()) {
            (true, _) => "wayland".to_string(),
            (false, true) => "x11".to_string(),
            (false, false) => "unknown".to_string(),
        },
    };
    let xwayland = kind == "wayland" && display.is_some();
    (kind, xwayland)
}

pub fn session() -> Session {
    let display = env_nonempty("DISPLAY");
    let wayland_display = env_nonempty("WAYLAND_DISPLAY");
    let (kind, xwayland) = classify_session(
        env_nonempty("XDG_SESSION_TYPE").as_deref(),
        display.as_deref(),
        wayland_display.as_deref(),
    );
    let (x11_reachable, x11_error) = match display.as_deref() {
        None => (false, Some("DISPLAY is not set: this process has no X server".to_string())),
        Some(_) => match X::connect() {
            Ok(_) => (true, None),
            Err(error) => (false, Some(format!("{error:#}"))),
        },
    };
    Session {
        kind,
        display,
        wayland_display,
        desktop: env_nonempty("XDG_CURRENT_DESKTOP"),
        xwayland,
        x11_reachable,
        x11_error,
    }
}

impl Session {
    /// Слова для владельца — чего нет и куда идти. Пусто — всё, что тело умеет, доступно.
    pub fn hints(&self) -> Vec<String> {
        let mut out = Vec::new();
        if !self.x11_reachable {
            out.push(format!(
                "no X server for this process ({}): windows, input and screenshots are \
                 unavailable; files, processes and the AT-SPI window tree may still work",
                self.x11_error.clone().unwrap_or_else(|| "unknown reason".into())
            ));
        }
        if self.kind == "wayland" {
            out.push(
                "this is a Wayland session: by Wayland's design the body sees, types into and \
                 photographs only X11 (XWayland) programs; native Wayland windows are missing \
                 from desktop.window.list and screenshots show them black or not at all. For \
                 full control choose the X11 session at login («GNOME на Xorg» / «Ubuntu on \
                 Xorg»); the window tree (AT-SPI) and element actions work in both"
                    .to_string(),
            );
        }
        out
    }
}

// ─── соединение ──────────────────────────────────────────────────────────────────────────

/// Атомы, которые тело спрашивает у каждого окна. Интернируются один раз на соединение.
#[derive(Debug, Clone, Copy)]
pub struct Atoms {
    pub net_client_list: Atom,
    pub net_client_list_stacking: Atom,
    pub net_active_window: Atom,
    pub net_wm_name: Atom,
    pub net_wm_pid: Atom,
    pub net_wm_state: Atom,
    pub net_wm_state_hidden: Atom,
    pub net_wm_window_type: Atom,
    pub net_frame_extents: Atom,
    pub net_supporting_wm_check: Atom,
    pub utf8_string: Atom,
    pub wm_state: Atom,
    types: [(Atom, &'static str); 13],
}

const WINDOW_TYPES: [&str; 13] = [
    "normal", "dialog", "utility", "toolbar", "menu", "splash", "dock", "desktop",
    "dropdown_menu", "popup_menu", "tooltip", "notification", "combo",
];

pub struct X {
    pub conn: RustConnection,
    pub root: Window,
    pub width: u16,
    pub height: u16,
    pub atoms: Atoms,
}

impl X {
    /// Соединение по `DISPLAY` процесса. Отказ — словами с причиной.
    pub fn connect() -> Result<Self> {
        let (conn, screen) = x11rb::connect(None).map_err(|error| {
            anyhow!(
                "cannot open the X server {:?}: {error}",
                std::env::var("DISPLAY").unwrap_or_default()
            )
        })?;
        let root_screen = conn
            .setup()
            .roots
            .get(screen)
            .context("the X server has no such screen")?
            .clone();
        let atoms = intern_atoms(&conn)?;
        Ok(Self {
            conn,
            root: root_screen.root,
            width: root_screen.width_in_pixels,
            height: root_screen.height_in_pixels,
            atoms,
        })
    }

    fn property(&self, window: Window, property: Atom, kind: impl Into<Atom>) -> Option<x11rb::protocol::xproto::GetPropertyReply> {
        let reply = self
            .conn
            .get_property(false, window, property, kind.into(), 0, 1 << 20)
            .ok()?
            .reply()
            .ok()?;
        (reply.type_ != u32::from(AtomEnum::NONE)).then_some(reply)
    }

    fn property32(&self, window: Window, property: Atom) -> Vec<u32> {
        self.property(window, property, AtomEnum::ANY)
            .and_then(|reply| reply.value32().map(|values| values.collect()))
            .unwrap_or_default()
    }

    fn property_text(&self, window: Window, property: Atom, kind: impl Into<Atom>) -> Option<String> {
        let reply = self.property(window, property, kind)?;
        if reply.format != 8 || reply.value.is_empty() {
            return None;
        }
        Some(String::from_utf8_lossy(&reply.value).trim_end_matches('\0').to_string())
    }

    /// Заголовок: `_NET_WM_NAME` (UTF-8), без него — `WM_NAME` (Latin-1 у старых
    /// программ; читаем байты как Latin-1, а не как UTF-8, чтобы «é» не стало «�»).
    pub fn title(&self, window: Window) -> Option<String> {
        if let Some(text) = self.property_text(window, self.atoms.net_wm_name, self.atoms.utf8_string) {
            return Some(text).filter(|t| !t.is_empty());
        }
        let reply = self.property(window, AtomEnum::WM_NAME.into(), AtomEnum::ANY)?;
        if reply.format != 8 {
            return None;
        }
        let text: String = if reply.type_ == self.atoms.utf8_string {
            String::from_utf8_lossy(&reply.value).into_owned()
        } else {
            reply.value.iter().map(|b| char::from(*b)).collect()
        };
        Some(text.trim_end_matches('\0').to_string()).filter(|t| !t.is_empty())
    }

    /// `WM_CLASS`: (instance, class) — две строки через ноль. Класс (`Gedit`, `firefox`)
    /// играет роль идентификатора пакета Mac и класса окна Windows.
    pub fn wm_class(&self, window: Window) -> Option<(String, String)> {
        let reply = self.property(window, AtomEnum::WM_CLASS.into(), AtomEnum::STRING)?;
        parse_wm_class(&reply.value)
    }

    /// pid владельца: `_NET_WM_PID`, а без него — XRes (сервер знает pid локального
    /// клиента сам). `None` — клиент удалённый или сервер XRes не умеет.
    pub fn pid(&self, window: Window) -> Option<u32> {
        if let Some(pid) = self.property32(window, self.atoms.net_wm_pid).first().copied()
            && pid > 0
        {
            return Some(pid);
        }
        let spec = ClientIdSpec { client: window, mask: ClientIdMask::LOCAL_CLIENT_PID };
        let reply = self.conn.res_query_client_ids(&[spec]).ok()?.reply().ok()?;
        reply
            .ids
            .iter()
            .find(|id| u32::from(id.spec.mask) & u32::from(ClientIdMask::LOCAL_CLIENT_PID) != 0)
            .and_then(|id| id.value.first().copied())
            .filter(|pid| *pid > 0)
    }

    pub fn window_type(&self, window: Window) -> Option<&'static str> {
        let values = self.property32(window, self.atoms.net_wm_window_type);
        let first = values.first()?;
        self.atoms
            .types
            .iter()
            .find(|(atom, _)| atom == first)
            .map(|(_, name)| *name)
    }

    fn hidden(&self, window: Window) -> bool {
        if self.property32(window, self.atoms.net_wm_state).contains(&self.atoms.net_wm_state_hidden) {
            return true;
        }
        // ICCCM: WM_STATE = IconicState (3) — свёрнуто, даже если менеджер не пишет
        // `_NET_WM_STATE_HIDDEN`.
        self.property32(window, self.atoms.wm_state).first() == Some(&3)
    }

    /// Прямоугольник окна С РАМКОЙ, в пикселях корня: (left, top, width, height).
    pub fn frame_rect(&self, window: Window) -> Option<(i32, i32, i32, i32)> {
        let geometry = self.conn.get_geometry(window).ok()?.reply().ok()?;
        let origin = self
            .conn
            .translate_coordinates(window, self.root, 0, 0)
            .ok()?
            .reply()
            .ok()?;
        let extents = self.property32(window, self.atoms.net_frame_extents);
        let (left, right, top, bottom) = match extents.as_slice() {
            [l, r, t, b, ..] => (*l as i32, *r as i32, *t as i32, *b as i32),
            _ => (0, 0, 0, 0),
        };
        Some((
            i32::from(origin.dst_x) - left,
            i32::from(origin.dst_y) - top,
            i32::from(geometry.width) + left + right,
            i32::from(geometry.height) + top + bottom,
        ))
    }

    fn viewable(&self, window: Window) -> bool {
        self.conn
            .get_window_attributes(window)
            .ok()
            .and_then(|cookie| cookie.reply().ok())
            .is_some_and(|attributes| attributes.map_state == MapState::VIEWABLE)
    }

    /// Окна верхнего уровня СВЕРХУ ВНИЗ. Источник — `_NET_CLIENT_LIST_STACKING` (снизу
    /// вверх по EWMH, поэтому переворачиваем), без него — `_NET_CLIENT_LIST` в порядке
    /// менеджера. Менеджера окон нет вовсе — пусто и `false`: без него у X-сервера нет
    /// понятия «окно программы», и выдумывать его из дерева окон значило бы показать
    /// меню и подсказки как окна.
    pub fn client_windows(&self) -> (Vec<Window>, bool) {
        let stacking = self.property32(self.root, self.atoms.net_client_list_stacking);
        if !stacking.is_empty() {
            return (stacking.into_iter().rev().collect(), true);
        }
        let list = self.property32(self.root, self.atoms.net_client_list);
        let managed = !list.is_empty() || self.window_manager().is_some();
        (list, managed)
    }

    /// Имя оконного менеджера (`_NET_SUPPORTING_WM_CHECK` → `_NET_WM_NAME`).
    pub fn window_manager(&self) -> Option<String> {
        let check = *self.property32(self.root, self.atoms.net_supporting_wm_check).first()?;
        self.property_text(check, self.atoms.net_wm_name, self.atoms.utf8_string)
            .or_else(|| Some("unnamed EWMH window manager".into()))
    }

    pub fn window_info(&self, window: Window, z_order: usize) -> Option<WindowInfo> {
        let (x, y, width, height) = self.frame_rect(window)?;
        let pid = self.pid(window);
        let class = self.wm_class(window);
        let kind = self.window_type(window);
        let hidden = self.hidden(window);
        let viewable = self.viewable(window);
        Some(WindowInfo {
            id: window,
            pid,
            title: self.title(window),
            class: class.as_ref().map(|(_, class)| class.clone()),
            instance: class.map(|(instance, _)| instance),
            window_type: kind,
            x,
            y,
            width,
            height,
            hidden,
            on_screen: viewable && !hidden,
            z_order,
        })
    }

    pub fn window_list(&self, visible_only: bool) -> Result<Vec<WindowInfo>> {
        let (windows, _) = self.client_windows();
        let mut out = Vec::with_capacity(windows.len());
        for window in windows {
            let Some(info) = self.window_info(window, out.len()) else {
                // Окно закрылось между списком и вопросом — его нет, это не ошибка.
                continue;
            };
            if visible_only && !info.on_screen {
                continue;
            }
            out.push(info);
        }
        Ok(out)
    }

    pub fn window_by_id(&self, id: Window) -> Option<WindowInfo> {
        let (windows, _) = self.client_windows();
        let z = windows.iter().position(|w| *w == id)?;
        self.window_info(id, z)
    }

    /// Активное окно по оконному менеджеру (`_NET_ACTIVE_WINDOW`) — то, что на Windows
    /// зовётся foreground. 0 и «нет свойства» — `None`.
    pub fn active(&self) -> Option<Window> {
        self.property32(self.root, self.atoms.net_active_window)
            .first()
            .copied()
            .filter(|window| *window != 0)
    }

    pub fn foreground(&self) -> Option<WindowInfo> {
        let id = self.active()?;
        self.window_by_id(id).or_else(|| self.window_info(id, 0))
    }

    /// Попросить менеджер окон поднять окно (EWMH `_NET_ACTIVE_WINDOW`, источник 2 —
    /// «пейджер»: менеджеры не режут такие просьбы защитой от кражи фокуса, как режут
    /// просьбы самих программ). Свёрнутое окно менеджер при этом разворачивает. Ответа у
    /// просьбы нет — успех проверяет вызывающий по `active()`.
    pub fn request_activate(&self, window: Window) -> Result<()> {
        let event = ClientMessageEvent::new(
            32,
            window,
            self.atoms.net_active_window,
            [2u32, x11rb::CURRENT_TIME, self.active().unwrap_or(0), 0, 0],
        );
        self.conn
            .send_event(
                false,
                self.root,
                EventMask::SUBSTRUCTURE_REDIRECT | EventMask::SUBSTRUCTURE_NOTIFY,
                event,
            )
            .context("send _NET_ACTIVE_WINDOW")?;
        self.conn.flush().context("flush X connection")?;
        Ok(())
    }

    // ─── экран ───────────────────────────────────────────────────────────────────────

    /// Весь корень и мониторы RandR. Корень и есть виртуальный экран: все мониторы X
    /// лежат в нём, и клик в любую точку корня попадает туда, где она видна.
    pub fn virtual_screen(&self) -> VirtualScreen {
        let monitors = self
            .conn
            .randr_get_monitors(self.root, true)
            .ok()
            .and_then(|cookie| cookie.reply().ok())
            .map(|reply| {
                reply
                    .monitors
                    .iter()
                    .map(|m| Monitor {
                        x: i32::from(m.x),
                        y: i32::from(m.y),
                        width: i32::from(m.width),
                        height: i32::from(m.height),
                        primary: m.primary,
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        VirtualScreen {
            left: 0,
            top: 0,
            width: i32::from(self.width),
            height: i32::from(self.height),
            displays: monitors.len().max(1),
            monitors,
        }
    }

    /// Куда X-сервер сейчас шлёт клавиатуру. Не то же, что «активное» окно менеджера:
    /// менеджер может считать окно активным, а фокус ввода — стоять на корне или на
    /// соседнем окне, и тогда набранное уходит не туда. `None` — фокуса нет (`None`/
    /// `PointerRoot` протокола).
    pub fn input_focus(&self) -> Option<Window> {
        let reply = self.conn.get_input_focus().ok()?.reply().ok()?;
        (reply.focus > 1).then_some(reply.focus)
    }

    /// Коды клавиш, которые сервер СЕЙЧАС считает нажатыми (`QueryKeymap`).
    pub fn keys_down(&self) -> Vec<u8> {
        let Some(reply) = self.conn.query_keymap().ok().and_then(|c| c.reply().ok()) else {
            return Vec::new();
        };
        pressed_keycodes(&reply.keys)
    }

    pub fn pointer(&self) -> Option<(i32, i32)> {
        let reply = self.conn.query_pointer(self.root).ok()?.reply().ok()?;
        Some((i32::from(reply.root_x), i32::from(reply.root_y)))
    }

    /// Пиксели прямоугольника корня — плотный BGRA. Прямоугольник уже обязан лежать в
    /// корне: GetImage за краем отвечает ошибкой BadMatch, и лучше сказать, что за краем,
    /// до запроса (см. `clip_to_root`).
    pub fn capture(&self, left: i32, top: i32, width: i32, height: i32) -> Result<Vec<u8>> {
        if width <= 0 || height <= 0 {
            bail!("capture rectangle must have positive width and height")
        }
        let reply = self
            .conn
            .get_image(
                ImageFormat::Z_PIXMAP,
                self.root,
                i16::try_from(left).context("capture left exceeds the X coordinate range")?,
                i16::try_from(top).context("capture top exceeds the X coordinate range")?,
                u16::try_from(width).context("capture width exceeds the X range")?,
                u16::try_from(height).context("capture height exceeds the X range")?,
                !0,
            )
            .context("GetImage request")?
            .reply()
            .context("GetImage: the X server refused the rectangle")?;
        let setup = self.conn.setup();
        let format = setup
            .pixmap_formats
            .iter()
            .find(|format| format.depth == reply.depth)
            .with_context(|| format!("the X server has no pixmap format for depth {}", reply.depth))?;
        let visual = setup
            .roots
            .iter()
            .flat_map(|screen| screen.allowed_depths.iter())
            .flat_map(|depth| depth.visuals.iter())
            .find(|visual| visual.visual_id == reply.visual)
            .context("the X server did not describe the visual of the captured image")?;
        let layout = PixelFormat {
            bits_per_pixel: format.bits_per_pixel,
            scanline_pad: format.scanline_pad,
            little_endian: setup.image_byte_order == ImageOrder::LSB_FIRST,
            red_mask: visual.red_mask,
            green_mask: visual.green_mask,
            blue_mask: visual.blue_mask,
        };
        to_bgra(&reply.data, width as usize, height as usize, layout)
    }

    // ─── ввод ────────────────────────────────────────────────────────────────────────

    pub fn keymap(&self) -> Result<Keymap> {
        let setup = self.conn.setup();
        let (min, max) = (setup.min_keycode, setup.max_keycode);
        let reply = self
            .conn
            .get_keyboard_mapping(min, max - min + 1)
            .context("GetKeyboardMapping")?
            .reply()
            .context("GetKeyboardMapping reply")?;
        Ok(Keymap {
            min,
            per: reply.keysyms_per_keycode,
            syms: reply.keysyms,
        })
    }

    /// Действующая группа раскладки XKB (0 — первая: обычно латиница; 1 — вторая: у
    /// «us,ru» это русская). `None` — XKB у сервера нет: тогда считаем первую.
    pub fn xkb_group(&self) -> Option<u8> {
        self.conn.xkb_use_extension(1, 0).ok()?.reply().ok()?;
        let state = self
            .conn
            .xkb_get_state(xkb::ID::USE_CORE_KBD.into())
            .ok()?
            .reply()
            .ok()?;
        Some(u8::from(state.group))
    }

    /// Переназначить одну клавишу на символ (все столбцы — он, чтобы ни Shift, ни
    /// активная раскладка не превратили его в другой) или вернуть её в пустоту.
    pub fn remap(&self, keycode: u8, per: u8, keysym: u32) -> Result<()> {
        let syms = vec![keysym; usize::from(per.max(1))];
        self.conn
            .change_keyboard_mapping(1, keycode, per.max(1), &syms)
            .context("ChangeKeyboardMapping")?;
        self.sync()
    }

    /// Дождаться, что сервер обработал всё посланное (круг до сервера и обратно).
    pub fn sync(&self) -> Result<()> {
        self.conn
            .get_input_focus()
            .context("sync request")?
            .reply()
            .context("sync reply")?;
        Ok(())
    }

    pub fn fake_key(&self, keycode: u8, down: bool) -> Result<()> {
        let kind = if down { x11rb::protocol::xproto::KEY_PRESS_EVENT } else { x11rb::protocol::xproto::KEY_RELEASE_EVENT };
        self.conn
            .xtest_fake_input(kind, keycode, x11rb::CURRENT_TIME, x11rb::NONE, 0, 0, 0)
            .context("XTestFakeInput (key)")?;
        Ok(())
    }

    pub fn fake_button(&self, button: u8, down: bool) -> Result<()> {
        let kind = if down { x11rb::protocol::xproto::BUTTON_PRESS_EVENT } else { x11rb::protocol::xproto::BUTTON_RELEASE_EVENT };
        self.conn
            .xtest_fake_input(kind, button, x11rb::CURRENT_TIME, x11rb::NONE, 0, 0, 0)
            .context("XTestFakeInput (button)")?;
        Ok(())
    }

    pub fn fake_motion(&self, x: i32, y: i32) -> Result<()> {
        self.conn
            .xtest_fake_input(
                x11rb::protocol::xproto::MOTION_NOTIFY_EVENT,
                0,
                x11rb::CURRENT_TIME,
                self.root,
                x.clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16,
                y.clamp(i32::from(i16::MIN), i32::from(i16::MAX)) as i16,
                0,
            )
            .context("XTestFakeInput (motion)")?;
        Ok(())
    }

    pub fn flush(&self) -> Result<()> {
        self.conn.flush().context("flush X connection")?;
        Ok(())
    }

    /// Есть ли у сервера XTest — без него ввод невозможен вовсе, и отказ должен прийти
    /// до первого события, а не молчанием.
    pub fn has_xtest(&self) -> bool {
        self.conn
            .extension_information(x11rb::protocol::xtest::X11_EXTENSION_NAME)
            .ok()
            .flatten()
            .is_some()
    }
}

fn intern_atoms(conn: &RustConnection) -> Result<Atoms> {
    let names: [&[u8]; 12] = [
        b"_NET_CLIENT_LIST",
        b"_NET_CLIENT_LIST_STACKING",
        b"_NET_ACTIVE_WINDOW",
        b"_NET_WM_NAME",
        b"_NET_WM_PID",
        b"_NET_WM_STATE",
        b"_NET_WM_STATE_HIDDEN",
        b"_NET_WM_WINDOW_TYPE",
        b"_NET_FRAME_EXTENTS",
        b"_NET_SUPPORTING_WM_CHECK",
        b"UTF8_STRING",
        b"WM_STATE",
    ];
    // Все просьбы сначала, ответы потом: двенадцать кругов до сервера стали бы одним.
    let cookies = names
        .iter()
        .map(|name| conn.intern_atom(false, name))
        .collect::<Result<Vec<_>, _>>()
        .context("InternAtom")?;
    let mut atoms = Vec::with_capacity(names.len());
    for cookie in cookies {
        atoms.push(cookie.reply().context("InternAtom reply")?.atom);
    }
    let type_cookies = WINDOW_TYPES
        .iter()
        .map(|name| {
            conn.intern_atom(false, format!("_NET_WM_WINDOW_TYPE_{}", name.to_ascii_uppercase()).as_bytes())
        })
        .collect::<Result<Vec<_>, _>>()
        .context("InternAtom (window types)")?;
    let mut types = [(0u32, ""); 13];
    for (slot, (cookie, name)) in types.iter_mut().zip(type_cookies.into_iter().zip(WINDOW_TYPES)) {
        *slot = (cookie.reply().context("InternAtom reply")?.atom, name);
    }
    Ok(Atoms {
        net_client_list: atoms[0],
        net_client_list_stacking: atoms[1],
        net_active_window: atoms[2],
        net_wm_name: atoms[3],
        net_wm_pid: atoms[4],
        net_wm_state: atoms[5],
        net_wm_state_hidden: atoms[6],
        net_wm_window_type: atoms[7],
        net_frame_extents: atoms[8],
        net_supporting_wm_check: atoms[9],
        utf8_string: atoms[10],
        wm_state: atoms[11],
        types,
    })
}

/// Битовая карта `QueryKeymap` (32 байта, бит на код клавиши) → нажатые коды. Чистая.
pub fn pressed_keycodes(keys: &[u8; 32]) -> Vec<u8> {
    let mut out = Vec::new();
    for (byte_index, byte) in keys.iter().enumerate() {
        for bit in 0..8 {
            if byte & (1 << bit) != 0 {
                out.push((byte_index * 8 + bit) as u8);
            }
        }
    }
    out
}

impl Keymap {
    /// Первый символ клавиши — для слов «что зажато».
    pub fn first_keysym(&self, keycode: u8) -> Option<u32> {
        self.row(keycode).and_then(|row| row.first().copied()).filter(|sym| *sym != NO_SYMBOL)
    }
}

/// `WM_CLASS` — «instance\0class\0». Чистая функция.
pub fn parse_wm_class(raw: &[u8]) -> Option<(String, String)> {
    let mut parts = raw.split(|b| *b == 0).filter(|part| !part.is_empty());
    let instance = String::from_utf8_lossy(parts.next()?).into_owned();
    let class = parts
        .next()
        .map(|part| String::from_utf8_lossy(part).into_owned())
        .unwrap_or_else(|| instance.clone());
    Some((instance, class))
}

// ─── окна ────────────────────────────────────────────────────────────────────────────────

/// Одно окно программы глазами оконного менеджера. Координаты — пиксели корня, с рамкой.
#[derive(Debug, Clone, Serialize)]
pub struct WindowInfo {
    /// Идентификатор X-окна (клиентского, не рамки) — в JSON едет как `hwnd` `0x…`.
    pub id: Window,
    pub pid: Option<u32>,
    pub title: Option<String>,
    /// Класс `WM_CLASS` (`Gedit`, `firefox`).
    pub class: Option<String>,
    pub instance: Option<String>,
    /// `_NET_WM_WINDOW_TYPE` словом (`normal`, `dialog`, `dock`…); `None` — не сказано,
    /// что по EWMH значит обычное окно.
    pub window_type: Option<&'static str>,
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    /// Свёрнуто (`_NET_WM_STATE_HIDDEN` или ICCCM IconicState).
    pub hidden: bool,
    pub on_screen: bool,
    pub z_order: usize,
}

impl WindowInfo {
    /// Обычное окно программы — то, что на Mac слой 0. Док, рабочий стол, всплывающие
    /// меню и подсказки — нет.
    pub fn is_ordinary(&self) -> bool {
        matches!(self.window_type, None | Some("normal" | "dialog" | "utility" | "toolbar" | "splash"))
            && self.width >= 1
            && self.height >= 1
    }

    /// «Слой» в духе Mac: 0 — обычные окна, иначе 1. Число ради одинаковой формы строки
    /// на трёх ОС; правда — поле `window_type`.
    pub fn layer(&self) -> i32 {
        if self.is_ordinary() { 0 } else { 1 }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Monitor {
    pub x: i32,
    pub y: i32,
    pub width: i32,
    pub height: i32,
    pub primary: bool,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct VirtualScreen {
    pub left: i32,
    pub top: i32,
    pub width: i32,
    pub height: i32,
    pub displays: usize,
    pub monitors: Vec<Monitor>,
}

/// Подрезать прямоугольник к корню: (left, top, width, height) внутри или `None`, если
/// пересечения нет. Чистая функция.
pub fn clip_to_root(
    left: i32,
    top: i32,
    width: i32,
    height: i32,
    root_width: i32,
    root_height: i32,
) -> Option<(i32, i32, i32, i32)> {
    let l = left.max(0);
    let t = top.max(0);
    let r = left.saturating_add(width).min(root_width);
    let b = top.saturating_add(height).min(root_height);
    (r > l && b > t).then_some((l, t, r - l, b - t))
}

// ─── пиксели ─────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct PixelFormat {
    pub bits_per_pixel: u8,
    pub scanline_pad: u8,
    pub little_endian: bool,
    pub red_mask: u32,
    pub green_mask: u32,
    pub blue_mask: u32,
}

fn channel(pixel: u32, mask: u32) -> u8 {
    if mask == 0 {
        return 0;
    }
    let shift = mask.trailing_zeros();
    let bits = (mask >> shift).count_ones();
    let value = (pixel & mask) >> shift;
    if bits >= 8 {
        (value >> (bits - 8)) as u8
    } else {
        // Меньше восьми бит на канал (16-битный цвет) — растянуть до 0..255.
        ((value * 255) / ((1u32 << bits) - 1)) as u8
    }
}

/// ZPixmap из GetImage в плотный BGRA (то, что ест `write_png`). Строки выровнены по
/// `scanline_pad`; каналы — по маскам визуала, а не «как обычно»: формат читается, а не
/// предполагается (тот же закон, что у Mac-ветки `pixel_layout`). Чистая функция.
pub fn to_bgra(data: &[u8], width: usize, height: usize, format: PixelFormat) -> Result<Vec<u8>> {
    let bpp = usize::from(format.bits_per_pixel);
    if !matches!(bpp, 16 | 24 | 32) {
        bail!("capture image has {bpp} bits per pixel; only 16, 24 and 32 are handled")
    }
    let pad = usize::from(format.scanline_pad.max(8));
    let row_bits = width.checked_mul(bpp).context("capture row size overflow")?;
    let stride = row_bits.div_ceil(pad) * pad / 8;
    let needed = stride.checked_mul(height).context("capture buffer size overflow")?;
    if data.len() < needed {
        bail!("capture buffer has {} bytes; {needed} needed for {width}x{height}", data.len())
    }
    let bytes_per_pixel = bpp / 8;
    let mut out = Vec::with_capacity(width * height * 4);
    for row in 0..height {
        let line = &data[row * stride..row * stride + width * bytes_per_pixel];
        for pixel in line.chunks_exact(bytes_per_pixel) {
            let value = match (bytes_per_pixel, format.little_endian) {
                (4, true) => u32::from_le_bytes([pixel[0], pixel[1], pixel[2], pixel[3]]),
                (4, false) => u32::from_be_bytes([pixel[0], pixel[1], pixel[2], pixel[3]]),
                (3, true) => u32::from_le_bytes([pixel[0], pixel[1], pixel[2], 0]),
                (3, false) => u32::from_be_bytes([0, pixel[0], pixel[1], pixel[2]]),
                (2, true) => u32::from(u16::from_le_bytes([pixel[0], pixel[1]])),
                (2, false) => u32::from(u16::from_be_bytes([pixel[0], pixel[1]])),
                _ => unreachable!("bytes per pixel checked above"),
            };
            out.extend_from_slice(&[
                channel(value, format.blue_mask),
                channel(value, format.green_mask),
                channel(value, format.red_mask),
                255,
            ]);
        }
    }
    Ok(out)
}

// ─── клавиатура ──────────────────────────────────────────────────────────────────────────

/// Раскладка сервера: `per` символов на клавишу, начиная с `min`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Keymap {
    pub min: u8,
    pub per: u8,
    pub syms: Vec<u32>,
}

pub const NO_SYMBOL: u32 = 0;

impl Keymap {
    fn row(&self, keycode: u8) -> Option<&[u32]> {
        let per = usize::from(self.per);
        let index = usize::from(keycode.checked_sub(self.min)?) * per;
        self.syms.get(index..index + per)
    }

    fn keycodes(&self) -> impl Iterator<Item = u8> + '_ {
        let count = self.syms.len() / usize::from(self.per.max(1));
        (0..count).filter_map(move |i| u8::try_from(usize::from(self.min) + i).ok())
    }

    /// Клавиша для символа в ПЕРВОЙ группе: (код, нужен ли Shift). Сочетания клавиш
    /// (`ctrl+c`) ищутся так: программы сверяют их по коду клавиши, и под любой активной
    /// раскладкой это та же клавиша C.
    pub fn find(&self, keysym: u32) -> Option<(u8, bool)> {
        // Сначала без Shift, по всем клавишам — только потом со Shift: иначе «1» нашлась
        // бы как Shift на какой-нибудь цифровой клавише с редкой раскладкой.
        for shifted in [false, true] {
            let column = usize::from(shifted);
            for keycode in self.keycodes() {
                if self.row(keycode).and_then(|row| row.get(column)) == Some(&keysym) {
                    return Some((keycode, shifted));
                }
            }
        }
        None
    }

    /// Клавиша для символа в ДЕЙСТВУЮЩЕЙ группе раскладки: (код, нужен ли Shift). В
    /// основной раскладке ядра X группа g — столбцы 2g и 2g+1 (первая группа — 0 и 1).
    /// Нашлась — знак набирается настоящей клавишей, как у человека, и раскладку трогать
    /// не надо; не нашлась (кириллица при латинской группе) — запасная клавиша.
    pub fn find_in_group(&self, keysym: u32, group: u8) -> Option<(u8, bool)> {
        let base = usize::from(group) * 2;
        for shifted in [false, true] {
            let column = base + usize::from(shifted);
            if column >= usize::from(self.per) {
                return None;
            }
            for keycode in self.keycodes() {
                if self.row(keycode).and_then(|row| row.get(column)) == Some(&keysym) {
                    return Some((keycode, shifted));
                }
            }
        }
        None
    }

    /// Свободная клавиша, которую можно временно переназначить на символ: у неё нет ни
    /// одного символа. Берём с верхнего края — туда настоящие клавиатуры не достают.
    pub fn scratch(&self) -> Option<u8> {
        let mut candidates: Vec<u8> = self
            .keycodes()
            .filter(|keycode| {
                self.row(*keycode)
                    .is_some_and(|row| row.iter().all(|sym| *sym == NO_SYMBOL))
            })
            .collect();
        candidates.pop()
    }
}

/// Символ X (keysym) для знака Юникода: Latin-1 — сам код, остальное — `0x01000000 | код`
/// (так X11 записывает любой знак Юникода). Чистая функция.
pub fn keysym_for_char(value: char) -> u32 {
    let code = u32::from(value);
    if (0x20..=0x7e).contains(&code) || (0xa0..=0xff).contains(&code) {
        code
    } else {
        0x0100_0000 | code
    }
}

// ─── процессы ────────────────────────────────────────────────────────────────────────────

/// Строка процесса из `/proc`: pid, родитель, uid, имя. Путь к бинарю — отдельно
/// (`process_path`): у чужих процессов без прав он не читается, и это не повод терять
/// саму строку.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProcRow {
    pub pid: u32,
    pub ppid: u32,
    pub uid: u32,
    pub comm: String,
}

/// Поля `/proc/<pid>/stat`: имя в скобках может содержать пробелы и скобки, поэтому
/// режем по ПОСЛЕДНЕЙ «)». Отдаёт (comm, ppid, starttime в тиках). Чистая функция.
pub fn parse_stat(text: &str) -> Option<(String, u32, u64)> {
    let open = text.find('(')?;
    let close = text.rfind(')')?;
    let comm = text.get(open + 1..close)?.to_string();
    let rest: Vec<&str> = text.get(close + 1..)?.split_whitespace().collect();
    // rest[0] — состояние, rest[1] — ppid, … rest[19] — starttime (поле 22 по man proc).
    let ppid = rest.get(1)?.parse().ok()?;
    let start = rest.get(19)?.parse().ok()?;
    Some((comm, ppid, start))
}

/// `Uid:` из `/proc/<pid>/status` — реальный uid (первое число). Чистая функция.
pub fn parse_status_uid(text: &str) -> Option<u32> {
    text.lines()
        .find_map(|line| line.strip_prefix("Uid:"))
        .and_then(|rest| rest.split_whitespace().next())
        .and_then(|uid| uid.parse().ok())
}

fn boot_time() -> Option<u64> {
    let text = std::fs::read_to_string("/proc/stat").ok()?;
    text.lines()
        .find_map(|line| line.strip_prefix("btime "))
        .and_then(|value| value.trim().parse().ok())
}

fn clock_ticks() -> u64 {
    let ticks = unsafe { libc::sysconf(libc::_SC_CLK_TCK) };
    if ticks > 0 { ticks as u64 } else { 100 }
}

pub fn proc_rows() -> Vec<(ProcRow, Option<u64>)> {
    let btime = boot_time();
    let ticks = clock_ticks();
    let mut rows = Vec::new();
    let Ok(entries) = std::fs::read_dir("/proc") else {
        return rows;
    };
    for entry in entries.flatten() {
        let Some(pid) = entry.file_name().to_str().and_then(|name| name.parse::<u32>().ok()) else {
            continue;
        };
        let Ok(stat) = std::fs::read_to_string(entry.path().join("stat")) else {
            continue;
        };
        let Some((comm, ppid, start)) = parse_stat(&stat) else {
            continue;
        };
        let uid = std::fs::read_to_string(entry.path().join("status"))
            .ok()
            .and_then(|text| parse_status_uid(&text))
            .unwrap_or(u32::MAX);
        let started = btime.map(|btime| btime + start / ticks);
        rows.push((ProcRow { pid, ppid, uid, comm }, started));
    }
    rows.sort_by_key(|(row, _)| row.pid);
    rows
}

/// Время старта процесса — Unix-секунды (`btime` + `starttime` / тики). Нужно отпечатку
/// окна, как на Mac; мёртвый pid — `None`.
pub fn process_started(pid: u32) -> Option<u64> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    let (_, _, start) = parse_stat(&stat)?;
    Some(boot_time()? + start / clock_ticks())
}

/// Путь исполняемого файла (`/proc/<pid>/exe`); чужие процессы без прав — `None`.
pub fn process_path(pid: u32) -> Option<String> {
    std::fs::read_link(format!("/proc/{pid}/exe"))
        .ok()
        .map(|path| path.to_string_lossy().trim_end_matches(" (deleted)").to_string())
}

pub fn process_name(pid: u32) -> Option<String> {
    std::fs::read_to_string(format!("/proc/{pid}/comm"))
        .ok()
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty())
}

// ─── сторож родителя ─────────────────────────────────────────────────────────────────────

/// Что сделать, если родитель умер, — ДО `process::exit` (см. `mac::on_parent_death`):
/// отпустить зажатые кнопки и модификаторы.
static ON_PARENT_DEATH: std::sync::Mutex<Vec<Box<dyn Fn() + Send + Sync>>> =
    std::sync::Mutex::new(Vec::new());

pub fn on_parent_death(hook: Box<dyn Fn() + Send + Sync>) {
    match ON_PARENT_DEATH.lock() {
        Ok(mut hooks) => hooks.push(hook),
        Err(poisoned) => poisoned.into_inner().push(hook),
    }
}

fn run_parent_death_hooks() {
    let hooks = match ON_PARENT_DEATH.lock() {
        Ok(hooks) => hooks,
        Err(poisoned) => poisoned.into_inner(),
    };
    for hook in hooks.iter() {
        hook();
    }
}

/// Умер родитель — уходим. Осиротевший процесс Linux переезжает к init или к
/// «subreaper» (systemd --user) — ppid меняется, это и есть сигнал; тот же приём, что у
/// Mac. `PR_SET_PDEATHSIG` здесь не годится: он срабатывает на смерть ПОТОКА родителя, а
/// не процесса, и убивал бы тело, когда у движка заканчивается рабочий поток.
pub fn watch_parent(name: &'static str) {
    let parent = unsafe { libc::getppid() };
    if parent <= 1 {
        return;
    }
    let spawned = std::thread::Builder::new()
        .name(format!("{name}-parent-watch"))
        .spawn(move || {
            loop {
                std::thread::sleep(Duration::from_secs(1));
                let now = unsafe { libc::getppid() };
                if now != parent {
                    tracing::warn!(
                        "{name}: родитель {parent} исчез (теперь ppid {now}) — завершаюсь вместе с ним"
                    );
                    run_parent_death_hooks();
                    std::process::exit(0);
                }
            }
        });
    if let Err(error) = spawned {
        tracing::warn!("{name}: сторож родителя не поднялся: {error}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn session_kind_comes_from_logind_then_from_displays() {
        assert_eq!(classify_session(Some("x11"), Some(":0"), None), ("x11".into(), false));
        assert_eq!(classify_session(Some("wayland"), Some(":0"), Some("wayland-0")), ("wayland".into(), true));
        assert_eq!(classify_session(Some("wayland"), None, Some("wayland-0")), ("wayland".into(), false));
        assert_eq!(classify_session(None, Some(":99"), None), ("x11".into(), false));
        assert_eq!(classify_session(Some(""), None, Some("wayland-1")), ("wayland".into(), false));
        assert_eq!(classify_session(None, None, None), ("unknown".into(), false));
        assert_eq!(classify_session(Some("tty"), None, None), ("tty".into(), false));
    }

    #[test]
    fn wm_class_has_instance_and_class() {
        assert_eq!(parse_wm_class(b"gedit\0Gedit\0"), Some(("gedit".into(), "Gedit".into())));
        assert_eq!(parse_wm_class(b"xterm\0"), Some(("xterm".into(), "xterm".into())));
        assert_eq!(parse_wm_class(b""), None);
    }

    #[test]
    fn stat_is_cut_at_the_last_parenthesis() {
        // Имя с пробелом и скобкой — живой случай (`(sd-pam)`, `Web Content`).
        let text = "4242 (tricky) name) S 17 4242 4242 0 -1 4194560 1 0 0 0 0 0 0 0 20 0 1 0 98765 100 1 18446744073709551615";
        assert_eq!(parse_stat(text), Some(("tricky) name".into(), 17, 98765)));
        assert_eq!(parse_stat("garbage"), None);
    }

    #[test]
    fn status_uid_is_the_real_uid() {
        let text = "Name:\tbash\nUmask:\t0022\nUid:\t1000\t1000\t1000\t1000\nGid:\t1000\n";
        assert_eq!(parse_status_uid(text), Some(1000));
        assert_eq!(parse_status_uid("Name: x\n"), None);
    }

    #[test]
    fn rectangles_are_clipped_to_the_root() {
        assert_eq!(clip_to_root(-10, -10, 50, 50, 1920, 1080), Some((0, 0, 40, 40)));
        assert_eq!(clip_to_root(1900, 1000, 100, 100, 1920, 1080), Some((1900, 1000, 20, 80)));
        assert_eq!(clip_to_root(2000, 0, 10, 10, 1920, 1080), None);
        assert_eq!(clip_to_root(0, 0, 1920, 1080, 1920, 1080), Some((0, 0, 1920, 1080)));
    }

    #[test]
    fn pixels_follow_the_visual_masks_and_the_scanline_pad() {
        // 2×1, 32 бита, младший байт первым: B G R X — обычный TrueColor.
        let format = PixelFormat {
            bits_per_pixel: 32,
            scanline_pad: 32,
            little_endian: true,
            red_mask: 0x00ff_0000,
            green_mask: 0x0000_ff00,
            blue_mask: 0x0000_00ff,
        };
        let data = [1, 2, 3, 0, 10, 20, 30, 0];
        assert_eq!(to_bgra(&data, 2, 1, format).unwrap(), vec![1, 2, 3, 255, 10, 20, 30, 255]);
        // 16 бит 5-6-5: чистый красный растягивается до 255, а не до 248.
        let format = PixelFormat {
            bits_per_pixel: 16,
            scanline_pad: 32,
            little_endian: true,
            red_mask: 0xf800,
            green_mask: 0x07e0,
            blue_mask: 0x001f,
        };
        // Одна точка, строка выровнена до 4 байт (2 байта пикселя + 2 заполнения).
        let data = [0x00, 0xf8, 0xaa, 0xaa];
        assert_eq!(to_bgra(&data, 1, 1, format).unwrap(), vec![0, 0, 255, 255]);
        // Короткий буфер — отказ словами, не паника.
        assert!(to_bgra(&[0, 0], 2, 2, format).is_err());
    }

    #[test]
    fn keymap_finds_unshifted_first_and_a_free_key_from_the_top() {
        // min=8, по 2 символа: 8 → a/A, 9 → 1/!, 10 → пусто, 11 → пусто.
        let map = Keymap { min: 8, per: 2, syms: vec![0x61, 0x41, 0x31, 0x21, 0, 0, 0, 0] };
        assert_eq!(map.find(0x61), Some((8, false)));
        assert_eq!(map.find(0x41), Some((8, true)));
        assert_eq!(map.find(0x21), Some((9, true)));
        assert_eq!(map.find(0x44f), None);
        assert_eq!(map.scratch(), Some(11));
    }

    #[test]
    fn a_character_is_looked_up_in_the_active_group_only() {
        // min=8, по 4 символа: 8 → a/A | ф/Ф, 9 → пусто.
        let map = Keymap { min: 8, per: 4, syms: vec![0x61, 0x41, 0x0100_0444, 0x0100_0424, 0, 0, 0, 0] };
        assert_eq!(map.find_in_group(0x61, 0), Some((8, false)));
        assert_eq!(map.find_in_group(0x41, 0), Some((8, true)));
        // При русской группе «a» на этой клавише НЕ набрать — будет «ф»: только запасная.
        assert_eq!(map.find_in_group(0x61, 1), None);
        assert_eq!(map.find_in_group(0x0100_0444, 1), Some((8, false)));
        // Группы, которой нет в столбцах, — нет.
        assert_eq!(map.find_in_group(0x61, 2), None);
    }

    #[test]
    fn the_keymap_bitmap_names_pressed_keycodes() {
        let mut keys = [0u8; 32];
        keys[4] = 0b0010_0000; // код 37 — левый Control у evdev
        keys[31] = 0b1000_0000; // код 255
        assert_eq!(pressed_keycodes(&keys), vec![37, 255]);
        assert!(pressed_keycodes(&[0u8; 32]).is_empty());
    }

    #[test]
    fn unicode_keysyms_follow_the_x11_rule() {
        assert_eq!(keysym_for_char('a'), 0x61);
        assert_eq!(keysym_for_char('é'), 0xe9);
        assert_eq!(keysym_for_char('я'), 0x0100_044f);
        assert_eq!(keysym_for_char('😀'), 0x0101_f600);
    }

    #[test]
    fn ordinary_windows_are_normal_dialog_or_untyped() {
        let mut window = WindowInfo {
            id: 1,
            pid: None,
            title: None,
            class: None,
            instance: None,
            window_type: None,
            x: 0,
            y: 0,
            width: 10,
            height: 10,
            hidden: false,
            on_screen: true,
            z_order: 0,
        };
        assert!(window.is_ordinary());
        window.window_type = Some("dock");
        assert!(!window.is_ordinary());
        assert_eq!(window.layer(), 1);
        window.window_type = Some("dialog");
        assert!(window.is_ordinary());
    }
}
