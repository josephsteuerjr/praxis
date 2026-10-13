/// Initial logical bounds inside the monitor's physical work area. Saved window
/// state still takes precedence through the existing window-state plugin.
pub(crate) struct InitialWindow {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
    pub min_width: f64,
    pub min_height: f64,
}

pub(crate) fn fit_initial_window(
    x: f64, y: f64, width: f64, height: f64, scale: f64,
) -> Option<InitialWindow> {
    if ![x, y, width, height, scale].iter().all(|v| v.is_finite())
        || width <= 0.0 || height <= 0.0 || scale <= 0.0
    {
        return None;
    }
    let available_width = width / scale;
    let available_height = height / scale;
    if ![available_width, available_height, x / scale, y / scale]
        .iter().all(|v| v.is_finite())
    {
        return None;
    }
    let w = 1360.0_f64.min(available_width);
    let h = 860.0_f64.min(available_height);
    Some(InitialWindow {
        x: (x + (width - w * scale) / 2.0) / scale,
        y: (y + (height - h * scale) / 2.0) / scale,
        width: w,
        height: h,
        min_width: 900.0_f64.min(available_width),
        min_height: 600.0_f64.min(available_height),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn small_screen_keeps_navigation_and_controls_inside_work_area() {
        let b = fit_initial_window(0.0, 25.0, 1024.0, 684.0, 1.0).unwrap();
        assert_eq!((b.x, b.y, b.width, b.height), (0.0, 25.0, 1024.0, 684.0));
        assert_eq!((b.min_width, b.min_height), (900.0, 600.0));
    }

    #[test]
    fn tiny_work_area_does_not_enforce_an_offscreen_minimum() {
        let b = fit_initial_window(20.0, 30.0, 768.0, 540.0, 1.0).unwrap();
        assert_eq!((b.x, b.y, b.width, b.height), (20.0, 30.0, 768.0, 540.0));
        assert_eq!((b.min_width, b.min_height), (768.0, 540.0));
    }

    #[test]
    fn high_dpi_and_negative_monitor_origin_preserve_physical_bounds() {
        let b = fit_initial_window(-3840.0, 80.0, 3840.0, 2000.0, 2.0).unwrap();
        assert_eq!((b.width, b.height), (1360.0, 860.0));
        assert_eq!((b.x * 2.0, b.y * 2.0), (-3280.0, 220.0));
        assert!(b.x * 2.0 >= -3840.0);
        assert!((b.x + b.width) * 2.0 <= 0.0);
        assert!(b.y * 2.0 >= 80.0);
        assert!((b.y + b.height) * 2.0 <= 2080.0);
    }

    #[test]
    fn invalid_monitor_metrics_leave_existing_fallback_available() {
        for scale in [0.0, -1.0, f64::NAN, f64::INFINITY, f64::MIN_POSITIVE] {
            assert!(fit_initial_window(0.0, 0.0, 1024.0, 768.0, scale).is_none());
        }
        assert!(fit_initial_window(0.0, 0.0, 0.0, 768.0, 1.0).is_none());
        assert!(fit_initial_window(f64::NAN, 0.0, 1024.0, 768.0, 1.0).is_none());
    }
}
