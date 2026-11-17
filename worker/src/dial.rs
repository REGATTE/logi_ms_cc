//! Context carried by native dial input, independent of the HID transport.
use crate::{control::Foreground, website::WebsiteSnapshot};
use std::time::{Duration, Instant};

pub const MAX_ROTATION_AGE: Duration = Duration::from_millis(250);

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Capture {
    pub foreground: Foreground,
    pub website: Option<WebsiteSnapshot>,
    pub clockwise: String,
    pub counterclockwise: String,
}

impl Capture {
    pub fn keys(&self, delta: i8) -> Option<&str> {
        match delta.cmp(&0) {
            std::cmp::Ordering::Greater => Some(&self.clockwise),
            std::cmp::Ordering::Less => Some(&self.counterclockwise),
            std::cmp::Ordering::Equal => None,
        }
    }

    pub fn current(&self, next: Option<&Self>, captured: Instant, now: Instant) -> bool {
        next == Some(self) && now.saturating_duration_since(captured) <= MAX_ROTATION_AGE
    }
}
