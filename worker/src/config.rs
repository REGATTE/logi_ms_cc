use serde::{Deserialize, Serialize};
use std::{collections::HashSet, fs, path::Path};

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub version: u32,
    pub auto_switch: bool,
    pub pages: Vec<Page>,
    pub profiles: Vec<Profile>,
    #[serde(default)]
    pub action_ring: ActionRing,
    #[serde(default)]
    pub device_actions: Vec<DeviceAction>,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
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
    OpenRing {},
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
        for action in self.actions() {
            if let Action::Launch {
                profile_id: Some(id),
                ..
            } = action
            {
                if id != "default" && !ids.contains(id) {
                    return Err(format!("unknown launcher profile: {id}"));
                }
            }
        }
        self.action_ring.validate()?;
        if self.action_ring.bindings.len() + self.device_actions.len() > 256 {
            return Err("at most 256 device button assignments are supported".into());
        }
        let mut bindings: HashSet<_> = self.action_ring.bindings.iter().cloned().collect();
        for mapping in &self.device_actions {
            let binding = mapping.binding();
            if !binding.supported() || !bindings.insert(binding) {
                return Err("unsupported or duplicate Logitech device button assignment".into());
            }
            if matches!(mapping.action, Action::None {}) {
                return Err(
                    "remove an assignment instead of assigning an empty device action".into(),
                );
            }
            mapping.action.validate()?;
        }
        Ok(())
    }

    /// The current set of controls intercepted by either input backend.
    pub fn configured_bindings(&self) -> Vec<DeviceBinding> {
        self.action_ring
            .bindings
            .iter()
            .cloned()
            .chain(self.device_actions.iter().map(DeviceAction::binding))
            .collect()
    }

    /// Resolve identity at dispatch time, so queued events cannot use removed mappings.
    pub fn device_action(&self, binding: &DeviceBinding) -> Option<Action> {
        self.device_actions
            .iter()
            .find(|mapping| mapping.binding() == *binding)
            .map(|mapping| mapping.action.clone())
            .or_else(|| {
                self.action_ring
                    .bindings
                    .contains(binding)
                    .then_some(Action::OpenRing {})
            })
    }

    pub fn actions(&self) -> impl Iterator<Item = &Action> {
        self.pages
            .iter()
            .chain(self.profiles.iter().map(|profile| &profile.page))
            .flat_map(|page| page.keys.iter().flatten().map(|key| &key.action))
            .chain(self.device_actions.iter().map(|mapping| &mapping.action))
            .chain(
                self.action_ring
                    .layouts
                    .iter()
                    .flat_map(|layout| layout.slots.iter().flatten().map(|slot| &slot.action)),
            )
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
            key.action.validate()?;
        }
        Ok(())
    }
}

impl Action {
    pub fn validate(&self) -> Result<(), String> {
        match self {
            Action::Launch {
                desktop_id,
                wm_classes,
                profile_id,
            } => {
                if !simple_filename(desktop_id) || !desktop_id.ends_with(".desktop") {
                    return Err("invalid desktop entry ID".into());
                }
                validate_classes(wm_classes)?;
                if profile_id.as_ref().is_some_and(|id| {
                    id.is_empty()
                        || id.len() > 64
                        || !id.bytes().all(|c| {
                            c.is_ascii_lowercase() || c.is_ascii_digit() || c == b'_' || c == b'-'
                        })
                }) {
                    return Err("invalid launcher profile ID".into());
                }
            }
            Action::Shortcut { keys } => {
                crate::input::parse_chord(keys)?;
            }
            _ => {}
        }
        Ok(())
    }
}

fn validate_classes(classes: &[String]) -> Result<(), String> {
    if classes.len() > 64
        || classes
            .iter()
            .any(|s| s.is_empty() || s.len() > 255 || s.chars().any(char::is_control))
    {
        return Err("invalid window class".into());
    }
    Ok(())
}

#[derive(Clone, Debug, Default, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ActionRing {
    #[serde(default)]
    pub layouts: Vec<RingLayout>,
    #[serde(default)]
    pub bindings: Vec<DeviceBinding>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RingLayout {
    pub id: String,
    pub wm_classes: Vec<String>,
    pub slots: Vec<Option<RingSlot>>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RingSlot {
    pub label: String,
    pub icon: String,
    pub action: Action,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq, Hash)]
#[serde(deny_unknown_fields)]
pub struct DeviceBinding {
    pub vendor_id: u16,
    pub product_id: u16,
    pub control_id: String,
}

impl DeviceBinding {
    /// Only identities already understood by the XInput or HID++ backend.
    pub fn supported(&self) -> bool {
        if self.vendor_id != 0x046d || self.product_id == 0 {
            return false;
        }
        match self.product_id {
            0xc07e => matches!(self.control_id.as_str(), "button:4" | "button:5"),
            0xbc00 => matches!(
                self.control_id.as_str(),
                "button:1" | "button:2" | "button:3" | "button:4"
            ),
            _ => matches!(
                self.control_id.as_str(),
                "key:272" | "key:273" | "key:274" | "key:275" | "key:276" | "key:277" | "key:278"
            ),
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DeviceAction {
    pub vendor_id: u16,
    pub product_id: u16,
    pub control_id: String,
    pub action: Action,
}

impl DeviceAction {
    pub fn binding(&self) -> DeviceBinding {
        DeviceBinding {
            vendor_id: self.vendor_id,
            product_id: self.product_id,
            control_id: self.control_id.clone(),
        }
    }
}

impl ActionRing {
    fn validate(&self) -> Result<(), String> {
        if self.layouts.len() > 257 || self.bindings.len() > 256 {
            return Err("too many ring layouts or device bindings".into());
        }
        let mut ids = HashSet::new();
        for layout in &self.layouts {
            if (layout.id != "default"
                && (!simple_filename(&layout.id) || !layout.id.ends_with(".desktop")))
                || !ids.insert(&layout.id)
            {
                return Err("ring layouts need unique desktop IDs".into());
            }
            if layout.slots.len() > 7 || layout.slots.iter().any(Option::is_none) {
                return Err("Action Ring supports up to seven configured actions without empty slots. Open settings to update this saved runtime layout.".into());
            }
            validate_classes(&layout.wm_classes)?;
            for slot in layout.slots.iter().flatten() {
                if slot.label.is_empty()
                    || slot.label.len() > 256
                    || slot.label.chars().any(char::is_control)
                    || slot.icon.len() > 4096
                    || slot.icon.chars().any(char::is_control)
                {
                    return Err("invalid ring label or icon".into());
                }
                if matches!(slot.action, Action::OpenRing {}) {
                    return Err("a ring cannot open another ring".into());
                }
                slot.action.validate()?;
            }
        }
        if !self.layouts.is_empty() && !ids.contains(&"default".to_string()) {
            return Err("ring layouts need a default layout".into());
        }
        let mut bindings = HashSet::new();
        for binding in &self.bindings {
            if binding.vendor_id != 0x046d
                || binding.product_id == 0
                || binding.control_id.len() > 64
                || binding.control_id.is_empty()
                || !binding
                    .control_id
                    .bytes()
                    .all(|c| c.is_ascii_alphanumeric() || b":_-".contains(&c))
                || !bindings.insert(binding)
            {
                return Err("invalid or duplicate Logitech ring binding".into());
            }
        }
        Ok(())
    }
}
