use crate::events::{Event, Sender};
use ksni::blocking::TrayMethods;
use std::path::{Path, PathBuf};

const INSTALLED_ICON: &str = "/usr/share/icons/hicolor/scalable/apps/mx-creative-control.svg";

fn icon_file(executable: Option<&Path>, installed: &Path) -> Option<PathBuf> {
    // Cargo's source-run binary lives below worker/target/{debug,release}.
    // Resolve relative to that binary, not the shell's working directory or
    // the build machine's embedded source path. Cross-target builds add one
    // directory below target, hence the five-ancestor bound.
    let source = executable
        .into_iter()
        .flat_map(|path| path.ancestors().skip(1).take(5))
        .map(|parent| parent.join("packaging/mx-creative-control.svg"));
    source
        .chain(std::iter::once(installed.to_path_buf()))
        .filter(|path| path.is_file())
        .find_map(|path| path.canonicalize().ok())
}

pub struct Tray {
    pub paused: bool,
    pub page: usize,
    pub connected: bool,
    sender: Sender,
    gui: PathBuf,
    icon: Option<PathBuf>,
}
impl Tray {
    fn open(&self) {
        let command = self.gui.clone();
        std::thread::spawn(move || match std::process::Command::new(command).spawn() {
            Ok(mut child) => {
                let _ = child.wait();
            }
            Err(e) => eprintln!("cannot open settings: {e}"),
        });
    }
}
impl ksni::Tray for Tray {
    fn id(&self) -> String {
        "mx-creative-control".into()
    }
    fn title(&self) -> String {
        "MX Creative Control".into()
    }
    fn icon_name(&self) -> String {
        // GNOME creates a ThemedIcon for any nonempty theme name, even when
        // absent, and then skips IconPixmap. A known absolute SVG is supported
        // by its AppIndicator host. If neither source nor installed icon is
        // available, leave the name empty so all hosts can use IconPixmap.
        self.icon
            .as_deref()
            .and_then(Path::to_str)
            .unwrap_or_default()
            .into()
    }
    fn icon_pixmap(&self) -> Vec<ksni::Icon> {
        // Pre-rendered from packaging/mx-creative-control.svg.
        // Provides a source-run fallback when no icon theme is installed.
        vec![ksni::Icon {
            width: 32,
            height: 32,
            data: include_bytes!("tray-icon.argb").to_vec(),
        }]
    }
    fn activate(&mut self, _x: i32, _y: i32) {
        self.open();
    }
    fn watcher_online(&self) {
        self.sender.send(Event::Tray(true));
    }
    fn watcher_offline(&self, _reason: ksni::OfflineReason) -> bool {
        self.sender.send(Event::Tray(false));
        true
    }
    fn menu(&self) -> Vec<ksni::MenuItem<Self>> {
        use ksni::menu::*;
        vec![
            StandardItem {
                label: if self.connected {
                    "Keypad connected"
                } else {
                    "Keypad disconnected"
                }
                .into(),
                enabled: false,
                ..Default::default()
            }
            .into(),
            StandardItem {
                label: "Open settings".into(),
                activate: Box::new(|t: &mut Self| t.open()),
                ..Default::default()
            }
            .into(),
            CheckmarkItem {
                label: "Pause shortcuts".into(),
                checked: self.paused,
                activate: Box::new(|t: &mut Self| {
                    t.paused = !t.paused;
                    t.sender.send(Event::Pause(t.paused));
                }),
                ..Default::default()
            }
            .into(),
            RadioGroup {
                selected: self.page,
                select: Box::new(|t: &mut Self, p| {
                    t.page = p;
                    t.sender.send(Event::Page(p));
                }),
                options: vec![
                    RadioItem {
                        label: "Apps page".into(),
                        ..Default::default()
                    },
                    RadioItem {
                        label: "Shortcuts page".into(),
                        ..Default::default()
                    },
                ],
            }
            .into(),
            ksni::MenuItem::Separator,
            StandardItem {
                label: "Quit".into(),
                activate: Box::new(|t: &mut Self| t.sender.send(Event::Shutdown)),
                ..Default::default()
            }
            .into(),
        ]
    }
}
pub fn start(sender: Sender, gui: PathBuf) -> Option<ksni::blocking::Handle<Tray>> {
    let executable = std::env::current_exe().ok();
    let icon = icon_file(executable.as_deref(), Path::new(INSTALLED_ICON));
    match (Tray {
        paused: false,
        page: 0,
        connected: false,
        sender,
        gui,
        icon,
    })
    .spawn()
    {
        Ok(h) => Some(h),
        Err(e) => {
            eprintln!("tray unavailable: {e}");
            None
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn source_icon_is_absolute_and_does_not_depend_on_working_directory() {
        let root = std::env::temp_dir().join(format!("mxcc-tray-source-{}", std::process::id()));
        let source = root.join("project/packaging/mx-creative-control.svg");
        let installed = root.join("installed/mx-creative-control.svg");
        fs::create_dir_all(source.parent().unwrap()).unwrap();
        fs::create_dir_all(installed.parent().unwrap()).unwrap();
        fs::write(&source, b"<svg/>").unwrap();
        fs::write(&installed, b"<svg/>").unwrap();
        for executable in [
            root.join("project/worker/target/release/mxcc-worker"),
            root.join("project/worker/target/x86_64-unknown-linux-gnu/release/mxcc-worker"),
        ] {
            let selected = icon_file(Some(&executable), &installed).unwrap();
            assert_eq!(selected, source.canonicalize().unwrap());
            assert!(selected.is_absolute());
        }
        fs::remove_file(&source).unwrap();
        assert_eq!(
            icon_file(
                Some(&root.join("project/worker/target/release/mxcc-worker")),
                &installed
            ),
            Some(installed.canonicalize().unwrap())
        );
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn missing_icon_files_select_the_embedded_pixmap_fallback() {
        let root = std::env::temp_dir().join(format!("mxcc-tray-missing-{}", std::process::id()));
        assert!(icon_file(Some(&root.join("mxcc-worker")), &root.join("absent.svg")).is_none());
        assert!(icon_file(None, &root.join("absent.svg")).is_none());
    }
}
