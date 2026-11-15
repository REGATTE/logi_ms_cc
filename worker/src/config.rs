use serde::Deserialize;
use std::{collections::HashSet, fs, path::Path};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub version: u32,
    pub auto_switch: bool,
    pub pages: Vec<Page>,
    pub profiles: Vec<Profile>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Page {
    pub name: String,
    pub keys: Vec<Option<Key>>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Key {
    pub label: String,
    pub action: Action,
    pub image: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case", deny_unknown_fields)]
pub enum Action {
    Launch {
        desktop_id: String,
        #[serde(default)]
        wm_classes: Vec<String>,
        #[serde(default)]
        profile_id: Option<String>,
    },
    Shortcut {
        keys: String,
    },
    Screenshot {},
    None {},
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Profile {
    pub id: String,
    pub wm_classes: Vec<String>,
    pub page: Page,
}

pub fn simple_filename(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 255
        && !s.starts_with('.')
        && !s.starts_with('-')
        && !s.contains(['/', '\\', '\0'])
        && !s.chars().any(char::is_control)
}

impl Config {
    pub fn load(path: &Path) -> Result<Self, String> {
        let file = fs::File::open(path).map_err(|e| format!("{}: {e}", path.display()))?;
        if file.metadata().map_err(|e| e.to_string())?.len() > 1_048_576 {
            return Err("runtime config exceeds 1 MiB".into());
        }
        let config: Self = serde_json::from_reader(file).map_err(|e| e.to_string())?;
        config.validate()?;
        Ok(config)
    }
    pub fn validate(&self) -> Result<(), String> {
        if self.version != 1 {
            return Err("unsupported runtime schema version".into());
        }
        if self.pages.len() != 2 {
            return Err("exactly two pages are required".into());
        }
        if self.profiles.len() > 256 {
            return Err("too many profiles".into());
        }
        for page in &self.pages {
            page.validate()?;
        }
        let mut ids = HashSet::new();
        for p in &self.profiles {
            if p.id.is_empty()
                || p.id.len() > 64
                || !p
                    .id
                    .bytes()
                    .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-')
                || !p.id.as_bytes()[0].is_ascii_alphanumeric()
                || !ids.insert(&p.id)
            {
                return Err(format!("invalid or duplicate profile ID: {}", p.id));
            }
            if p.wm_classes.len() > 64
                || p.wm_classes
                    .iter()
                    .any(|s| s.is_empty() || s.len() > 255 || s.chars().any(char::is_control))
            {
                return Err("invalid window class".into());
            }
            p.page.validate()?;
        }
        for page in self
            .pages
            .iter()
            .chain(self.profiles.iter().map(|profile| &profile.page))
        {
            for key in page.keys.iter().flatten() {
                if let Action::Launch {
                    profile_id: Some(id),
                    ..
                } = &key.action
                {
                    if id != "default" && !ids.contains(id) {
                        return Err(format!("unknown launcher profile: {id}"));
                    }
                }
            }
        }
        Ok(())
    }
}

impl Page {
    fn validate(&self) -> Result<(), String> {
        if self.keys.len() != 9 || self.name.is_empty() || self.name.len() > 128 {
            return Err("page needs a name and exactly nine keys".into());
        }
        for key in self.keys.iter().flatten() {
            if key.label.len() > 256
                || !simple_filename(&key.image)
                || !key.image.to_ascii_lowercase().ends_with(".jpg")
            {
                return Err("invalid key label or JPEG filename".into());
            }
            match &key.action {
                Action::Launch {
                    desktop_id,
                    wm_classes,
                    ..
                } => {
                    if !simple_filename(desktop_id) || !desktop_id.ends_with(".desktop") {
                        return Err("invalid desktop entry ID".into());
                    }
                    if wm_classes.len() > 64
                        || wm_classes.iter().any(|value| {
                            value.is_empty()
                                || value.len() > 255
                                || value.chars().any(char::is_control)
                        })
                    {
                        return Err("invalid launcher window class".into());
                    }
                }
                Action::Shortcut { keys } => {
                    crate::input::parse_chord(keys)?;
                }
                _ => {}
            }
        }
        Ok(())
    }
}
