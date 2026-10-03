//! Platform gesture phases, not a guessed physical contact count.
//! A stationary session stays active until its explicit end/cancel. Bridge
//! liveness is separate; callers must clear the session on focus/device loss.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Session {
    pub available: bool,
    pub active: bool,
    pub momentum: bool,
}

impl Session {
    pub fn mac(phase: usize, momentum: usize, precise: bool) -> Self {
        // Public NSEventPhase bits: began/stationary/changed, ended/cancelled,
        // mayBegin. MayBegin alone isn't an ongoing two-finger scroll.
        if !precise || (phase | momentum) == 0 {
            return Self::default();
        }
        let terminal = 8 | 16;
        let moving = 1 | 2 | 4;
        if momentum & terminal == 0 && momentum & moving != 0 {
            Self {
                available: true,
                active: false,
                momentum: true,
            }
        } else {
            Self {
                available: true,
                active: phase & terminal == 0 && phase & moving != 0,
                momentum: false,
            }
        }
    }

    pub fn wayland(touchpad: bool, smooth: bool, stop: bool) -> Self {
        if !touchpad || !smooth {
            return Self::default();
        }
        Self {
            available: true,
            active: !stop,
            momentum: false,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mac_phases_distinguish_stationary_release_and_inertia() {
        for phase in [1, 2, 4] {
            assert!(Session::mac(phase, 0, true).active);
        }
        for phase in [8, 16, 32] {
            assert!(!Session::mac(phase, 0, true).active);
        }
        assert!(Session::mac(8, 1, true).momentum);
        assert!(!Session::mac(0, 8, true).momentum);
        assert!(Session::mac(1, 0, true).active); // immediate pickup
        assert_eq!(Session::mac(0, 0, true), Session::default());
        assert_eq!(Session::mac(1, 0, false), Session::default());
        assert!(!Session::mac(1 | 16, 0, true).active);
    }
    #[test]
    fn wayland_requires_both_source_and_smooth_scroll() {
        assert!(Session::wayland(true, true, false).active);
        assert!(!Session::wayland(true, true, true).active);
        assert_eq!(Session::wayland(false, true, false), Session::default());
        assert_eq!(Session::wayland(true, false, false), Session::default());
    }
}
