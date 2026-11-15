use crate::config::{Config, Page};
use serde::{Deserialize, Serialize};

#[derive(Debug, Deserialize)]
#[serde(tag = "command", rename_all = "snake_case", deny_unknown_fields)]
pub enum Request {
    Status {},
    Reload {},
    Pause { value: bool },
    Page { index: usize },
    Profile { id: Option<String> },
    Shutdown {},
}

#[derive(Clone, Debug, Serialize)]
pub struct Status {
    pub ok: bool,
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
}

pub struct State {
    pub config: Config,
    pub status: Status,
}
impl State {
    pub fn new(config: Config, session: String) -> Self {
        Self {
            config,
            status: Status {
                ok: true,
                pid: std::process::id(),
                connected: false,
                keypad_access: false,
                input_access: false,
                session,
                focus_supported: false,
                active_app: String::new(),
                page: 0,
                paused: false,
                tray_available: false,
                last_error: None,
                manual_profile: None,
            },
        }
    }
    pub fn page(&self) -> &Page {
        if self.status.page == 1 {
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
        self.status.page = page;
        Ok(())
    }

    pub fn select_profile(&mut self, id: Option<String>) -> Result<(), String> {
        if let Some(id) = &id {
            if id != "default" && !self.config.profiles.iter().any(|p| &p.id == id) {
                return Err(format!("unknown profile: {id}"));
            }
        }
        self.status.manual_profile = id;
        Ok(())
    }

    pub fn reload(&mut self, config: Config) {
        self.config = config;
        if self
            .status
            .manual_profile
            .as_ref()
            .is_some_and(|id| id != "default" && !self.config.profiles.iter().any(|p| &p.id == id))
        {
            self.status.manual_profile = None;
        }
    }
}
