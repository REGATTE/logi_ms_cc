//! Activate existing windows before launching; coalesce repeated startup presses.

use crate::activation::{self, Activation};
use std::{
    collections::HashMap,
    env,
    time::{Duration, Instant},
};

const STARTUP_GRACE: Duration = Duration::from_secs(12);

pub enum Outcome {
    Existing(Activation),
    Started,
    Pending,
}

#[derive(Default)]
pub struct Launcher {
    starting: HashMap<String, Instant>,
}

impl Launcher {
    pub fn activate(
        &mut self,
        desktop_id: &str,
        classes: &[String],
        x11: bool,
        now: Instant,
    ) -> Result<Outcome, String> {
        self.activate_with(
            desktop_id,
            now,
            || {
                if x11 {
                    activation::activate_existing(desktop_id, classes)
                } else {
                    Ok(None)
                }
            },
            || spawn(desktop_id),
        )
    }

    fn activate_with(
        &mut self,
        desktop_id: &str,
        now: Instant,
        existing: impl FnOnce() -> Result<Option<Activation>, String>,
        spawn: impl FnOnce() -> Result<(), String>,
    ) -> Result<Outcome, String> {
        self.starting
            .retain(|_, since| now.saturating_duration_since(*since) < STARTUP_GRACE);
        if let Some(window) = existing()? {
            self.starting.remove(desktop_id);
            return Ok(Outcome::Existing(window));
        }
        if self.starting.contains_key(desktop_id) {
            return Ok(Outcome::Pending);
        }
        spawn()?;
        self.starting.insert(desktop_id.into(), now);
        Ok(Outcome::Started)
    }
}

fn spawn(desktop_id: &str) -> Result<(), String> {
    let mut command = std::process::Command::new("gtk-launch");
    command.arg(desktop_id);
    let home = env::var("HOME").unwrap_or_default();
    let defaults =
        env::var("XDG_DATA_DIRS").unwrap_or_else(|_| "/usr/local/share:/usr/share".into());
    command.env("XDG_DATA_DIRS", format!("{defaults}:/var/lib/snapd/desktop:/var/lib/flatpak/exports/share:{home}/.local/share/flatpak/exports/share"));
    let mut child = command
        .spawn()
        .map_err(|error| format!("Cannot launch {desktop_id}: {error}"))?;
    std::thread::spawn(move || {
        if let Ok(status) = child.wait() {
            if !status.success() {
                eprintln!("gtk-launch exited with {status}");
            }
        }
    });
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_existing_window_or_activation_failure_never_spawns_another() {
        let mut launcher = Launcher::default();
        let never_spawn = || -> Result<(), String> { panic!("must not open a duplicate") };
        let result = launcher
            .activate_with(
                "app.desktop",
                Instant::now(),
                || {
                    Ok(Some(Activation {
                        window: 42,
                        wm_class: "App".into(),
                        already_active: false,
                    }))
                },
                never_spawn,
            )
            .unwrap();
        assert!(matches!(
            result,
            Outcome::Existing(Activation { window: 42, .. })
        ));
        assert!(launcher
            .activate_with(
                "app.desktop",
                Instant::now(),
                || Err("Cannot contact desktop".into()),
                never_spawn
            )
            .is_err());
    }

    #[test]
    fn repeated_startup_presses_wait_for_a_window_then_focus_it() {
        let mut launcher = Launcher::default();
        let now = Instant::now();
        assert!(matches!(
            launcher
                .activate_with("app.desktop", now, || Ok(None), || Ok(()))
                .unwrap(),
            Outcome::Started
        ));
        assert!(matches!(
            launcher
                .activate_with(
                    "app.desktop",
                    now + Duration::from_secs(1),
                    || Ok(None),
                    || panic!("startup duplicate")
                )
                .unwrap(),
            Outcome::Pending
        ));
        assert!(matches!(
            launcher
                .activate_with(
                    "app.desktop",
                    now + Duration::from_secs(2),
                    || Ok(Some(Activation {
                        window: 7,
                        wm_class: "App".into(),
                        already_active: true
                    })),
                    || panic!("existing duplicate")
                )
                .unwrap(),
            Outcome::Existing(_)
        ));
        assert!(launcher.starting.is_empty());
    }

    #[test]
    fn startup_guard_expires_and_failed_spawn_can_retry() {
        let mut launcher = Launcher::default();
        let now = Instant::now();
        assert!(launcher
            .activate_with(
                "app.desktop",
                now,
                || Ok(None),
                || Err("not installed".into())
            )
            .is_err());
        assert!(matches!(
            launcher
                .activate_with("app.desktop", now, || Ok(None), || Ok(()))
                .unwrap(),
            Outcome::Started
        ));
        assert!(matches!(
            launcher
                .activate_with("app.desktop", now + STARTUP_GRACE, || Ok(None), || Ok(()))
                .unwrap(),
            Outcome::Started
        ));
    }
}
