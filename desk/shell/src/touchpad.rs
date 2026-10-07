//! Observe PTP contact lifetimes without replacing Chromium's scroll gestures.
//! Only digitizer/touchpad Raw Input is registered. Nothing is retained outside
//! this process's foreground window, and no coordinates or keys are published.
use std::{
    cell::RefCell,
    collections::HashMap,
    mem::{size_of, zeroed},
    ptr,
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tauri::Manager;
use windows_sys::Win32::{
    Devices::HumanInterfaceDevice::*,
    Foundation::{HWND, LPARAM, LRESULT, WPARAM},
    System::{LibraryLoader::GetModuleHandleW, Threading::GetCurrentProcessId},
    UI::{Input::*, WindowsAndMessaging::*},
};

const DIGITIZER: u16 = 0x0d;
const TOUCHPAD: u16 = 0x05;
const HEARTBEAT: Duration = Duration::from_millis(100);
thread_local! { static READER: RefCell<Option<Reader>> = const { RefCell::new(None) }; }

#[derive(Default)]
struct Frame {
    expected: usize,
    fingers: HashMap<u32, bool>,
}
impl Frame {
    // Contact Count includes lifted fingers; count only Tip+Confidence.
    // In hybrid reports only the first packet has a nonzero Contact Count.
    fn push(&mut self, count: usize, fingers: impl IntoIterator<Item = (u32, bool)>) -> Option<u8> {
        if count > 0 {
            self.expected = count;
            self.fingers.clear();
        }
        if self.expected == 0 {
            return Some(0);
        }
        for (id, down) in fingers {
            self.fingers.insert(id, down);
            if self.fingers.len() >= self.expected {
                break;
            }
        }
        if self.fingers.len() < self.expected {
            return None;
        }
        let active = self.fingers.values().filter(|&&down| down).count() as u8;
        self.expected = 0;
        self.fingers.clear();
        Some(active)
    }
}

#[derive(Default)]
struct Finger {
    tip: Option<u16>,
    confidence: Option<u16>,
    id: Option<u16>,
}
struct Device {
    // HID preparsed data is DWORD-aligned, not an arbitrary Vec<u8> allocation.
    preparsed: Vec<u64>,
    fingers: HashMap<u8, Vec<Finger>>,
    counts: HashMap<u8, u16>,
    data_len: u32,
    frame: Frame,
    active: u8,
}
unsafe fn button_index(cap: &HIDP_BUTTON_CAPS, usage: u16) -> Option<u16> {
    if cap.UsagePage != DIGITIZER {
        return None;
    }
    if cap.IsRange != 0 {
        let r = cap.Anonymous.Range;
        (usage >= r.UsageMin && usage <= r.UsageMax).then(|| r.DataIndexMin + usage - r.UsageMin)
    } else {
        let r = cap.Anonymous.NotRange;
        (r.Usage == usage).then_some(r.DataIndex)
    }
}
unsafe fn value_index(cap: &HIDP_VALUE_CAPS, usage: u16) -> Option<u16> {
    if cap.UsagePage != DIGITIZER {
        return None;
    }
    if cap.IsRange != 0 {
        let r = cap.Anonymous.Range;
        (usage >= r.UsageMin && usage <= r.UsageMax).then(|| r.DataIndexMin + usage - r.UsageMin)
    } else {
        let r = cap.Anonymous.NotRange;
        (r.Usage == usage).then_some(r.DataIndex)
    }
}
impl Device {
    unsafe fn load(handle: isize) -> Option<Self> {
        let mut bytes = 0;
        if GetRawInputDeviceInfoW(handle as _, RIDI_PREPARSEDDATA, ptr::null_mut(), &mut bytes)
            == u32::MAX
            || bytes == 0
            || bytes > 65536
        {
            return None;
        }
        let mut preparsed = vec![0u64; (bytes as usize + 7) / 8];
        if GetRawInputDeviceInfoW(
            handle as _,
            RIDI_PREPARSEDDATA,
            preparsed.as_mut_ptr().cast(),
            &mut bytes,
        ) == u32::MAX
        {
            return None;
        }
        let pp = preparsed.as_ptr() as isize;
        let mut caps: HIDP_CAPS = zeroed();
        if HidP_GetCaps(pp, &mut caps) != HIDP_STATUS_SUCCESS
            || caps.UsagePage != DIGITIZER
            || caps.Usage != TOUCHPAD
            || caps.NumberInputDataIndices > 1024
        {
            return None;
        }
        let mut n = caps.NumberInputButtonCaps;
        let mut buttons = vec![zeroed::<HIDP_BUTTON_CAPS>(); n as usize];
        if HidP_GetButtonCaps(HidP_Input, buttons.as_mut_ptr(), &mut n, pp) != HIDP_STATUS_SUCCESS {
            return None;
        }
        let mut slots: HashMap<(u8, u16), Finger> = HashMap::new();
        for cap in &buttons[..n as usize] {
            if let Some(index) = button_index(cap, 0x42) {
                slots
                    .entry((cap.ReportID, cap.LinkCollection))
                    .or_default()
                    .tip = Some(index);
            }
            if let Some(index) = button_index(cap, 0x47) {
                slots
                    .entry((cap.ReportID, cap.LinkCollection))
                    .or_default()
                    .confidence = Some(index);
            }
        }
        let mut n = caps.NumberInputValueCaps;
        let mut values = vec![zeroed::<HIDP_VALUE_CAPS>(); n as usize];
        if HidP_GetValueCaps(HidP_Input, values.as_mut_ptr(), &mut n, pp) != HIDP_STATUS_SUCCESS {
            return None;
        }
        let mut counts = HashMap::new();
        for cap in &values[..n as usize] {
            if let Some(index) = value_index(cap, 0x51) {
                slots
                    .entry((cap.ReportID, cap.LinkCollection))
                    .or_default()
                    .id = Some(index);
            }
            if let Some(index) = value_index(cap, 0x54) {
                counts.insert(cap.ReportID, index);
            }
        }
        let mut ordered: Vec<_> = slots.into_iter().collect();
        ordered.sort_by_key(|(key, _)| *key);
        let mut fingers: HashMap<u8, Vec<Finger>> = HashMap::new();
        for ((report, _), finger) in ordered {
            if finger.tip.is_some()
                && finger.confidence.is_some()
                && finger.id.is_some()
                && counts.contains_key(&report)
            {
                fingers.entry(report).or_default().push(finger);
            }
        }
        if fingers.is_empty() {
            return None;
        }
        Some(Self {
            preparsed,
            fingers,
            counts,
            data_len: caps.NumberInputDataIndices as u32,
            frame: Frame::default(),
            active: 0,
        })
    }
    unsafe fn report(&mut self, report: &mut [u8]) -> Result<(), ()> {
        let Some(&report_id) = report.first() else {
            return Err(());
        };
        let Some(slots) = self.fingers.get(&report_id) else {
            return Ok(());
        };
        let mut data = vec![zeroed::<HIDP_DATA>(); self.data_len as usize];
        let mut n = self.data_len;
        if HidP_GetData(
            HidP_Input,
            data.as_mut_ptr(),
            &mut n,
            self.preparsed.as_ptr() as isize,
            report.as_mut_ptr(),
            report.len() as u32,
        ) != HIDP_STATUS_SUCCESS
        {
            return Err(());
        }
        let values: HashMap<_, _> = data[..n as usize]
            .iter()
            .map(|d| (d.DataIndex, d.Anonymous.RawValue))
            .collect();
        let Some(&count) = values.get(&self.counts[&report_id]) else {
            return Err(());
        };
        if count > 16 {
            return Err(());
        }
        let fingers = slots.iter().filter_map(|slot| {
            let id = *values.get(&slot.id?)?;
            let tip = values.get(&slot.tip?).copied().unwrap_or(0) != 0;
            let confidence = values.get(&slot.confidence?).copied().unwrap_or(0) != 0;
            Some((id, tip && confidence))
        });
        if let Some(active) = self.frame.push(count as usize, fingers) {
            self.active = active;
        }
        Ok(())
    }
}

struct Reader {
    app: tauri::AppHandle,
    devices: HashMap<isize, Option<Device>>,
    published: (bool, u8),
    sent: Instant,
}
impl Reader {
    fn foreground(&self) -> bool {
        unsafe {
            let mut pid = 0;
            GetWindowThreadProcessId(GetForegroundWindow(), &mut pid);
            pid == GetCurrentProcessId()
        }
    }
    fn publish(&mut self) {
        let available = self.devices.values().any(Option::is_some);
        let contacts = self
            .devices
            .values()
            .filter_map(Option::as_ref)
            .map(|d| d.active)
            .max()
            .unwrap_or(0);
        let current = (available, contacts);
        if current == self.published && (contacts == 0 || self.sent.elapsed() < HEARTBEAT) {
            return;
        }
        self.published = current;
        self.sent = Instant::now();
        if let Some(window) = self.app.get_webview_window("main") {
            let sent_at = SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .unwrap_or_default()
                .as_millis();
            let state =
                serde_json::json!({"available":available,"contacts":contacts,"sentAt":sent_at});
            // Drop queued stale contact heartbeats. A stalled bridge expires in
            // the scroller as well; it cannot leave a held edge indefinitely.
            let js=format!("(()=>{{const v={state};if(Date.now()-v.sentAt>600)return;window.__HELENE_TOUCHPAD_CONTACT=v;window.dispatchEvent(new CustomEvent('helene-touchpad-contact',{{detail:v}}));}})()");
            let _ = window.eval(&js);
        }
    }
    fn clear(&mut self) {
        for d in self.devices.values_mut().filter_map(Option::as_mut) {
            d.active = 0;
            d.frame = Frame::default();
        }
    }
    unsafe fn input(&mut self, raw: LPARAM) {
        if !self.foreground() {
            self.clear();
            self.publish();
            return;
        }
        let mut bytes = 0;
        if GetRawInputData(
            raw as _,
            RID_INPUT,
            ptr::null_mut(),
            &mut bytes,
            size_of::<RAWINPUTHEADER>() as u32,
        ) == u32::MAX
            || bytes > 1048576
            || (bytes as usize) < size_of::<RAWINPUTHEADER>() + 8
        {
            return;
        }
        let mut buf = vec![0u8; bytes as usize];
        if GetRawInputData(
            raw as _,
            RID_INPUT,
            buf.as_mut_ptr().cast(),
            &mut bytes,
            size_of::<RAWINPUTHEADER>() as u32,
        ) == u32::MAX
        {
            return;
        }
        let head = ptr::read_unaligned(buf.as_ptr().cast::<RAWINPUTHEADER>());
        if head.dwType != RIM_TYPEHID {
            return;
        }
        let handle = head.hDevice as isize;
        let device = self
            .devices
            .entry(handle)
            .or_insert_with(|| Device::load(handle));
        let Some(device) = device.as_mut() else {
            return;
        };
        let offset = size_of::<RAWINPUTHEADER>();
        let length = u32::from_le_bytes(buf[offset..offset + 4].try_into().unwrap()) as usize;
        let count = u32::from_le_bytes(buf[offset + 4..offset + 8].try_into().unwrap()) as usize;
        if length == 0
            || count > 65536
            || length
                .checked_mul(count)
                .and_then(|n| n.checked_add(offset + 8))
                .filter(|&n| n <= buf.len())
                .is_none()
        {
            self.clear();
            self.publish();
            return;
        }
        for report in buf[offset + 8..offset + 8 + length * count].chunks_exact_mut(length) {
            if device.report(report).is_err() {
                device.active = 0;
                device.frame = Frame::default();
            }
        }
        self.publish();
    }
    fn tick(&mut self) {
        if !self.foreground() {
            self.clear();
        }
        self.publish();
    }
}

unsafe extern "system" fn window_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    // No unwind may escape a Win32 callback.
    let result = std::panic::catch_unwind(|| {
        READER.with(|state| {
            if let Some(reader) = state.borrow_mut().as_mut() {
                if msg == WM_INPUT {
                    reader.input(lp);
                } else if msg == WM_INPUT_DEVICE_CHANGE && wp == 2 {
                    reader.devices.remove(&lp);
                    reader.publish();
                }
            }
        })
    });
    if result.is_err() {
        READER.with(|state| {
            if let Ok(mut state) = state.try_borrow_mut() {
                if let Some(reader) = state.as_mut() {
                    reader.devices.clear();
                    reader.publish();
                }
            }
        });
    }
    DefWindowProcW(hwnd, msg, wp, lp)
}

pub fn start(app: tauri::AppHandle) {
    let result = std::thread::Builder::new()
        .name("helene-touchpad".into())
        .spawn(move || unsafe {
            // Do not overwrite a PTP registration belonging to another component.
            let mut count = 0;
            if GetRegisteredRawInputDevices(
                ptr::null_mut(),
                &mut count,
                size_of::<RAWINPUTDEVICE>() as u32,
            ) == u32::MAX
            {
                return;
            }
            let mut registered = vec![zeroed::<RAWINPUTDEVICE>(); count as usize];
            if count > 0
                && (GetRegisteredRawInputDevices(
                    registered.as_mut_ptr(),
                    &mut count,
                    size_of::<RAWINPUTDEVICE>() as u32,
                ) == u32::MAX
                    || registered
                        .iter()
                        .any(|d| d.usUsagePage == DIGITIZER && d.usUsage == TOUCHPAD))
            {
                crate::log_line("touchpad: existing PTP receiver; using wheel fallback");
                return;
            }
            let class: Vec<u16> = "HeleneTouchpadContacts\0".encode_utf16().collect();
            let instance = GetModuleHandleW(ptr::null());
            let wc = WNDCLASSW {
                lpfnWndProc: Some(window_proc),
                hInstance: instance,
                lpszClassName: class.as_ptr(),
                ..zeroed()
            };
            if RegisterClassW(&wc) == 0 {
                crate::log_line("touchpad: cannot register contact window");
                return;
            }
            let hwnd = CreateWindowExW(
                0,
                class.as_ptr(),
                class.as_ptr(),
                0,
                0,
                0,
                0,
                0,
                HWND_MESSAGE,
                ptr::null_mut(),
                instance,
                ptr::null(),
            );
            if hwnd.is_null() {
                return;
            }
            let device = RAWINPUTDEVICE {
                usUsagePage: DIGITIZER,
                usUsage: TOUCHPAD,
                dwFlags: RIDEV_INPUTSINK | RIDEV_DEVNOTIFY,
                hwndTarget: hwnd,
            };
            if RegisterRawInputDevices(&device, 1, size_of::<RAWINPUTDEVICE>() as u32) == 0 {
                DestroyWindow(hwnd);
                crate::log_line("touchpad: raw contact unavailable; using wheel fallback");
                return;
            }
            READER.with(|s| {
                *s.borrow_mut() = Some(Reader {
                    app,
                    devices: HashMap::new(),
                    published: (false, 0),
                    sent: Instant::now(),
                })
            });
            crate::log_line("touchpad: contact reader ready (PTP only)");
            let mut msg: MSG = zeroed();
            loop {
                while PeekMessageW(&mut msg, ptr::null_mut(), 0, 0, PM_REMOVE) != 0 {
                    if msg.message == WM_QUIT {
                        return;
                    }
                    TranslateMessage(&msg);
                    DispatchMessageW(&msg);
                }
                READER.with(|s| {
                    if let Some(r) = s.borrow_mut().as_mut() {
                        r.tick()
                    }
                });
                std::thread::sleep(Duration::from_millis(20));
            }
        });
    if let Err(e) = result {
        crate::log_line(&format!("touchpad: contact reader did not start: {e}"));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn parallel_frame_counts_tips_not_contact_count() {
        let mut f = Frame::default();
        assert_eq!(f.push(2, [(3, true), (7, true), (9, false)]), Some(2));
        assert_eq!(f.push(2, [(3, false), (7, true)]), Some(1));
        assert_eq!(f.push(1, [(7, false)]), Some(0));
        assert_eq!(f.push(0, []), Some(0));
    }
    #[test]
    fn hybrid_frame_waits_for_all_contact_ids() {
        let mut f = Frame::default();
        assert_eq!(f.push(3, [(3, true)]), None);
        assert_eq!(f.push(0, [(7, true)]), None);
        assert_eq!(f.push(0, [(9, false)]), Some(2));
        assert_eq!(f.push(2, [(3, false)]), None);
        assert_eq!(f.push(0, [(7, false)]), Some(0));
    }
    #[test]
    fn lost_partial_frame_is_replaced_by_next_scan() {
        let mut f = Frame::default();
        assert_eq!(f.push(3, [(3, true)]), None);
        assert_eq!(f.push(1, [(7, false)]), Some(0));
    }
}
