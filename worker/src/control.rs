use crate::config::{Action, Config, DeviceBinding, Page};
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

const LAUNCH_FOCUS_TIMEOUT: Duration = Duration::from_secs(12);

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Foreground {
    pub window: u32,
    pub name: String,
    pub identities: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Status {},
    Reload {},
    Pause { value: bool },
    Page { index: usize },
    Profile { id: Option<String> },
    RingToggle {},
    RingContext {},
    RingExecute { token: String, slot: usize },
    RingCancel { token: String },
    RingConfigure { token: String },
    Shutdown {},
}

#[derive(Clone, Debug, Serialize)]
pub struct Status {
    pub ok: bool,
    pub version: String,
    pub capabilities: Vec<String>,
    pub pid: u32,
    pub connected: bool,
    pub keypad_access: bool,
    pub input_access: bool,
    pub session: String,
    pub focus_supported: bool,
    pub active_app: String,
    pub page: usize,
    pub paused: bool,
    pub tray_available: bool,
    pub last_error: Option<String>,
    pub manual_profile: Option<String>,
    pub ring_open: bool,
    pub ring_supported: bool,
    pub ring_error: Option<String>,
    pub device_buttons: serde_json::Value,
    pub dialpad_buttons: serde_json::Value,
}

pub struct State {
    pub config: Config,
    pub status: Status,
    foreground: Foreground,
    launch_context: Option<LaunchContext>,
}

struct LaunchContext {
    desktop_id: String,
    wm_classes: Vec<String>,
    profile_id: String,
    target_window: Option<u32>,
    waiting_until: Option<Instant>,
}

impl LaunchContext {
    fn matches(&self, foreground: &Foreground) -> bool {
        if self
            .target_window
            .is_some_and(|window| window != 0 && window == foreground.window)
        {
            return true;
        }
        foreground
            .identities
            .iter()
            .chain(std::iter::once(&foreground.name))
            .any(|name| {
                !name.is_empty()
                    && (self
                        .wm_classes
                        .iter()
                        .any(|class| class.eq_ignore_ascii_case(name))
                        || self.desktop_id.eq_ignore_ascii_case(name)
                        || self
                            .desktop_id
                            .strip_suffix(".desktop")
                            .unwrap_or(&self.desktop_id)
                            .eq_ignore_ascii_case(name))
            })
    }
}
impl State {
    pub fn new(config: Config, session: String) -> Self {
        Self {
            config,
            foreground: Foreground::default(),
            launch_context: None,
            status: Status {
                ok: true,
                version: env!("CARGO_PKG_VERSION").into(),
                capabilities: vec![
                    "launcher_activation_v1".into(),
                    "actions_ring_v1".into(),
                    "actions_ring_v2".into(),
                    "device_actions_v1".into(),
                ],
                pid: std::process::id(),
                connected: false,
                keypad_access: false,
                input_access: false,
                ring_supported: session == "x11",
                ring_error: None,
                session,
                focus_supported: false,
                active_app: String::new(),
                page: 0,
                paused: false,
                tray_available: false,
                last_error: None,
                manual_profile: None,
                ring_open: false,
                dialpad_buttons: serde_json::json!({"available":false,"backend":"hidpp","active":[],"errors":[]}),
                device_buttons: serde_json::json!({"available":false,"backend":"x11","active":[],"errors":[]}),
            },
        }
    }
    pub fn page(&self) -> &Page {
        if self.status.page == 1 {
            if let Some(context) = self
                .launch_context
                .as_ref()
                .filter(|c| c.waiting_until.is_none())
            {
                if context.profile_id == "default" {
                    return &self.config.pages[1];
                }
                if let Some(profile) = self
                    .config
                    .profiles
                    .iter()
                    .find(|p| p.id == context.profile_id)
                {
                    return &profile.page;
                }
            }
            if self.status.manual_profile.as_deref() == Some("default") {
                return &self.config.pages[1];
            }
            if let Some(profile) = self
                .status
                .manual_profile
                .as_deref()
                .and_then(|id| self.config.profiles.iter().find(|p| p.id == id))
            {
                return &profile.page;
            }
        }
        if self.status.page == 1 && self.config.auto_switch {
            if let Some(p) = self.config.profiles.iter().find(|p| {
                p.wm_classes
                    .iter()
                    .any(|c| c.eq_ignore_ascii_case(&self.status.active_app))
            }) {
                return &p.page;
            }
        }
        &self.config.pages[self.status.page]
    }
    pub fn select_page(&mut self, page: usize) -> Result<(), String> {
        if page >= self.config.pages.len() {
            return Err("page index must be 0 or 1".into());
        }
        self.launch_context = None;
        self.status.page = page;
        Ok(())
    }

    pub fn select_profile(&mut self, id: Option<String>) -> Result<(), String> {
        if let Some(id) = &id {
            if id != "default" && !self.config.profiles.iter().any(|p| &p.id == id) {
                return Err(format!("unknown profile: {id}"));
            }
        }
        self.launch_context = None;
        self.status.manual_profile = id;
        Ok(())
    }

    /// Arm a successful launcher action; only confirmed focus selects shortcuts.
    pub fn launch_succeeded(
        &mut self,
        desktop_id: &str,
        wm_classes: &[String],
        profile_id: Option<&str>,
        target_window: Option<u32>,
        confirmed_active: bool,
        now: Instant,
    ) -> bool {
        let cancelled = self.cancel_launch();
        if !self.config.auto_switch || self.status.session != "x11" || !self.status.focus_supported
        {
            return cancelled;
        }
        let Some(profile_id) = profile_id.filter(|id| self.profile_exists(id)) else {
            return cancelled;
        };
        let mut context = LaunchContext {
            desktop_id: desktop_id.into(),
            wm_classes: wm_classes.to_vec(),
            profile_id: profile_id.into(),
            target_window,
            waiting_until: Some(now + LAUNCH_FOCUS_TIMEOUT),
        };
        self.status.manual_profile = None;
        if confirmed_active {
            context.waiting_until = None;
            self.status.page = 1;
        } else {
            self.status.page = 0;
        }
        self.launch_context = Some(context);
        true
    }

    /// Called by the event-driven foreground watcher, including class changes.
    pub fn focus_changed(&mut self, foreground: Foreground, now: Instant) -> bool {
        let changed = self.foreground != foreground;
        let previous_page = self.status.page;
        self.status.active_app = foreground.name.clone();
        self.foreground = foreground;
        let expired = self.tick(now);
        if let Some(context) = &mut self.launch_context {
            if context.matches(&self.foreground) {
                context.waiting_until = None;
                self.status.page = 1;
            } else if context.waiting_until.is_none() {
                self.cancel_launch();
            }
        }
        changed || expired || previous_page != self.status.page
    }

    /// Tracking failure must never leave shortcuts for an unconfirmed app.
    pub fn set_focus_supported(&mut self, supported: bool) -> bool {
        self.status.focus_supported = supported;
        !supported && self.cancel_launch()
    }

    /// The worker can fold this deadline into its existing blocking poll.
    pub fn deadline(&self) -> Option<Instant> {
        self.launch_context
            .as_ref()
            .and_then(|context| context.waiting_until)
    }

    pub fn tick(&mut self, now: Instant) -> bool {
        if self.deadline().is_some_and(|deadline| now >= deadline) {
            return self.cancel_launch();
        }
        false
    }

    fn profile_exists(&self, id: &str) -> bool {
        id == "default" || self.config.profiles.iter().any(|profile| profile.id == id)
    }

    fn cancel_launch(&mut self) -> bool {
        if self.launch_context.take().is_some() {
            self.status.page = 0;
            return true;
        }
        false
    }

    pub fn reload(&mut self, config: Config) {
        self.config = config;
        if self.launch_context.as_ref().is_some_and(|context| {
            !self.config.auto_switch
                || !self.profile_exists(&context.profile_id)
                || !self.config.actions().any(|action| {
                    matches!(action, Action::Launch { desktop_id, profile_id, .. }
                        if desktop_id == &context.desktop_id
                            && profile_id.as_deref() == Some(context.profile_id.as_str()))
                })
        }) {
            self.cancel_launch();
        }
        if self
            .status
            .manual_profile
            .as_ref()
            .is_some_and(|id| id != "default" && !self.config.profiles.iter().any(|p| &p.id == id))
        {
            self.status.manual_profile = None;
        }
    }

    pub fn device_action(&self, binding: &DeviceBinding) -> Option<Action> {
        if self.status.paused {
            None
        } else {
            self.config.device_action(binding)
        }
    }
}
