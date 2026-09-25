//! Дерево окна через Accessibility (macOS) — то, что на Windows делает `uia.rs` через COM.
//!
//! Устройство — зеркало `uia.rs`, и это не эстетика: дерево Праксис (`read_window`,
//! `find_elements`, `act_element`) читает формы JSON и слова отказов, выученные на
//! Windows, и не должно заметить, что под ним другая ОС. Поэтому здесь те же пределы, тот
//! же обход в ширину, тот же отбор (`element::Selector`), те же квитанции
//! (`element::receipt`, `ambiguous_error`, `not_found`) и тот же рендер (`uia::render`).
//!
//! Файл разрезан на две части:
//! * чистая — таблица ролей, обход, отбор, описание узла, слова отказов. Она не знает об
//!   ОС: элемент дерева — это трейт [`Element`], и стенды гоняют её на подставном дереве
//!   на любой ОС (поэтому модуль собирается и под `test` вне macOS);
//! * живая ([`live`], только macOS) — `extern "C"` к HIServices (`AXUIElement*`) и обёртка
//!   `AxElement`, реализующая тот же трейт. Здесь и только здесь FFI к Accessibility;
//!   окна, масштаб и TCC — в `mac.rs`, их не дублируем.
//!
//! ⚠ Каждый вызов Accessibility — межпроцессное сообщение в чужое приложение. Поэтому
//! атрибуты узла читаются ОДНИМ `AXUIElementCopyMultipleAttributeValues` (аналог
//! `BuildCache` у UIA), дети — одним ранжированным `AXUIElementCopyAttributeValues` не
//! больше потолка, а зависшее приложение ограничено `AXUIElementSetMessagingTimeout`. Без
//! этого обход Electron-окна не укладывался бы ни в какой срок — тот же урок, что записан
//! в `uia.rs` про кэш (живая проба 10.09).
#![cfg(any(target_os = "macos", test))]
#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use std::collections::VecDeque;
use std::fmt;
use std::time::Instant;

use serde_json::{Map, Value, json};

use crate::element::{Act, NodeView, Selector};

// ─── роли ───────────────────────────────────────────────────────────────────────────────

/// Роль словами словаря UIA — ТЕМИ ЖЕ словами, что печатает `uia::role_name` на Windows
/// (`check_box`, `menu_item`, `tool_bar`: с подчёркиванием). Отбор `role` в дереве Праксис
/// выучен на них, и синоним без подчёркивания был бы промахом отбора на ровном месте.
///
/// Сырые `AXRole`/`AXSubrole` едут рядом отдельными полями узла, так что упрощение здесь
/// ничего не прячет. Незнакомая роль — `custom`, отсутствующая — `unknown` (как код 0 у
/// UIA). Родитель нужен ровно для одного случая: вкладки на macOS — это `AXRadioButton`
/// внутри `AXTabGroup`, и назвать их «переключателями» значило бы соврать про то, что
/// человек видит.
pub fn role_of(
    ax_role: Option<&str>,
    ax_subrole: Option<&str>,
    parent_ax_role: Option<&str>,
) -> &'static str {
    let Some(role) = ax_role.map(str::trim).filter(|role| !role.is_empty()) else {
        return "unknown";
    };
    let subrole = ax_subrole.unwrap_or("");
    match role {
        "AXWindow" | "AXSheet" => "window",
        "AXDrawer" | "AXPopover" | "AXScrollArea" | "AXSplitGroup" | "AXLayoutArea"
        | "AXMatte" | "AXGrowArea" => "pane",
        "AXButton" | "AXMenuButton" | "AXDisclosureTriangle" | "AXColorWell" | "AXDockItem" => {
            "button"
        }
        "AXPopUpButton" | "AXComboBox" => "combo_box",
        "AXCheckBox" => "check_box",
        "AXRadioButton" => {
            if parent_ax_role == Some("AXTabGroup") {
                "tab_item"
            } else {
                "radio_button"
            }
        }
        "AXRadioGroup" | "AXGroup" | "AXLayoutItem" | "AXColumn" => "group",
        "AXTabGroup" => "tab",
        // `AXSecureTextField` встречается и ролью, и подролью (у `AXTextField`). И то и
        // другое — поле ввода: `edit`. Что оно с паролем, говорит отдельное поле `secure`,
        // а не выдуманная роль, которой нет в словаре UIA.
        "AXTextField" | "AXTextArea" | "AXDateField" | "AXTimeField" | "AXSecureTextField" => {
            "edit"
        }
        "AXStaticText" | "AXHeading" => "text",
        "AXImage" => "image",
        "AXLink" => "hyperlink",
        "AXList" | "AXGrid" | "AXBrowser" => "list",
        "AXOutline" => "tree",
        "AXTable" => "table",
        "AXRow" => {
            if subrole == "AXOutlineRow" || parent_ax_role == Some("AXOutline") {
                "tree_item"
            } else {
                "list_item"
            }
        }
        "AXCell" => "data_item",
        "AXMenuBar" => "menu_bar",
        "AXMenuBarItem" | "AXMenuItem" => "menu_item",
        "AXMenu" => "menu",
        "AXToolbar" => "tool_bar",
        "AXScrollBar" => "scroll_bar",
        "AXSlider" => "slider",
        "AXValueIndicator" | "AXHandle" => "thumb",
        "AXSplitter" => "separator",
        "AXIncrementor" => "spinner",
        "AXProgressIndicator" | "AXBusyIndicator" | "AXLevelIndicator"
        | "AXRelevanceIndicator" => "progress_bar",
        "AXHelpTag" => "tool_tip",
        "AXWebArea" => "document",
        "AXUnknown" => "unknown",
        _ => "custom",
    }
}

/// Переключатель ли это для `toggle` (у UIA — TogglePattern): галочка, раскрывающий
/// треугольник, кнопка-переключатель или системный «выключатель» (`AXSwitch`).
fn is_toggle(ax_role: &str, ax_subrole: Option<&str>) -> bool {
    ax_role == "AXCheckBox"
        || ax_role == "AXDisclosureTriangle"
        || (ax_role == "AXButton" && matches!(ax_subrole, Some("AXToggle") | Some("AXSwitch")))
}

/// Поле пароля. macOS помечает его подролью `AXSecureTextField` (у `AXTextField`), а
/// кое-где — прямо ролью. Для нас это значит три вещи разом, и все три — про честность,
/// а не про удобство:
/// * значение НЕ читается. Система отдаёт туда точки, а не пароль, но прочитанное
///   уехало бы в кадр, в журнал и в расписку — и однажды это был бы настоящий пароль;
/// * `value_contains` по нему не ищет: искать нечего, пустое поле не должно выглядеть
///   совпадением;
/// * `set_value` отказан ДО вызова Accessibility: пароль в чужое поле кладёт владелец
///   руками. Тело умеет нажать и сфокусировать — этого хватит, чтобы он это сделал.
fn is_secure(ax_role: &str, ax_subrole: Option<&str>) -> bool {
    ax_role == "AXSecureTextField" || ax_subrole == Some("AXSecureTextField")
}

/// Слова отказа на `set_value` в поле пароля — одни и те же и в `patterns`, и в действии.
pub const SECURE_FIELD_REFUSAL: &str =
    "this is a password field (AXSecureTextField): set_value into it is refused before \
     Accessibility is even asked, and its value is never read — «поле пароля: значение туда \
     кладёт только владелец руками». invoke, focus and scroll_into_view still work, so you \
     can bring the field up and ask him to type";

// ─── что читается у элемента ────────────────────────────────────────────────────────────

/// Атрибуты, которые обход спрашивает у КАЖДОГО узла — одним сообщением. Порядок здесь и
/// в [`Attributes::from_values`] один и тот же, и стенд это держит.
pub const ATTRIBUTE_NAMES: [&str; 15] = [
    "AXRole",
    "AXSubrole",
    "AXTitle",
    "AXDescription",
    "AXLabel",
    "AXHelp",
    "AXRoleDescription",
    "AXIdentifier",
    "AXValue",
    "AXPosition",
    "AXSize",
    "AXEnabled",
    "AXFocused",
    "AXExpanded",
    "AXSelected",
];

/// Значение `AXValue` как оно есть: строка, число или булево. Всё остальное (элемент,
/// диапазон, что угодно) — [`AxValue::Other`] с именем типа: показать «что-то есть» честнее,
/// чем промолчать или выдать описание чужого объекта за текст.
#[derive(Debug, Clone, PartialEq)]
pub enum AxValue {
    Text(String),
    Number(f64),
    Bool(bool),
    Other(String),
}

impl AxValue {
    /// Текст для поля `value`. Число — без хвоста `.0`, чтобы «1» читалось как «1».
    pub fn text(&self) -> Option<String> {
        match self {
            Self::Text(text) => Some(text.clone()),
            Self::Number(number) => Some(if number.fract() == 0.0 && number.abs() < 1e15 {
                format!("{}", *number as i64)
            } else {
                format!("{number}")
            }),
            Self::Bool(value) => Some(value.to_string()),
            Self::Other(_) => None,
        }
    }

    fn number(&self) -> Option<f64> {
        match self {
            Self::Number(number) => Some(*number),
            Self::Bool(value) => Some(if *value { 1.0 } else { 0.0 }),
            _ => None,
        }
    }
}

/// Что элемент рассказал о себе. `None` — атрибута нет или он пуст для системы
/// (`kCFNull`, `kAXErrorNoValue`, `kAXErrorAttributeUnsupported`): это «не отдаёт», а не
/// «false», и наружу такое едет отсутствием ключа — как у UIA.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Attributes {
    pub role: Option<String>,
    pub subrole: Option<String>,
    pub title: Option<String>,
    pub description: Option<String>,
    pub label: Option<String>,
    pub help: Option<String>,
    pub role_description: Option<String>,
    pub identifier: Option<String>,
    pub value: Option<AxValue>,
    /// Пункты, глобально, начало сверху слева (CoreGraphics).
    pub position: Option<(f64, f64)>,
    pub size: Option<(f64, f64)>,
    pub enabled: Option<bool>,
    pub focused: Option<bool>,
    pub expanded: Option<bool>,
    pub selected: Option<bool>,
}

/// Один ответ Accessibility на один атрибут — до разбора по полям.
#[derive(Debug, Clone, PartialEq)]
pub enum Raw {
    Text(String),
    Number(f64),
    Bool(bool),
    Point(f64, f64),
    Size(f64, f64),
    Other(String),
}

impl Attributes {
    /// Разложить ответы в порядке [`ATTRIBUTE_NAMES`] по полям. Строковые поля — только
    /// текст (число в `AXTitle` — не заголовок); `AXValue` берётся любым.
    pub fn from_values(values: &[Option<Raw>]) -> Self {
        let text = |index: usize| -> Option<String> {
            match values.get(index)? {
                Some(Raw::Text(text)) => Some(text.clone()),
                _ => None,
            }
        };
        let flag = |index: usize| -> Option<bool> {
            match values.get(index)? {
                Some(Raw::Bool(value)) => Some(*value),
                Some(Raw::Number(number)) => Some(*number != 0.0),
                _ => None,
            }
        };
        Self {
            role: text(0),
            subrole: text(1),
            title: text(2),
            description: text(3),
            label: text(4),
            help: text(5),
            role_description: text(6),
            identifier: text(7),
            value: match values.get(8) {
                Some(Some(Raw::Text(text))) => Some(AxValue::Text(text.clone())),
                Some(Some(Raw::Number(number))) => Some(AxValue::Number(*number)),
                Some(Some(Raw::Bool(value))) => Some(AxValue::Bool(*value)),
                Some(Some(Raw::Point(..))) => Some(AxValue::Other("CGPoint".into())),
                Some(Some(Raw::Size(..))) => Some(AxValue::Other("CGSize".into())),
                Some(Some(Raw::Other(kind))) => Some(AxValue::Other(kind.clone())),
                _ => None,
            },
            position: match values.get(9) {
                Some(Some(Raw::Point(x, y))) => Some((*x, *y)),
                _ => None,
            },
            size: match values.get(10) {
                Some(Some(Raw::Size(width, height))) => Some((*width, *height)),
                _ => None,
            },
            enabled: flag(11),
            focused: flag(12),
            expanded: flag(13),
            selected: flag(14),
        }
    }
}

/// Отказ Accessibility — код и что делали. Коды — из `AXError.h`; имя, а не число,
/// потому что число никому ничего не скажет, а «api_disabled» скажет всё.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AxFailure {
    pub code: i32,
    pub what: String,
}

pub const AX_ERROR_SUCCESS: i32 = 0;
pub const AX_ERROR_FAILURE: i32 = -25200;
pub const AX_ERROR_ILLEGAL_ARGUMENT: i32 = -25201;
pub const AX_ERROR_INVALID_UI_ELEMENT: i32 = -25202;
pub const AX_ERROR_INVALID_UI_ELEMENT_OBSERVER: i32 = -25203;
pub const AX_ERROR_CANNOT_COMPLETE: i32 = -25204;
pub const AX_ERROR_ATTRIBUTE_UNSUPPORTED: i32 = -25205;
pub const AX_ERROR_ACTION_UNSUPPORTED: i32 = -25206;
pub const AX_ERROR_NOTIFICATION_UNSUPPORTED: i32 = -25207;
pub const AX_ERROR_NOT_IMPLEMENTED: i32 = -25208;
pub const AX_ERROR_NOTIFICATION_ALREADY_REGISTERED: i32 = -25209;
pub const AX_ERROR_NOTIFICATION_NOT_REGISTERED: i32 = -25210;
pub const AX_ERROR_API_DISABLED: i32 = -25211;
pub const AX_ERROR_NO_VALUE: i32 = -25212;
pub const AX_ERROR_PARAMETERIZED_ATTRIBUTE_UNSUPPORTED: i32 = -25213;
pub const AX_ERROR_NOT_ENOUGH_PRECISION: i32 = -25214;

pub fn ax_error_name(code: i32) -> String {
    let known = match code {
        AX_ERROR_SUCCESS => "success",
        AX_ERROR_FAILURE => "failure",
        AX_ERROR_ILLEGAL_ARGUMENT => "illegal_argument",
        AX_ERROR_INVALID_UI_ELEMENT => "invalid_ui_element",
        AX_ERROR_INVALID_UI_ELEMENT_OBSERVER => "invalid_ui_element_observer",
        AX_ERROR_CANNOT_COMPLETE => "cannot_complete",
        AX_ERROR_ATTRIBUTE_UNSUPPORTED => "attribute_unsupported",
        AX_ERROR_ACTION_UNSUPPORTED => "action_unsupported",
        AX_ERROR_NOTIFICATION_UNSUPPORTED => "notification_unsupported",
        AX_ERROR_NOT_IMPLEMENTED => "not_implemented",
        AX_ERROR_NOTIFICATION_ALREADY_REGISTERED => "notification_already_registered",
        AX_ERROR_NOTIFICATION_NOT_REGISTERED => "notification_not_registered",
        AX_ERROR_API_DISABLED => "api_disabled",
        AX_ERROR_NO_VALUE => "no_value",
        AX_ERROR_PARAMETERIZED_ATTRIBUTE_UNSUPPORTED => "parameterized_attribute_unsupported",
        AX_ERROR_NOT_ENOUGH_PRECISION => "not_enough_precision",
        // Незнакомый код — числом, а не «failure»: пусть лучше увидят непонятное, чем
        // поверят в понятное и неверное (то же правило, что у ролей UIA).
        _ => return format!("ax_error_{code}"),
    };
    known.to_string()
}

impl AxFailure {
    pub fn new(code: i32, what: impl Into<String>) -> Self {
        Self { code, what: what.into() }
    }

    /// Элемента больше нет. Это единственный отказ, который обход считает СВИДЕТЕЛЬСТВОМ
    /// (элемент исчез — значит, его нет), а не помехой: у Electron элементы пропадают
    /// между обнаружением и чтением постоянно, и рвать из-за этого весь поиск значило бы
    /// сделать действие над таким окном лотереей.
    pub fn vanished(&self) -> bool {
        self.code == AX_ERROR_INVALID_UI_ELEMENT
    }

    /// «Универсальный доступ» отозвали посреди дела.
    pub fn api_disabled(&self) -> bool {
        self.code == AX_ERROR_API_DISABLED
    }
}

impl fmt::Display for AxFailure {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}: {} ({})", self.what, ax_error_name(self.code), self.code)
    }
}

/// Элемент дерева глазами обхода. Живая реализация — `live::AxElement`; стендовая — в
/// `tests::fake`. Всё, что обход и действие знают об элементе, проходит через эти семь
/// методов, и ни один из них не подразумевает ОС.
pub trait Element: Clone {
    /// Всё об элементе — одним сообщением.
    fn attributes(&self) -> Result<Attributes, AxFailure>;
    /// Имена действий (`AXPress`, `AXShowMenu`, `AXScrollToVisible`, …).
    fn actions(&self) -> Result<Vec<String>, AxFailure>;
    /// Можно ли писать в атрибут. Отказ считается за «нельзя».
    fn settable(&self, attribute: &str) -> bool;
    /// Не больше `max` детей и флаг «есть ещё».
    fn children(&self, max: u64) -> Result<(Vec<Self>, bool), AxFailure>;
    fn perform(&self, action: &str) -> Result<(), AxFailure>;
    fn set_text(&self, attribute: &str, text: &str) -> Result<(), AxFailure>;
    fn set_bool(&self, attribute: &str, value: bool) -> Result<(), AxFailure>;
}

// ─── узел ───────────────────────────────────────────────────────────────────────────────

/// Что можно сделать над элементом — те же восемь слов, что у `element::Act`, и каждое
/// выведено из того, что элемент сам сказал: действий и записываемых атрибутов.
pub fn patterns_of<E: Element>(
    element: &E,
    ax_role: &str,
    ax_subrole: Option<&str>,
    attributes: &Attributes,
    actions: &[String],
) -> Vec<&'static str> {
    let has = |name: &str| actions.iter().any(|action| action == name);
    let mut out = Vec::new();
    if has("AXPress") {
        out.push("invoke");
    }
    // Проверка записываемости — сообщение в чужой процесс, поэтому её задают только про
    // атрибуты, которые у элемента ЕСТЬ: спрашивать «можно ли писать в AXExpanded» у
    // кнопки без AXExpanded — тратить время на заведомое «нет».
    // Поле пароля не получает `set_value`, даже если AX говорит «пиши»: обещать глагол,
    // который отказан, — это промах модели на ровном месте.
    if attributes.value.is_some()
        && !is_secure(ax_role, ax_subrole)
        && element.settable("AXValue")
    {
        out.push("set_value");
    }
    if has("AXPress") && is_toggle(ax_role, ax_subrole) {
        out.push("toggle");
    }
    let expanded_settable = attributes.expanded.is_some() && element.settable("AXExpanded");
    if expanded_settable || has("AXShowMenu") {
        out.push("expand");
    }
    if expanded_settable {
        out.push("collapse");
    }
    if attributes.selected.is_some() && element.settable("AXSelected") {
        out.push("select");
    }
    if has("AXScrollToVisible") {
        out.push("scroll_into_view");
    }
    if attributes.focused.is_some() && element.settable("AXFocused") {
        out.push("focus");
    }
    out
}

/// Прочитанный узел — ровно те поля, что у `uia::Node`, плюс сырые роль/подроль и
/// `patterns`. Прямоугольник — пункты (`left, top, right, bottom`).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct AxNode {
    pub parent: Option<usize>,
    pub depth: u64,
    pub role: &'static str,
    pub ax_role: Option<String>,
    pub ax_subrole: Option<String>,
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
    /// Поле пароля: `value` у такого узла всегда `None` — не «пусто», а «не читаем».
    pub secure: bool,
    pub text_truncated: bool,
    pub children_unread: Option<&'static str>,
}

impl AxNode {
    /// Узел глазами отбора: роль словаря. Второй взгляд — [`AxNode::raw_view`].
    pub fn view(&self) -> NodeView<'_> {
        NodeView {
            role: self.role,
            name: self.name.as_deref(),
            value: self.value.as_deref(),
            automation_id: self.automation_id.as_deref(),
        }
    }

    /// Тот же узел, но роль — сырая `AXRole`: она сказала `role: "AXButton"`, списав с
    /// ответа чтения, — и это тоже должно подойти.
    pub fn raw_view(&self) -> Option<NodeView<'_>> {
        Some(NodeView {
            role: self.ax_role.as_deref()?,
            name: self.name.as_deref(),
            value: self.value.as_deref(),
            automation_id: self.automation_id.as_deref(),
        })
    }

    /// Подходит ли под отбор — по роли словаря ИЛИ по сырой роли.
    pub fn matches(&self, select: &Selector) -> bool {
        select.matches(&self.view()) || self.raw_view().is_some_and(|view| select.matches(&view))
    }

    /// Элемент словами — тем же набором полей, что у чтения окна, чтобы расписка действия,
    /// список кандидатов и расписка чтения читались одинаково.
    pub fn describe(&self) -> Value {
        let mut object = Map::new();
        object.insert("role".into(), json!(self.role));
        for (key, value) in [
            ("ax_role", self.ax_role.as_ref()),
            ("ax_subrole", self.ax_subrole.as_ref()),
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

/// Та же форма прямоугольника, что у `uia::Rect::json`: с шириной, высотой и центром —
/// точкой, которой можно ткнуть (`desktop.input.perform` на macOS считает в пунктах).
pub fn rect_json(left: i32, top: i32, right: i32, bottom: i32) -> Value {
    json!({
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
        "center": {
            "x": left + (right - left) / 2,
            "y": top + (bottom - top) / 2,
        },
    })
}

/// Экран, накрывающий все дисплеи (пункты): всё, что вне него, — `offscreen`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Screen {
    pub left: f64,
    pub top: f64,
    pub width: f64,
    pub height: f64,
}

impl Screen {
    fn intersects(&self, left: f64, top: f64, right: f64, bottom: f64) -> bool {
        right > self.left
            && bottom > self.top
            && left < self.left + self.width
            && top < self.top + self.height
    }
}

/// Обрезка по единицам UTF-16 — так считает длину и CFString, и Windows; копия правила из
/// `uia::clip_utf16`, чтобы предел `max_text_chars` значил одно и то же на обеих ОС.
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

/// Прочитать один элемент в узел. `max_text_chars` — потолок надписей (`u64::MAX` —
/// без обрезки: так читают поиск и действие, как UIA читает кэш целиком).
pub fn node_from<E: Element>(
    element: &E,
    depth: u64,
    parent: Option<usize>,
    parent_ax_role: Option<&str>,
    max_text_chars: u64,
    screen: Screen,
) -> Result<AxNode, AxFailure> {
    let attributes = element.attributes()?;
    let actions = element.actions().unwrap_or_default();
    let ax_role = attributes.role.clone();
    let role = role_of(ax_role.as_deref(), attributes.subrole.as_deref(), parent_ax_role);
    let patterns = patterns_of(
        element,
        ax_role.as_deref().unwrap_or(""),
        attributes.subrole.as_deref(),
        &attributes,
        &actions,
    );

    let mut truncated = false;
    let mut text = |value: Option<String>| -> Option<String> {
        let value = value?;
        if value.is_empty() {
            return None;
        }
        let (clipped, cut) = clip_utf16(&value, max_text_chars);
        truncated |= cut;
        Some(clipped)
    };

    // Имя: AXTitle ∥ AXDescription ∥ AXLabel ∥ AXHelp; у статического текста имя — его
    // значение (так же UIA называет Text: надпись и есть имя). Значение тогда не
    // повторяется вторым полем: оно то же самое.
    let is_static_text = matches!(ax_role.as_deref(), Some("AXStaticText") | Some("AXHeading"));
    // Поле пароля: значение не читается вовсе — ни в узел, ни в отбор, ни в расписку.
    let secure = is_secure(ax_role.as_deref().unwrap_or(""), attributes.subrole.as_deref());
    let value_text = if secure {
        None
    } else {
        attributes.value.as_ref().and_then(AxValue::text)
    };
    let mut named_by_value = false;
    let name = attributes
        .title
        .clone()
        .filter(|s| !s.is_empty())
        .or_else(|| attributes.description.clone().filter(|s| !s.is_empty()))
        .or_else(|| attributes.label.clone().filter(|s| !s.is_empty()))
        .or_else(|| attributes.help.clone().filter(|s| !s.is_empty()))
        .or_else(|| {
            if is_static_text {
                named_by_value = true;
                value_text.clone()
            } else {
                None
            }
        });
    let value = if named_by_value { None } else { value_text };

    let rect = match (attributes.position, attributes.size) {
        (Some((x, y)), Some((width, height))) if width > 0.0 && height > 0.0 => Some((
            x.round() as i32,
            y.round() as i32,
            (x + width).round() as i32,
            (y + height).round() as i32,
        )),
        _ => None,
    };
    // Без прямоугольника элемент не на экране (пункты меню закрытого меню, служебные
    // обёртки); с прямоугольником — смотрим, пересекает ли он хоть один дисплей.
    let offscreen = Some(match rect {
        None => true,
        Some((left, top, right, bottom)) => {
            !screen.intersects(left as f64, top as f64, right as f64, bottom as f64)
        }
    });

    let ax_role_str = ax_role.as_deref().unwrap_or("");
    let number = attributes.value.as_ref().and_then(AxValue::number);
    let checked = if is_toggle(ax_role_str, attributes.subrole.as_deref()) {
        number.map(|n| match n as i64 {
            0 => "off",
            1 => "on",
            _ => "mixed",
        })
    } else {
        None
    };
    // Радиокнопка (и вкладка — это она же) выбрана, когда её значение 1; строки и ячейки
    // говорят об этом прямо через AXSelected.
    let selected = attributes.selected.or_else(|| {
        if ax_role_str == "AXRadioButton" {
            number.map(|n| n as i64 == 1)
        } else {
            None
        }
    });
    let expanded = attributes
        .expanded
        .map(|value| if value { "expanded" } else { "collapsed" });

    Ok(AxNode {
        parent,
        depth,
        role,
        ax_role: text(ax_role.clone()),
        ax_subrole: text(attributes.subrole.clone()),
        localized_role: text(attributes.role_description.clone()),
        name: text(name),
        value: text(value),
        automation_id: text(attributes.identifier.clone()),
        rect,
        enabled: attributes.enabled,
        focused: attributes.focused,
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

/// Пределы обхода — те же, что у `uia::Limits`, без срока: срок приезжает мгновением.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct WalkLimits {
    pub max_nodes: u64,
    pub max_depth: u64,
    pub max_children_per_node: u64,
    pub max_text_chars: u64,
}

/// Результат обхода — счётчики те же, что у `uia::Walk`, плюс число элементов, которые
/// Accessibility отказалась читать (они видны в дереве как `custom` без атрибутов).
#[derive(Debug, Default, PartialEq)]
pub struct AxWalk {
    pub nodes: Vec<AxNode>,
    pub discovered_unread: u64,
    pub subtrees_unread: u64,
    pub skipped_offscreen: u64,
    pub depth_reached: u64,
    pub truncated_by: Vec<&'static str>,
    pub walk_ms: u64,
    pub read_failures: u64,
    pub first_read_failure: Option<String>,
}

impl AxWalk {
    fn mark(&mut self, reason: &'static str) {
        if !self.truncated_by.contains(&reason) {
            self.truncated_by.push(reason);
        }
    }
}

/// Обход в ширину с теми же пределами и в том же порядке проверок, что `uia_walk`:
/// потолок узлов и срок — перед каждым узлом, глубина и ширина — при раскрытии детей.
pub fn walk<E: Element>(
    root: E,
    limits: WalkLimits,
    deadline: Instant,
    screen: Screen,
    visible_only: bool,
) -> AxWalk {
    let started = Instant::now();
    let mut walk = AxWalk::default();
    let mut queue: VecDeque<(E, u64, Option<usize>, Option<String>)> = VecDeque::new();
    queue.push_back((root, 0, None, None));

    while !queue.is_empty() {
        if walk.nodes.len() as u64 >= limits.max_nodes {
            walk.mark("max_nodes");
            break;
        }
        if Instant::now() >= deadline {
            walk.mark("timeout");
            break;
        }
        let (element, depth, parent, parent_role) = queue.pop_front().expect("queue is not empty");
        let mut node = match node_from(
            &element,
            depth,
            parent,
            parent_role.as_deref(),
            limits.max_text_chars,
            screen,
        ) {
            Ok(node) => node,
            Err(failure) => {
                walk.read_failures += 1;
                if walk.first_read_failure.is_none() {
                    walk.first_read_failure = Some(failure.to_string());
                }
                if failure.api_disabled() {
                    // Разрешение отозвали на ходу: дальше — только ложь, останавливаемся
                    // и называем причину.
                    walk.mark("api_disabled");
                    break;
                }
                AxNode { parent, depth, role: "custom", ..AxNode::default() }
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
                        queue.push_back((child, depth + 1, Some(index), node.ax_role.clone()));
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

/// Кто подошёл: сам элемент (чтобы над ним действовать), узел и его описание.
#[derive(Debug)]
pub struct Hit<E> {
    pub element: E,
    pub node: AxNode,
    pub json: Value,
    /// Сырая роль родителя — нужна, чтобы перечитать элемент перед ударом ТЕМ ЖЕ
    /// словарём: вкладка без родителя стала бы «radio_button» и ложно «изменилась».
    pub parent_ax_role: Option<String>,
}

/// Обход окна ОДИН на обе руки — поиск и действие, как `sweep` у UIA: возвращает, кто
/// подошёл, сколько узлов осмотрено и во что упёрся обход (`max_nodes`, `max_depth`,
/// `timeout`) или `None`, если дочитал окно.
///
/// Отказ Accessibility при чтении узла — не свидетельство «не подошёл»: он рвёт поиск
/// словами (как у UIA — «search incomplete»). Исключение одно — элемент исчез
/// (`invalid_ui_element`): его нет, и это ответ.
pub fn sweep<E: Element>(
    root: &E,
    select: &Selector,
    max_nodes: u64,
    max_depth: u64,
    deadline: Instant,
    screen: Screen,
) -> Result<(Vec<Hit<E>>, usize, Option<&'static str>), AxFailure> {
    let mut found: Vec<Hit<E>> = Vec::new();
    let mut queue: VecDeque<(E, u64, Option<String>)> = VecDeque::new();
    queue.push_back((root.clone(), 0, None));
    let mut scanned = 0usize;
    let mut hit_limit: Option<&'static str> = None;
    while let Some((element, depth, parent_role)) = queue.pop_front() {
        if scanned as u64 >= max_nodes {
            hit_limit = Some("max_nodes");
            break;
        }
        // Срок проверяется на КАЖДОМ проходе, включая первый (урок живой пробы 10.09).
        if Instant::now() >= deadline {
            hit_limit = Some("timeout");
            break;
        }
        scanned += 1;
        let node = match node_from(&element, depth, None, parent_role.as_deref(), u64::MAX, screen) {
            Ok(node) => node,
            Err(failure) if failure.vanished() => continue,
            Err(failure) => {
                return Err(AxFailure::new(
                    failure.code,
                    format!("selector attributes: search incomplete: {}", failure.what),
                ));
            }
        };
        if node.matches(select) {
            found.push(Hit {
                element: element.clone(),
                json: node.describe(),
                node: node.clone(),
                parent_ax_role: parent_role.clone(),
            });
        }
        let (children, more) = match element.children(max_nodes) {
            Ok(children) => children,
            Err(failure) if failure.vanished() => (Vec::new(), false),
            Err(failure) => {
                return Err(AxFailure::new(
                    failure.code,
                    format!("children: search incomplete: {}", failure.what),
                ));
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
            queue.push_back((child, depth + 1, node.ax_role.clone()));
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

/// Сделать над элементом. Никаких откатов к координатам: нет нужного — отказ называет
/// то, что у элемента ЕСТЬ (слова те же, что у UIA). `guard` зовётся прямо перед
/// изменением — как у UIA, чтобы окно, уехавшее за время поиска, не получило удар.
pub fn perform<E: Element>(
    element: &E,
    node: &AxNode,
    act: Act,
    text: Option<&str>,
    guard: impl Fn() -> Result<(), String>,
) -> Result<(), String> {
    let missing = |want: &str| -> String {
        let have = if node.patterns.is_empty() {
            "none".to_string()
        } else {
            node.patterns.join(", ")
        };
        format!(
            "this element does not support {want}; patterns it does support: {have}. \
             The verb refuses to fall back to a click at coordinates - that is the \
             unreliability it exists to avoid"
        )
    };
    let failed = |what: &str, failure: AxFailure| -> String { format!("{what}: {failure}") };
    // Пароль — раньше всего остального и БЕЗ единого сообщения в чужое приложение (даже
    // спрашивать действия не за чем): значение туда кладёт владелец руками.
    if act == Act::SetValue
        && (node.secure
            || is_secure(node.ax_role.as_deref().unwrap_or(""), node.ax_subrole.as_deref()))
    {
        return Err(SECURE_FIELD_REFUSAL.to_string());
    }
    // Свежий взгляд в момент действия, а не то, что помнил обход: между ними окно могло
    // перерисоваться (у UIA — `GetCurrentPattern` вместо кэша).
    let actions = element.actions().map_err(|f| failed("AXUIElementCopyActionNames", f))?;
    let has = |name: &str| actions.iter().any(|action| action == name);
    match act {
        Act::Invoke => {
            if !has("AXPress") {
                return Err(missing("AXPress (invoke)"));
            }
            guard()?;
            element.perform("AXPress").map_err(|f| failed("AXPress", f))?;
        }
        Act::SetValue => {
            if !element.settable("AXValue") {
                return Err(missing("a settable AXValue (set_value)"));
            }
            guard()?;
            element
                .set_text("AXValue", text.unwrap_or_default())
                .map_err(|f| failed("AXUIElementSetAttributeValue(AXValue)", f))?;
        }
        Act::Toggle => {
            let ax_role = node.ax_role.as_deref().unwrap_or("");
            if !(has("AXPress") && is_toggle(ax_role, node.ax_subrole.as_deref())) {
                return Err(missing("AXPress on a check box or switch (toggle)"));
            }
            guard()?;
            element.perform("AXPress").map_err(|f| failed("AXPress", f))?;
        }
        Act::Expand => {
            if element.settable("AXExpanded") {
                guard()?;
                element
                    .set_bool("AXExpanded", true)
                    .map_err(|f| failed("AXUIElementSetAttributeValue(AXExpanded)", f))?;
            } else if has("AXShowMenu") {
                guard()?;
                element.perform("AXShowMenu").map_err(|f| failed("AXShowMenu", f))?;
            } else {
                return Err(missing("a settable AXExpanded or AXShowMenu (expand)"));
            }
        }
        Act::Collapse => {
            if !element.settable("AXExpanded") {
                return Err(missing("a settable AXExpanded (collapse)"));
            }
            guard()?;
            element
                .set_bool("AXExpanded", false)
                .map_err(|f| failed("AXUIElementSetAttributeValue(AXExpanded)", f))?;
        }
        Act::Select => {
            if !element.settable("AXSelected") {
                return Err(missing("a settable AXSelected (select)"));
            }
            guard()?;
            element
                .set_bool("AXSelected", true)
                .map_err(|f| failed("AXUIElementSetAttributeValue(AXSelected)", f))?;
        }
        Act::ScrollIntoView => {
            if !has("AXScrollToVisible") {
                return Err(missing("AXScrollToVisible (scroll_into_view)"));
            }
            guard()?;
            element
                .perform("AXScrollToVisible")
                .map_err(|f| failed("AXScrollToVisible", f))?;
        }
        Act::Focus => {
            if !element.settable("AXFocused") {
                return Err(missing("a settable AXFocused (focus)"));
            }
            guard()?;
            element
                .set_bool("AXFocused", true)
                .map_err(|f| failed("AXUIElementSetAttributeValue(AXFocused)", f))?;
        }
    }
    Ok(())
}

// ─── слова отказов ──────────────────────────────────────────────────────────────────────

/// Отказ до дела: «Универсального доступа» нет. Слова — со ссылкой на настройку из
/// `mac::Tcc::hints()`, потому что отказ без дороги к починке — не ответ.
pub fn accessibility_refusal(capability: &str, hints: &[&str]) -> String {
    let mut words = format!(
        "{capability} on macOS needs the «Универсальный доступ» (Accessibility) permission, \
         and the system says Helene does not have it: without it the window tree cannot be \
         read and elements cannot be acted on"
    );
    if hints.is_empty() {
        words.push('.');
    } else {
        words.push_str(". ");
        words.push_str(&hints.join("; "));
    }
    words
}

// ─── живая часть ────────────────────────────────────────────────────────────────────────

#[cfg(target_os = "macos")]
pub mod live {
    use std::ffi::c_void;
    use std::ptr;
    use std::sync::OnceLock;
    use std::time::Duration;

    use core_foundation::array::{CFArray, CFArrayRef};
    use core_foundation::base::{
        Boolean, CFCopyTypeIDDescription, CFGetTypeID, CFIndex, CFNullGetTypeID, CFType,
        CFTypeID, CFTypeRef, TCFType,
    };
    use core_foundation::boolean::CFBoolean;
    use core_foundation::bundle::CFBundle;
    use core_foundation::number::CFNumber;
    use core_foundation::string::{CFString, CFStringRef};
    use core_foundation::url::CFURL;
    use core_graphics::geometry::{CGPoint, CGSize};
    use core_graphics::window::CGWindowID;

    use super::{
        ATTRIBUTE_NAMES, AX_ERROR_ATTRIBUTE_UNSUPPORTED, AX_ERROR_NO_VALUE, AX_ERROR_SUCCESS,
        Attributes, AxFailure, Element, Raw,
    };
    use crate::mac;

    type AXUIElementRef = CFTypeRef;
    type AXValueRef = CFTypeRef;
    type AXError = i32;
    type AXValueType = u32;

    const AX_VALUE_TYPE_CGPOINT: AXValueType = 1;
    const AX_VALUE_TYPE_CGSIZE: AXValueType = 2;
    const AX_VALUE_TYPE_AXERROR: AXValueType = 5;

    #[link(name = "ApplicationServices", kind = "framework")]
    unsafe extern "C" {
        fn AXUIElementCreateApplication(pid: libc::pid_t) -> AXUIElementRef;
        fn AXUIElementCreateSystemWide() -> AXUIElementRef;
        fn AXUIElementGetTypeID() -> CFTypeID;
        fn AXUIElementCopyAttributeValue(
            element: AXUIElementRef,
            attribute: CFStringRef,
            value: *mut CFTypeRef,
        ) -> AXError;
        fn AXUIElementCopyAttributeValues(
            element: AXUIElementRef,
            attribute: CFStringRef,
            index: CFIndex,
            max_values: CFIndex,
            values: *mut CFArrayRef,
        ) -> AXError;
        fn AXUIElementCopyMultipleAttributeValues(
            element: AXUIElementRef,
            attributes: CFArrayRef,
            options: u32,
            values: *mut CFArrayRef,
        ) -> AXError;
        fn AXUIElementCopyAttributeNames(element: AXUIElementRef, names: *mut CFArrayRef) -> AXError;
        fn AXUIElementCopyActionNames(element: AXUIElementRef, names: *mut CFArrayRef) -> AXError;
        fn AXUIElementPerformAction(element: AXUIElementRef, action: CFStringRef) -> AXError;
        fn AXUIElementSetAttributeValue(
            element: AXUIElementRef,
            attribute: CFStringRef,
            value: CFTypeRef,
        ) -> AXError;
        fn AXUIElementIsAttributeSettable(
            element: AXUIElementRef,
            attribute: CFStringRef,
            settable: *mut Boolean,
        ) -> AXError;
        fn AXUIElementGetPid(element: AXUIElementRef, pid: *mut libc::pid_t) -> AXError;
        fn AXUIElementSetMessagingTimeout(element: AXUIElementRef, timeout_in_seconds: f32) -> AXError;
        fn AXValueGetTypeID() -> CFTypeID;
        fn AXValueGetType(value: AXValueRef) -> AXValueType;
        fn AXValueGetValue(value: AXValueRef, the_type: AXValueType, value_ptr: *mut c_void) -> Boolean;
    }

    /// `_AXUIElementGetWindow` — приватная функция HIServices, единственный прямой мост
    /// AXWindow ↔ CGWindowID. Берём её через `dlsym`, а не через `extern`: исчезни она в
    /// следующей macOS — тело продолжит собираться и пойдёт запасным путём (сопоставление
    /// по pid, заголовку и рамке), а не перестанет линковаться.
    type GetWindowFn = unsafe extern "C" fn(AXUIElementRef, *mut CGWindowID) -> AXError;

    pub(crate) fn private_get_window() -> Option<GetWindowFn> {
        static CELL: OnceLock<Option<usize>> = OnceLock::new();
        let address = *CELL.get_or_init(|| {
            let symbol = unsafe { libc::dlsym(libc::RTLD_DEFAULT, c"_AXUIElementGetWindow".as_ptr()) };
            if symbol.is_null() { None } else { Some(symbol as usize) }
        });
        // SAFETY: адрес взят у dlsym для функции с известной сигнатурой из HIServices.
        address.map(|address| unsafe { std::mem::transmute::<usize, GetWindowFn>(address) })
    }

    fn key(name: &str) -> CFString {
        CFString::new(name)
    }

    /// Обёртка над `AXUIElementRef`. Живёт на потоке, который её создал: CF-объект можно
    /// передавать между потоками, но здесь это не нужно, и `Send` нарочно не объявлен.
    #[derive(Clone)]
    pub struct AxElement {
        handle: CFType,
        /// Срок одного сообщения этому элементу, секунды. Наследуется детьми: срок,
        /// поставленный на элемент, действует только на него самого, поэтому каждый новый
        /// элемент получает его при рождении (операция локальная, без сообщения).
        timeout: f32,
    }

    impl AxElement {
        fn wrap(raw: AXUIElementRef, timeout: f32) -> Option<Self> {
            if raw.is_null() {
                return None;
            }
            // SAFETY: raw — живой AXUIElementRef, полученный по правилу Get (из массива):
            // счётчик поднимается здесь и опускается в Drop у CFType.
            let handle = unsafe { CFType::wrap_under_get_rule(raw) };
            let element = Self { handle, timeout };
            element.apply_timeout();
            Some(element)
        }

        fn own(raw: AXUIElementRef, timeout: f32) -> Option<Self> {
            if raw.is_null() {
                return None;
            }
            // SAFETY: raw — только что созданный AXUIElementRef по правилу Create.
            let handle = unsafe { CFType::wrap_under_create_rule(raw) };
            let element = Self { handle, timeout };
            element.apply_timeout();
            Some(element)
        }

        fn apply_timeout(&self) {
            if self.timeout > 0.0 {
                unsafe { AXUIElementSetMessagingTimeout(self.raw(), self.timeout) };
            }
        }

        fn raw(&self) -> AXUIElementRef {
            self.handle.as_CFTypeRef()
        }

        /// Элемент приложения по pid. Создаётся всегда — есть ли у процесса Accessibility,
        /// выяснится первым сообщением.
        pub fn application(pid: i32, timeout: Duration) -> Option<Self> {
            Self::own(unsafe { AXUIElementCreateApplication(pid) }, timeout.as_secs_f32())
        }

        /// Общесистемный срок сообщений Accessibility ЭТОГО процесса. Ставится на время
        /// операции; умолчание системы — 6 с, что дольше любого нашего срока чтения.
        pub fn set_global_timeout(timeout: Duration) {
            let system = unsafe { AXUIElementCreateSystemWide() };
            if system.is_null() {
                return;
            }
            // SAFETY: правило Create — освобождаем через CFType.
            let system = unsafe { CFType::wrap_under_create_rule(system) };
            unsafe { AXUIElementSetMessagingTimeout(system.as_CFTypeRef(), timeout.as_secs_f32()) };
        }

        /// pid процесса, которому принадлежит элемент (хранится в самом элементе, без
        /// сообщения). Обходу не нужен — им сверяются живые стенды.
        #[allow(dead_code)]
        pub fn pid(&self) -> Option<i32> {
            let mut pid: libc::pid_t = 0;
            (unsafe { AXUIElementGetPid(self.raw(), &mut pid) } == AX_ERROR_SUCCESS).then_some(pid)
        }

        /// CGWindowID окна — через приватную функцию; `None` — функции нет или окно её не
        /// знает (тогда сопоставляют по рамке).
        pub fn window_id(&self) -> Option<CGWindowID> {
            let get = private_get_window()?;
            let mut id: CGWindowID = 0;
            (unsafe { get(self.raw(), &mut id) } == AX_ERROR_SUCCESS && id != 0).then_some(id)
        }

        /// Один атрибут. `Ok(None)` — атрибута нет или он пуст; `Err` — Accessibility
        /// отказала по другой причине.
        pub fn copy_attribute(&self, name: &str) -> Result<Option<CFType>, AxFailure> {
            let mut out: CFTypeRef = ptr::null();
            let error = unsafe {
                AXUIElementCopyAttributeValue(self.raw(), key(name).as_concrete_TypeRef(), &mut out)
            };
            match error {
                AX_ERROR_SUCCESS if out.is_null() => Ok(None),
                // SAFETY: Copy-функция — правило Create.
                AX_ERROR_SUCCESS => Ok(present(unsafe { CFType::wrap_under_create_rule(out) })),
                AX_ERROR_NO_VALUE | AX_ERROR_ATTRIBUTE_UNSUPPORTED => Ok(None),
                code => Err(AxFailure::new(code, format!("AXUIElementCopyAttributeValue({name})"))),
            }
        }

        /// Много атрибутов одним сообщением. Отсутствующие приезжают как `AXValue` с
        /// ошибкой внутри или `kCFNull` — и становятся `None`. Если приложение такого
        /// запроса не понимает, спрашиваем по одному.
        pub fn copy_multiple(&self, names: &[&'static str]) -> Result<Vec<Option<CFType>>, AxFailure> {
            let keys: Vec<CFString> = names.iter().map(|name| CFString::from_static_string(name)).collect();
            let request = CFArray::from_CFTypes(&keys);
            let mut out: CFArrayRef = ptr::null();
            let error = unsafe {
                AXUIElementCopyMultipleAttributeValues(
                    self.raw(),
                    request.as_concrete_TypeRef(),
                    0,
                    &mut out,
                )
            };
            if error == AX_ERROR_SUCCESS && !out.is_null() {
                // SAFETY: правило Create.
                let values: CFArray<*const c_void> = unsafe { CFArray::wrap_under_create_rule(out) };
                if values.len() as usize == names.len() {
                    let mut result = Vec::with_capacity(names.len());
                    for item in values.iter() {
                        result.push(wrap_present(*item));
                    }
                    return Ok(result);
                }
            }
            if error == AX_ERROR_SUCCESS || error == AX_ERROR_ILLEGAL_ARGUMENT_FOR_MULTIPLE {
                // Приложение не умеет пакет — по одному, честно платя сообщениями.
                let mut result = Vec::with_capacity(names.len());
                for name in names {
                    result.push(self.copy_attribute(name)?);
                }
                return Ok(result);
            }
            Err(AxFailure::new(error, "AXUIElementCopyMultipleAttributeValues"))
        }

        /// Окна приложения (`AXWindows`).
        pub fn windows(&self) -> Result<Vec<AxElement>, AxFailure> {
            let Some(value) = self.copy_attribute("AXWindows")? else {
                return Ok(Vec::new());
            };
            Ok(self.elements_of(&value))
        }

        fn elements_of(&self, value: &CFType) -> Vec<AxElement> {
            let Some(array) = value.downcast::<CFArray<*const c_void>>() else {
                return Vec::new();
            };
            let element_type = unsafe { AXUIElementGetTypeID() };
            array
                .iter()
                .filter_map(|item| {
                    let raw = *item;
                    if raw.is_null() || unsafe { CFGetTypeID(raw) } != element_type {
                        return None;
                    }
                    AxElement::wrap(raw, self.timeout)
                })
                .collect()
        }

        fn names(&self, copy: unsafe extern "C" fn(AXUIElementRef, *mut CFArrayRef) -> AXError, what: &str) -> Result<Vec<String>, AxFailure> {
            let mut out: CFArrayRef = ptr::null();
            let error = unsafe { copy(self.raw(), &mut out) };
            match error {
                AX_ERROR_SUCCESS if out.is_null() => Ok(Vec::new()),
                AX_ERROR_SUCCESS => {
                    // SAFETY: правило Create.
                    let array: CFArray<*const c_void> = unsafe { CFArray::wrap_under_create_rule(out) };
                    Ok(array
                        .iter()
                        .filter_map(|item| wrap_present(*item))
                        .filter_map(|value| value.downcast::<CFString>().map(|s| s.to_string()))
                        .collect())
                }
                AX_ERROR_NO_VALUE | AX_ERROR_ATTRIBUTE_UNSUPPORTED => Ok(Vec::new()),
                code => Err(AxFailure::new(code, what)),
            }
        }

        /// Имена атрибутов элемента — для диагностики; обход ими не пользуется.
        #[allow(dead_code)]
        pub fn attribute_names(&self) -> Result<Vec<String>, AxFailure> {
            self.names(AXUIElementCopyAttributeNames, "AXUIElementCopyAttributeNames")
        }

        fn set(&self, attribute: &str, value: &CFType) -> Result<(), AxFailure> {
            let error = unsafe {
                AXUIElementSetAttributeValue(
                    self.raw(),
                    key(attribute).as_concrete_TypeRef(),
                    value.as_CFTypeRef(),
                )
            };
            if error == AX_ERROR_SUCCESS {
                Ok(())
            } else {
                Err(AxFailure::new(error, format!("AXUIElementSetAttributeValue({attribute})")))
            }
        }
    }

    /// `kAXErrorIllegalArgument` от пакетного запроса — «такого я не умею», а не поломка.
    const AX_ERROR_ILLEGAL_ARGUMENT_FOR_MULTIPLE: AXError = super::AX_ERROR_ILLEGAL_ARGUMENT;

    /// `kCFNull` и `AXValue` с ошибкой внутри — это «нет значения».
    fn present(value: CFType) -> Option<CFType> {
        let type_id = value.type_of();
        if type_id == unsafe { CFNullGetTypeID() } {
            return None;
        }
        if type_id == unsafe { AXValueGetTypeID() }
            && unsafe { AXValueGetType(value.as_CFTypeRef()) } == AX_VALUE_TYPE_AXERROR
        {
            return None;
        }
        Some(value)
    }

    fn wrap_present(raw: *const c_void) -> Option<CFType> {
        if raw.is_null() {
            return None;
        }
        // SAFETY: указатель из живого CFArray — правило Get.
        present(unsafe { CFType::wrap_under_get_rule(raw) })
    }

    /// CF-значение → [`Raw`]: строка, число, булево, точка, размер — или имя типа.
    pub fn raw_of(value: &CFType) -> Raw {
        if let Some(text) = value.downcast::<CFString>() {
            return Raw::Text(text.to_string());
        }
        if let Some(flag) = value.downcast::<CFBoolean>() {
            return Raw::Bool(flag.into());
        }
        if let Some(number) = value.downcast::<CFNumber>() {
            return Raw::Number(number.to_f64().unwrap_or(f64::NAN));
        }
        let type_id = value.type_of();
        if type_id == unsafe { AXValueGetTypeID() } {
            let kind = unsafe { AXValueGetType(value.as_CFTypeRef()) };
            if kind == AX_VALUE_TYPE_CGPOINT {
                let mut point = CGPoint::new(0.0, 0.0);
                if unsafe {
                    AXValueGetValue(value.as_CFTypeRef(), kind, &mut point as *mut CGPoint as *mut c_void)
                } != 0
                {
                    return Raw::Point(point.x, point.y);
                }
            }
            if kind == AX_VALUE_TYPE_CGSIZE {
                let mut size = CGSize::new(0.0, 0.0);
                if unsafe {
                    AXValueGetValue(value.as_CFTypeRef(), kind, &mut size as *mut CGSize as *mut c_void)
                } != 0
                {
                    return Raw::Size(size.width, size.height);
                }
            }
            return Raw::Other(format!("AXValue type {kind}"));
        }
        // SAFETY: Copy-функция — правило Create; описание типа всегда есть.
        let description = unsafe { CFCopyTypeIDDescription(type_id) };
        if description.is_null() {
            return Raw::Other(format!("CFTypeID {type_id}"));
        }
        Raw::Other(unsafe { CFString::wrap_under_create_rule(description) }.to_string())
    }

    impl Element for AxElement {
        fn attributes(&self) -> Result<Attributes, AxFailure> {
            let values = self.copy_multiple(&ATTRIBUTE_NAMES)?;
            let raw: Vec<Option<Raw>> = values.iter().map(|v| v.as_ref().map(raw_of)).collect();
            let mut attributes = Attributes::from_values(&raw);
            if [&attributes.title, &attributes.description, &attributes.label, &attributes.help]
                .iter().all(|value| value.as_deref().is_none_or(str::is_empty))
            {
                // One hop only: a cyclic/stale label relationship cannot recurse.
                // A stale optional label does not mean the source node disappeared.
                let related = self.copy_attribute("AXTitleUIElement").or_else(|error| {
                    if matches!(error.code, super::AX_ERROR_INVALID_UI_ELEMENT | super::AX_ERROR_CANNOT_COMPLETE) {
                        Ok(None)
                    } else { Err(error) }
                })?;
                if let Some(value) = related
                    && value.type_of() == unsafe { AXUIElementGetTypeID() }
                    && let Some(label) = AxElement::wrap(value.as_CFTypeRef(), self.timeout)
                {
                    let names = match label.copy_multiple(&[
                        "AXRole", "AXTitle", "AXDescription", "AXLabel", "AXHelp",
                    ]) {
                        Ok(names) => names,
                        Err(error) if matches!(error.code, super::AX_ERROR_INVALID_UI_ELEMENT | super::AX_ERROR_CANNOT_COMPLETE) => return Ok(attributes),
                        Err(error) => return Err(error),
                    };
                    attributes.label = names.iter().skip(1)
                        .filter_map(|value| value.as_ref()?.downcast::<CFString>())
                        .map(|value| value.to_string()).find(|value| !value.is_empty());
                    // A label's own static text is a name, never the input's value.
                    // Never fetch AXValue from an input/password field through this link.
                    let role = names.first().and_then(|value| value.as_ref())
                        .and_then(|value| value.downcast::<CFString>())
                        .map(|value| value.to_string());
                    if attributes.label.is_none()
                        && matches!(role.as_deref(), Some("AXStaticText") | Some("AXHeading"))
                    {
                        // Тот же or_else, что у двух хопов выше: исчезнувшая или занятая
                        // подпись — не отказ исходного узла и не «search incomplete»
                        // (ревью 25.09, A8 F2).
                        let value = match label.copy_attribute("AXValue") {
                            Ok(value) => value,
                            Err(error) if matches!(error.code, super::AX_ERROR_INVALID_UI_ELEMENT | super::AX_ERROR_CANNOT_COMPLETE) => return Ok(attributes),
                            Err(error) => return Err(error),
                        };
                        attributes.label = value
                            .and_then(|value| value.downcast::<CFString>())
                            .map(|value| value.to_string()).filter(|value| !value.is_empty());
                    }
                }
            }
            Ok(attributes)
        }

        fn actions(&self) -> Result<Vec<String>, AxFailure> {
            self.names(AXUIElementCopyActionNames, "AXUIElementCopyActionNames")
        }

        fn settable(&self, attribute: &str) -> bool {
            let mut settable: Boolean = 0;
            let error = unsafe {
                AXUIElementIsAttributeSettable(
                    self.raw(),
                    key(attribute).as_concrete_TypeRef(),
                    &mut settable,
                )
            };
            error == AX_ERROR_SUCCESS && settable != 0
        }

        fn children(&self, max: u64) -> Result<(Vec<Self>, bool), AxFailure> {
            // На одного больше потолка: так узнаётся «есть ещё», не забирая у списка на
            // десять тысяч строк все десять тысяч.
            let want = max.saturating_add(1).min(CFIndex::MAX as u64) as CFIndex;
            let mut out: CFArrayRef = ptr::null();
            let error = unsafe {
                AXUIElementCopyAttributeValues(
                    self.raw(),
                    key("AXChildren").as_concrete_TypeRef(),
                    0,
                    want,
                    &mut out,
                )
            };
            let mut children = match error {
                AX_ERROR_SUCCESS if out.is_null() => Vec::new(),
                AX_ERROR_SUCCESS => {
                    // SAFETY: правило Create.
                    let array: CFArray<*const c_void> = unsafe { CFArray::wrap_under_create_rule(out) };
                    let value = array.into_CFType();
                    self.elements_of(&value)
                }
                AX_ERROR_NO_VALUE | AX_ERROR_ATTRIBUTE_UNSUPPORTED => Vec::new(),
                // Ранжированный запрос приложение может и не понять — тогда весь список.
                _ => match self.copy_attribute("AXChildren")? {
                    Some(value) => self.elements_of(&value),
                    None => Vec::new(),
                },
            };
            let more = children.len() as u64 > max;
            children.truncate(max as usize);
            Ok((children, more))
        }

        fn perform(&self, action: &str) -> Result<(), AxFailure> {
            let error = unsafe { AXUIElementPerformAction(self.raw(), key(action).as_concrete_TypeRef()) };
            if error == AX_ERROR_SUCCESS {
                Ok(())
            } else {
                Err(AxFailure::new(error, format!("AXUIElementPerformAction({action})")))
            }
        }

        fn set_text(&self, attribute: &str, text: &str) -> Result<(), AxFailure> {
            self.set(attribute, &CFString::new(text).into_CFType())
        }

        fn set_bool(&self, attribute: &str, value: bool) -> Result<(), AxFailure> {
            self.set(attribute, &CFBoolean::from(value).into_CFType())
        }
    }

    /// Окно, привязанное к своему AX-элементу, и как именно его нашли. Элемент приложения
    /// здесь не хранится: AX-окно живёт своим счётчиком ссылок, родитель ему не нужен.
    pub struct Attached {
        pub window: AxElement,
        /// `window_id` — по CGWindowID через приватную функцию; `frame` — по рамке и
        /// заголовку; едет в заметки ответа, чтобы промах сопоставления был виден.
        pub matched_by: &'static str,
        /// Заголовок из Accessibility: он есть и без «Записи экрана», в отличие от
        /// заголовка из списка окон WindowServer.
        pub title: Option<String>,
        /// Сколько AX-окон у приложения сравнили.
        pub compared: usize,
    }

    /// Найти AX-окно для окна WindowServer. Сначала — по номеру окна, если приватная
    /// функция на месте; иначе — по рамке (±1 пункт) с предпочтением равному заголовку.
    pub fn attach(window: &mac::WindowInfo, timeout: Duration) -> Result<Attached, String> {
        let application = AxElement::application(window.pid, timeout)
            .ok_or_else(|| format!("AXUIElementCreateApplication({}) returned NULL", window.pid))?;
        let windows = application.windows().map_err(|failure| {
            format!(
                "Accessibility could not list the windows of pid {} ({}): {failure}; the \
                 application may not support Accessibility, may be busy, or «Универсальный \
                 доступ» was revoked",
                window.pid, window.owner
            )
        })?;
        let compared = windows.len();
        let mut by_frame: Vec<(AxElement, Option<String>)> = Vec::new();
        for candidate in windows {
            if candidate.window_id() == Some(window.id) {
                let title = candidate.copy_attribute("AXTitle").ok().flatten().and_then(|v| {
                    v.downcast::<CFString>().map(|s| s.to_string())
                });
                return Ok(Attached { window: candidate, matched_by: "window_id", title, compared });
            }
            let position = candidate.copy_attribute("AXPosition").ok().flatten().map(|v| raw_of(&v));
            let size = candidate.copy_attribute("AXSize").ok().flatten().map(|v| raw_of(&v));
            if let (Some(Raw::Point(x, y)), Some(Raw::Size(width, height))) = (position, size)
                && (x - window.x).abs() <= 1.0
                && (y - window.y).abs() <= 1.0
                && (width - window.width).abs() <= 1.0
                && (height - window.height).abs() <= 1.0
            {
                let title = candidate.copy_attribute("AXTitle").ok().flatten().and_then(|v| {
                    v.downcast::<CFString>().map(|s| s.to_string())
                });
                by_frame.push((candidate, title));
            }
        }
        if by_frame.is_empty() {
            return Err(format!(
                "window 0x{:X} of {} (pid {}) is not exposed by Accessibility: {compared} AX \
                 windows of the application were compared by window id and frame and none \
                 matched (overlays, hidden and some Electron windows have no AX window)",
                window.id, window.owner, window.pid
            ));
        }
        // An unresolved frame collision must never choose an arbitrary mutation target.
        let index = if by_frame.len() == 1 {
            0
        } else {
            let matches: Vec<usize> = by_frame.iter().enumerate()
                .filter(|(_, (_, title))| window.title.as_ref().is_some_and(|wanted| !wanted.is_empty() && title.as_ref() == Some(wanted)))
                .map(|(index, _)| index).collect();
            if matches.len() != 1 {
                return Err(format!("ambiguous Accessibility window binding: {} windows match frame for 0x{:X}; no action performed", by_frame.len(), window.id));
            }
            matches[0]
        };
        let (matched, title) = by_frame.swap_remove(index);
        Ok(Attached {
            window: matched,
            matched_by: if compared == 1 { "frame" } else { "frame_among_several" },
            title,
            compared,
        })
    }

    /// AX-окно для окна WindowServer — одной строкой, для тех, кому нужен только
    /// элемент. Единственный путь связи CGWindowID ↔ AXWindow в теле: приватная
    /// `_AXUIElementGetWindow` берётся через `dlsym` (см. [`private_get_window`]), а
    /// если её нет — сопоставление по pid, рамке и заголовку внутри [`attach`].
    /// `desktop.rs` (поднятие окна) зовёт ЭТО, а не второй `extern`: два разных способа
    /// найти одно окно однажды разъехались бы, и разъезд был бы молчаливым.
    pub(crate) fn ax_window_for(
        window: &mac::WindowInfo,
        timeout: Duration,
    ) -> Result<AxElement, String> {
        attach(window, timeout).map(|found| found.window)
    }

    /// Идентификатор пакета приложения по pid — то, что на Windows зовётся `class`:
    /// `proc_pidpath` → `…/Foo.app` → `Info.plist` → `CFBundleIdentifier`. Без AppKit.
    pub fn bundle_identifier(pid: i32) -> Option<String> {
        let mut buffer = vec![0u8; 4 * 1024];
        let length = unsafe {
            libc::proc_pidpath(pid, buffer.as_mut_ptr() as *mut c_void, buffer.len() as u32)
        };
        if length <= 0 {
            return None;
        }
        let path = String::from_utf8_lossy(&buffer[..length as usize]).to_string();
        // Последний `.app/` — вложенные помощники (`Foo.app/…/Helper.app/…`) живут в
        // своём пакете, и это он сейчас на экране.
        let end = path.rfind(".app/")? + 4;
        let url = CFURL::from_path(&path[..end], true)?;
        let bundle = CFBundle::new(url)?;
        bundle
            .info_dictionary()
            .find(&CFString::from_static_string("CFBundleIdentifier"))
            .and_then(|value| value.downcast::<CFString>())
            .map(|value| value.to_string())
            .filter(|value| !value.is_empty())
    }
}

// ─── стенды ─────────────────────────────────────────────────────────────────────────────

#[cfg(test)]
pub(crate) mod fake {
    //! Подставное дерево: элемент — `Rc<RefCell<Spec>>`, чтобы действие было видно
    //! снаружи (`log`) и меняло значение, как сделало бы живое окно.
    use std::cell::RefCell;
    use std::rc::Rc;

    use super::{AX_ERROR_ACTION_UNSUPPORTED, AX_ERROR_ATTRIBUTE_UNSUPPORTED, Attributes, AxFailure, AxValue, Element};

    #[derive(Debug, Default)]
    pub struct Spec {
        pub attributes: Attributes,
        pub actions: Vec<&'static str>,
        pub settable: Vec<&'static str>,
        pub children: Vec<Fake>,
        /// Ответ `attributes()` — ошибка с этим кодом.
        pub fail: Option<i32>,
        pub log: Vec<String>,
    }

    #[derive(Debug, Clone, Default)]
    pub struct Fake(pub Rc<RefCell<Spec>>);

    impl Fake {
        pub fn new(role: &str) -> Self {
            let fake = Self::default();
            fake.0.borrow_mut().attributes.role = Some(role.to_string());
            fake
        }

        pub fn title(self, title: &str) -> Self {
            self.0.borrow_mut().attributes.title = Some(title.to_string());
            self
        }

        pub fn subrole(self, subrole: &str) -> Self {
            self.0.borrow_mut().attributes.subrole = Some(subrole.to_string());
            self
        }

        pub fn description(self, text: &str) -> Self {
            self.0.borrow_mut().attributes.description = Some(text.to_string());
            self
        }

        pub fn help(self, text: &str) -> Self {
            self.0.borrow_mut().attributes.help = Some(text.to_string());
            self
        }

        pub fn identifier(self, id: &str) -> Self {
            self.0.borrow_mut().attributes.identifier = Some(id.to_string());
            self
        }

        pub fn value(self, value: AxValue) -> Self {
            self.0.borrow_mut().attributes.value = Some(value);
            self
        }

        pub fn rect(self, x: f64, y: f64, width: f64, height: f64) -> Self {
            let mut spec = self.0.borrow_mut();
            spec.attributes.position = Some((x, y));
            spec.attributes.size = Some((width, height));
            drop(spec);
            self
        }

        pub fn actions(self, actions: &[&'static str]) -> Self {
            self.0.borrow_mut().actions = actions.to_vec();
            self
        }

        pub fn settable(self, attributes: &[&'static str]) -> Self {
            self.0.borrow_mut().settable = attributes.to_vec();
            self
        }

        pub fn enabled(self, value: bool) -> Self {
            self.0.borrow_mut().attributes.enabled = Some(value);
            self
        }

        pub fn focused(self, value: bool) -> Self {
            self.0.borrow_mut().attributes.focused = Some(value);
            self
        }

        pub fn expanded(self, value: bool) -> Self {
            self.0.borrow_mut().attributes.expanded = Some(value);
            self
        }

        pub fn selected(self, value: bool) -> Self {
            self.0.borrow_mut().attributes.selected = Some(value);
            self
        }

        pub fn failing(self, code: i32) -> Self {
            self.0.borrow_mut().fail = Some(code);
            self
        }

        pub fn child(self, child: Fake) -> Self {
            self.0.borrow_mut().children.push(child);
            self
        }

        pub fn children(self, children: Vec<Fake>) -> Self {
            self.0.borrow_mut().children.extend(children);
            self
        }

        pub fn log(&self) -> Vec<String> {
            self.0.borrow().log.clone()
        }
    }

    impl Element for Fake {
        fn attributes(&self) -> Result<Attributes, AxFailure> {
            let spec = self.0.borrow();
            match spec.fail {
                Some(code) => Err(AxFailure::new(code, "fake attributes")),
                None => Ok(spec.attributes.clone()),
            }
        }

        fn actions(&self) -> Result<Vec<String>, AxFailure> {
            Ok(self.0.borrow().actions.iter().map(|a| a.to_string()).collect())
        }

        fn settable(&self, attribute: &str) -> bool {
            self.0.borrow().settable.contains(&attribute)
        }

        fn children(&self, max: u64) -> Result<(Vec<Self>, bool), AxFailure> {
            let spec = self.0.borrow();
            let more = spec.children.len() as u64 > max;
            Ok((spec.children.iter().take(max as usize).cloned().collect(), more))
        }

        fn perform(&self, action: &str) -> Result<(), AxFailure> {
            let mut spec = self.0.borrow_mut();
            if !spec.actions.contains(&action) {
                return Err(AxFailure::new(AX_ERROR_ACTION_UNSUPPORTED, format!("fake perform {action}")));
            }
            spec.log.push(format!("perform {action}"));
            // Галочка от нажатия переключается — как в живом окне.
            if action == "AXPress" && spec.attributes.role.as_deref() == Some("AXCheckBox") {
                let now = match spec.attributes.value {
                    Some(AxValue::Number(n)) if n != 0.0 => 0.0,
                    _ => 1.0,
                };
                spec.attributes.value = Some(AxValue::Number(now));
            }
            Ok(())
        }

        fn set_text(&self, attribute: &str, text: &str) -> Result<(), AxFailure> {
            let mut spec = self.0.borrow_mut();
            if !spec.settable.contains(&attribute) {
                return Err(AxFailure::new(AX_ERROR_ATTRIBUTE_UNSUPPORTED, format!("fake set {attribute}")));
            }
            spec.log.push(format!("set {attribute}={text:?}"));
            if attribute == "AXValue" {
                spec.attributes.value = Some(AxValue::Text(text.to_string()));
            }
            Ok(())
        }

        fn set_bool(&self, attribute: &str, value: bool) -> Result<(), AxFailure> {
            let mut spec = self.0.borrow_mut();
            if !spec.settable.contains(&attribute) {
                return Err(AxFailure::new(AX_ERROR_ATTRIBUTE_UNSUPPORTED, format!("fake set {attribute}")));
            }
            spec.log.push(format!("set {attribute}={value}"));
            match attribute {
                "AXFocused" => spec.attributes.focused = Some(value),
                "AXExpanded" => spec.attributes.expanded = Some(value),
                "AXSelected" => spec.attributes.selected = Some(value),
                _ => {}
            }
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use std::time::{Duration, Instant};

    use serde_json::json;

    use super::fake::Fake;
    use super::*;
    use crate::element::{Choice, choose};

    const SCREEN: Screen = Screen { left: 0.0, top: 0.0, width: 1440.0, height: 900.0 };
    const LIMITS: WalkLimits = WalkLimits {
        max_nodes: 400,
        max_depth: 24,
        max_children_per_node: 128,
        max_text_chars: 240,
    };

    fn later() -> Instant {
        Instant::now() + Duration::from_secs(30)
    }

    /// Окно с кнопкой, полем, галочкой, статическим текстом и вкладками — то, что есть в
    /// любом диалоге macOS.
    fn dialog() -> Fake {
        Fake::new("AXWindow")
            .title("Сохранить")
            .rect(100.0, 100.0, 400.0, 300.0)
            .actions(&["AXRaise"])
            .children(vec![
                Fake::new("AXButton")
                    .title("OK")
                    .identifier("okBtn")
                    .rect(120.0, 350.0, 80.0, 30.0)
                    .actions(&["AXPress"])
                    .settable(&["AXFocused"])
                    .enabled(true)
                    .focused(false),
                Fake::new("AXButton")
                    .title("Отмена")
                    .rect(210.0, 350.0, 80.0, 30.0)
                    .actions(&["AXPress"])
                    .settable(&["AXFocused"])
                    .focused(false),
                Fake::new("AXTextField")
                    .description("Имя файла")
                    .value(AxValue::Text("отчёт.txt".into()))
                    .rect(120.0, 200.0, 300.0, 24.0)
                    .settable(&["AXValue", "AXFocused"])
                    .focused(true),
                Fake::new("AXCheckBox")
                    .title("Открыть после сохранения")
                    .value(AxValue::Number(0.0))
                    .rect(120.0, 240.0, 200.0, 20.0)
                    .actions(&["AXPress"])
                    .settable(&["AXFocused"])
                    .focused(false),
                Fake::new("AXStaticText")
                    .value(AxValue::Text("Куда сохранить файл".into()))
                    .rect(120.0, 150.0, 300.0, 20.0),
                Fake::new("AXTabGroup")
                    .rect(120.0, 280.0, 300.0, 40.0)
                    .child(
                        Fake::new("AXRadioButton")
                            .title("Общие")
                            .value(AxValue::Number(1.0))
                            .rect(120.0, 280.0, 100.0, 40.0)
                            .actions(&["AXPress"]),
                    )
                    .child(
                        Fake::new("AXRadioButton")
                            .title("Ещё")
                            .value(AxValue::Number(0.0))
                            .rect(220.0, 280.0, 100.0, 40.0)
                            .actions(&["AXPress"]),
                    ),
            ])
    }

    #[test]
    fn roles_speak_the_same_words_as_uia_on_windows() {
        assert_eq!(role_of(Some("AXButton"), None, None), "button");
        assert_eq!(role_of(Some("AXCheckBox"), None, None), "check_box");
        assert_eq!(role_of(Some("AXPopUpButton"), None, None), "combo_box");
        assert_eq!(role_of(Some("AXMenuItem"), None, None), "menu_item");
        assert_eq!(role_of(Some("AXToolbar"), None, None), "tool_bar");
        assert_eq!(role_of(Some("AXStaticText"), None, None), "text");
        assert_eq!(role_of(Some("AXTextField"), Some("AXSecureTextField"), None), "edit");
        assert_eq!(role_of(Some("AXWebArea"), None, None), "document");
        // Вкладка на macOS — радиокнопка внутри AXTabGroup.
        assert_eq!(role_of(Some("AXRadioButton"), None, Some("AXTabGroup")), "tab_item");
        assert_eq!(role_of(Some("AXRadioButton"), None, Some("AXRadioGroup")), "radio_button");
        // Строка списка Finder — AXRow с подролью AXOutlineRow.
        assert_eq!(role_of(Some("AXRow"), Some("AXOutlineRow"), Some("AXOutline")), "tree_item");
        assert_eq!(role_of(Some("AXRow"), Some("AXTableRow"), Some("AXTable")), "list_item");
        // Незнакомое — custom, отсутствующее — unknown: сырая роль едет рядом.
        assert_eq!(role_of(Some("AXSomethingNew"), None, None), "custom");
        assert_eq!(role_of(None, None, None), "unknown");
        assert_eq!(role_of(Some("  "), None, None), "unknown");
    }

    #[test]
    fn every_dictionary_word_is_one_uia_prints() {
        // Слова, которые печатает uia::role_name (50000..50040) плюс unknown/custom.
        let uia = [
            "button", "calendar", "check_box", "combo_box", "edit", "hyperlink", "image",
            "list_item", "list", "menu", "menu_bar", "menu_item", "progress_bar",
            "radio_button", "scroll_bar", "slider", "spinner", "status_bar", "tab",
            "tab_item", "text", "tool_bar", "tool_tip", "tree", "tree_item", "custom",
            "group", "thumb", "data_grid", "data_item", "document", "split_button",
            "window", "pane", "header", "header_item", "table", "title_bar", "separator",
            "semantic_zoom", "app_bar", "unknown",
        ];
        for role in [
            "AXWindow", "AXSheet", "AXDrawer", "AXPopover", "AXScrollArea", "AXSplitGroup",
            "AXLayoutArea", "AXMatte", "AXGrowArea", "AXButton", "AXMenuButton",
            "AXDisclosureTriangle", "AXColorWell", "AXDockItem", "AXPopUpButton", "AXComboBox",
            "AXCheckBox", "AXRadioButton", "AXRadioGroup", "AXGroup", "AXLayoutItem",
            "AXColumn", "AXTabGroup", "AXTextField", "AXTextArea", "AXDateField",
            "AXTimeField", "AXStaticText", "AXHeading", "AXImage", "AXLink", "AXList",
            "AXGrid", "AXBrowser", "AXOutline", "AXTable", "AXRow", "AXCell", "AXMenuBar",
            "AXMenuBarItem", "AXMenuItem", "AXMenu", "AXToolbar", "AXScrollBar", "AXSlider",
            "AXValueIndicator", "AXHandle", "AXSplitter", "AXIncrementor",
            "AXProgressIndicator", "AXBusyIndicator", "AXLevelIndicator",
            "AXRelevanceIndicator", "AXHelpTag", "AXWebArea", "AXUnknown", "AXApplication",
        ] {
            let word = role_of(Some(role), None, None);
            assert!(uia.contains(&word), "{role} → {word} is not a word UIA prints");
        }
    }

    #[test]
    fn a_name_is_title_then_description_then_label_then_help_then_static_value() {
        let by_title = Fake::new("AXButton").title("OK").description("кнопка OK");
        let node = node_from(&by_title, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.name.as_deref(), Some("OK"));

        let by_description = Fake::new("AXTextField").description("Имя файла").help("введите имя");
        let node = node_from(&by_description, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.name.as_deref(), Some("Имя файла"));

        let by_help = Fake::new("AXButton").help("Закрыть окно");
        let node = node_from(&by_help, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.name.as_deref(), Some("Закрыть окно"));

        // Статический текст: имя — значение, и значение не повторяется вторым полем.
        let label = Fake::new("AXStaticText").value(AxValue::Text("Куда сохранить".into()));
        let node = node_from(&label, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.name.as_deref(), Some("Куда сохранить"));
        assert_eq!(node.value, None);

        // А у поля значение остаётся значением, имя — своё.
        let field = Fake::new("AXTextField").title("Имя").value(AxValue::Text("отчёт".into()));
        let node = node_from(&field, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.name.as_deref(), Some("Имя"));
        assert_eq!(node.value.as_deref(), Some("отчёт"));
    }

    #[test]
    fn numbers_and_booleans_become_text_without_a_trailing_zero() {
        assert_eq!(AxValue::Number(1.0).text().as_deref(), Some("1"));
        assert_eq!(AxValue::Number(0.5).text().as_deref(), Some("0.5"));
        assert_eq!(AxValue::Number(-3.0).text().as_deref(), Some("-3"));
        assert_eq!(AxValue::Bool(true).text().as_deref(), Some("true"));
        assert_eq!(AxValue::Text("x".into()).text().as_deref(), Some("x"));
        assert_eq!(AxValue::Other("AXUIElement".into()).text(), None);
    }

    #[test]
    fn attributes_are_read_in_the_declared_order() {
        let values: Vec<Option<Raw>> = vec![
            Some(Raw::Text("AXButton".into())),
            None,
            Some(Raw::Text("OK".into())),
            None,
            None,
            None,
            Some(Raw::Text("кнопка".into())),
            Some(Raw::Text("okBtn".into())),
            Some(Raw::Number(1.0)),
            Some(Raw::Point(10.0, 20.0)),
            Some(Raw::Size(30.0, 40.0)),
            Some(Raw::Bool(true)),
            Some(Raw::Bool(false)),
            None,
            Some(Raw::Number(1.0)),
        ];
        assert_eq!(values.len(), ATTRIBUTE_NAMES.len());
        let attributes = Attributes::from_values(&values);
        assert_eq!(attributes.role.as_deref(), Some("AXButton"));
        assert_eq!(attributes.title.as_deref(), Some("OK"));
        assert_eq!(attributes.role_description.as_deref(), Some("кнопка"));
        assert_eq!(attributes.identifier.as_deref(), Some("okBtn"));
        assert_eq!(attributes.value, Some(AxValue::Number(1.0)));
        assert_eq!(attributes.position, Some((10.0, 20.0)));
        assert_eq!(attributes.size, Some((30.0, 40.0)));
        assert_eq!(attributes.enabled, Some(true));
        assert_eq!(attributes.focused, Some(false));
        assert_eq!(attributes.expanded, None);
        // Число в булевом атрибуте — 1 значит «да» (так отвечают некоторые приложения).
        assert_eq!(attributes.selected, Some(true));
        // Число в текстовом поле заголовком не становится.
        let odd: Vec<Option<Raw>> = vec![Some(Raw::Number(5.0)), None, Some(Raw::Number(1.0))];
        let attributes = Attributes::from_values(&odd);
        assert_eq!(attributes.role, None);
        assert_eq!(attributes.title, None);
    }

    #[test]
    fn patterns_follow_actions_and_settable_attributes() {
        let button = Fake::new("AXButton").actions(&["AXPress"]).settable(&["AXFocused"]).focused(false);
        let node = node_from(&button, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.patterns, vec!["invoke", "focus"]);

        let field = Fake::new("AXTextField")
            .value(AxValue::Text(String::new()))
            .settable(&["AXValue", "AXFocused"])
            .focused(false);
        let node = node_from(&field, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.patterns, vec!["set_value", "focus"]);

        let check = Fake::new("AXCheckBox").value(AxValue::Number(1.0)).actions(&["AXPress"]);
        let node = node_from(&check, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.patterns, vec!["invoke", "toggle"]);
        assert_eq!(node.checked, Some("on"));

        let switch = Fake::new("AXButton").subrole("AXSwitch").value(AxValue::Number(0.0)).actions(&["AXPress"]);
        let node = node_from(&switch, 0, None, None, 240, SCREEN).unwrap();
        assert!(node.patterns.contains(&"toggle"));
        assert_eq!(node.checked, Some("off"));

        let disclosure = Fake::new("AXRow")
            .subrole("AXOutlineRow")
            .expanded(false)
            .selected(false)
            .settable(&["AXExpanded", "AXSelected"])
            .actions(&["AXScrollToVisible"]);
        let node = node_from(&disclosure, 0, None, Some("AXOutline"), 240, SCREEN).unwrap();
        assert_eq!(node.patterns, vec!["expand", "collapse", "select", "scroll_into_view"]);
        assert_eq!(node.expanded, Some("collapsed"));
        assert_eq!(node.selected, Some(false));

        let popup = Fake::new("AXPopUpButton").actions(&["AXPress", "AXShowMenu"]);
        let node = node_from(&popup, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.patterns, vec!["invoke", "expand"]);

        // Записываемость проверяется только у атрибутов, которые ЕСТЬ: AXValue отсутствует
        // — set_value не появится, даже если подставной элемент «разрешает» писать.
        let odd = Fake::new("AXGroup").settable(&["AXValue"]);
        let node = node_from(&odd, 0, None, None, 240, SCREEN).unwrap();
        assert!(node.patterns.is_empty());
    }

    #[test]
    fn a_tab_is_selected_when_its_value_is_one() {
        let walk = walk(dialog(), LIMITS, later(), SCREEN, false);
        let tabs: Vec<&AxNode> = walk.nodes.iter().filter(|n| n.role == "tab_item").collect();
        assert_eq!(tabs.len(), 2);
        assert_eq!(tabs[0].selected, Some(true));
        assert_eq!(tabs[1].selected, Some(false));
        assert_eq!(tabs[0].ax_role.as_deref(), Some("AXRadioButton"));
    }

    #[test]
    fn a_rect_comes_in_points_with_a_centre_and_offscreen_is_computed() {
        let inside = Fake::new("AXButton").rect(10.0, 20.0, 100.0, 40.0);
        let node = node_from(&inside, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.rect, Some((10, 20, 110, 60)));
        assert_eq!(node.offscreen, Some(false));
        let json = node.describe();
        assert_eq!(json["rect"]["width"], 100);
        assert_eq!(json["rect"]["center"]["x"], 60);
        assert_eq!(json["rect"]["center"]["y"], 40);

        let outside = Fake::new("AXButton").rect(-500.0, 20.0, 100.0, 40.0);
        let node = node_from(&outside, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.offscreen, Some(true));

        // Без прямоугольника элемент не на экране; пустой размер — тоже.
        let bare = Fake::new("AXMenuItem");
        let node = node_from(&bare, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.rect, None);
        assert_eq!(node.offscreen, Some(true));
        let flat = Fake::new("AXGroup").rect(10.0, 10.0, 0.0, 30.0);
        let node = node_from(&flat, 0, None, None, 240, SCREEN).unwrap();
        assert_eq!(node.rect, None);
    }

    #[test]
    fn long_text_is_clipped_by_utf16_units_and_marked() {
        let long = "𐐀".repeat(200) + "конец";
        let field = Fake::new("AXTextField").value(AxValue::Text(long));
        let node = node_from(&field, 0, None, None, 6, SCREEN).unwrap();
        assert_eq!(node.value.as_deref(), Some("𐐀𐐀𐐀"));
        assert!(node.text_truncated);
        // Поиск и действие читают целиком: потолок — u64::MAX.
        let node = node_from(&field, 0, None, None, u64::MAX, SCREEN).unwrap();
        assert!(!node.text_truncated);
        assert!(node.value.unwrap().ends_with("конец"));
    }

    #[test]
    fn the_walk_keeps_parent_links_depth_and_order() {
        let walk = walk(dialog(), LIMITS, later(), SCREEN, false);
        assert_eq!(walk.nodes.len(), 9);
        assert_eq!(walk.truncated_by, Vec::<&str>::new());
        assert_eq!(walk.depth_reached, 2);
        assert_eq!(walk.nodes[0].role, "window");
        assert_eq!(walk.nodes[0].name.as_deref(), Some("Сохранить"));
        assert_eq!(walk.nodes[1].parent, Some(0));
        assert_eq!(walk.nodes[1].depth, 1);
        assert_eq!(walk.nodes[1].automation_id.as_deref(), Some("okBtn"));
        // Вкладки — последние, в ширину: после всех детей окна.
        assert_eq!(walk.nodes[7].parent, Some(6));
        assert_eq!(walk.nodes[7].depth, 2);
        assert_eq!(walk.discovered_unread, 0);
        assert_eq!(walk.read_failures, 0);
    }

    #[test]
    fn every_limit_is_named_when_it_cuts() {
        let cut = walk(
            dialog(),
            WalkLimits { max_nodes: 3, ..LIMITS },
            later(),
            SCREEN,
            false,
        );
        assert_eq!(cut.nodes.len(), 3);
        assert_eq!(cut.truncated_by, vec!["max_nodes"]);
        assert_eq!(cut.discovered_unread, 4, "увиденные, но не прочитанные дети окна");

        let shallow = walk(
            dialog(),
            WalkLimits { max_depth: 1, ..LIMITS },
            later(),
            SCREEN,
            false,
        );
        assert_eq!(shallow.truncated_by, vec!["max_depth"]);
        let tab_group = shallow.nodes.iter().find(|n| n.role == "tab").unwrap();
        assert_eq!(tab_group.children_unread, Some("max_depth"));
        // Как у UIA: на пределе глубины помечается КАЖДЫЙ узел, и лист тоже — узнать,
        // есть ли у него дети, стоило бы ещё одного сообщения в чужой процесс.
        assert_eq!(shallow.subtrees_unread, 6);
        assert_eq!(shallow.nodes.len(), 7);

        let narrow = walk(
            dialog(),
            WalkLimits { max_children_per_node: 2, ..LIMITS },
            later(),
            SCREEN,
            false,
        );
        assert_eq!(narrow.truncated_by, vec!["max_children_per_node"]);
        assert_eq!(narrow.nodes[0].children_unread, Some("max_children_per_node"));
        assert_eq!(narrow.nodes.len(), 3);

        let expired = walk(dialog(), LIMITS, Instant::now() - Duration::from_millis(1), SCREEN, false);
        assert_eq!(expired.nodes.len(), 0);
        assert_eq!(expired.truncated_by, vec!["timeout"]);
        assert_eq!(expired.discovered_unread, 1, "корень увиден и не прочитан");
    }

    #[test]
    fn visible_only_skips_offscreen_elements_with_everything_under_them() {
        let tree = Fake::new("AXWindow").rect(0.0, 0.0, 100.0, 100.0).child(
            Fake::new("AXMenu").child(Fake::new("AXMenuItem").title("Скрытый пункт")),
        );
        let walk = walk(tree, LIMITS, later(), SCREEN, true);
        assert_eq!(walk.nodes.len(), 1);
        assert_eq!(walk.skipped_offscreen, 1);
        assert!(walk.nodes.iter().all(|n| n.name.as_deref() != Some("Скрытый пункт")));
    }

    #[test]
    fn an_unreadable_element_is_counted_and_shown_as_custom_not_dropped() {
        let tree = Fake::new("AXWindow")
            .child(Fake::new("AXButton").title("OK").failing(AX_ERROR_CANNOT_COMPLETE))
            .child(Fake::new("AXButton").title("Отмена"));
        let walk = walk(tree, LIMITS, later(), SCREEN, false);
        assert_eq!(walk.nodes.len(), 3);
        assert_eq!(walk.read_failures, 1);
        assert_eq!(walk.nodes[1].role, "custom");
        assert_eq!(walk.nodes[1].name, None);
        assert!(walk.first_read_failure.as_deref().unwrap().contains("cannot_complete"));
        assert_eq!(walk.nodes[2].name.as_deref(), Some("Отмена"));
        assert!(walk.truncated_by.is_empty());
    }

    #[test]
    fn a_revoked_permission_stops_the_walk_by_name() {
        let tree = Fake::new("AXWindow")
            .child(Fake::new("AXButton").title("OK").failing(AX_ERROR_API_DISABLED))
            .child(Fake::new("AXButton").title("Отмена"));
        let walk = walk(tree, LIMITS, later(), SCREEN, false);
        assert_eq!(walk.nodes.len(), 1);
        assert_eq!(walk.truncated_by, vec!["api_disabled"]);
        assert!(walk.first_read_failure.as_deref().unwrap().contains("api_disabled"));
    }

    #[test]
    fn describe_carries_the_same_fields_as_a_read_node() {
        let walk = walk(dialog(), LIMITS, later(), SCREEN, false);
        let ok = walk.nodes[1].describe();
        assert_eq!(ok["role"], "button");
        assert_eq!(ok["ax_role"], "AXButton");
        assert_eq!(ok["name"], "OK");
        assert_eq!(ok["automation_id"], "okBtn");
        assert_eq!(ok["state"]["enabled"], true);
        assert_eq!(ok["state"]["focused"], false);
        assert_eq!(ok["state"]["offscreen"], false);
        assert_eq!(ok["patterns"], json!(["invoke", "focus"]));
        assert!(ok.get("value").is_none());
        // Ключа состояния НЕТ, когда элемент его не отдаёт.
        let text = walk.nodes[5].describe();
        assert!(text["state"].get("enabled").is_none());
        assert!(text.get("patterns").is_none());
    }

    #[test]
    fn the_sweep_matches_by_dictionary_role_or_raw_role_and_never_picks_for_her() {
        let root = dialog();
        let select = Selector { role: Some("button".into()), ..Selector::default() };
        let (found, scanned, limit) = sweep(&root, &select, 4_000, 40, later(), SCREEN).unwrap();
        assert_eq!(found.len(), 2);
        assert_eq!(scanned, 9);
        assert_eq!(limit, None);
        let indexes: Vec<usize> = (0..found.len()).collect();
        assert_eq!(choose(&indexes, None), Choice::Ambiguous { total: 2 });
        assert_eq!(choose(&indexes, Some(1)), Choice::One { index: 1, total: 2 });
        assert_eq!(found[1].json["name"], "Отмена");

        let raw = Selector { role: Some("AXButton".into()), name: Some("ok".into()), ..Selector::default() };
        let (found, ..) = sweep(&root, &raw, 4_000, 40, later(), SCREEN).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].node.automation_id.as_deref(), Some("okBtn"));

        let by_value = Selector { value_contains: Some("отчёт".into()), ..Selector::default() };
        let (found, ..) = sweep(&root, &by_value, 4_000, 40, later(), SCREEN).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(found[0].node.role, "edit");

        let nothing = Selector { name: Some("Нет такой".into()), ..Selector::default() };
        let (found, _, limit) = sweep(&root, &nothing, 4_000, 40, later(), SCREEN).unwrap();
        assert!(found.is_empty());
        assert_eq!(limit, None, "окно дочитано — вот теперь это «нет такого»");
    }

    #[test]
    fn an_unfinished_sweep_names_what_stopped_it() {
        let root = dialog();
        let select = Selector { name: Some("Нет такой".into()), ..Selector::default() };
        let (_, scanned, limit) = sweep(&root, &select, 3, 40, later(), SCREEN).unwrap();
        assert_eq!(limit, Some("max_nodes"));
        assert!(scanned <= 3);
        let (_, _, limit) = sweep(&root, &select, 4_000, 1, later(), SCREEN).unwrap();
        assert_eq!(limit, Some("max_depth"));
        let (_, scanned, limit) =
            sweep(&root, &select, 4_000, 40, Instant::now() - Duration::from_millis(1), SCREEN).unwrap();
        assert_eq!(limit, Some("timeout"));
        assert_eq!(scanned, 0);
        // Развилка «не нашлось» / «не досмотрел» — в element, и её слова те же.
        assert_eq!(crate::element::not_found(limit, false, false).0, "timeout");
    }

    #[test]
    fn a_vanished_element_is_absence_and_any_other_failure_tears_the_search() {
        let gone = Fake::new("AXWindow")
            .child(Fake::new("AXButton").title("OK").failing(AX_ERROR_INVALID_UI_ELEMENT))
            .child(Fake::new("AXButton").title("Отмена"));
        let select = Selector { role: Some("button".into()), ..Selector::default() };
        let (found, scanned, limit) = sweep(&gone, &select, 4_000, 40, later(), SCREEN).unwrap();
        assert_eq!(found.len(), 1);
        assert_eq!(scanned, 3);
        assert_eq!(limit, None);

        let busy = Fake::new("AXWindow")
            .child(Fake::new("AXButton").title("OK").failing(AX_ERROR_CANNOT_COMPLETE));
        let error = sweep(&busy, &select, 4_000, 40, later(), SCREEN).unwrap_err();
        assert!(error.to_string().contains("search incomplete"), "{error}");
        assert!(error.to_string().contains("cannot_complete"), "{error}");
    }

    #[test]
    fn invoke_presses_and_set_value_writes_into_the_field_not_into_focus() {
        let root = dialog();
        let ok = root.0.borrow().children[0].clone();
        let node = node_from(&ok, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        perform(&ok, &node, Act::Invoke, None, || Ok(())).unwrap();
        assert_eq!(ok.log(), vec!["perform AXPress"]);

        let field = root.0.borrow().children[2].clone();
        let node = node_from(&field, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        perform(&field, &node, Act::SetValue, Some("новое имя"), || Ok(())).unwrap();
        assert_eq!(field.log(), vec!["set AXValue=\"новое имя\""]);
        let after = node_from(&field, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        assert_eq!(after.value.as_deref(), Some("новое имя"));

        let check = root.0.borrow().children[3].clone();
        let node = node_from(&check, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        assert_eq!(node.checked, Some("off"));
        perform(&check, &node, Act::Toggle, None, || Ok(())).unwrap();
        let after = node_from(&check, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        assert_eq!(after.checked, Some("on"));
    }

    /// Поле пароля: значение НЕ читается, `set_value` отказан до единого сообщения в
    /// чужое приложение, а нажать и сфокусировать — можно (этого хватит, чтобы владелец
    /// напечатал сам).
    #[test]
    fn a_password_field_hides_its_value_and_refuses_set_value_but_not_focus() {
        for field in [
            // Обычная разметка macOS: роль текстового поля, подроль — «секретное».
            Fake::new("AXTextField")
                .subrole("AXSecureTextField")
                .title("Пароль")
                .value(AxValue::Text("hunter2".into()))
                .focused(false)
                .actions(&["AXPress", "AXScrollToVisible"])
                .settable(&["AXValue", "AXFocused"]),
            // Встречается и прямо ролью — слово то же, и смысл обязан быть тот же.
            Fake::new("AXSecureTextField")
                .title("Пароль")
                .value(AxValue::Text("hunter2".into()))
                .focused(false)
                .actions(&["AXPress", "AXScrollToVisible"])
                .settable(&["AXValue", "AXFocused"]),
        ] {
            let node = node_from(&field, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
            assert_eq!(node.role, "edit", "{:?}", node.ax_role);
            assert!(node.secure, "поле пароля не помечено: {node:?}");
            // Значение спрятано — и это НЕ «поле пустое»: об этом говорит `secure`.
            assert_eq!(node.value, None, "значение поля пароля уехало в узел: {node:?}");
            assert_eq!(node.describe()["secure"], json!(true));
            assert!(node.describe().get("value").is_none());
            // Обещанных глаголов не больше, чем разрешено: `set_value` среди них нет.
            assert!(!node.patterns.contains(&"set_value"), "{:?}", node.patterns);
            assert!(node.patterns.contains(&"invoke") && node.patterns.contains(&"focus"));

            // Отбор по значению по нему не идёт: искать нечего, и пустое совпадением
            // быть не должно.
            let by_value =
                Selector { value_contains: Some("hunter".into()), ..Selector::default() };
            assert!(!node.matches(&by_value));

            // Отказ — словами, и ни одного сообщения в приложение: журнал пуст.
            let error = perform(&field, &node, Act::SetValue, Some("hunter2"), || Ok(())).unwrap_err();
            assert!(error.contains("password field"), "{error}");
            assert!(error.contains("владелец"), "{error}");
            assert_eq!(field.log(), Vec::<String>::new(), "AX всё-таки позвали: {:?}", field.log());

            // А нажать и сфокусировать — можно: тело поднимает поле, печатает человек.
            perform(&field, &node, Act::Invoke, None, || Ok(())).unwrap();
            perform(&field, &node, Act::Focus, None, || Ok(())).unwrap();
            assert_eq!(field.log(), vec!["perform AXPress", "set AXFocused=true"]);
        }
    }

    #[test]
    fn a_missing_pattern_is_refused_with_what_the_element_has_and_no_click_fallback() {
        let root = dialog();
        let ok = root.0.borrow().children[0].clone();
        let node = node_from(&ok, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        let error = perform(&ok, &node, Act::SetValue, Some("x"), || Ok(())).unwrap_err();
        assert!(error.contains("does not support a settable AXValue"), "{error}");
        assert!(error.contains("patterns it does support: invoke, focus"), "{error}");
        assert!(error.contains("refuses to fall back to a click"), "{error}");
        assert!(ok.log().is_empty(), "отказ ничего не нажимает");

        // Кнопка — не переключатель, хоть и нажимается.
        let error = perform(&ok, &node, Act::Toggle, None, || Ok(())).unwrap_err();
        assert!(error.contains("toggle"), "{error}");

        let text = root.0.borrow().children[4].clone();
        let node = node_from(&text, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        let error = perform(&text, &node, Act::Invoke, None, || Ok(())).unwrap_err();
        assert!(error.contains("patterns it does support: none"), "{error}");
    }

    #[test]
    fn expand_prefers_a_settable_attribute_and_collapse_has_no_menu_road() {
        let row = Fake::new("AXRow").expanded(false).settable(&["AXExpanded"]);
        let node = node_from(&row, 0, None, None, u64::MAX, SCREEN).unwrap();
        perform(&row, &node, Act::Expand, None, || Ok(())).unwrap();
        perform(&row, &node, Act::Collapse, None, || Ok(())).unwrap();
        assert_eq!(row.log(), vec!["set AXExpanded=true", "set AXExpanded=false"]);

        let popup = Fake::new("AXPopUpButton").actions(&["AXPress", "AXShowMenu"]);
        let node = node_from(&popup, 0, None, None, u64::MAX, SCREEN).unwrap();
        perform(&popup, &node, Act::Expand, None, || Ok(())).unwrap();
        assert_eq!(popup.log(), vec!["perform AXShowMenu"]);
        let error = perform(&popup, &node, Act::Collapse, None, || Ok(())).unwrap_err();
        assert!(error.contains("settable AXExpanded (collapse)"), "{error}");
    }

    #[test]
    fn the_guard_runs_before_the_change_and_can_stop_it() {
        let root = dialog();
        let ok = root.0.borrow().children[0].clone();
        let node = node_from(&ok, 1, Some(0), Some("AXWindow"), u64::MAX, SCREEN).unwrap();
        let error = perform(&ok, &node, Act::Invoke, None, || Err("foreground changed before element action".into()))
            .unwrap_err();
        assert!(error.contains("foreground changed"), "{error}");
        assert!(ok.log().is_empty(), "сторож остановил удар до того, как он ушёл");
    }

    #[test]
    fn focus_writes_axfocused_and_select_needs_a_settable_axselected() {
        let stubborn = Fake::new("AXButton").actions(&["AXPress"]).settable(&["AXFocused"]).focused(false);
        let node = node_from(&stubborn, 0, None, None, u64::MAX, SCREEN).unwrap();
        // Подставной элемент разрешает писать в AXFocused, но AXSelected — нет.
        let error = perform(&stubborn, &node, Act::Select, None, || Ok(())).unwrap_err();
        assert!(error.contains("settable AXSelected"), "{error}");
        perform(&stubborn, &node, Act::Focus, None, || Ok(())).unwrap();
        assert_eq!(stubborn.log(), vec!["set AXFocused=true"]);
    }

    #[test]
    fn the_refusal_without_accessibility_names_the_permission_and_the_road_to_it() {
        let words = accessibility_refusal(
            "desktop.window.read",
            &["нет разрешения «Универсальный доступ»: Системные настройки → Конфиденциальность"],
        );
        assert!(words.contains("«Универсальный доступ»"), "{words}");
        assert!(words.contains("desktop.window.read"), "{words}");
        assert!(words.contains("Системные настройки"), "{words}");
        let bare = accessibility_refusal("desktop.element.act", &[]);
        assert!(bare.ends_with('.'), "{bare}");
    }

    #[test]
    fn ax_errors_have_names_and_strangers_keep_their_number() {
        assert_eq!(ax_error_name(AX_ERROR_API_DISABLED), "api_disabled");
        assert_eq!(ax_error_name(AX_ERROR_CANNOT_COMPLETE), "cannot_complete");
        assert_eq!(ax_error_name(AX_ERROR_INVALID_UI_ELEMENT), "invalid_ui_element");
        assert_eq!(ax_error_name(-1), "ax_error_-1");
        let failure = AxFailure::new(AX_ERROR_ACTION_UNSUPPORTED, "AXPress");
        assert_eq!(failure.to_string(), "AXPress: action_unsupported (-25206)");
        assert!(!failure.vanished());
        assert!(AxFailure::new(AX_ERROR_INVALID_UI_ELEMENT, "x").vanished());
    }
}

/// Живые стенды — на раннере macOS. Различают «нет разрешения» и «сломано»: без
/// «Универсального доступа» обязан прийти отказ словами; с ним — хоть одно окно читается
/// или на экране честно нет ни одного обычного окна.
#[cfg(all(test, target_os = "macos"))]
mod live_tests {
    use std::time::Duration;

    use super::live::{AxElement, attach};
    use super::*;
    use crate::mac;

    #[test]
    fn without_accessibility_the_application_element_refuses_and_with_it_windows_are_read() {
        let tcc = mac::tcc();
        let own = std::process::id() as i32;
        let application = AxElement::application(own, Duration::from_secs(2)).expect("application element");
        // pid хранится в самом элементе — это работает и без разрешения.
        assert_eq!(application.pid(), Some(own));
        if !tcc.accessibility {
            // Без разрешения система отказывает (обычно kAXErrorAPIDisabled) или, для
            // собственного процесса, отвечает пустым списком. «Сломано» здесь — это
            // паника обёртки, а не код ответа: любой ответ словами — не поломка.
            match application.windows() {
                Err(failure) => println!("без «Универсального доступа»: {failure}"),
                Ok(windows) => println!(
                    "без «Универсального доступа» система всё же отдала {} окон собственного процесса",
                    windows.len()
                ),
            }
            let words = accessibility_refusal("desktop.window.read", &tcc.hints());
            assert!(words.contains("«Универсальный доступ»"));
            return;
        }
        // Разрешение есть. Собственный процесс стенда — не AppKit-программа: Accessibility
        // в нём не реализован, и система честно отвечает kAXErrorNotImplemented (-25208)
        // на любой его атрибут — так упал третий круг CI, ждавший пустого списка без
        // ошибки. Пустой список и этот код — оба «окон нет», поломка — любой другой отказ.
        match application.windows() {
            Ok(windows) => println!("собственный процесс: {} AX-окон (ожидается 0)", windows.len()),
            Err(failure) if failure.code == -25208 => println!(
                "собственный процесс без AppKit не реализует Accessibility ({failure}) — это не поломка"
            ),
            Err(failure) => panic!("AXWindows of own process: {failure}"),
        }
        let mut candidates: Vec<mac::WindowInfo> = mac::frontmost().expect("window list").into_iter().collect();
        candidates.extend(
            mac::window_list(true)
                .expect("window list")
                .into_iter()
                .filter(|w| w.owner == "Finder" && w.is_ordinary()),
        );
        if candidates.is_empty() {
            println!("на экране нет ни одного обычного окна — читать нечего, это честный пропуск");
            return;
        }
        let mut read_any = false;
        for window in candidates {
            let attached = match attach(&window, Duration::from_secs(2)) {
                Ok(attached) => attached,
                Err(words) => {
                    println!("окно 0x{:X} ({}) не привязалось: {words}", window.id, window.owner);
                    continue;
                }
            };
            println!(
                "окно 0x{:X} «{}» ({}): найдено по {}, сравнили {}",
                window.id,
                attached.title.clone().unwrap_or_default(),
                window.owner,
                attached.matched_by,
                attached.compared
            );
            let screen = mac::virtual_screen();
            let read = walk(
                attached.window,
                WalkLimits { max_nodes: 200, max_depth: 12, max_children_per_node: 64, max_text_chars: 120 },
                std::time::Instant::now() + Duration::from_secs(5),
                Screen { left: screen.left, top: screen.top, width: screen.width, height: screen.height },
                false,
            );
            println!(
                "узлов {} за {} мс, обрезано {:?}, роли: {:?}",
                read.nodes.len(),
                read.walk_ms,
                read.truncated_by,
                read.nodes.iter().take(12).map(|n| n.role).collect::<Vec<_>>()
            );
            assert!(!read.nodes.is_empty(), "окно есть, а узлов нет");
            assert_eq!(read.nodes[0].role, "window");
            read_any = true;
            break;
        }
        assert!(read_any, "«Универсальный доступ» есть, окна есть, а ни одно не привязалось к Accessibility");
    }
}
