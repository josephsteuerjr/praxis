//! Window operations for the headless host. Business logic stays in main.rs.
//! Events travel over the private child pipe, never a network listener.
use std::any::Any;
use std::io::Write;
use std::marker::PhantomData;
use std::ops::Deref;
use std::sync::{Arc, atomic::{AtomicBool, Ordering}};

pub fn event(name: &str, data: serde_json::Value) {
    send(&serde_json::json!({"event": name, "data": data}));
}
pub fn send(value: &serde_json::Value) {
    let mut out = std::io::stdout().lock();
    if let Ok(mut line) = serde_json::to_vec(value) {
        line.push(b'\n');
        let _ = out.write_all(&line);
        let _ = out.flush();
    }
}

#[derive(Clone)]
pub struct ShellHandle {
    state: Arc<dyn Any + Send + Sync>,
    window: Arc<WindowState>,
}
struct WindowState { alive: AtomicBool, visible: AtomicBool, focused: AtomicBool }
pub struct ShellState<'a, T: Send + Sync + 'static>(Arc<T>, PhantomData<&'a T>);
impl<T: Send + Sync + 'static> Deref for ShellState<'_, T> {
    type Target = T;
    fn deref(&self) -> &T { &self.0 }
}
impl ShellHandle {
    pub fn new<T: Any + Send + Sync>(state: T) -> Self {
        Self { state: Arc::new(state), window: Arc::new(WindowState {
            alive: AtomicBool::new(true), visible: AtomicBool::new(false), focused: AtomicBool::new(false),
        }) }
    }
    pub fn state<T: Any + Send + Sync>(&self) -> ShellState<'_, T> {
        ShellState(self.state.clone().downcast::<T>().expect("host state type"), PhantomData)
    }
    pub fn get_webview_window(&self, _: &str) -> Option<HostWindow> {
        self.window.alive.load(Ordering::Relaxed).then(|| HostWindow(self.window.clone()))
    }
    pub fn visibility(&self, visible: bool, focused: bool) {
        self.window.visible.store(visible, Ordering::Relaxed);
        self.window.focused.store(focused, Ordering::Relaxed);
    }
    pub fn replace(&self, script: &str) -> Result<(), String> {
        self.window.alive.store(true, Ordering::Relaxed);
        event("replace-window", serde_json::json!({"script": script}));
        Ok(())
    }
    pub fn exit(&self, code: i32) { event("exit", serde_json::json!({"code": code})); }
}
pub struct HostWindow(Arc<WindowState>);
impl HostWindow {
    pub fn is_visible(&self) -> Result<bool, String> { Ok(self.0.visible.load(Ordering::Relaxed)) }
    pub fn is_focused(&self) -> Result<bool, String> { Ok(self.0.focused.load(Ordering::Relaxed)) }
    pub fn show(&self) -> Result<(), String> { event("show-window", serde_json::Value::Null); Ok(()) }
    pub fn unminimize(&self) -> Result<(), String> { Ok(()) }
    pub fn set_focus(&self) -> Result<(), String> { Ok(()) }
    pub fn set_always_on_top(&self, _: bool) -> Result<(), String> { Ok(()) }
    pub fn destroy(&self) -> Result<(), String> { self.0.alive.store(false, Ordering::Relaxed); Ok(()) }
}

pub mod async_runtime {
    pub use tokio::task::spawn_blocking;
}
