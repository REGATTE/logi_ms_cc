//! Private, short-lived context from the worker-owned browser accessibility helper.
//! These reports are never accepted through the public browser-context IPC endpoint.
use crate::{config::valid_domain, control::Foreground, website::WebsiteSnapshot};
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

pub const CONTEXT_TTL: Duration = Duration::from_millis(350);

pub fn is_chatgpt(foreground: &Foreground) -> bool {
    foreground.window != 0 && foreground.name.eq_ignore_ascii_case("chatgpt")
}

pub fn browser_kind(foreground: &Foreground) -> Option<&'static str> {
    if is_chatgpt(foreground) {
        Some("chatgpt")
    } else if crate::website::is_firefox(foreground) {
        Some("firefox")
    } else if crate::browser_catalog::is_browser(foreground) {
        Some("browser")
    } else {
        None
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct Target {
    pub browser: String,
    pub window: u32,
    pub pid: u32,
    pub domains: Vec<String>,
    pub target_revision: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Report {
    pub window: u32,
    pub pid: u32,
    pub target_revision: u64,
    pub revision: u64,
    pub domain: Option<String>,
    pub ready: bool,
    pub error: Option<String>,
}

#[derive(Clone, Debug)]
pub struct Observation {
    pub generation: u64,
    pub received: Instant,
    pub result: Result<Report, String>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct Status {
    pub browser: Option<String>,
    pub available: bool,
    pub connected: bool,
    pub errors: Vec<String>,
}

#[derive(Default)]
pub struct Context {
    target: Target,
    generation: u64,
    revision: Option<u64>,
    last_domain: Option<String>,
    snapshot: Option<WebsiteSnapshot>,
    expires: Option<Instant>,
    available: bool,
    error: Option<String>,
}

impl Context {
    fn matches_foreground(&self, foreground: &Foreground) -> bool {
        browser_kind(foreground) == Some(self.target.browser.as_str())
            && foreground.window == self.target.window
    }

    pub fn target(&mut self, target: Target) {
        if self.target != target {
            self.target = target;
            self.snapshot = None;
            self.revision = None;
            self.last_domain = None;
        }
    }

    pub fn update(
        &mut self,
        observation: Observation,
        foreground: &Foreground,
        pid: u32,
        now: Instant,
    ) -> Result<(), String> {
        if observation.generation < self.generation {
            return Ok(());
        }
        if observation.generation != self.generation {
            self.generation = observation.generation;
            self.snapshot = None;
            self.revision = None;
            self.last_domain = None;
        }
        let report = match observation.result {
            Ok(report) => report,
            Err(error) => {
                self.snapshot = None;
                self.expires = None;
                self.available = false;
                self.error = Some(error);
                return Ok(());
            }
        };
        // Import/startup failures can be reported before the helper reads its first
        // target. They carry diagnostics only and can never create website context.
        if !report.ready
            && report.domain.is_none()
            && report.window == 0
            && report.pid == 0
            && report
                .error
                .as_ref()
                .is_some_and(|error| error.len() <= 1024)
        {
            self.available = false;
            self.snapshot = None;
            self.expires = None;
            self.error = report.error;
            return Ok(());
        }
        if report.window != self.target.window
            || report.pid != self.target.pid
            || report.target_revision != self.target.target_revision
        {
            return Ok(());
        }
        let invalid = |text: &str| Err(text.to_string());
        if report
            .error
            .as_ref()
            .is_some_and(|error| error.len() > 1024)
            || report.domain.as_ref().is_some_and(|domain| {
                !valid_domain(domain)
                    || !self.target.domains.contains(domain)
                    || !report.ready
                    || report.error.is_some()
                    || report.window == 0
                    || report.pid == 0
            })
        {
            self.snapshot = None;
            return invalid("Invalid browser accessibility context report");
        }
        if self.revision.is_some_and(|revision| {
            report.revision < revision
                || report.revision == revision && report.domain != self.last_domain
        }) {
            self.snapshot = None;
            return invalid("Stale browser accessibility context revision");
        }
        self.available = report.ready;
        self.error = report.error;
        self.expires = Some(observation.received + CONTEXT_TTL);
        self.revision = Some(report.revision);
        self.last_domain = report.domain.clone();
        self.snapshot = None;
        if now.saturating_duration_since(observation.received) >= CONTEXT_TTL
            || !report.ready
            || self.error.is_some()
            || !self.matches_foreground(foreground)
            || pid == 0
            || pid != self.target.pid
        {
            return Ok(());
        }
        if let Some(domain) = report.domain {
            let source = match self.target.browser.as_str() {
                "chatgpt" => "chatgpt",
                "firefox" => "firefox_accessibility",
                "browser" => "browser_accessibility",
                _ => return Ok(()),
            };
            self.snapshot = Some(WebsiteSnapshot::embedded(
                source,
                domain,
                report.window,
                report.pid,
                self.generation,
                report.revision,
                report.target_revision,
            ));
        }
        Ok(())
    }

    pub fn focus_changed(&mut self, foreground: &Foreground) {
        if !self.matches_foreground(foreground) {
            self.snapshot = None;
        }
    }

    pub fn snapshot(&self, foreground: &Foreground, now: Instant) -> Option<WebsiteSnapshot> {
        self.snapshot
            .as_ref()
            .filter(|snapshot| {
                self.matches_foreground(foreground)
                    && foreground.window == snapshot.window
                    && self.expires.is_some_and(|expiry| now < expiry)
            })
            .cloned()
    }

    pub fn deadline(&self) -> Option<Instant> {
        self.expires
    }

    pub fn tick(&mut self, now: Instant) {
        if self.expires.is_some_and(|expiry| now >= expiry) {
            self.snapshot = None;
            self.expires = None;
            self.error = Some("Automatic website detection is not responding.".into());
        }
    }

    pub fn status(&self, now: Instant) -> Status {
        Status {
            browser: (self.target.window != 0 && !self.target.browser.is_empty())
                .then(|| self.target.browser.clone()),
            available: self.available,
            connected: self.available && self.expires.is_some_and(|expiry| now < expiry),
            errors: self.error.iter().cloned().collect(),
        }
    }
}
