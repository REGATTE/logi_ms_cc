//! Exact browser identities from desktop registration; never execute launchers.
use crate::control::Foreground;
use std::{
    collections::{HashMap, HashSet},
    env, fs,
    io::Read,
    os::unix::fs::OpenOptionsExt,
    path::PathBuf,
    sync::{
        atomic::{AtomicU64, Ordering},
        OnceLock, RwLock,
    },
    time::Duration,
};

const MAX_ENTRIES: usize = 4096;
const MAX_DIRECTORIES: usize = 256;
const MAX_DEPTH: usize = 4;
const MAX_ENTRY_BYTES: u64 = 128 * 1024;
const REFRESH_INTERVAL: Duration = Duration::from_secs(30);

// Portable/unregistered browser builds still have stable window classes. Do
// not add generic toolkit or window classes such as Electron or Navigator.
const KNOWN_IDENTITIES: &[&str] = &[
    "google-chrome",
    "google-chrome-stable",
    "google-chrome-beta",
    "google-chrome-unstable",
    "com.google.chrome",
    "com.google.chrome.beta",
    "chromium",
    "chromium-browser",
    "chromium_chromium",
    "org.chromium.chromium",
    "io.github.ungoogled_software.ungoogled_chromium",
    "brave-browser",
    "brave-browser-beta",
    "brave-browser-nightly",
    "com.brave.browser",
    "brave_brave",
    "microsoft-edge",
    "microsoft-edge-beta",
    "microsoft-edge-dev",
    "com.microsoft.edge",
    "vivaldi-stable",
    "vivaldi-snapshot",
    "vivaldi",
    "com.vivaldi.vivaldi",
    "opera",
    "opera-beta",
    "opera-developer",
    "com.opera.opera",
    "librewolf",
    "io.gitlab.librewolf-community",
    "zen",
    "zen-browser",
    "zen-bin",
    "app.zen_browser.zen",
    "waterfox",
    "net.waterfox.waterfox",
    "floorp",
    "one.ablaze.floorp",
    "firefox-nightly",
    "firefox-developer-edition",
    "firefoxdeveloperedition",
    "org.mozilla.firefoxdeveloperedition",
    "epiphany",
    "org.gnome.epiphany",
    "gnome-web",
    "falkon",
    "org.kde.falkon",
    "midori",
    "org.midori_browser.midori",
    "qutebrowser",
    "org.qutebrowser.qutebrowser",
    "torbrowser",
    "org.torproject.torbrowser-launcher",
    "seamonkey",
    "palemoon",
    "basilisk",
];

#[derive(Default, PartialEq, Eq)]
struct Catalog {
    identities: HashSet<String>,
}

fn valid_identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 255
        && !value.chars().any(char::is_control)
        && !value.contains(['/', '\\'])
        && ![
            "electron",
            "navigator",
            "browser",
            "application",
            "xterm",
            "gnome-terminal",
        ]
        .iter()
        .any(|generic| value.eq_ignore_ascii_case(generic))
}

fn browser_entry(content: &str, desktop_id: &str) -> Vec<String> {
    let mut in_entry = false;
    let mut fields = HashMap::new();
    for line in content.lines() {
        let line = line.trim();
        if line.starts_with('#') || line.is_empty() {
            continue;
        }
        if line.starts_with('[') && line.ends_with(']') {
            in_entry = line == "[Desktop Entry]";
            continue;
        }
        if in_entry {
            if let Some((key, value)) = line.split_once('=') {
                fields.insert(key.trim(), value.trim());
            }
        }
    }
    if fields.get("Type") != Some(&"Application")
        || fields.get("Hidden") == Some(&"true")
        || fields.get("Terminal") == Some(&"true")
        || !fields
            .get("Categories")
            .is_some_and(|categories| categories.split(';').any(|v| v == "WebBrowser"))
    {
        return Vec::new();
    }
    // Name, GenericName, Exec and localized values never become identities.
    [Some(desktop_id), fields.get("StartupWMClass").copied()]
        .into_iter()
        .flatten()
        .filter(|value| valid_identity(value))
        .map(str::to_ascii_lowercase)
        .collect()
}

impl Catalog {
    fn from_directories(directories: &[PathBuf]) -> Self {
        let mut catalog = Self::default();
        let mut seen_ids = HashSet::new();
        let mut entries_left = MAX_ENTRIES;
        let mut directories_left = MAX_DIRECTORIES;
        for root in directories {
            let mut pending = vec![(root.clone(), 0)];
            while let Some((directory, depth)) = pending.pop() {
                if directories_left == 0 || entries_left == 0 {
                    break;
                }
                directories_left -= 1;
                let Ok(children) = fs::read_dir(directory) else {
                    continue;
                };
                let mut children: Vec<_> =
                    children.take(entries_left).filter_map(Result::ok).collect();
                entries_left = entries_left.saturating_sub(children.len());
                children.sort_by_key(|entry| entry.file_name());
                for entry in children {
                    let Ok(kind) = entry.file_type() else {
                        continue;
                    };
                    let path = entry.path();
                    if kind.is_dir() && depth < MAX_DEPTH {
                        pending.push((path, depth + 1));
                        continue;
                    }
                    if path.extension().and_then(|v| v.to_str()) != Some("desktop") {
                        continue;
                    }
                    let Ok(relative) = path.strip_prefix(root) else {
                        continue;
                    };
                    let Some(id) = relative.to_str() else {
                        continue;
                    };
                    let id = id.trim_end_matches(".desktop").replace('/', "-");
                    // Even Hidden=true or invalid higher-priority entries mask a
                    // system entry with the same desktop ID, as on the desktop.
                    if !seen_ids.insert(id.clone()) {
                        continue;
                    }
                    let Ok(metadata) = fs::metadata(&path) else {
                        continue;
                    };
                    if !metadata.is_file() || metadata.len() > MAX_ENTRY_BYTES {
                        continue;
                    }
                    let Ok(file) = fs::OpenOptions::new()
                        .read(true)
                        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
                        .open(path)
                    else {
                        continue;
                    };
                    let Ok(opened) = file.metadata() else {
                        continue;
                    };
                    if !opened.is_file() || opened.len() > MAX_ENTRY_BYTES {
                        continue;
                    }
                    let mut content = String::new();
                    if file
                        .take(MAX_ENTRY_BYTES + 1)
                        .read_to_string(&mut content)
                        .is_err()
                        || content.len() as u64 > MAX_ENTRY_BYTES
                    {
                        continue;
                    }
                    catalog.identities.extend(browser_entry(&content, &id));
                }
            }
        }
        catalog
    }

    fn contains(&self, foreground: &Foreground) -> bool {
        foreground.window != 0
            && foreground
                .identities
                .iter()
                .chain(std::iter::once(&foreground.name))
                .any(|identity| self.identities.contains(&identity.to_ascii_lowercase()))
    }
}

fn desktop_directories() -> Vec<PathBuf> {
    let home = env::var_os("HOME").map(PathBuf::from);
    let mut roots = Vec::new();
    if let Some(data) = env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .filter(|path| path.is_absolute())
        .or_else(|| home.as_ref().map(|path| path.join(".local/share")))
    {
        roots.push(data);
    }
    if let Some(home) = &home {
        roots.push(home.join(".local/share/flatpak/exports/share"));
    }
    roots.extend(
        env::split_paths(
            &env::var_os("XDG_DATA_DIRS").unwrap_or_else(|| "/usr/local/share:/usr/share".into()),
        )
        .filter(|path| path.is_absolute()),
    );
    roots.extend([
        PathBuf::from("/var/lib/snapd/desktop"),
        PathBuf::from("/var/lib/flatpak/exports/share"),
    ]);
    let mut seen = HashSet::new();
    roots
        .into_iter()
        .map(|path| path.join("applications"))
        .filter(|path| seen.insert(path.clone()))
        .collect()
}

static CATALOG: OnceLock<RwLock<Catalog>> = OnceLock::new();
static GENERATION: AtomicU64 = AtomicU64::new(1);
static STARTED: OnceLock<()> = OnceLock::new();

fn catalog() -> &'static RwLock<Catalog> {
    CATALOG.get_or_init(|| RwLock::new(Catalog::from_directories(&desktop_directories())))
}

pub fn generation() -> u64 {
    GENERATION.load(Ordering::Acquire)
}

/// Scan outside the input thread. New installations become eligible without
/// restarting the app; generation changes make the current target re-evaluate.
pub fn start() {
    STARTED.get_or_init(|| {
        let _ = catalog();
        std::thread::spawn(|| loop {
            std::thread::sleep(REFRESH_INTERVAL);
            let next = Catalog::from_directories(&desktop_directories());
            if let Ok(mut current) = catalog().write() {
                if *current != next {
                    *current = next;
                    GENERATION.fetch_add(1, Ordering::Release);
                }
            }
        });
    });
}

pub fn is_browser(foreground: &Foreground) -> bool {
    if foreground.window == 0 {
        return false;
    }
    foreground
        .identities
        .iter()
        .chain(std::iter::once(&foreground.name))
        .any(|identity| {
            KNOWN_IDENTITIES
                .iter()
                .any(|known| identity.eq_ignore_ascii_case(known))
        })
        || catalog()
            .read()
            .is_ok_and(|catalog| catalog.contains(foreground))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn app(name: &str) -> Foreground {
        Foreground {
            window: 42,
            name: name.into(),
            identities: vec![name.to_ascii_lowercase()],
        }
    }

    #[test]
    fn common_browser_families_use_exact_identities_only() {
        for name in [
            "Google-chrome",
            "chromium",
            "Brave-browser",
            "Microsoft-edge",
            "vivaldi-stable",
            "Opera",
            "librewolf",
            "zen",
            "Waterfox",
            "floorp",
            "org.gnome.Epiphany",
            "Falkon",
            "qutebrowser",
        ] {
            assert!(is_browser(&app(name)), "{name}");
        }
        for name in [
            "Google-chrome-malware",
            "Electron",
            "Navigator",
            "Code",
            "Firefox video - Editor",
        ] {
            assert!(!is_browser(&app(name)), "{name}");
        }
        assert!(!is_browser(&Foreground {
            window: 0,
            ..app("Google-chrome")
        }));
    }

    #[test]
    fn desktop_registration_uses_category_and_identity_never_exec_or_name() {
        let entry = "[Desktop Entry]\nType=Application\nName=Secret page title\nExec=sh -c malicious\nCategories=Network;WebBrowser;\nStartupWMClass=MyBrowser\nNoDisplay=true\n[Desktop Action Other]\nStartupWMClass=Wrong\n";
        assert_eq!(
            browser_entry(entry, "org.example.Web"),
            vec!["org.example.web", "mybrowser"]
        );
        for suffix in [
            "Hidden=true\n",
            "Terminal=true\n",
            "Categories=Network;\n",
            "Type=Link\n",
        ] {
            let source =
                format!("[Desktop Entry]\nType=Application\nCategories=WebBrowser;\n{suffix}");
            assert!(browser_entry(&source, "test").is_empty());
        }
        assert_eq!(browser_entry("[Desktop Entry]\nType=Application\nCategories=WebBrowser;\nStartupWMClass=Electron\n", "org.example.Web"), vec!["org.example.web"]);
    }

    #[test]
    fn user_masking_nested_ids_and_new_installation_refresh() {
        let root = env::temp_dir().join(format!(
            "mxcc-browser-catalog-{}-{}",
            std::process::id(),
            GENERATION.fetch_add(1, Ordering::Relaxed)
        ));
        let user = root.join("user");
        let system = root.join("system");
        fs::create_dir_all(&user).unwrap();
        fs::create_dir_all(system.join("vendor")).unwrap();
        let entry = "[Desktop Entry]\nType=Application\nCategories=WebBrowser;\nStartupWMClass=CustomBrowser\n";
        fs::write(system.join("masked.desktop"), entry).unwrap();
        fs::write(
            user.join("masked.desktop"),
            "[Desktop Entry]\nHidden=true\n",
        )
        .unwrap();
        fs::write(
            system.join("vendor/web.desktop"),
            entry.replace("CustomBrowser", "NestedBrowser"),
        )
        .unwrap();
        let directories = vec![user, system.clone()];
        let first = Catalog::from_directories(&directories);
        assert!(!first.contains(&app("CustomBrowser")));
        assert!(first.contains(&app("vendor-web")) && first.contains(&app("NestedBrowser")));
        fs::write(system.join("new.desktop"), entry).unwrap();
        let second = Catalog::from_directories(&directories);
        assert!(second.contains(&app("CustomBrowser")) && second.contains(&app("new")));
        assert_ne!(first.identities, second.identities);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn bounded_scan_accepts_regular_file_links_but_skips_cycles_and_nonregular_entries() {
        use std::os::unix::fs::symlink;
        let root = env::temp_dir().join(format!("mxcc-browser-links-{}", std::process::id()));
        fs::create_dir_all(root.join("applications")).unwrap();
        let applications = root.join("applications");
        let entry = "[Desktop Entry]\nType=Application\nCategories=WebBrowser;\nStartupWMClass=LinkedBrowser\n";
        fs::write(root.join("real-entry"), entry).unwrap();
        symlink(root.join("real-entry"), applications.join("linked.desktop")).unwrap();
        symlink(&applications, applications.join("cycle")).unwrap();
        fs::write(
            applications.join("oversized.desktop"),
            vec![b' '; MAX_ENTRY_BYTES as usize + 1],
        )
        .unwrap();
        let fifo = std::ffi::CString::new(
            applications
                .join("pipe.desktop")
                .as_os_str()
                .as_encoded_bytes(),
        )
        .unwrap();
        // SAFETY: CString remains live for this fixed pathname call.
        assert_eq!(unsafe { libc::mkfifo(fifo.as_ptr(), 0o600) }, 0);
        let catalog = Catalog::from_directories(&[applications]);
        assert_eq!(
            catalog.identities,
            HashSet::from(["linked".into(), "linkedbrowser".into()])
        );
        fs::remove_dir_all(root).unwrap();
    }
}
