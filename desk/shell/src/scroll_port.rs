//! Native scroll-session observers. Original wheel events are never consumed.
use crate::scroll_session::Session;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(target_os = "macos")]
pub(crate) fn mac_event(e: &objc2_app_kit::NSEvent) -> Session {
    if e.modifierFlags()
        .contains(objc2_app_kit::NSEventModifierFlags::Control)
    {
        Session::default()
    } else {
        Session::mac(
            e.phase().0,
            e.momentumPhase().0,
            e.hasPreciseScrollingDeltas(),
        )
    }
}

fn support(hold: &str, source: &str) -> String {
    format!(
        "window.__HELENE_SCROLL_SUPPORT={};",
        serde_json::json!({"hold":hold,"source":source})
    )
}

pub(crate) fn script(state: Session, seq: u64, source: &str) -> String {
    let sent_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis();
    let value = serde_json::json!({"available":state.available,"active":state.active,
        "momentum":state.momentum,"seq":seq,"source":source,"sentAt":sent_at});
    // Late native callbacks/heartbeats must not resurrect an ended gesture.
    format!("(()=>{{const v={value},old=window.__HELENE_SCROLL_SESSION;if(Date.now()-v.sentAt>600||(old&&(old.seq>v.seq||old.sentAt>v.sentAt)))return;window.__HELENE_SCROLL_SESSION=v;window.dispatchEvent(new CustomEvent('helene-scroll-session',{{detail:v}}));}})()")
}

#[cfg(target_os = "macos")]
mod mac {
    use super::*;
    use block2::RcBlock;
    use objc2::{rc::Retained, runtime::AnyObject, MainThreadMarker};
    use objc2_app_kit::{NSEvent, NSEventMask};
    use std::{
        cell::RefCell,
        sync::{
            atomic::{AtomicBool, Ordering},
            Arc, Mutex,
        },
        time::Duration,
    };
    use tauri::Manager;

    struct Monitor(Retained<AnyObject>);
    impl Drop for Monitor {
        fn drop(&mut self) {
            unsafe {
                NSEvent::removeMonitor(&self.0);
            }
        }
    }
    thread_local! { static MONITOR: RefCell<Option<Monitor>> = const { RefCell::new(None) }; }

    pub fn start(app: tauri::AppHandle) {
        let Some(mtm) = MainThreadMarker::new() else {
            crate::log_line("scroll: macOS observer requires the main thread");
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.eval(&support("unavailable", "macos"));
            }
            return;
        };
        if MONITOR.with(|m| m.borrow().is_some()) {
            return;
        }
        let state = Arc::new(Mutex::new((Session::default(), 0u64, 0usize)));
        let callback_state = state.clone();
        let callback_app = app.clone();
        let block = RcBlock::new(move |event: std::ptr::NonNull<NSEvent>| {
            // A local monitor observes only this app. Keep the original event
            // and only observe its current main window (including recreation).
            let e = unsafe { event.as_ref() };
            if let Some(window) = callback_app.get_webview_window("main") {
                let ours = window.ns_window().ok();
                let target = e.window(mtm);
                let same_window = target
                    .as_ref()
                    .is_some_and(|w| Some(Retained::as_ptr(w).cast_mut().cast()) == ours);
                if same_window {
                    let phase = mac_event(e);
                    if let Ok(mut guard) = callback_state.lock() {
                        guard.0 = phase;
                        guard.1 += 1;
                        guard.2 = ours.unwrap_or_default() as usize;
                        let _ = window.eval(&script(phase, guard.1, "macos"));
                    }
                }
            }
            event.as_ptr()
        });
        let monitor = unsafe {
            NSEvent::addLocalMonitorForEventsMatchingMask_handler(NSEventMask::ScrollWheel, &block)
        };
        let Some(monitor) = monitor else {
            crate::log_line("scroll: macOS phase observer unavailable; wheel fallback");
            if let Some(w) = app.get_webview_window("main") {
                let _ = w.eval(&support("unavailable", "macos"));
            }
            return;
        };
        MONITOR.with(|m| *m.borrow_mut() = Some(Monitor(monitor)));
        // Bound the queued work to one callback if the UI thread is blocked.
        let queued = Arc::new(AtomicBool::new(false));
        let _ = std::thread::Builder::new()
            .name("helene-scroll-session".into())
            .spawn(move || loop {
                std::thread::sleep(Duration::from_millis(100));
                if queued.swap(true, Ordering::AcqRel) {
                    continue;
                }
                let queued_done = queued.clone();
                let state = state.clone();
                let app_main = app.clone();
                if app
                    .run_on_main_thread(move || {
                        queued_done.store(false, Ordering::Release);
                        if let Ok(mut guard) = state.lock() {
                            let window = app_main.get_webview_window("main");
                            let mut changed = false;
                            let generation = window
                                .as_ref()
                                .and_then(|w| w.ns_window().ok())
                                .unwrap_or_default()
                                as usize;
                            if generation != guard.2
                                || !window.as_ref().is_some_and(|w| {
                                    w.is_focused().unwrap_or(false)
                                        && w.is_visible().unwrap_or(false)
                                })
                            {
                                if guard.0 != Session::default() {
                                    guard.0 = Session::default();
                                    guard.1 += 1;
                                    changed = true;
                                }
                                guard.2 = generation;
                            }
                            if let Some(window) =
                                window.filter(|_| changed || guard.0.active || guard.0.momentum)
                            {
                                let _ = window.eval(&script(guard.0, guard.1, "macos"));
                            }
                        }
                    })
                    .is_err()
                {
                    break;
                }
            });
        crate::log_line("scroll: macOS phase observer ready");
        // Each recreated page also receives this via its initialization script.
    }
}

#[cfg(target_os = "macos")]
pub use mac::start;

#[cfg(target_os = "linux")]
pub fn attach(window: &tauri::WebviewWindow) {
    use gtk::{gdk, glib, prelude::*};
    use std::{cell::Cell, rc::Rc, time::Duration};
    use webkit2gtk::WebViewExt;
    // Don't call Tauri dispatchers from with_webview/GTK signal callbacks.
    // The dispatcher can still hold its window-id mutex. Use WebKit directly.
    fn emit(view: &webkit2gtk::WebView, js: &str) {
        view.evaluate_javascript(js, None, None, None::<&gtk::gio::Cancellable>, |_| {});
    }
    let result = window.with_webview(move |platform| {
        let view = platform.inner();
        let wayland = view.display().type_().name().contains("Wayland");
        if !wayland {
            crate::log_line("scroll: X11 has no verified gesture end; wheel fallback");
            return;
        }
        let state = Rc::new(Cell::new((Session::default(), 0u64)));
        let input = state.clone();
        view.connect_scroll_event(move |view, event| {
            let touchpad = event
                .source_device()
                .is_some_and(|d| d.source() == gdk::InputSource::Touchpad);
            let phase = Session::wayland(
                touchpad && !event.state().contains(gdk::ModifierType::CONTROL_MASK),
                event.direction() == gdk::ScrollDirection::Smooth,
                event.is_stop(),
            );
            let seq = input.get().1 + 1;
            input.set((phase, seq));
            emit(view, &script(phase, seq, "wayland"));
            glib::Propagation::Proceed
        });
        let lost = state.clone();
        view.connect_focus_out_event(move |view, _| {
            let seq = lost.get().1 + 1;
            lost.set((Session::default(), seq));
            emit(view, &script(Session::default(), seq, "wayland"));
            glib::Propagation::Proceed
        });
        let weak_view = view.downgrade();
        glib::timeout_add_local(Duration::from_millis(100), move || {
            let Some(view) = weak_view.upgrade() else {
                return glib::ControlFlow::Break;
            };
            let mut changed = false;
            let focused = view
                .toplevel()
                .and_then(|w| w.downcast::<gtk::Window>().ok())
                .is_some_and(|w| w.is_active() && w.is_visible());
            if !focused {
                let (old, seq) = state.get();
                if old != Session::default() {
                    state.set((Session::default(), seq + 1));
                    changed = true;
                }
            }
            let (phase, seq) = state.get();
            if changed || phase.active || phase.momentum {
                emit(&view, &script(phase, seq, "wayland"));
            }
            glib::ControlFlow::Continue
        });
        crate::log_line("scroll: Wayland phase observer ready");
    });
    if let Err(e) = result {
        crate::log_line(&format!("scroll: GTK phase observer unavailable: {e}"));
        let _ = window.eval(&support("unavailable", "wayland"));
    }
}

pub fn initial_support() -> String {
    #[cfg(target_os = "macos")]
    {
        support("unverified", "macos")
    }
    #[cfg(target_os = "linux")]
    {
        use gtk::prelude::*;
        if gtk::gdk::Display::default().is_some_and(|d| d.type_().name().contains("Wayland")) {
            support("unverified", "wayland")
        } else {
            support("unavailable", "x11")
        }
    }
}
