//! Дерево окна через AT-SPI2 (Linux) — то, что на Windows делает `uia.rs` через COM, а на
//! macOS — `ax.rs` через Accessibility. Аналог UIA на Linux: GTK, Qt (с мостом доступности),
//! Chromium/Electron (с включённой доступностью), LibreOffice, Firefox отдают по D-Bus
//! дерево элементов с ролями, именами, состояниями и действиями.
//!
//! Устройство — зеркало `ax.rs`, и это не эстетика: дерево Праксис (`read_window`,
//! `find_elements`, `act_element`) читает формы JSON и слова отказов, выученные на Windows,
//! и не должно заметить, что под ним другая ОС. Те же пределы, обход в ширину, отбор
//! (`element::Selector`), квитанции (`element::receipt` …) и рендер (`uia::render`).
//!
//! Файл разрезан на две части:
//! * чистая — таблица ролей, состояния, обход, отбор, описание узла, действие, слова
//!   отказов. Элемент дерева — трейт [`Accessible`], и стенды гоняют её на подставном дереве
//!   на любой ОС (модуль собирается и под `test`);
//! * живая ([`live`], только Linux) — D-Bus через `zbus` (чистый Rust, без libdbus): шина
//!   доступности (`org.a11y.Bus.GetAddress`), реестр приложений, объекты `org.a11y.atspi.*`.
//!
//! ⚠ Каждый вопрос к элементу — межпроцессный вызов в чужое приложение. У соединения стоит
//! срок на вызов (`method_timeout`), чтобы зависшее приложение не вешало тело; обход идёт на
//! своём потоке (`uia.rs`, linux-ветка `platform`), как COM у Windows.
//!
//! ⚠ Доступность на Linux бывает ВЫКЛЮЧЕНА (`org.a11y.Status.IsEnabled = false`): GTK3 всё
//! равно отдаёт дерево, а Qt и Chromium — нет. Живая часть включает её перед чтением и
//! говорит об этом вслух: программы, запущенные раньше, могут показать дерево только после
//! перезапуска.
#![cfg(any(target_os = "linux", test))]
#![cfg_attr(not(target_os = "linux"), allow(dead_code))]

use std::collections::VecDeque;
use std::fmt;
use std::time::Instant;

use serde_json::{Map, Value, json};

use crate::element::{Act, NodeView, Selector};

// ─── роли ───────────────────────────────────────────────────────────────────────────────

/// Имя роли AT-SPI как его отдают разные тулкиты: «push button», «push_button», «PushButton»
/// — к одному виду: строчные, слова через пробел.
pub fn normalize_role(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len() + 4);
    let mut previous_lower = false;
    for value in raw.trim().chars() {
        if value == '_' || value == '-' || value == ' ' {
            if !out.ends_with(' ') && !out.is_empty() {
                out.push(' ');
            }
            previous_lower = false;
            continue;
        }
        if value.is_uppercase() && previous_lower && !out.ends_with(' ') {
            out.push(' ');
        }
        previous_lower = value.is_lowercase();
        out.extend(value.to_lowercase());
    }
    out.trim().to_string()
}

/// Роль словами словаря UIA — ТЕМИ ЖЕ словами, что печатает `uia::role_name` на Windows
/// и `ax::role_of` на Mac (`check_box`, `menu_item`: с подчёркиванием). Отбор `role` в дереве
/// Праксис выучен на них. Сырое имя роли AT-SPI едет рядом полем `atspi_role`. Роль
/// ищется по ИМЕНИ (`GetRoleName`), а не по номеру: имена стабильны между версиями
/// at-spi2-core, номера — дописываются в конец и в разных сборках бывают разными.
pub fn role_of(role_name: Option<&str>) -> &'static str {
    let Some(raw) = role_name.filter(|r| !r.trim().is_empty()) else {
        return "unknown";
    };
    match normalize_role(raw).as_str() {
        "push button" | "button" | "push button menu" | "toggle button" => "button",
        "check box" | "switch" => "check_box",
        "radio button" => "radio_button",
        "menu item" | "check menu item" | "radio menu item" | "tearoff menu item" => "menu_item",
        "combo box" => "combo_box",
        "text" | "entry" | "password text" | "date editor" | "autocomplete" | "edit bar" => "edit",
        "label" | "static" | "accelerator label" | "caption" | "heading" | "paragraph"
        | "description term" | "description value" | "footnote" => "text",
        "link" => "hyperlink",
        "image" | "icon" | "desktop icon" | "image map" | "animation" => "image",
        "list" | "list box" | "description list" => "list",
        "list item" => "list_item",
        "menu" | "popup menu" => "menu",
        "menu bar" => "menu_bar",
        "page tab list" => "tab",
        "page tab" => "tab_item",
        "progress bar" | "level bar" => "progress_bar",
        "scroll bar" => "scroll_bar",
        "slider" | "dial" => "slider",
        "spin button" => "spinner",
        "status bar" => "status_bar",
        "tool bar" => "tool_bar",
        "tool tip" => "tool_tip",
        "tree" | "tree table" => "tree",
        "tree item" => "tree_item",
        "table" | "document spreadsheet" => "table",
        "table cell" | "table row" => "data_item",
        "column header" | "table column header" | "row header" | "table row header" => "header_item",
        "header" => "header",
        "frame" | "window" | "dialog" | "alert" | "file chooser" | "color chooser"
        | "font chooser" | "internal frame" | "notification" | "input method window" => "window",
        "panel" | "filler" | "scroll pane" | "viewport" | "split pane" | "layered pane"
        | "root pane" | "glass pane" | "option pane" | "directory pane" | "html container"
        | "canvas" | "drawing area" | "embedded" | "embedded component" => "pane",
        "section" | "form" | "grouping" | "landmark" | "info bar" | "article" | "block quote"
        | "footer" | "log" | "marquee" | "timer" | "definition" | "comment" => "group",
        "document frame" | "document web" | "document text" | "document presentation"
        | "document email" | "page" | "terminal" => "document",
        "separator" => "separator",
        "title bar" => "title_bar",
        "calendar" => "calendar",
        "unknown" | "invalid" => "unknown",
        _ => "custom",
    }
}

fn is_checkable_role(role_name: &str) -> bool {
    matches!(
        role_name,
        "check box" | "switch" | "toggle button" | "radio button" | "check menu item" | "radio menu item"
    )
}

/// Поле пароля. Значение НЕ читается (даже если тулкит отдаёт звёздочки — прочитанное
/// уехало бы в кадр, журнал и расписку), `value_contains` по нему не ищет, `set_value`
/// отказан ДО вызова AT-SPI: пароль в чужое поле кладёт владелец руками.
pub fn is_secure(role_name: &str) -> bool {
    role_name == "password text"
}

pub const SECURE_FIELD_REFUSAL: &str =
    "this is a password field (AT-SPI role «password text»): set_value into it is refused before \
     AT-SPI is even asked, and its value is never read — «поле пароля: значение туда кладёт только \
     владелец руками». invoke, focus and scroll_into_view still work, so you can bring the field up \
     and ask him to type";

// ─── состояния ──────────────────────────────────────────────────────────────────────────

/// Номера битов `AtspiStateType` (at-spi2-core, `atspi-constants.h`). `GetState` отдаёт
/// их двумя словами по 32 бита. Таблица — справочник: не каждый бит читается кодом.
#[allow(dead_code)]
pub mod state {
    pub const ACTIVE: u32 = 1;
    pub const CHECKED: u32 = 4;
    pub const EDITABLE: u32 = 7;
    pub const ENABLED: u32 = 8;
    pub const EXPANDABLE: u32 = 9;
    pub const EXPANDED: u32 = 10;
    pub const FOCUSABLE: u32 = 11;
    pub const FOCUSED: u32 = 12;
    pub const PRESSED: u32 = 20;
    pub const SELECTABLE: u32 = 22;
    pub const SELECTED: u32 = 23;
    pub const SENSITIVE: u32 = 24;
    pub const SHOWING: u32 = 25;
    pub const VISIBLE: u32 = 30;
    pub const INDETERMINATE: u32 = 32;
    pub const CHECKABLE: u32 = 41;
    pub const READ_ONLY: u32 = 43;
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct States(pub u64);

impl States {
    pub fn from_words(words: &[u32]) -> Self {
        let low = u64::from(words.first().copied().unwrap_or(0));
        let high = u64::from(words.get(1).copied().unwrap_or(0));
        Self(low | (high << 32))
    }

    pub fn has(self, bit: u32) -> bool {
        bit < 64 && self.0 & (1u64 << bit) != 0
    }

    #[cfg(test)]
    pub fn with(bits: &[u32]) -> Self {
        Self(bits.iter().fold(0u64, |all, bit| all | (1u64 << bit)))
    }
}

// ─── что читается у элемента ────────────────────────────────────────────────────────────

/// Что элемент рассказал о себе. `None` — не отдаёт, а не «пусто».
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Facts {
    /// `GetRoleName` — английское имя роли (`push button`), уже приведённое `normalize_role`.
    pub role_name: Option<String>,
    /// `GetRole` — номер (для сырого поля; словарь ищется по имени).
    pub role: Option<u32>,
    pub localized_role: Option<String>,
    pub name: Option<String>,
    pub description: Option<String>,
    /// `AccessibleId` (at-spi2-core 2.34+) — id от разработчика окна, аналог AutomationId.
    pub identifier: Option<String>,
    pub states: States,
    /// Экранные координаты (x, y, ширина, высота) из `Component.GetExtents(SCREEN)`.
    pub extents: Option<(i32, i32, i32, i32)>,
    /// Короткие имена интерфейсов: `Component`, `Action`, `Text`, `EditableText`, `Value`,
    /// `Selection`…
    pub interfaces: Vec<String>,
    /// Текст элемента (`Text.GetText`), уже ограниченный потолком чтения; `None` у полей
    /// пароля — всегда.
    pub text: Option<String>,
    pub text_truncated: bool,
    /// Число из `Value.CurrentValue` (ползунки, счётчики, прогресс).
    pub number: Option<f64>,
}

impl Facts {
    pub fn has_interface(&self, name: &str) -> bool {
        self.interfaces.iter().any(|iface| iface == name)
    }

    fn role_str(&self) -> &str {
        self.role_name.as_deref().unwrap_or("")
    }
}

/// Отказ AT-SPI словами: что делали и что ответила шина.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Failure {
    pub kind: FailureKind,
    pub what: String,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FailureKind {
    /// Элемента (или приложения) больше нет — единственный отказ, который обход считает
    /// СВИДЕТЕЛЬСТВОМ, а не помехой (у Electron элементы пропадают между обнаружением и
    /// чтением постоянно — тот же урок, что у Mac).
    Vanished,
    /// Приложение не ответило за срок вызова.
    Timeout,
    /// У элемента нет такого интерфейса или метода.
    Unsupported,
    Other,
}

impl Failure {
    pub fn new(kind: FailureKind, what: impl Into<String>) -> Self {
        Self { kind, what: what.into() }
    }

    pub fn vanished(&self) -> bool {
        self.kind == FailureKind::Vanished
    }
}

impl fmt::Display for Failure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        let kind = match self.kind {
            FailureKind::Vanished => "vanished",
            FailureKind::Timeout => "timeout",
            FailureKind::Unsupported => "unsupported",
            FailureKind::Other => "error",
        };
        write!(f, "{}: {kind}", self.what)
    }
}

/// Элемент дерева глазами обхода. Живая реализация — `live::Obj`; стендовая — в `tests`.
pub trait Accessible: Clone {
    /// Всё об элементе; текст — не длиннее `max_text` единиц UTF-16.
    fn facts(&self, max_text: u64) -> Result<Facts, Failure>;
    /// Имена действий (строчными) в порядке их номеров (`Action.GetActions`).
    fn actions(&self) -> Result<Vec<String>, Failure>;
    /// Не больше `max` детей и флаг «есть ещё».
    fn children(&self, max: u64) -> Result<(Vec<Self>, bool), Failure>;
    fn do_action(&self, index: usize) -> Result<(), Failure>;
    fn set_text(&self, text: &str) -> Result<(), Failure>;
    fn set_number(&self, value: f64) -> Result<(), Failure>;
    fn grab_focus(&self) -> Result<(), Failure>;
    /// Выбрать себя у родителя (`Selection.SelectChild(свой номер)`).
    fn select(&self) -> Result<(), Failure>;
    fn scroll_to(&self) -> Result<(), Failure>;
}

// ─── действия и что они значат ──────────────────────────────────────────────────────────

/// Имена действий «нажать» у разных тулкитов: GTK — `click`, `press` (комбобокс),
/// `activate` (поле ввода, строка), ссылки — `jump`; Qt — `Press`; Chromium — `click`.
const INVOKE_ACTIONS: [&str; 5] = ["click", "press", "activate", "jump", "open"];
const EXPAND_ACTIONS: [&str; 3] = ["expand", "expand or contract", "showmenu"];
const COLLAPSE_ACTIONS: [&str; 2] = ["collapse", "expand or contract"];

fn find_action(actions: &[String], wanted: &[&str]) -> Option<usize> {
    wanted
        .iter()
        .find_map(|want| actions.iter().position(|action| action == want))
}

fn settable_text(facts: &Facts) -> bool {
    facts.has_interface("EditableText")
        && facts.states.has(state::EDITABLE)
        && !facts.states.has(state::READ_ONLY)
}

/// Что можно сделать над элементом — те же восемь слов, что у `element::Act`, и каждое
/// выведено из того, что элемент сам сказал: действий, интерфейсов и состояний.
pub fn patterns_of(facts: &Facts, actions: &[String]) -> Vec<&'static str> {
    let role = facts.role_str();
    let secure = is_secure(role);
    let mut out = Vec::new();
    let invoke = find_action(actions, &INVOKE_ACTIONS);
    if invoke.is_some() {
        out.push("invoke");
    }
    if !secure && (settable_text(facts) || facts.has_interface("Value") && facts.number.is_some()) {
        out.push("set_value");
    }
    if (is_checkable_role(role) || facts.states.has(state::CHECKABLE))
        && (invoke.is_some() || actions.iter().any(|a| a == "toggle"))
    {
        out.push("toggle");
    }
    let expandable = facts.states.has(state::EXPANDABLE);
    if find_action(actions, &EXPAND_ACTIONS).is_some() || (expandable && invoke.is_some()) {
        out.push("expand");
    }
    if find_action(actions, &COLLAPSE_ACTIONS).is_some() || (expandable && invoke.is_some()) {
        out.push("collapse");
    }
    if facts.states.has(state::SELECTABLE) {
        out.push("select");
    }
    if facts.has_interface("Component") {
        out.push("scroll_into_view");
    }
    if facts.states.has(state::FOCUSABLE)
        && (facts.has_interface("Component") || actions.iter().any(|a| a == "setfocus"))
    {
        out.push("focus");
    }
    out
}

// ─── узел ───────────────────────────────────────────────────────────────────────────────

/// Прочитанный узел — ровно те поля, что у `uia::Node`, плюс сырая роль AT-SPI и
/// `patterns`. Прямоугольник — пиксели экрана (`left, top, right, bottom`).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Node {
    pub parent: Option<usize>,
    pub depth: u64,
    pub role: &'static str,
    pub atspi_role: Option<String>,
    pub localized_role: Option<String>,
    pub name: Option<String>,
    pub value: Option<String>,
    pub automation_id: Option<String>,
    pub rect: Option<(i32, i32, i32, i32)>,
    pub enabled: Option<bool>,
    pub focused: Option<bool>,
    pub offscreen: Option<bool>,
    pub checked: Option<&'static str>,
    pub selected: Option<bool>,
    pub expanded: Option<&'static str>,
    pub patterns: Vec<&'static str>,
    pub secure: bool,
    pub text_truncated: bool,
    pub children_unread: Option<&'static str>,
}

impl Node {
    pub fn view(&self) -> NodeView<'_> {
        NodeView {
            role: self.role,
            name: self.name.as_deref(),
            value: self.value.as_deref(),
            automation_id: self.automation_id.as_deref(),
        }
    }

    /// Тот же узел, но роль — сырая роль AT-SPI (`push button`): она могла списать её с
    /// ответа чтения, — и это тоже должно подойти.
    pub fn raw_view(&self) -> Option<NodeView<'_>> {
        Some(NodeView {
            role: self.atspi_role.as_deref()?,
            name: self.name.as_deref(),
            value: self.value.as_deref(),
            automation_id: self.automation_id.as_deref(),
        })
    }

    pub fn matches(&self, select: &Selector) -> bool {
        select.matches(&self.view()) || self.raw_view().is_some_and(|view| select.matches(&view))
    }

    /// Элемент словами — тем же набором полей, что у чтения окна.
    pub fn describe(&self) -> Value {
        let mut object = Map::new();
        object.insert("role".into(), json!(self.role));
        for (key, value) in [
            ("atspi_role", self.atspi_role.as_ref()),
            ("name", self.name.as_ref()),
            ("automation_id", self.automation_id.as_ref()),
            ("value", self.value.as_ref()),
            ("localized_role", self.localized_role.as_ref()),
        ] {
            if let Some(value) = value {
                object.insert(key.into(), json!(value));
            }
        }
        if let Some((left, top, right, bottom)) = self.rect {
            object.insert("rect".into(), rect_json(left, top, right, bottom));
        }
        let mut state = Map::new();
        for (key, value) in [
            ("enabled", self.enabled),
            ("focused", self.focused),
            ("offscreen", self.offscreen),
            ("selected", self.selected),
        ] {
            if let Some(value) = value {
                state.insert(key.into(), json!(value));
            }
        }
        for (key, value) in [("checked", self.checked), ("expanded", self.expanded)] {
            if let Some(value) = value {
                state.insert(key.into(), json!(value));
            }
        }
        if !state.is_empty() {
            object.insert("state".into(), Value::Object(state));
        }
        if !self.patterns.is_empty() {
            object.insert("patterns".into(), json!(self.patterns));
        }
        if self.secure {
            object.insert("secure".into(), json!(true));
        }
        Value::Object(object)
    }
}

/// Та же форма прямоугольника, что у `uia::Rect::json` и `ax::rect_json`.
pub fn rect_json(left: i32, top: i32, right: i32, bottom: i32) -> Value {
    json!({
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
        "center": {"x": left + (right - left) / 2, "y": top + (bottom - top) / 2},
    })
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Screen {
    pub left: f64,
    pub top: f64,
    pub width: f64,
    pub height: f64,
}

impl Screen {
    fn intersects(&self, left: f64, top: f64, right: f64, bottom: f64) -> bool {
        right > self.left && bottom > self.top && left < self.left + self.width && top < self.top + self.height
    }
}

/// Обрезка по единицам UTF-16 — копия правила `uia::clip_utf16`, чтобы `max_text_chars`
/// значил одно и то же на трёх ОС.
pub fn clip_utf16(text: &str, limit: u64) -> (String, bool) {
    let limit = usize::try_from(limit).unwrap_or(usize::MAX);
    let mut result = String::new();
    let mut units = 0usize;
    for value in text.chars() {
        let width = value.len_utf16();
        if units + width > limit {
            return (result, true);
        }
        result.push(value);
        units += width;
    }
    (result, false)
}

fn number_text(number: f64) -> String {
    if number.fract() == 0.0 && number.abs() < 1e15 {
        format!("{}", number as i64)
    } else {
        format!("{number}")
    }
}

/// Роли, у которых текст элемента — это его ЗНАЧЕНИЕ (поле ввода, документ), а не имя.
fn text_is_value(role_name: &str) -> bool {
    matches!(
        role_name,
        "text" | "entry" | "password text" | "autocomplete" | "edit bar" | "spin button"
            | "combo box" | "terminal" | "document frame" | "document text" | "document web"
            | "document email" | "date editor"
    )
}

/// Прочитать один элемент в узел. `max_text_chars` — потолок надписей (`u64::MAX` — без
/// обрезки: так читают поиск и действие).
pub fn node_from<E: Accessible>(
    element: &E,
    depth: u64,
    parent: Option<usize>,
    max_text_chars: u64,
    screen: Screen,
) -> Result<Node, Failure> {
    let facts = element.facts(max_text_chars)?;
    let actions = if facts.has_interface("Action") {
        element.actions().unwrap_or_default()
    } else {
        Vec::new()
    };
    let role_name = facts.role_str().to_string();
    let role = role_of(facts.role_name.as_deref());
    let patterns = patterns_of(&facts, &actions);
    let secure = is_secure(&role_name);

    let mut truncated = facts.text_truncated;
    let mut clip = |value: Option<String>| -> Option<String> {
        let value = value?;
        if value.is_empty() {
            return None;
        }
        let (clipped, cut) = clip_utf16(&value, max_text_chars);
        truncated |= cut;
        Some(clipped)
    };

    // Имя: Name ∥ Description; у надписи без имени имя — её текст (как у UIA: надпись и
    // есть имя). Значение: текст поля (не у пароля) или число `Value`.
    let text = if secure { None } else { facts.text.clone().filter(|t| !t.is_empty()) };
    let mut name = facts
        .name
        .clone()
        .filter(|s| !s.is_empty())
        .or_else(|| facts.description.clone().filter(|s| !s.is_empty()));
    let mut value = None;
    if text_is_value(&role_name) {
        value = text;
    } else if name.is_none() {
        name = text;
    } else if let Some(text) = text
        && Some(&text) != name.as_ref()
        && role == "text"
    {
        value = Some(text);
    }
    if value.is_none()
        && !secure
        && let Some(number) = facts.number
    {
        value = Some(number_text(number));
    }

    let rect = match facts.extents {
        Some((x, y, width, height)) if width > 0 && height > 0 && (x, y) != (-1, -1) => {
            Some((x, y, x + width, y + height))
        }
        _ => None,
    };
    let showing = facts.states.has(state::SHOWING);
    let offscreen = Some(
        !showing
            || rect.is_some_and(|(l, t, r, b)| !screen.intersects(l as f64, t as f64, r as f64, b as f64)),
    );
    let states = facts.states;
    let enabled = Some(states.has(state::ENABLED) || states.has(state::SENSITIVE));
    let focused = if states.has(state::FOCUSABLE) || states.has(state::FOCUSED) {
        Some(states.has(state::FOCUSED))
    } else {
        None
    };
    let checked = if is_checkable_role(&role_name) || states.has(state::CHECKABLE) {
        Some(if states.has(state::INDETERMINATE) {
            "mixed"
        } else if states.has(state::CHECKED) || states.has(state::PRESSED) {
            "on"
        } else {
            "off"
        })
    } else {
        None
    };
    let selected = states.has(state::SELECTABLE).then(|| states.has(state::SELECTED));
    let expanded = states
        .has(state::EXPANDABLE)
        .then(|| if states.has(state::EXPANDED) { "expanded" } else { "collapsed" });

    Ok(Node {
        parent,
        depth,
        role,
        atspi_role: clip(facts.role_name.clone()),
        localized_role: clip(facts.localized_role.clone()),
        name: clip(name),
        value: clip(value),
        automation_id: clip(facts.identifier.clone()),
        rect,
        enabled,
        focused,
        offscreen,
        checked,
        selected,
        expanded,
        patterns,
        secure,
        text_truncated: truncated,
        children_unread: None,
    })
}

// ─── обход ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WalkLimits {
    pub max_nodes: u64,
    pub max_depth: u64,
    pub max_children_per_node: u64,
    pub max_text_chars: u64,
}

#[derive(Debug, Default, PartialEq)]
pub struct Walk {
    pub nodes: Vec<Node>,
    pub discovered_unread: u64,
    pub subtrees_unread: u64,
    pub skipped_offscreen: u64,
    pub depth_reached: u64,
    pub truncated_by: Vec<&'static str>,
    pub walk_ms: u64,
    pub read_failures: u64,
    pub first_read_failure: Option<String>,
}

impl Walk {
    fn mark(&mut self, reason: &'static str) {
        if !self.truncated_by.contains(&reason) {
            self.truncated_by.push(reason);
        }
    }
}

/// Обход в ширину с теми же пределами и в том же порядке проверок, что `uia_walk` и
/// `ax::walk`: потолок узлов и срок — перед каждым узлом, глубина и ширина — при раскрытии.
pub fn walk<E: Accessible>(root: E, limits: WalkLimits, deadline: Instant, screen: Screen, visible_only: bool) -> Walk {
    let started = Instant::now();
    let mut walk = Walk::default();
    let mut queue: VecDeque<(E, u64, Option<usize>)> = VecDeque::new();
    queue.push_back((root, 0, None));
    while !queue.is_empty() {
        if walk.nodes.len() as u64 >= limits.max_nodes {
            walk.mark("max_nodes");
            break;
        }
        if Instant::now() >= deadline {
            walk.mark("timeout");
            break;
        }
        let (element, depth, parent) = queue.pop_front().expect("queue is not empty");
        let mut node = match node_from(&element, depth, parent, limits.max_text_chars, screen) {
            Ok(node) => node,
            Err(failure) => {
                walk.read_failures += 1;
                if walk.first_read_failure.is_none() {
                    walk.first_read_failure = Some(failure.to_string());
                }
                if failure.vanished() {
                    // Исчез между обнаружением и чтением — его нет, это не помеха.
                    continue;
                }
                Node { parent, depth, role: "custom", ..Node::default() }
            }
        };
        if visible_only && node.offscreen == Some(true) {
            walk.skipped_offscreen += 1;
            continue;
        }
        walk.depth_reached = walk.depth_reached.max(depth);
        let index = walk.nodes.len();
        if depth + 1 > limits.max_depth {
            node.children_unread = Some("max_depth");
            walk.subtrees_unread += 1;
            walk.mark("max_depth");
        } else {
            match element.children(limits.max_children_per_node) {
                Ok((children, more)) => {
                    if more {
                        node.children_unread = Some("max_children_per_node");
                        walk.subtrees_unread += 1;
                        walk.mark("max_children_per_node");
                    }
                    for child in children {
                        queue.push_back((child, depth + 1, Some(index)));
                    }
                }
                Err(failure) => {
                    node.children_unread = Some("accessibility_error");
                    walk.subtrees_unread += 1;
                    walk.read_failures += 1;
                    if walk.first_read_failure.is_none() {
                        walk.first_read_failure = Some(failure.to_string());
                    }
                }
            }
        }
        walk.nodes.push(node);
    }
    walk.discovered_unread = queue.len() as u64;
    walk.walk_ms = started.elapsed().as_millis() as u64;
    walk
}

// ─── отбор ──────────────────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub struct Hit<E> {
    pub element: E,
    pub node: Node,
    pub json: Value,
}

/// Что вернул обход: находки, число осмотренных узлов и чем он кончился.
pub type Sweep<E> = Result<(Vec<Hit<E>>, usize, Option<&'static str>), Failure>;

/// Обход окна ОДИН на поиск и действие (как `ax::sweep`). Отказ AT-SPI при чтении узла —
/// не «не подошёл»: он рвёт поиск словами. Исключение — элемент исчез.
pub fn sweep<E: Accessible>(
    root: &E,
    select: &Selector,
    max_nodes: u64,
    max_depth: u64,
    deadline: Instant,
    screen: Screen,
) -> Sweep<E> {
    let mut found: Vec<Hit<E>> = Vec::new();
    let mut queue: VecDeque<(E, u64)> = VecDeque::new();
    queue.push_back((root.clone(), 0));
    let mut scanned = 0usize;
    let mut hit_limit: Option<&'static str> = None;
    while let Some((element, depth)) = queue.pop_front() {
        if scanned as u64 >= max_nodes {
            hit_limit = Some("max_nodes");
            break;
        }
        if Instant::now() >= deadline {
            hit_limit = Some("timeout");
            break;
        }
        scanned += 1;
        let node = match node_from(&element, depth, None, u64::MAX, screen) {
            Ok(node) => node,
            Err(failure) if failure.vanished() => continue,
            Err(failure) => {
                return Err(Failure::new(failure.kind, format!("selector attributes: search incomplete: {}", failure.what)));
            }
        };
        if node.matches(select) {
            found.push(Hit { element: element.clone(), json: node.describe(), node: node.clone() });
        }
        let (children, more) = match element.children(max_nodes) {
            Ok(children) => children,
            Err(failure) if failure.vanished() => (Vec::new(), false),
            Err(failure) => {
                return Err(Failure::new(failure.kind, format!("children: search incomplete: {}", failure.what)));
            }
        };
        if depth >= max_depth && !children.is_empty() {
            hit_limit = Some("max_depth");
            break;
        }
        for child in children {
            if Instant::now() >= deadline {
                hit_limit = Some("timeout");
                break;
            }
            if scanned + queue.len() >= max_nodes as usize {
                hit_limit = Some("max_nodes");
                break;
            }
            queue.push_back((child, depth + 1));
        }
        if more && hit_limit.is_none() {
            hit_limit = Some("max_nodes");
        }
        if hit_limit.is_some() {
            break;
        }
    }
    Ok((found, scanned, hit_limit))
}

// ─── действие ───────────────────────────────────────────────────────────────────────────

/// Сделать над элементом. Никаких откатов к координатам: нет нужного — отказ называет то,
/// что у элемента ЕСТЬ (слова те же, что у UIA и AX). `guard` зовётся прямо перед
/// изменением: окно, уехавшее за время поиска, не должно получить удар.
pub fn perform<E: Accessible>(
    element: &E,
    node: &Node,
    act: Act,
    text: Option<&str>,
    guard: impl Fn() -> Result<(), String>,
) -> Result<(), String> {
    let missing = |want: &str| -> String {
        let have = if node.patterns.is_empty() { "none".to_string() } else { node.patterns.join(", ") };
        format!(
            "this element does not support {want}; patterns it does support: {have}. The verb \
             refuses to fall back to a click at coordinates - that is the unreliability it exists \
             to avoid"
        )
    };
    let failed = |what: &str, failure: Failure| -> String { format!("{what}: {failure}") };
    if act == Act::SetValue && (node.secure || is_secure(node.atspi_role.as_deref().unwrap_or(""))) {
        return Err(SECURE_FIELD_REFUSAL.to_string());
    }
    // Свежий взгляд в момент действия, а не то, что помнил обход.
    let facts = element.facts(u64::MAX).map_err(|f| failed("re-reading the element", f))?;
    let actions = if facts.has_interface("Action") {
        element.actions().map_err(|f| failed("Action.GetActions", f))?
    } else {
        Vec::new()
    };
    let role_name = facts.role_str().to_string();
    let do_named = |index: usize| -> Result<(), String> {
        guard()?;
        element
            .do_action(index)
            .map_err(|f| failed(&format!("Action.DoAction({})", actions[index]), f))
    };
    match act {
        Act::Invoke => {
            let index = find_action(&actions, &INVOKE_ACTIONS)
                .ok_or_else(|| missing("a click/press/activate action (invoke)"))?;
            do_named(index)?;
        }
        Act::SetValue => {
            let said = text.unwrap_or_default();
            if settable_text(&facts) {
                guard()?;
                element.set_text(said).map_err(|f| failed("EditableText.SetTextContents", f))?;
            } else if facts.has_interface("Value") && facts.number.is_some() {
                let number: f64 = said.trim().replace(',', ".").parse().map_err(|_| {
                    format!("this element holds a number (AT-SPI Value); {said:?} is not a number")
                })?;
                guard()?;
                element.set_number(number).map_err(|f| failed("Value.CurrentValue", f))?;
            } else {
                return Err(missing("an editable text or a Value (set_value)"));
            }
        }
        Act::Toggle => {
            let checkable = is_checkable_role(&role_name) || facts.states.has(state::CHECKABLE);
            let index = if checkable {
                actions
                    .iter()
                    .position(|a| a == "toggle")
                    .or_else(|| find_action(&actions, &INVOKE_ACTIONS))
            } else {
                None
            };
            let index = index.ok_or_else(|| missing("a click or toggle action on a check box or switch (toggle)"))?;
            do_named(index)?;
        }
        Act::Expand => {
            if facts.states.has(state::EXPANDED) {
                // Уже раскрыт: «expand or contract» свернул бы его — это был бы обратный
                // удар. Сказать правду, ничего не трогая.
                return Ok(());
            }
            let index = find_action(&actions, &EXPAND_ACTIONS)
                .or_else(|| facts.states.has(state::EXPANDABLE).then(|| find_action(&actions, &INVOKE_ACTIONS)).flatten())
                .ok_or_else(|| missing("an expand action (expand)"))?;
            do_named(index)?;
        }
        Act::Collapse => {
            if facts.states.has(state::EXPANDABLE) && !facts.states.has(state::EXPANDED) {
                return Ok(());
            }
            let index = find_action(&actions, &COLLAPSE_ACTIONS)
                .or_else(|| facts.states.has(state::EXPANDABLE).then(|| find_action(&actions, &INVOKE_ACTIONS)).flatten())
                .ok_or_else(|| missing("a collapse action (collapse)"))?;
            do_named(index)?;
        }
        Act::Select => {
            if !facts.states.has(state::SELECTABLE) {
                return Err(missing("a selectable item (select)"));
            }
            guard()?;
            element.select().map_err(|f| failed("Selection.SelectChild on the parent", f))?;
        }
        Act::ScrollIntoView => {
            if !facts.has_interface("Component") {
                return Err(missing("the Component interface (scroll_into_view)"));
            }
            guard()?;
            element.scroll_to().map_err(|f| failed("Component.ScrollTo", f))?;
        }
        Act::Focus => {
            if !facts.states.has(state::FOCUSABLE) {
                return Err(missing("a focusable element (focus)"));
            }
            if facts.has_interface("Component") {
                guard()?;
                element.grab_focus().map_err(|f| failed("Component.GrabFocus", f))?;
            } else if let Some(index) = actions.iter().position(|a| a == "setfocus") {
                do_named(index)?;
            } else {
                return Err(missing("Component.GrabFocus or a SetFocus action (focus)"));
            }
        }
    }
    Ok(())
}

// ─── живая часть ────────────────────────────────────────────────────────────────────────

/// Состояние AT-SPI для `desktop.status`: есть ли шина, включена ли доступность, сколько
/// приложений в реестре, и что сказать владельцу, если чего-то нет.
#[cfg(target_os = "linux")]
pub fn status() -> Value {
    live::status()
}

#[cfg(not(target_os = "linux"))]
pub fn status() -> Value {
    Value::Null
}

#[cfg(target_os = "linux")]
pub mod live {
    use std::time::Duration;

    use serde_json::{Value, json};
    use zbus::blocking::Connection;
    use zbus::zvariant::{OwnedObjectPath, OwnedValue, Structure, Value as ZValue};

    use super::{Accessible, Facts, Failure, FailureKind, States, clip_utf16, normalize_role, state};

    const REGISTRY: &str = "org.a11y.atspi.Registry";
    const ROOT_PATH: &str = "/org/a11y/atspi/accessible/root";
    const PROPS: &str = "org.freedesktop.DBus.Properties";
    const ACC: &str = "org.a11y.atspi.Accessible";
    /// `ATSPI_COORD_TYPE_SCREEN`.
    const COORD_SCREEN: u32 = 0;
    /// `ATSPI_SCROLL_ANYWHERE` — пусть тулкит сам решит, куда подвинуть.
    const SCROLL_ANYWHERE: u32 = 6;

    fn failure_of(what: &str, error: zbus::Error) -> Failure {
        let text = error.to_string();
        let kind = match &error {
            zbus::Error::MethodError(name, _, _) => match name.as_str() {
                "org.freedesktop.DBus.Error.UnknownObject"
                | "org.freedesktop.DBus.Error.ServiceUnknown"
                | "org.freedesktop.DBus.Error.NameHasNoOwner" => FailureKind::Vanished,
                "org.freedesktop.DBus.Error.NoReply" | "org.freedesktop.DBus.Error.Timeout" => FailureKind::Timeout,
                "org.freedesktop.DBus.Error.UnknownMethod"
                | "org.freedesktop.DBus.Error.UnknownInterface"
                | "org.freedesktop.DBus.Error.UnknownProperty"
                | "org.freedesktop.DBus.Error.InvalidArgs" => FailureKind::Unsupported,
                _ => FailureKind::Other,
            },
            zbus::Error::InputOutput(_) => FailureKind::Timeout,
            _ if text.to_lowercase().contains("timed out") || text.to_lowercase().contains("timeout") => {
                FailureKind::Timeout
            }
            _ => FailureKind::Other,
        };
        Failure::new(kind, format!("{what}: {text}"))
    }

    // ─── шина ────────────────────────────────────────────────────────────────────────

    fn session(timeout: Duration) -> Result<Connection, String> {
        zbus::blocking::connection::Builder::session()
            .and_then(|builder| builder.method_timeout(timeout).build())
            .map_err(|error| {
                format!(
                    "no D-Bus session bus for this process ({error}): the window tree lives on the \
                     session's accessibility bus, and a process outside the graphical session \
                     (a system service, ssh without a session) cannot reach it"
                )
            })
    }

    fn a11y_address(session: &Connection) -> Result<String, String> {
        if let Ok(address) = std::env::var("AT_SPI_BUS_ADDRESS")
            && !address.trim().is_empty()
        {
            return Ok(address);
        }
        let reply = session
            .call_method(Some("org.a11y.Bus"), "/org/a11y/bus", Some("org.a11y.Bus"), "GetAddress", &())
            .map_err(|error| {
                format!(
                    "the accessibility bus did not answer (org.a11y.Bus.GetAddress: {error}); \
                     at-spi2-core is not installed or not running in this session"
                )
            })?;
        reply
            .body()
            .deserialize::<String>()
            .map_err(|error| format!("org.a11y.Bus.GetAddress answered strangely: {error}"))
    }

    /// Соединение с шиной доступности со сроком на каждый вызов.
    pub fn connect(timeout: Duration) -> Result<Connection, String> {
        let session = session(timeout)?;
        let address = a11y_address(&session)?;
        zbus::blocking::connection::Builder::address(address.as_str())
            .and_then(|builder| builder.method_timeout(timeout).build())
            .map_err(|error| format!("cannot open the accessibility bus {address:?}: {error}"))
    }

    fn get_status(session: &Connection, name: &str) -> Option<bool> {
        let reply = session
            .call_method(Some("org.a11y.Bus"), "/org/a11y/bus", Some(PROPS), "Get", &("org.a11y.Status", name))
            .ok()?;
        let value: OwnedValue = reply.body().deserialize().ok()?;
        bool::try_from(value).ok()
    }

    /// Включить доступность в сессии. -> было ли выключено (и теперь включено).
    pub fn ensure_enabled(timeout: Duration) -> Result<bool, String> {
        let session = session(timeout)?;
        if get_status(&session, "IsEnabled") == Some(true) {
            return Ok(false);
        }
        session
            .call_method(
                Some("org.a11y.Bus"),
                "/org/a11y/bus",
                Some(PROPS),
                "Set",
                &("org.a11y.Status", "IsEnabled", ZValue::from(true)),
            )
            .map_err(|error| format!("could not switch accessibility on (org.a11y.Status.IsEnabled): {error}"))?;
        Ok(true)
    }

    pub fn status() -> Value {
        let timeout = Duration::from_millis(1_500);
        let session = match session(timeout) {
            Ok(session) => session,
            Err(words) => {
                return json!({"bus": false, "enabled": null, "applications": null, "hint": words});
            }
        };
        let enabled = get_status(&session, "IsEnabled");
        let screen_reader = get_status(&session, "ScreenReaderEnabled");
        let applications = connect(timeout).ok().and_then(|conn| {
            Obj::root(&conn).children(10_000).ok().map(|(apps, _)| apps.len())
        });
        let hint = match (applications, enabled) {
            (None, _) => Some(
                "the accessibility bus (at-spi2-core) is not reachable: desktop.window.read and \
                 desktop.element.* cannot read window trees; install at-spi2-core"
                    .to_string(),
            ),
            (_, Some(false)) => Some(
                "accessibility is switched off in this session (org.a11y.Status.IsEnabled=false): \
                 GTK programs still show their tree, Qt and Chromium/Electron do not; the body \
                 switches it on at the first window read, and programs started before that may \
                 need a restart"
                    .to_string(),
            ),
            _ => None,
        };
        json!({
            "bus": applications.is_some(),
            "enabled": enabled,
            "screen_reader": screen_reader,
            "applications": applications,
            "hint": hint,
        })
    }

    // ─── объект ──────────────────────────────────────────────────────────────────────

    /// Объект AT-SPI: имя приложения на шине и путь объекта.
    #[derive(Clone)]
    pub struct Obj {
        conn: Connection,
        pub bus: String,
        pub path: OwnedObjectPath,
    }

    impl std::fmt::Debug for Obj {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            write!(f, "Obj({} {})", self.bus, self.path.as_str())
        }
    }

    impl Obj {
        pub fn root(conn: &Connection) -> Self {
            Self {
                conn: conn.clone(),
                bus: REGISTRY.into(),
                path: OwnedObjectPath::try_from(ROOT_PATH).expect("static object path"),
            }
        }

        fn at(&self, (bus, path): (String, OwnedObjectPath)) -> Self {
            Self { conn: self.conn.clone(), bus, path }
        }

        fn call<B, R>(&self, iface: &str, method: &str, body: &B) -> Result<R, Failure>
        where
            B: serde::Serialize + zbus::zvariant::DynamicType,
            R: for<'d> zbus::zvariant::DynamicDeserialize<'d>,
        {
            let what = format!("{iface}.{method}");
            let reply = self
                .conn
                .call_method(Some(self.bus.as_str()), self.path.as_str(), Some(iface), method, body)
                .map_err(|error| failure_of(&what, error))?;
            reply
                .body()
                .deserialize::<R>()
                .map_err(|error| Failure::new(FailureKind::Other, format!("{what} answered strangely: {error}")))
        }

        fn prop(&self, iface: &str, name: &str) -> Result<OwnedValue, Failure> {
            self.call(PROPS, "Get", &(iface, name))
        }

        fn prop_text(&self, iface: &str, name: &str) -> Option<String> {
            self.prop(iface, name).ok().and_then(|value| String::try_from(value).ok())
        }

        /// pid процесса-владельца этого объекта (по шине доступности).
        pub fn pid(&self) -> Option<u32> {
            let reply = self
                .conn
                .call_method(
                    Some("org.freedesktop.DBus"),
                    "/org/freedesktop/DBus",
                    Some("org.freedesktop.DBus"),
                    "GetConnectionUnixProcessID",
                    &(self.bus.as_str(),),
                )
                .ok()?;
            reply.body().deserialize::<u32>().ok()
        }

        pub fn name(&self) -> Option<String> {
            self.prop_text(ACC, "Name").filter(|name| !name.is_empty())
        }

        pub fn states(&self) -> States {
            self.call::<_, Vec<u32>>(ACC, "GetState", &())
                .map(|words| States::from_words(&words))
                .unwrap_or_default()
        }

        pub fn extents(&self) -> Option<(i32, i32, i32, i32)> {
            self.call::<_, (i32, i32, i32, i32)>("org.a11y.atspi.Component", "GetExtents", &(COORD_SCREEN,))
                .ok()
        }

        pub fn role_name(&self) -> Option<String> {
            self.call::<_, String>(ACC, "GetRoleName", &()).ok().map(|name| normalize_role(&name))
        }

        fn parent(&self) -> Result<Obj, Failure> {
            let value = self.prop(ACC, "Parent")?;
            let structure = Structure::try_from(value)
                .map_err(|error| Failure::new(FailureKind::Other, format!("Accessible.Parent: {error}")))?;
            let fields = structure.fields();
            let bus = match fields.first() {
                Some(ZValue::Str(text)) => text.to_string(),
                _ => return Err(Failure::new(FailureKind::Other, "Accessible.Parent: no bus name")),
            };
            let path = match fields.get(1) {
                Some(ZValue::ObjectPath(path)) => OwnedObjectPath::from(path.to_owned()),
                _ => return Err(Failure::new(FailureKind::Other, "Accessible.Parent: no object path")),
            };
            Ok(self.at((bus, path)))
        }
    }

    impl Accessible for Obj {
        fn facts(&self, max_text: u64) -> Result<Facts, Failure> {
            // Роль спрашиваем первой: исчезнувший элемент отвечает на неё UnknownObject, и
            // это свидетельство, а не «элемент без роли».
            let role: u32 = self.call(ACC, "GetRole", &())?;
            let role_name = self.role_name();
            let interfaces: Vec<String> = self
                .call::<_, Vec<String>>(ACC, "GetInterfaces", &())
                .unwrap_or_default()
                .into_iter()
                .map(|iface| iface.trim_start_matches("org.a11y.atspi.").to_string())
                .collect();
            let has = |name: &str| interfaces.iter().any(|iface| iface == name);
            let secure = role_name.as_deref() == Some("password text");
            let (text, text_truncated) = if has("Text") && !secure {
                let count = self
                    .prop("org.a11y.atspi.Text", "CharacterCount")
                    .ok()
                    .and_then(|value| i32::try_from(value).ok())
                    .unwrap_or(-1);
                // Не тянуть весь документ: не больше потолка (плюс один знак — понять, что
                // обрезано).
                let end = if count < 0 {
                    -1
                } else {
                    count.min(i32::try_from(max_text.saturating_add(1)).unwrap_or(i32::MAX))
                };
                match self.call::<_, String>("org.a11y.atspi.Text", "GetText", &(0i32, end)) {
                    Ok(text) => {
                        let (clipped, cut) = clip_utf16(&text, max_text);
                        (Some(clipped), cut || (count >= 0 && i64::from(count) > max_text as i64))
                    }
                    Err(_) => (None, false),
                }
            } else {
                (None, false)
            };
            let number = if has("Value") {
                self.prop("org.a11y.atspi.Value", "CurrentValue")
                    .ok()
                    .and_then(|value| f64::try_from(value).ok())
            } else {
                None
            };
            Ok(Facts {
                role_name,
                role: Some(role),
                localized_role: self.call::<_, String>(ACC, "GetLocalizedRoleName", &()).ok(),
                name: self.name(),
                description: self.prop_text(ACC, "Description").filter(|d| !d.is_empty()),
                identifier: self.prop_text(ACC, "AccessibleId").filter(|id| !id.is_empty()),
                states: self.states(),
                extents: if has("Component") { self.extents() } else { None },
                interfaces,
                text,
                text_truncated,
                number,
            })
        }

        fn actions(&self) -> Result<Vec<String>, Failure> {
            let actions: Vec<(String, String, String)> = self.call("org.a11y.atspi.Action", "GetActions", &())?;
            Ok(actions.into_iter().map(|(name, _, _)| name.trim().to_lowercase()).collect())
        }

        fn children(&self, max: u64) -> Result<(Vec<Self>, bool), Failure> {
            let children: Vec<(String, OwnedObjectPath)> = self.call(ACC, "GetChildren", &())?;
            let more = children.len() as u64 > max;
            let taken = children
                .into_iter()
                .take(usize::try_from(max).unwrap_or(usize::MAX))
                // Пустой путь — «нет объекта» в протоколе AT-SPI.
                .filter(|(bus, path)| !bus.is_empty() && path.as_str() != "/org/a11y/atspi/null")
                .map(|child| self.at(child))
                .collect();
            Ok((taken, more))
        }

        fn do_action(&self, index: usize) -> Result<(), Failure> {
            let index = i32::try_from(index).map_err(|_| Failure::new(FailureKind::Other, "action index overflow"))?;
            let done: bool = self.call("org.a11y.atspi.Action", "DoAction", &(index,))?;
            if done { Ok(()) } else { Err(Failure::new(FailureKind::Other, "Action.DoAction answered false")) }
        }

        fn set_text(&self, text: &str) -> Result<(), Failure> {
            let done: bool = self.call("org.a11y.atspi.EditableText", "SetTextContents", &(text,))?;
            if done {
                Ok(())
            } else {
                Err(Failure::new(FailureKind::Other, "EditableText.SetTextContents answered false"))
            }
        }

        fn set_number(&self, value: f64) -> Result<(), Failure> {
            self.call::<_, ()>(PROPS, "Set", &("org.a11y.atspi.Value", "CurrentValue", ZValue::from(value)))
        }

        fn grab_focus(&self) -> Result<(), Failure> {
            let done: bool = self.call("org.a11y.atspi.Component", "GrabFocus", &())?;
            if done { Ok(()) } else { Err(Failure::new(FailureKind::Other, "Component.GrabFocus answered false")) }
        }

        fn select(&self) -> Result<(), Failure> {
            let index: i32 = self.call(ACC, "GetIndexInParent", &())?;
            let parent = self.parent()?;
            let done: bool = parent.call("org.a11y.atspi.Selection", "SelectChild", &(index,))?;
            if done { Ok(()) } else { Err(Failure::new(FailureKind::Other, "Selection.SelectChild answered false")) }
        }

        fn scroll_to(&self) -> Result<(), Failure> {
            let done: bool = self.call("org.a11y.atspi.Component", "ScrollTo", &(SCROLL_ANYWHERE,))?;
            if done { Ok(()) } else { Err(Failure::new(FailureKind::Other, "Component.ScrollTo answered false")) }
        }
    }

    // ─── окно ────────────────────────────────────────────────────────────────────────

    /// AT-SPI-окно, найденное для X-окна (или активное, когда X нет).
    pub struct Attached {
        pub window: Obj,
        pub title: Option<String>,
        pub matched_by: &'static str,
        pub compared: usize,
        pub application: Option<String>,
        pub pid: Option<u32>,
    }

    /// Что знаем о целевом окне со стороны X: pid, заголовок, прямоугольник С РАМКОЙ.
    pub struct WindowHint<'a> {
        pub pid: Option<u32>,
        pub title: Option<&'a str>,
        pub rect: Option<(i32, i32, i32, i32)>,
    }

    fn top_levels(app: &Obj) -> Vec<Obj> {
        app.children(4_096).map(|(children, _)| children).unwrap_or_default()
    }

    /// Найти AT-SPI-окно для X-окна: приложение — по pid (у шины доступности свой pid на
    /// каждое соединение), окно — по заголовку, потом по прямоугольнику (рамку менеджера
    /// AT-SPI не считает — допуск на неё), потом «единственное окно приложения».
    pub fn attach(conn: &Connection, hint: &WindowHint<'_>) -> Result<Attached, String> {
        let root = Obj::root(conn);
        let (apps, _) = root.children(10_000).map_err(|f| format!("the accessibility registry did not list applications: {f}"))?;
        let pid = hint.pid.ok_or_else(|| {
            "the window's process is unknown (no _NET_WM_PID, no XRes), so its accessibility tree \
             cannot be matched to it"
                .to_string()
        })?;
        let mine: Vec<Obj> = apps.into_iter().filter(|app| app.pid() == Some(pid)).collect();
        if mine.is_empty() {
            return Err(format!(
                "the program (pid {pid}) is not on the accessibility bus: it does not expose an \
                 AT-SPI tree. GTK programs do by default; Qt needs accessibility switched on before \
                 it starts (QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1), Chromium/Electron — \
                 --force-renderer-accessibility; programs started before accessibility was switched \
                 on need a restart"
            ));
        }
        let application = mine.first().and_then(Obj::name);
        let candidates: Vec<Obj> = mine.iter().flat_map(top_levels).collect();
        let compared = candidates.len();
        if let Some(title) = hint.title.filter(|t| !t.is_empty())
            && let Some(window) = candidates.iter().find(|w| w.name().as_deref() == Some(title))
        {
            return Ok(Attached {
                window: window.clone(),
                title: Some(title.to_string()),
                matched_by: "title",
                compared,
                application,
                pid: Some(pid),
            });
        }
        if let Some((left, top, width, height)) = hint.rect {
            let mut best: Option<(i32, &Obj)> = None;
            for window in &candidates {
                let Some((x, y, w, h)) = window.extents() else { continue };
                if w <= 0 || h <= 0 {
                    continue;
                }
                // Клиентская часть лежит ВНУТРИ рамки: сдвиг не больше рамки, размер меньше.
                let (dx, dy) = ((x - left).abs(), (y - top).abs());
                let (dw, dh) = ((width - w).abs(), (height - h).abs());
                if dx <= 80 && dy <= 120 && dw <= 160 && dh <= 160 {
                    let score = dx + dy + dw + dh;
                    if best.is_none_or(|(s, _)| score < s) {
                        best = Some((score, window));
                    }
                }
            }
            if let Some((_, window)) = best {
                return Ok(Attached {
                    window: window.clone(),
                    title: window.name(),
                    matched_by: "rect",
                    compared,
                    application,
                    pid: Some(pid),
                });
            }
        }
        if let Some(window) = candidates.iter().find(|w| w.states().has(state::ACTIVE)) {
            return Ok(Attached {
                window: window.clone(),
                title: window.name(),
                matched_by: "active_state",
                compared,
                application,
                pid: Some(pid),
            });
        }
        if candidates.len() == 1 {
            let window = candidates[0].clone();
            return Ok(Attached { title: window.name(), window, matched_by: "only_window", compared, application, pid: Some(pid) });
        }
        Err(format!(
            "the program (pid {pid}) has {compared} accessible windows and none matches this one by \
             title or position"
        ))
    }

    /// Активное окно рабочего стола по AT-SPI (состояние ACTIVE) — когда X-сервера нет
    /// (чистый Wayland) и спросить менеджер окон не у кого.
    pub fn active_window(conn: &Connection) -> Result<Attached, String> {
        let root = Obj::root(conn);
        let (apps, _) = root.children(10_000).map_err(|f| format!("the accessibility registry did not list applications: {f}"))?;
        let mut compared = 0usize;
        for app in &apps {
            for window in top_levels(app) {
                compared += 1;
                if window.states().has(state::ACTIVE) {
                    return Ok(Attached {
                        title: window.name(),
                        application: app.name(),
                        pid: app.pid(),
                        window,
                        matched_by: "active_state",
                        compared,
                    });
                }
            }
        }
        Err(format!("no accessible window is active ({compared} windows of {} programs compared)", apps.len()))
    }
}

// ─── стенды ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use std::cell::RefCell;
    use std::rc::Rc;
    use std::time::Duration;

    use super::*;

    type FakeTree = Vec<(Facts, Vec<String>, Vec<usize>)>;

    /// Подставное дерево: узлы с фактами и действиями, журнал того, что с ними сделали.
    #[derive(Clone)]
    struct Fake {
        tree: Rc<FakeTree>,
        index: usize,
        log: Rc<RefCell<Vec<String>>>,
        vanished: Rc<RefCell<Vec<usize>>>,
    }

    impl Fake {
        fn node(&self) -> &(Facts, Vec<String>, Vec<usize>) {
            &self.tree[self.index]
        }
        fn gone(&self) -> Result<(), Failure> {
            if self.vanished.borrow().contains(&self.index) {
                return Err(Failure::new(FailureKind::Vanished, "Accessible.GetRole"));
            }
            Ok(())
        }
    }

    impl Accessible for Fake {
        fn facts(&self, max_text: u64) -> Result<Facts, Failure> {
            self.gone()?;
            let mut facts = self.node().0.clone();
            if let Some(text) = &facts.text {
                let (clipped, cut) = clip_utf16(text, max_text);
                facts.text = Some(clipped);
                facts.text_truncated = cut;
            }
            Ok(facts)
        }
        fn actions(&self) -> Result<Vec<String>, Failure> {
            self.gone()?;
            Ok(self.node().1.clone())
        }
        fn children(&self, max: u64) -> Result<(Vec<Self>, bool), Failure> {
            self.gone()?;
            let all = &self.node().2;
            let kids = all
                .iter()
                .take(max as usize)
                .map(|i| Fake { index: *i, ..self.clone() })
                .collect();
            Ok((kids, all.len() as u64 > max))
        }
        fn do_action(&self, index: usize) -> Result<(), Failure> {
            self.log.borrow_mut().push(format!("{}:action:{}", self.index, self.node().1[index]));
            Ok(())
        }
        fn set_text(&self, text: &str) -> Result<(), Failure> {
            self.log.borrow_mut().push(format!("{}:text:{text}", self.index));
            Ok(())
        }
        fn set_number(&self, value: f64) -> Result<(), Failure> {
            self.log.borrow_mut().push(format!("{}:number:{value}", self.index));
            Ok(())
        }
        fn grab_focus(&self) -> Result<(), Failure> {
            self.log.borrow_mut().push(format!("{}:focus", self.index));
            Ok(())
        }
        fn select(&self) -> Result<(), Failure> {
            self.log.borrow_mut().push(format!("{}:select", self.index));
            Ok(())
        }
        fn scroll_to(&self) -> Result<(), Failure> {
            self.log.borrow_mut().push(format!("{}:scroll", self.index));
            Ok(())
        }
    }

    fn facts(role: &str, name: Option<&str>, states: &[u32], interfaces: &[&str]) -> Facts {
        Facts {
            role_name: Some(normalize_role(role)),
            role: Some(0),
            name: name.map(str::to_string),
            states: States::with(states),
            extents: Some((10, 10, 100, 30)),
            interfaces: interfaces.iter().map(|s| s.to_string()).collect(),
            ..Facts::default()
        }
    }

    /// Окно: «Save» (кнопка), галочка, поле ввода, поле пароля, ползунок, пункт списка.
    fn dialog() -> Fake {
        use state::*;
        let shown = [SHOWING, VISIBLE, ENABLED, SENSITIVE];
        let mut entry = facts("text", Some("Имя"), &[SHOWING, ENABLED, SENSITIVE, EDITABLE, FOCUSABLE], &["Component", "Text", "EditableText", "Action"]);
        entry.text = Some("Егор".into());
        let mut password = facts("password text", Some("Пароль"), &[SHOWING, ENABLED, EDITABLE, FOCUSABLE], &["Component", "Text", "EditableText"]);
        password.text = Some("секрет".into());
        let mut slider = facts("slider", Some("Громкость"), &shown, &["Component", "Value"]);
        slider.number = Some(40.0);
        let tree = vec![
            (facts("frame", Some("Настройки"), &[SHOWING, ACTIVE], &["Component"]), vec![], vec![1, 2, 3, 4, 5, 6]),
            (facts("push button", Some("Save"), &[SHOWING, ENABLED, SENSITIVE, FOCUSABLE], &["Component", "Action"]), vec!["click".into()], vec![]),
            (facts("check box", Some("Автозапуск"), &[SHOWING, ENABLED, CHECKED], &["Component", "Action"]), vec!["click".into()], vec![]),
            (entry, vec!["activate".into()], vec![]),
            (password, vec![], vec![]),
            (slider, vec![], vec![]),
            (facts("list item", Some("Второй"), &[SHOWING, SELECTABLE], &["Component"]), vec![], vec![]),
        ];
        Fake { tree: Rc::new(tree), index: 0, log: Rc::default(), vanished: Rc::default() }
    }

    fn screen() -> Screen {
        Screen { left: 0.0, top: 0.0, width: 1920.0, height: 1080.0 }
    }

    fn later() -> Instant {
        Instant::now() + Duration::from_secs(5)
    }

    fn limits() -> WalkLimits {
        WalkLimits { max_nodes: 100, max_depth: 10, max_children_per_node: 100, max_text_chars: 240 }
    }

    #[test]
    fn role_names_are_normalized_across_toolkits() {
        assert_eq!(normalize_role("push button"), "push button");
        assert_eq!(normalize_role("push_button"), "push button");
        assert_eq!(normalize_role("PushButton"), "push button");
        assert_eq!(normalize_role("  Check Box "), "check box");
    }

    #[test]
    fn roles_speak_the_same_words_as_uia_on_windows() {
        assert_eq!(role_of(Some("push button")), "button");
        assert_eq!(role_of(Some("PushButton")), "button");
        assert_eq!(role_of(Some("check box")), "check_box");
        assert_eq!(role_of(Some("page tab")), "tab_item");
        assert_eq!(role_of(Some("entry")), "edit");
        assert_eq!(role_of(Some("password text")), "edit");
        assert_eq!(role_of(Some("label")), "text");
        assert_eq!(role_of(Some("frame")), "window");
        assert_eq!(role_of(Some("menu item")), "menu_item");
        assert_eq!(role_of(Some("document web")), "document");
        assert_eq!(role_of(Some("something new")), "custom");
        assert_eq!(role_of(None), "unknown");
    }

    #[test]
    fn states_come_in_two_words() {
        let states = States::from_words(&[1 << state::FOCUSED, 1 << (state::CHECKABLE - 32)]);
        assert!(states.has(state::FOCUSED));
        assert!(states.has(state::CHECKABLE));
        assert!(!states.has(state::CHECKED));
    }

    #[test]
    fn the_walk_reads_names_values_states_and_patterns() {
        let walk = walk(dialog(), limits(), later(), screen(), false);
        assert_eq!(walk.nodes.len(), 7);
        let by_name = |name: &str| walk.nodes.iter().find(|n| n.name.as_deref() == Some(name)).unwrap();
        let save = by_name("Save");
        assert_eq!(save.role, "button");
        assert_eq!(save.atspi_role.as_deref(), Some("push button"));
        assert!(save.patterns.contains(&"invoke"));
        assert_eq!(save.rect, Some((10, 10, 110, 40)));
        let check = by_name("Автозапуск");
        assert_eq!(check.checked, Some("on"));
        assert!(check.patterns.contains(&"toggle"));
        let entry = by_name("Имя");
        assert_eq!(entry.role, "edit");
        assert_eq!(entry.value.as_deref(), Some("Егор"));
        assert!(entry.patterns.contains(&"set_value"));
        assert_eq!(entry.focused, Some(false));
        let slider = by_name("Громкость");
        assert_eq!(slider.value.as_deref(), Some("40"));
        assert!(slider.patterns.contains(&"set_value"));
        let item = by_name("Второй");
        assert_eq!(item.selected, Some(false));
        assert!(item.patterns.contains(&"select"));
    }

    #[test]
    fn a_password_field_hides_its_value_and_refuses_set_value() {
        let root = dialog();
        let walk = walk(root.clone(), limits(), later(), screen(), false);
        let password = walk.nodes.iter().find(|n| n.name.as_deref() == Some("Пароль")).unwrap();
        assert!(password.secure);
        assert_eq!(password.value, None, "значение пароля не читается");
        assert!(!password.patterns.contains(&"set_value"));
        let element = Fake { index: 4, ..root.clone() };
        let said = perform(&element, password, Act::SetValue, Some("x"), || Ok(())).unwrap_err();
        assert!(said.contains("password field"), "{said}");
        assert!(root.log.borrow().is_empty(), "ни одного вызова в чужое приложение");
    }

    #[test]
    fn invoke_presses_and_set_value_writes_into_the_field() {
        let root = dialog();
        let walk = walk(root.clone(), limits(), later(), screen(), false);
        let save = walk.nodes[1].clone();
        perform(&Fake { index: 1, ..root.clone() }, &save, Act::Invoke, None, || Ok(())).unwrap();
        let entry = walk.nodes[3].clone();
        perform(&Fake { index: 3, ..root.clone() }, &entry, Act::SetValue, Some("Привет"), || Ok(())).unwrap();
        let slider = walk.nodes[5].clone();
        perform(&Fake { index: 5, ..root.clone() }, &slider, Act::SetValue, Some("7,5"), || Ok(())).unwrap();
        assert_eq!(
            *root.log.borrow(),
            vec!["1:action:click".to_string(), "3:text:Привет".into(), "5:number:7.5".into()]
        );
    }

    #[test]
    fn a_missing_pattern_is_refused_with_what_the_element_has() {
        let root = dialog();
        let walk = walk(root.clone(), limits(), later(), screen(), false);
        let item = walk.nodes[6].clone();
        let said = perform(&Fake { index: 6, ..root.clone() }, &item, Act::Invoke, None, || Ok(())).unwrap_err();
        assert!(said.contains("does not support"), "{said}");
        assert!(said.contains("select"), "названо, что есть: {said}");
    }

    #[test]
    fn the_guard_runs_before_the_change_and_can_stop_it() {
        let root = dialog();
        let walk = walk(root.clone(), limits(), later(), screen(), false);
        let save = walk.nodes[1].clone();
        let said = perform(&Fake { index: 1, ..root.clone() }, &save, Act::Invoke, None, || Err("moved".into())).unwrap_err();
        assert_eq!(said, "moved");
        assert!(root.log.borrow().is_empty());
    }

    #[test]
    fn every_limit_is_named_when_it_cuts() {
        let tight = WalkLimits { max_nodes: 3, ..limits() };
        let walk = walk(dialog(), tight, later(), screen(), false);
        assert_eq!(walk.nodes.len(), 3);
        assert_eq!(walk.truncated_by, vec!["max_nodes"]);
        assert_eq!(walk.discovered_unread, 4);
        let shallow = WalkLimits { max_depth: 0, ..limits() };
        let walk = super::walk(dialog(), shallow, later(), screen(), false);
        assert_eq!(walk.nodes.len(), 1);
        assert_eq!(walk.nodes[0].children_unread, Some("max_depth"));
    }

    #[test]
    fn a_vanished_element_is_absence_not_a_torn_search() {
        let root = dialog();
        root.vanished.borrow_mut().push(2);
        let select = Selector { name: Some("Save".into()), ..Selector::default() };
        let (found, scanned, limit) = sweep(&root, &select, 100, 10, later(), screen()).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(scanned, 7);
        assert_eq!(limit, None);
        let walk = walk(root, limits(), later(), screen(), false);
        assert_eq!(walk.nodes.len(), 6, "исчезнувший не выдуман узлом custom");
        assert_eq!(walk.read_failures, 1);
    }

    #[test]
    fn the_sweep_matches_by_dictionary_role_or_raw_role() {
        let root = dialog();
        let by_word = Selector { role: Some("button".into()), ..Selector::default() };
        let (found, _, _) = sweep(&root, &by_word, 100, 10, later(), screen()).unwrap();
        assert_eq!(found.len(), 1);
        let by_raw = Selector { role: Some("push button".into()), ..Selector::default() };
        let (found, _, _) = sweep(&root, &by_raw, 100, 10, later(), screen()).unwrap();
        assert_eq!(found.len(), 1);
    }

    #[test]
    fn visible_only_skips_what_is_not_showing() {
        let mut tree = (*dialog().tree).clone();
        tree[2].0.states = States::with(&[state::ENABLED]);
        let root = Fake { tree: Rc::new(tree), index: 0, log: Rc::default(), vanished: Rc::default() };
        let walk = walk(root, limits(), later(), screen(), true);
        assert_eq!(walk.skipped_offscreen, 1);
        assert_eq!(walk.nodes.len(), 6);
    }

    #[test]
    fn expand_on_an_expanded_node_does_not_collapse_it() {
        use state::*;
        let node_facts = facts("tree item", Some("Папка"), &[SHOWING, EXPANDABLE, EXPANDED], &["Component", "Action"]);
        let tree = vec![(node_facts, vec!["expand or contract".to_string()], vec![])];
        let root = Fake { tree: Rc::new(tree), index: 0, log: Rc::default(), vanished: Rc::default() };
        let node = node_from(&root, 0, None, 240, screen()).unwrap();
        assert_eq!(node.expanded, Some("expanded"));
        perform(&root, &node, Act::Expand, None, || Ok(())).unwrap();
        assert!(root.log.borrow().is_empty(), "раскрытый не сворачивается просьбой раскрыть");
        perform(&root, &node, Act::Collapse, None, || Ok(())).unwrap();
        assert_eq!(*root.log.borrow(), vec!["0:action:expand or contract".to_string()]);
    }
}
