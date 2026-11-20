//! Ephemeral browser context, bounded to one Firefox connection and focused window.
use crate::{config::valid_domain, control::Foreground};
use serde::Deserialize;
use std::{
    collections::HashMap,
    time::{Duration, Instant},
};

pub const CONTEXT_TTL: Duration = Duration::from_secs(15);

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BrowserReport {
    pub browser: String,
    pub session: String,
    pub domain: Option<String>,
    pub focused: bool,
    pub window_id: Option<i32>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct WebsiteSnapshot {
    pub domain: String,
    pub window: u32,
    pub source: &'static str,
    session: String,
    browser_window: i32,
    revision: u64,
    generation: u64,
    process: u32,
    target_revision: u64,
}

impl WebsiteSnapshot {
    pub(crate) fn embedded(
        source: &'static str,
        domain: String,
        window: u32,
        process: u32,
        generation: u64,
        revision: u64,
        target_revision: u64,
    ) -> Self {
        Self {
            domain,
            window,
            source,
            session: String::new(),
            browser_window: 0,
            revision,
            generation,
            process,
            target_revision,
        }
    }

    pub fn matches_process(&self, pid: u32) -> bool {
        !self.uses_accessibility() || self.process == pid && pid != 0
    }

    pub fn uses_accessibility(&self) -> bool {
        matches!(
            self.source,
            "chatgpt" | "firefox_accessibility" | "browser_accessibility"
        )
    }
}

struct Context {
    snapshot: WebsiteSnapshot,
    expires: Instant,
}

#[derive(Default)]
pub struct BrowserContext {
    sessions: HashMap<String, Instant>,
    windows: HashMap<(String, i32), u32>,
    current: Option<Context>,
    revision: u64,
}

pub fn is_firefox(foreground: &Foreground) -> bool {
    foreground.window != 0
        && foreground
            .identities
            .iter()
            .chain(std::iter::once(&foreground.name))
            .any(|name| {
                [
                    "firefox",
                    "firefox_firefox",
                    "org.mozilla.firefox",
                    "firefox-esr",
                ]
                .iter()
                .any(|allowed| name.eq_ignore_ascii_case(allowed))
            })
}

impl BrowserContext {
    fn validate_source(browser: &str, session: &str) -> Result<(), String> {
        if browser != "firefox"
            || session.len() != 32
            || !session
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
        {
            return Err("Invalid Firefox context connection".into());
        }
        Ok(())
    }

    pub fn disconnect(&mut self, browser: &str, session: &str) -> Result<(), String> {
        Self::validate_source(browser, session)?;
        self.sessions.remove(session);
        self.windows.retain(|(source, _), _| source != session);
        if self
            .current
            .as_ref()
            .is_some_and(|context| context.snapshot.session == session)
        {
            self.current = None;
        }
        Ok(())
    }

    pub fn update(
        &mut self,
        report: &BrowserReport,
        foreground: &Foreground,
        now: Instant,
    ) -> Result<(), String> {
        let BrowserReport {
            browser,
            session,
            domain,
            focused,
            window_id,
        } = report;
        let (browser, session, domain, focused) = (
            browser.as_str(),
            session.as_str(),
            domain.as_deref(),
            *focused,
        );
        Self::validate_source(browser, session)?;
        if window_id.is_some_and(|id| id <= 0) || domain.is_some() && window_id.is_none() {
            return Err(
                "Browser context requires a positive Firefox window ID for its hostname".into(),
            );
        }
        if domain.is_some_and(|domain| !valid_domain(domain)) {
            return Err("Browser context requires a canonical hostname, not a URL".into());
        }
        self.tick(now);
        // A background or disconnected host cannot erase another connection's
        // active context, or manufacture a conflict merely by sending null.
        if !self.sessions.is_empty()
            && !self.sessions.contains_key(session)
            && (!focused || domain.is_none())
        {
            return Ok(());
        }
        if self.sessions.len() >= 8 && !self.sessions.contains_key(session) {
            self.current = None;
            return Err("Too many Firefox connections; use one Firefox profile".into());
        }
        self.sessions.insert(session.into(), now + CONTEXT_TTL);
        if self.sessions.len() > 1 {
            self.current = None;
            return Err(
                "Multiple Firefox connections: keep the extension enabled in one Firefox profile"
                    .into(),
            );
        }
        let Some(domain) = domain.filter(|_| is_firefox(foreground)) else {
            if self
                .current
                .as_ref()
                .is_some_and(|context| context.snapshot.session == session)
            {
                self.current = None;
            }
            return Ok(());
        };
        let browser_window = window_id.expect("validated domain window ID");
        let key = (session.to_string(), browser_window);
        if let Some(bound_window) = self.windows.get(&key) {
            if *bound_window != foreground.window {
                return Err("Stale Firefox window report; waiting for the active window".into());
            }
        } else {
            if !focused {
                return Ok(());
            }
            if self
                .windows
                .values()
                .any(|window| *window == foreground.window)
            {
                return Err(
                    "Firefox window identity changed; reconnect the browser extension".into(),
                );
            }
            if self.windows.len() >= 32 {
                return Err(
                    "Too many Firefox window identities; reconnect the browser extension".into(),
                );
            }
            self.windows.insert(key, foreground.window);
        }
        if let Some(context) = &mut self.current {
            if context.snapshot.session == session
                && context.snapshot.domain == domain
                && context.snapshot.browser_window == browser_window
                && context.snapshot.window == foreground.window
            {
                context.expires = now + CONTEXT_TTL;
                return Ok(());
            }
        }
        // An unfocused report can refresh an already-bound window (the ring),
        // but cannot attach a previous normal tab to a newly focused private window.
        if !focused {
            let same_window = self.current.as_ref().is_some_and(|context| {
                context.snapshot.session == session
                    && context.snapshot.window == foreground.window
                    && context.snapshot.browser_window == browser_window
            });
            if !same_window {
                return Ok(());
            }
        }
        self.revision = self.revision.wrapping_add(1);
        self.current = Some(Context {
            snapshot: WebsiteSnapshot {
                domain: domain.into(),
                window: foreground.window,
                source: "firefox",
                session: session.into(),
                browser_window,
                revision: self.revision,
                generation: 0,
                process: 0,
                target_revision: 0,
            },
            expires: now + CONTEXT_TTL,
        });
        Ok(())
    }

    pub fn focus_changed(&mut self, foreground: &Foreground) {
        if self.current.as_ref().is_some_and(|context| {
            !is_firefox(foreground) || context.snapshot.window != foreground.window
        }) {
            self.current = None;
        }
    }

    pub fn tick(&mut self, now: Instant) {
        self.sessions.retain(|_, expiry| now < *expiry);
        self.windows
            .retain(|(source, _), _| self.sessions.contains_key(source));
        if self
            .current
            .as_ref()
            .is_some_and(|context| now >= context.expires)
        {
            self.current = None;
        }
    }

    pub fn deadline(&self) -> Option<Instant> {
        self.sessions
            .values()
            .copied()
            .chain(self.current.as_ref().map(|context| context.expires))
            .min()
    }

    pub fn connected(&self, now: Instant) -> bool {
        self.sessions.values().any(|expiry| now < *expiry)
    }

    pub fn error(&self, now: Instant) -> Option<String> {
        (self
            .sessions
            .values()
            .filter(|expiry| now < **expiry)
            .count()
            > 1)
        .then(|| {
            "Multiple Firefox connections: keep the extension enabled in one Firefox profile".into()
        })
    }

    pub fn snapshot(&self, foreground: &Foreground, now: Instant) -> Option<WebsiteSnapshot> {
        self.current
            .as_ref()
            .filter(|context| {
                now < context.expires
                    && is_firefox(foreground)
                    && context.snapshot.window == foreground.window
            })
            .map(|context| context.snapshot.clone())
    }
}
