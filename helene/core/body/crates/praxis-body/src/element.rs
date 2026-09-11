//! Действие НАД ЭЛЕМЕНТОМ, а не над точкой экрана.
//!
//! Живой случай, ради которого написан модуль. Рука `computer` умеет читать окно текстом
//! (`desktop.window.read`) и умеет бить по координатам (`desktop.input.perform`). Между
//! этими двумя есть провал, и он стоит дорого:
//!
//! * прочитали дерево, нашли кнопку, взяли её `rect`, ударили в середину — и попали не
//!   туда, если между чтением и ударом окно проехало, список прокрутился или система
//!   стоит на другом масштабе (DPI);
//! * ввод шёл В ФОКУС: «напечатать в поле пароля» значило «сначала как-нибудь навести
//!   фокус, а потом надеяться». Куда именно уехали знаки, никто не проверял;
//! * повторить вчерашнее действие было нечем: числовые `id` из чтения действуют ровно
//!   на один ответ (так и написано в его расписке), а координаты назавтра указывают в
//!   другое место.
//!
//! Здесь этот провал закрыт одним глаголом: НАЗОВИ элемент — и над ним будет сделано
//! действие. Элемент называется тем, что переживает перерисовку окна: `automation_id`,
//! роль, надпись. Само действие идёт через паттерны UI Automation (Invoke, Value,
//! Toggle, ExpandCollapse, ScrollItem, SetFocus), то есть пикселей в этом пути нет
//! вовсе — и DPI, и прокрутка перестают быть вопросом.
//!
//! ⚠ Чего этот модуль НЕ делает, и это важнее того, что делает:
//!
//! * **не выбирает за неё, когда подошли двое.** Два элемента под один отбор — это не
//!   «возьмём первый», а вопрос без ответа: отказ называет всех кандидатов, и она решает
//!   сама (или уточняет отбор, или говорит `nth`);
//! * **не подменяет паттерн ударом по координатам.** Нет у элемента нужного паттерна —
//!   отказ говорит, какие у него ЕСТЬ. Тихий откат к пикселям вернул бы ровно ту
//!   ненадёжность, ради ухода от которой всё это написано;
//! * **не обещает, что действие подействовало.** Расписка говорит, что паттерн был
//!   вызван и каким стал элемент после; «нажалось» — это вывод, и делает его она.
//!
//! COM здесь не живёт: соседний `uia` — единственное место в теле, где он есть, и
//! исполнение уходит туда. Тут разбор аргументов, отбор элемента и расписка — то есть
//! всё, что можно проверить стендом без Windows.

use anyhow::{Result, bail};
use serde::Deserialize;
use serde_json::{Map, Value, json};

/// Глаголы модуля: сделать над элементом и найти элемент.
pub const CAPABILITY: &str = "desktop.element.act";
/// Поиск по дереву ОТДЕЛЬНОЙ рукой.
///
/// ⚠ Зачем он, если есть чтение окна и есть действие. Затем, что между ними снова был
/// провал, и живая проба 10.09 показала его цену: чтобы узнать, как называется кнопка,
/// приходилось читать ВСЁ окно (у Electron это тысячи узлов и обход, упирающийся в
/// срок) — либо промахнуться отбором и прочитать кандидатов в тексте ОТКАЗА. То есть
/// единственным способом посмотреть на дерево прицельно была ошибка.
///
/// И второе: ожидание. «Дождаться, пока появится диалог» раньше значило «поспать
/// секунду и надеяться». Здесь ожидание — часть просьбы: ищем до `timeout_ms`, и
/// расписка говорит, сколько ждали и сколько раз смотрели.
pub const FIND_CAPABILITY: &str = "desktop.element.find";
pub const VERSION: u32 = 1;

/// Диспетчер спрашивает это ПЕРЕД общей веткой `starts_with("desktop.")`: имя начинается
/// с того же префикса, и общая ветка увела бы его в `desktop::dispatch`, где его нет.
pub fn handles(capability: &str) -> bool {
    capability == CAPABILITY || capability == FIND_CAPABILITY
}

/// Что можно сделать над элементом. Каждое — отдельный паттерн UI Automation, и ни одно
/// не подменяется ударом по координатам.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Act {
    /// InvokePattern: нажать. Кнопки, пункты меню, ссылки.
    Invoke,
    /// ValuePattern.SetValue: положить текст В ПОЛЕ, а не в фокус.
    SetValue,
    /// TogglePattern: переключить галочку.
    Toggle,
    /// ExpandCollapsePattern: раскрыть.
    Expand,
    /// ExpandCollapsePattern: свернуть.
    Collapse,
    /// SelectionItemPattern: выбрать пункт списка.
    Select,
    /// ScrollItemPattern: прокрутить окно так, чтобы элемент стал виден.
    ScrollIntoView,
    /// SetFocus: перевести фокус, ничего не меняя.
    Focus,
}

impl Act {
    pub fn name(self) -> &'static str {
        match self {
            Self::Invoke => "invoke",
            Self::SetValue => "set_value",
            Self::Toggle => "toggle",
            Self::Expand => "expand",
            Self::Collapse => "collapse",
            Self::Select => "select",
            Self::ScrollIntoView => "scroll_into_view",
            Self::Focus => "focus",
        }
    }

    fn parse(said: &str) -> Result<Self> {
        Ok(match said.trim() {
            "invoke" => Self::Invoke,
            "set_value" => Self::SetValue,
            "toggle" => Self::Toggle,
            "expand" => Self::Expand,
            "collapse" => Self::Collapse,
            "select" => Self::Select,
            "scroll_into_view" => Self::ScrollIntoView,
            "focus" => Self::Focus,
            other => bail!(
                "do must be one of invoke, set_value, toggle, expand, collapse, select, \
                 scroll_into_view, focus - not {other:?}"
            ),
        })
    }

    /// Меняет ли действие что-нибудь в чужом окне. Расписка обязана это назвать, а
    /// диспетчер — знать: `focus` и `scroll_into_view` двигают вид, но не данные.
    pub fn mutating(self) -> bool {
        !matches!(self, Self::Focus | Self::ScrollIntoView)
    }
}

/// Чем элемент назван. Всё, что здесь есть, переживает перерисовку окна — числовых `id`
/// чтения здесь нет намеренно: они действуют на один ответ.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Selector {
    pub automation_id: Option<String>,
    pub role: Option<String>,
    pub name: Option<String>,
    pub name_contains: Option<String>,
    pub value_contains: Option<String>,
    /// Который по счёту из подошедших, считая с нуля. Задан — двое перестают быть
    /// вопросом без ответа: она сказала, который берём.
    pub nth: Option<usize>,
}

impl Selector {
    /// Пуст ли отбор. Пустой — это «любой элемент окна», то есть промах по устройству:
    /// действовать над «чем угодно» нельзя.
    pub fn is_empty(&self) -> bool {
        self.automation_id.is_none()
            && self.role.is_none()
            && self.name.is_none()
            && self.name_contains.is_none()
            && self.value_contains.is_none()
    }

    /// Подходит ли узел. Все заданные поля должны совпасть — «и», а не «или»: отбор
    /// сужают, чтобы он стал однозначным, и «или» работало бы против этого.
    ///
    /// Точные поля (`automation_id`, `role`, `name`) сверяются целиком и с учётом
    /// регистра у `automation_id` (его пишет разработчик окна, и он точен), а `role` и
    /// `name` — без учёта регистра: их человек списывает с экрана.
    pub fn matches(&self, node: &NodeView<'_>) -> bool {
        if let Some(want) = &self.automation_id
            && node.automation_id != Some(want.as_str())
        {
            return false;
        }
        if let Some(want) = &self.role
            && !same_fold(node.role, want)
        {
            return false;
        }
        if let Some(want) = &self.name {
            match node.name {
                Some(got) if same_fold(got, want) => {}
                _ => return false,
            }
        }
        if let Some(want) = &self.name_contains {
            let want = want.to_lowercase();
            match node.name {
                Some(got) if got.to_lowercase().contains(&want) => {}
                _ => return false,
            }
        }
        if let Some(want) = &self.value_contains {
            let want = want.to_lowercase();
            match node.value {
                Some(got) if got.to_lowercase().contains(&want) => {}
                _ => return false,
            }
        }
        true
    }

    fn json(&self) -> Value {
        let mut object = Map::new();
        for (key, value) in [
            ("automation_id", self.automation_id.as_ref()),
            ("role", self.role.as_ref()),
            ("name", self.name.as_ref()),
            ("name_contains", self.name_contains.as_ref()),
            ("value_contains", self.value_contains.as_ref()),
        ] {
            if let Some(value) = value {
                object.insert(key.into(), json!(value));
            }
        }
        if let Some(nth) = self.nth {
            object.insert("nth".into(), json!(nth));
        }
        Value::Object(object)
    }
}

/// Равны ли надписи без учёта регистра — по-настоящему, а не по-английски.
///
/// ⚠ Здесь стоял `eq_ignore_ascii_case`, и стенд поймал это первым же вопросом: он
/// складывает регистр ТОЛЬКО у латиницы, а надписи в этом продукте русские. «ОК» на
/// кнопке и «ок», списанное человеком с экрана, оказывались разными строками — то есть
/// отбор промахивался ровно в самом частом случае, ради которого написан.
fn same_fold(left: &str, right: &str) -> bool {
    left == right || left.to_lowercase() == right.to_lowercase()
}

/// Узел глазами отбора. Ссылки, а не копии: отбор бежит по живому обходу окна, и
/// копировать каждую надпись ради сравнения незачем.
#[derive(Debug, Clone, Copy, Default)]
pub struct NodeView<'a> {
    pub role: &'a str,
    pub name: Option<&'a str>,
    pub value: Option<&'a str>,
    pub automation_id: Option<&'a str>,
}

/// Кого выбрал отбор — и почему именно его.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Choice {
    /// Ровно один подошёл (или `nth` назвал, который из многих).
    One { index: usize, total: usize },
    /// Не подошёл никто.
    None,
    /// Подошли несколько, а `nth` не сказан. Это НЕ «берём первый».
    Ambiguous { total: usize },
}

/// Выбрать элемент из подошедших.
///
/// ⚠ Здесь и есть главное решение модуля. «Взять первый подошедший» — самый естественный
/// и самый опасный вариант: у окна с тремя кнопками «OK» первая та, что первой попалась
/// обходу, а не та, которую человек имел в виду. Молчаливый выбор из двух — это ошибка,
/// которая проявится один раз из десяти и будет выглядеть как случайность.
pub fn choose(matched: &[usize], nth: Option<usize>) -> Choice {
    match (matched.len(), nth) {
        (0, _) => Choice::None,
        (_, Some(n)) => match matched.get(n) {
            Some(&index) => Choice::One {
                index,
                total: matched.len(),
            },
            None => Choice::None,
        },
        (1, None) => Choice::One {
            index: matched[0],
            total: 1,
        },
        (total, None) => Choice::Ambiguous { total },
    }
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ActArgs {
    hwnd: Option<Value>,
    #[serde(default)]
    select: SelectArgs,
    #[serde(rename = "do")]
    act: Option<String>,
    text: Option<String>,
    /// Сколько ждать появления элемента, прежде чем сдаться. 0 — не ждать вовсе.
    timeout_ms: Option<u64>,
    /// Потолок обхода: те же пределы, что у чтения окна.
    max_nodes: Option<u64>,
    max_depth: Option<u64>,
}

#[derive(Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
struct SelectArgs {
    automation_id: Option<String>,
    role: Option<String>,
    name: Option<String>,
    name_contains: Option<String>,
    value_contains: Option<String>,
    nth: Option<usize>,
}

/// Разобранная просьба.
#[derive(Debug, Clone)]
pub struct Plan {
    pub hwnd: Option<u64>,
    pub select: Selector,
    pub act: Act,
    pub text: Option<String>,
    pub timeout_ms: u64,
    pub max_nodes: u64,
    pub max_depth: u64,
}

/// Пределы обхода. Свои, а не общие с чтением: здесь ищут ОДИН элемент, и потолок нужен
/// другой — глубже, но у́же по надписям.
const MAX_NODES_DEFAULT: u64 = 4_000;
const MAX_NODES_CAP: u64 = 40_000;
const MAX_DEPTH_DEFAULT: u64 = 40;
const MAX_DEPTH_CAP: u64 = 120;
const TIMEOUT_DEFAULT_MS: u64 = 3_000;
const TIMEOUT_CAP_MS: u64 = 60_000;

fn clamp(said: Option<u64>, default: u64, cap: u64) -> u64 {
    said.unwrap_or(default).min(cap).max(1)
}

pub fn plan(args: Value) -> Result<Plan> {
    let args: ActArgs =
        serde_json::from_value(args).map_err(|e| anyhow::anyhow!("{CAPABILITY} arguments: {e}"))?;
    let act = Act::parse(args.act.as_deref().unwrap_or_default())?;
    // ⚠ Пустое значение поля отбора — ОТКАЗ, а не «условия не было».
    //
    // Раньше строка из одних пробелов молча превращалась в `None`: условие
    // исчезало, отбор становился шире, чем просила она, и «ровно одно
    // совпадение» могло оказаться ложной уникальностью — то есть действие
    // ушло бы в чужой элемент. Названо ревью 11.09; молчаливое исчезновение
    // условия в этом модуле запрещено по устройству.
    let blank: Vec<&str> = [
        ("automation_id", &args.select.automation_id),
        ("role", &args.select.role),
        ("name", &args.select.name),
        ("name_contains", &args.select.name_contains),
        ("value_contains", &args.select.value_contains),
    ]
    .iter()
    .filter(|(_, v)| v.as_deref().is_some_and(|s| s.trim().is_empty()))
    .map(|(k, _)| *k)
    .collect();
    if !blank.is_empty() {
        bail!(
            "these selector fields are present but blank: {}. A blank field is not \
             \"no condition\": dropping it silently would widen the selector and could \
             make a different element look unique. Remove the key or give it a value",
            blank.join(", ")
        );
    }
    let trim = |v: Option<String>| {
        v.filter(|s| !s.trim().is_empty())
    };
    let select = Selector {
        automation_id: trim(args.select.automation_id),
        role: trim(args.select.role),
        name: trim(args.select.name),
        name_contains: trim(args.select.name_contains),
        value_contains: trim(args.select.value_contains),
        nth: args.select.nth,
    };
    if select.is_empty() {
        bail!(
            "select is empty: name the element by automation_id, role, name, name_contains \
             or value_contains. Acting on \"any element of the window\" is a miss by design"
        );
    }
    // `set_value` без текста — не «положить пустоту», а забытый аргумент: очистить поле
    // просят явно, пустой строкой в `text`, а не отсутствием ключа.
    if act == Act::SetValue && args.text.is_none() {
        bail!("set_value needs text: pass an empty string to clear the field, not no key at all");
    }
    if act != Act::SetValue && args.text.is_some() {
        bail!("text belongs to set_value only: {} ignores it", act.name());
    }
    Ok(Plan {
        hwnd: match args.hwnd {
            Some(value) => Some(hwnd_of(&value)?),
            None => None,
        },
        select,
        act,
        text: args.text,
        timeout_ms: args.timeout_ms.unwrap_or(TIMEOUT_DEFAULT_MS).min(TIMEOUT_CAP_MS),
        max_nodes: clamp(args.max_nodes, MAX_NODES_DEFAULT, MAX_NODES_CAP),
        max_depth: clamp(args.max_depth, MAX_DEPTH_DEFAULT, MAX_DEPTH_CAP),
    })
}

/// Аргументы поиска. Отбор тот же, что у действия, — одно правило именования на две
/// руки; `nth` здесь бессмысленно (возвращаем всех, кто подошёл), а `limit` — нужен.
#[derive(Debug, Deserialize)]
struct FindArgs {
    hwnd: Option<Value>,
    #[serde(default)]
    select: SelectArgs,
    /// Сколько кандидатов вернуть. Ответ читает модель, и «все три тысячи» — не ответ.
    limit: Option<u64>,
    /// Сколько ждать ПЕРВОГО совпадения. 0 — посмотреть один раз и ответить как есть.
    timeout_ms: Option<u64>,
    max_nodes: Option<u64>,
    max_depth: Option<u64>,
}

/// Разобранная просьба поиска.
#[derive(Debug, Clone)]
pub struct FindPlan {
    pub hwnd: Option<u64>,
    pub select: Selector,
    pub limit: usize,
    pub timeout_ms: u64,
    pub max_nodes: u64,
    pub max_depth: u64,
}

const LIMIT_DEFAULT: u64 = 20;
const LIMIT_CAP: u64 = 200;

pub fn find_plan(args: Value) -> Result<FindPlan> {
    let args: FindArgs = serde_json::from_value(args)
        .map_err(|e| anyhow::anyhow!("{FIND_CAPABILITY} arguments: {e}"))?;
    // Та же проверка, что у действия: пустое поле — отказ, а не тихое расширение
    // отбора. У поиска цена ошибки меньше (он ничего не нажимает), но ответ
    // «нашлось одно» читается так же, и врать им нельзя.
    let blank: Vec<&str> = [
        ("automation_id", &args.select.automation_id),
        ("role", &args.select.role),
        ("name", &args.select.name),
        ("name_contains", &args.select.name_contains),
        ("value_contains", &args.select.value_contains),
    ]
    .iter()
    .filter(|(_, v)| v.as_deref().is_some_and(|s| s.trim().is_empty()))
    .map(|(k, _)| *k)
    .collect();
    if !blank.is_empty() {
        bail!(
            "these selector fields are present but blank: {}. Remove the key or give \
             it a value: a blank field is not \"no condition\"",
            blank.join(", ")
        );
    }
    let trim = |v: Option<String>| v.map(|s| s.trim().to_string()).filter(|s| !s.is_empty());
    let select = Selector {
        automation_id: trim(args.select.automation_id),
        role: trim(args.select.role),
        name: trim(args.select.name),
        name_contains: trim(args.select.name_contains),
        value_contains: trim(args.select.value_contains),
        // ⚠ `nth` в поиске молча игнорировать нельзя: она сказала бы «третий», получила
        // бы всех и решила, что отбор её не понял. Лучше сказать словами.
        nth: None,
    };
    if args.select.nth.is_some() {
        bail!(
            "nth belongs to {CAPABILITY}: find returns every match, and picking one from \
             them is what nth is for when you act"
        );
    }
    if select.is_empty() {
        bail!(
            "select is empty: name what to look for by automation_id, role, name, \
             name_contains or value_contains. To see the whole window there is \
             desktop.window.read"
        );
    }
    Ok(FindPlan {
        hwnd: match args.hwnd {
            Some(value) => Some(hwnd_of(&value)?),
            None => None,
        },
        select,
        limit: clamp(args.limit, LIMIT_DEFAULT, LIMIT_CAP) as usize,
        // Умолчание ЗДЕСЬ ноль, а не три секунды: поиск чаще спрашивают «что там
        // сейчас», и молчаливое ожидание превращало бы простой вопрос в паузу.
        // Ждать просят явно.
        timeout_ms: args.timeout_ms.unwrap_or(0).min(TIMEOUT_CAP_MS),
        max_nodes: clamp(args.max_nodes, MAX_NODES_DEFAULT, MAX_NODES_CAP),
        max_depth: clamp(args.max_depth, MAX_DEPTH_DEFAULT, MAX_DEPTH_CAP),
    })
}

/// Расписка поиска. `matched` — сколько подошло всего, `elements` — сколько показано.
///
/// `searched_whole_window` здесь по той же причине, что и у действия: «не нашлось» и
/// «не досмотрел» — разные ответы, и путать их нельзя.
pub fn find_receipt(
    plan: &FindPlan,
    matched: usize,
    elements: Vec<Value>,
    scanned: usize,
    whole: bool,
    waited_ms: u64,
    polls: u64,
) -> Value {
    let shown = elements.len();
    json!({
        "ok": true,
        "select": plan.select.json(),
        "matched": matched,
        "shown": shown,
        "truncated": matched > shown,
        "elements": elements,
        "nodes_scanned": scanned,
        "searched_whole_window": whole,
        "waited_ms": waited_ms,
        "polls": polls,
        "note": "ids are not part of this answer on purpose: name the element the same \
                 way when you act on it (desktop.element.act)",
    })
}

/// Тот же разбор, что у чтения окна: и `"0x1F4"`, и `"500"`, и `500`.
fn hwnd_of(value: &Value) -> Result<u64> {
    let parsed = match value {
        Value::Number(number) => number.as_u64(),
        Value::String(text) => {
            let text = text.trim();
            match text.strip_prefix("0x").or_else(|| text.strip_prefix("0X")) {
                Some(hex) => u64::from_str_radix(hex, 16).ok(),
                None => text.parse::<u64>().ok(),
            }
        }
        _ => None,
    };
    match parsed {
        Some(value) if value != 0 && value <= usize::MAX as u64 => Ok(value),
        _ => bail!("invalid hwnd"),
    }
}

/// Почему не нашлось — и это ТРИ разных ответа, а не один.
///
/// ⚠ Разница не косметическая. «Элемента нет» и «я не досмотрел окно» выглядят одинаково
/// изнутри (в обоих случаях совпадений ноль), но значат противоположное: в первом случае
/// отбор назвал то, чего в окне не существует, во втором — окно не дочитано, и сказать
/// «не нашлось» значило бы соврать о том, чего мы не смотрели.
///
/// Живая проба 10.09 нашла это первым же вызовом: окно Electron несёт тысячи элементов,
/// обход упёрся в срок на 1413-м, и без этой развилки ответ был бы «такой кнопки нет» —
/// про кнопку, до которой обход просто не дошёл.
///
/// `limit` — во что упёрся обход (`max_nodes`, `timeout`) или `None`, если дочитал окно.
pub fn not_found(limit: Option<&str>, nth_given: bool, found_any: bool) -> (&'static str, &'static str) {
    match limit {
        Some("max_nodes") => (
            "max_nodes",
            "the walk stopped at max_nodes before the window ended: raise max_nodes, \
             or point hwnd at a smaller window",
        ),
        Some("timeout") => (
            "timeout",
            "the walk ran out of timeout_ms before the window ended: raise timeout_ms - \
             big Electron/Chromium windows carry thousands of elements",
        ),
        Some("max_depth") => ("max_depth", "the walk stopped at max_depth; raise max_depth"),
        Some(_) => ("incomplete", "the walk stopped early for an unnamed reason"),
        None if nth_given && found_any => (
            "nth_out_of_range",
            "the selector matched, but fewer elements than the nth you asked for",
        ),
        None => (
            "not_found",
            "read the window (desktop.window.read) to see what is actually there; \
             the selector may name something this window does not have",
        ),
    }
}

/// Отказ «подошли несколько» — со списком кандидатов, а не одним числом.
///
/// Список здесь не украшение: он и есть ответ на вопрос «как уточнить». Владелец (и она)
/// видят, чем кандидаты отличаются, и дописывают отбор — или называют `nth`.
pub fn ambiguous_error(total: usize, candidates: Vec<Value>) -> Value {
    json!({
        "ok": false,
        "reason": "ambiguous",
        "matched": total,
        "candidates": candidates,
        "hint": "narrow the selector (automation_id is exact) or say select.nth to pick one",
    })
}

/// Расписка удачного действия.
///
/// ⚠ Говорит ровно то, что произошло: паттерн был вызван, и вот каким элемент стал
/// ПОСЛЕ. Слова «получилось» здесь нет: вызов паттерна и достигнутая цель — разные
/// вещи, и вывод делает она.
pub fn receipt(plan: &Plan, chosen: Value, after: Value, waited_ms: u64, polls: u64) -> Value {
    json!({
        "ok": true,
        "did": plan.act.name(),
        "mutating": plan.act.mutating(),
        "select": plan.select.json(),
        "element": chosen,
        "element_after": after,
        "waited_ms": waited_ms,
        "polls": polls,
        "note": "the pattern was invoked and the element re-read afterwards; \
                 whether that achieved the goal is yours to judge",
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn node<'a>(role: &'a str, name: Option<&'a str>, id: Option<&'a str>) -> NodeView<'a> {
        NodeView {
            role,
            name,
            automation_id: id,
            value: None,
        }
    }

    #[test]
    fn exact_fields_keep_whitespace_and_unknown_selectors_fail_closed() {
        let p = plan(json!({"do": "invoke", "select": {"automation_id": " save ", "name": " OK "}})).unwrap();
        assert_eq!(p.select.automation_id.as_deref(), Some(" save "));
        assert_eq!(p.select.name.as_deref(), Some(" OK "));
        assert!(plan(json!({"do": "invoke", "select": {"role": "button", "automationId": "save"}})).is_err());
        assert!(plan(json!({"do": "invoke", "select": {"role": "button"}, "expected_pid": 123})).is_err());
    }

    #[test]
    fn find_refuses_an_empty_selector_and_points_at_reading_the_window() {
        let error = find_plan(json!({})).unwrap_err().to_string();
        assert!(error.contains("select is empty"), "{error}");
        assert!(error.contains("desktop.window.read"), "куда идти за всем окном: {error}");
    }

    #[test]
    fn find_says_that_nth_is_not_its_word() {
        let error = find_plan(json!({"select": {"role": "button", "nth": 2}}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("nth belongs to"), "{error}");
    }

    #[test]
    fn find_waits_only_when_asked() {
        // Умолчание — посмотреть один раз: «что там сейчас» не должно превращаться
        // в паузу. Ждать просят явно, и потолок ожидания тот же, что у действия.
        let quick = find_plan(json!({"select": {"role": "button"}})).unwrap();
        assert_eq!(quick.timeout_ms, 0);
        assert_eq!(quick.limit, LIMIT_DEFAULT as usize);
        let waiting = find_plan(json!({"select": {"role": "button"}, "timeout_ms": 999_999}))
            .unwrap();
        assert_eq!(waiting.timeout_ms, TIMEOUT_CAP_MS);
        let many = find_plan(json!({"select": {"role": "button"}, "limit": 9_999})).unwrap();
        assert_eq!(many.limit, LIMIT_CAP as usize);
    }

    #[test]
    fn find_receipt_says_when_it_showed_fewer_than_it_found() {
        let plan = find_plan(json!({"select": {"role": "button"}, "limit": 2})).unwrap();
        let shown = vec![json!({"role": "button"}), json!({"role": "button"})];
        let receipt = find_receipt(&plan, 7, shown, 120, true, 0, 1);
        assert_eq!(receipt["matched"], 7);
        assert_eq!(receipt["shown"], 2);
        assert_eq!(receipt["truncated"], true);
        assert_eq!(receipt["searched_whole_window"], true);
    }

    #[test]
    fn an_empty_selector_is_refused_by_name() {
        let error = plan(json!({"do": "invoke"})).unwrap_err().to_string();
        assert!(error.contains("select is empty"), "{error}");
        assert!(error.contains("automation_id"), "подсказка обязана назвать чем звать: {error}");
    }

    #[test]
    fn an_unknown_action_lists_the_known_ones() {
        let error = plan(json!({"do": "press", "select": {"name": "OK"}}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("invoke"), "{error}");
        assert!(error.contains("scroll_into_view"), "{error}");
    }

    #[test]
    fn setting_a_value_without_text_is_a_forgotten_argument_not_an_empty_field() {
        let error = plan(json!({"do": "set_value", "select": {"role": "edit"}}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("empty string to clear"), "{error}");
        // А пустая строка — законная просьба очистить поле.
        let ok = plan(json!({"do": "set_value", "select": {"role": "edit"}, "text": ""})).unwrap();
        assert_eq!(ok.text.as_deref(), Some(""));
    }

    #[test]
    fn text_offered_to_an_action_that_ignores_it_is_refused_rather_than_dropped() {
        let error = plan(json!({"do": "invoke", "select": {"name": "OK"}, "text": "привет"}))
            .unwrap_err()
            .to_string();
        assert!(error.contains("set_value only"), "{error}");
    }

    #[test]
    fn every_selector_field_must_match_at_once() {
        let want = Selector {
            role: Some("button".into()),
            name_contains: Some("сохран".into()),
            ..Selector::default()
        };
        assert!(want.matches(&node("button", Some("Сохранить как"), None)));
        assert!(!want.matches(&node("menu_item", Some("Сохранить как"), None)));
        assert!(!want.matches(&node("button", Some("Отмена"), None)));
    }

    #[test]
    fn a_name_written_off_the_screen_matches_regardless_of_case() {
        // ⚠ Кириллица — не мелочь, а самый частый случай: надписи в этом продукте
        // русские, и человек списывает их с экрана как получится. `eq_ignore_ascii_case`,
        // стоявший здесь сначала, складывал регистр только у латиницы — и отбор
        // промахивался ровно там, ради чего написан.
        let want = Selector { name: Some("ок".into()), ..Selector::default() };
        assert!(want.matches(&node("button", Some("ОК"), None)));

        let save = Selector { name: Some("СОХРАНИТЬ".into()), ..Selector::default() };
        assert!(save.matches(&node("button", Some("Сохранить"), None)));

        // Латиница по-прежнему складывается — починка не должна была её сломать.
        let ok = Selector { name: Some("cancel".into()), ..Selector::default() };
        assert!(ok.matches(&node("button", Some("Cancel"), None)));

        // И `name_contains` тоже: он всегда шёл через to_lowercase, но проверить надо.
        let part = Selector { name_contains: Some("ХРАНИ".into()), ..Selector::default() };
        assert!(part.matches(&node("button", Some("Сохранить как"), None)));
    }

    #[test]
    fn an_automation_id_is_compared_exactly() {
        // Его пишет разработчик окна, и он точен: «saveBtn» и «savebtn» — разные штуки,
        // и списывать его с экрана человеку не приходится.
        let want = Selector { automation_id: Some("saveBtn".into()), ..Selector::default() };
        assert!(want.matches(&node("button", None, Some("saveBtn"))));
        assert!(!want.matches(&node("button", None, Some("savebtn"))));
    }

    #[test]
    fn choosing_refuses_to_pick_for_her() {
        assert_eq!(choose(&[], None), Choice::None);
        assert_eq!(choose(&[7], None), Choice::One { index: 7, total: 1 });
        assert_eq!(choose(&[7, 9], None), Choice::Ambiguous { total: 2 });
        // Сказала, который из двух — вопрос снят.
        assert_eq!(choose(&[7, 9], Some(1)), Choice::One { index: 9, total: 2 });
        // Сказала про третий, а их два — это промах, а не «возьмём последний».
        assert_eq!(choose(&[7, 9], Some(2)), Choice::None);
    }

    #[test]
    fn limits_are_clamped_and_never_zero() {
        let big = plan(json!({
            "do": "focus", "select": {"role": "edit"},
            "max_nodes": 999_999, "max_depth": 999, "timeout_ms": 999_999
        }))
        .unwrap();
        assert_eq!(big.max_nodes, MAX_NODES_CAP);
        assert_eq!(big.max_depth, MAX_DEPTH_CAP);
        assert_eq!(big.timeout_ms, TIMEOUT_CAP_MS);
        let zero = plan(json!({"do": "focus", "select": {"role": "edit"}, "max_nodes": 0}))
            .unwrap();
        assert_eq!(zero.max_nodes, 1, "ноль узлов — это не обход, а тишина");
    }

    #[test]
    fn moving_the_view_is_not_called_a_change_of_data() {
        assert!(!Act::Focus.mutating());
        assert!(!Act::ScrollIntoView.mutating());
        assert!(Act::Invoke.mutating());
        assert!(Act::SetValue.mutating());
    }

    #[test]
    fn the_receipt_does_not_claim_success() {
        let p = plan(json!({"do": "invoke", "select": {"name": "OK"}})).unwrap();
        let said = receipt(&p, json!({"role": "button"}), json!({"role": "button"}), 12, 2);
        let text = said.to_string();
        assert!(text.contains("element_after"), "{text}");
        assert!(
            text.contains("yours to judge"),
            "расписка не имеет права объявлять цель достигнутой: {text}"
        );
    }

    #[test]
    fn an_unfinished_walk_is_never_called_absence() {
        // Живая проба 10.09: окно Electron, обход упёрся в срок на 1413-м элементе.
        // Без этой развилки ответ был бы «такой кнопки нет» — про кнопку, до которой
        // обход просто не дошёл.
        let (reason, hint) = not_found(Some("timeout"), false, false);
        assert_eq!(reason, "timeout");
        assert!(hint.contains("raise timeout_ms"), "{hint}");

        let (reason, hint) = not_found(Some("max_nodes"), false, false);
        assert_eq!(reason, "max_nodes");
        assert!(hint.contains("raise max_nodes"), "{hint}");

        assert_eq!(not_found(Some("max_depth"), false, false).0, "max_depth");
        assert_eq!(not_found(Some("provider_error"), false, false).0, "incomplete");

        // Дочитали окно целиком и не нашли — вот теперь это «нет такого».
        let (reason, hint) = not_found(None, false, false);
        assert_eq!(reason, "not_found");
        assert!(hint.contains("desktop.window.read"), "{hint}");

        // Сказала `nth`, а совпадений меньше — это промах отбора, а не пустое окно.
        assert_eq!(not_found(None, true, true).0, "nth_out_of_range");
        // Но если не нашлось вовсе, `nth` ничего не объясняет.
        assert_eq!(not_found(None, true, false).0, "not_found");
    }

    #[test]
    fn ambiguity_is_answered_with_the_candidates_themselves() {
        let said = ambiguous_error(3, vec![json!({"name": "OK", "automation_id": "ok1"})]);
        let text = said.to_string();
        assert!(text.contains("ambiguous"), "{text}");
        assert!(text.contains("ok1"), "кандидаты обязаны приехать целиком: {text}");
        assert!(text.contains("nth"), "отказ обязан сказать, как уточнить: {text}");
    }
}
