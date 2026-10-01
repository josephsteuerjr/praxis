//! Ordered resume: verify the launch infrastructure before releasing owner stop.
pub fn run(
    prepare: impl FnOnce() -> Result<(), String>,
    clear_pause: impl FnOnce() -> Result<(), String>,
    clear_stop: impl FnOnce() -> Result<(), String>,
    start: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    prepare()?;
    clear_pause()?;
    clear_stop()?;
    start().map_err(|e| format!("Запрет работы снят, но запуск не подтверждён: {e}"))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;

    #[test]
    fn missing_infrastructure_preserves_stop_and_pause() {
        assert!(run(|| Err("missing task".into()),
            || panic!("must preserve pause"), || panic!("must preserve stop"),
            || panic!("must not start")).unwrap_err().contains("missing task"));
    }

    #[test]
    fn pause_failure_preserves_owner_stop() {
        assert!(run(|| Ok(()), || Err("pause denied".into()),
            || panic!("must preserve stop"), || panic!("must not start")).is_err());
    }

    #[test]
    fn stop_failure_never_requests_start() {
        assert!(run(|| Ok(()), || Ok(()), || Err("stop denied".into()),
            || panic!("must not start")).is_err());
    }

    #[test]
    fn launch_failure_is_not_success_after_stop_cleared() {
        let calls = RefCell::new(Vec::new());
        let step = |name| { calls.borrow_mut().push(name); Ok(()) };
        let error = run(|| step("prepare"), || step("pause"), || step("stop"),
            || { calls.borrow_mut().push("start"); Err("task refused".into()) }).unwrap_err();
        assert_eq!(*calls.borrow(), ["prepare", "pause", "stop", "start"]);
        assert!(error.contains("Запрет работы снят"));
        assert!(error.contains("task refused"));
    }

    #[test]
    fn success_requires_all_steps_in_order() {
        let calls = RefCell::new(Vec::new());
        let step = |name| { calls.borrow_mut().push(name); Ok(()) };
        run(|| step("prepare"), || step("pause"), || step("stop"), || step("start")).unwrap();
        assert_eq!(*calls.borrow(), ["prepare", "pause", "stop", "start"]);
    }
}
