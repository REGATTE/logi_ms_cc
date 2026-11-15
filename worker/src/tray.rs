use crate::events::{Event, Sender};
use ksni::blocking::TrayMethods;
use std::path::PathBuf;

pub struct Tray {
    pub paused: bool,
    pub page: usize,
    pub connected: bool,
    sender: Sender,
    gui: PathBuf,
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
        "mx-creative-control".into()
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
    match (Tray {
        paused: false,
        page: 0,
        connected: false,
        sender,
        gui,
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
