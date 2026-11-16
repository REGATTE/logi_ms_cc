//! Per-device XInput2 passive grabs. Only assigned buttons are intercepted.
//!
//! A slave-device grab suppresses the original click and automatically ends on
//! release. Actions run on release so the pointer is attached again before
//! a popup or shortcut runs. No keyboard events or pointer motion are monitored.

use crate::events::{Event, Sender};
use mx_creative_worker::config::DeviceBinding;
use serde_json::{json, Value};
use std::{
    collections::{BTreeMap, BTreeSet},
    io::{Read, Write},
    os::{fd::AsRawFd, unix::net::UnixStream},
    sync::mpsc,
};
use x11rb::{
    connection::Connection,
    protocol::{
        xinput::{
            self, DeviceClassData, DeviceType, GrabMode22, GrabOwner, GrabType, HierarchyMask,
            ModifierMask, XIEventMask,
        },
        xproto::{AtomEnum, ConnectionExt, GrabMode, GrabStatus},
        Event as XEvent,
    },
    rust_connection::RustConnection,
    CURRENT_TIME, NONE,
};

enum Command {
    Update(Vec<DeviceBinding>, bool),
    Stop,
}

pub struct Handle {
    commands: mpsc::Sender<Command>,
    wake: UnixStream,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Handle {
    pub fn update(&self, bindings: Vec<DeviceBinding>, paused: bool) {
        if self
            .commands
            .send(Command::Update(bindings, paused))
            .is_ok()
        {
            let _ = (&self.wake).write(&[1]);
        }
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        let _ = self.commands.send(Command::Stop);
        let _ = (&self.wake).write(&[1]);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

pub fn start(sender: Sender, bindings: Vec<DeviceBinding>) -> Handle {
    // The worker already needs a local socket; failure here is a process-level
    // resource exhaustion rather than a recoverable device permission issue.
    let (wake, mut reader) = UnixStream::pair().expect("device button wake socket");
    wake.set_nonblocking(true)
        .expect("device button wake socket");
    reader
        .set_nonblocking(true)
        .expect("device button wake socket");
    let (commands, incoming) = mpsc::channel();
    let thread = std::thread::spawn(move || {
        if let Err(error) = run(&sender, bindings, &incoming, &mut reader) {
            sender.send(Event::DeviceButtonsStatus(json!({
                "available": false, "backend": "x11", "active": [], "errors": [error]
            })));
        }
    });
    Handle {
        commands,
        wake,
        thread: Some(thread),
    }
}

fn run(
    sender: &Sender,
    mut bindings: Vec<DeviceBinding>,
    incoming: &mpsc::Receiver<Command>,
    wake: &mut UnixStream,
) -> Result<(), String> {
    if std::env::var("XDG_SESSION_TYPE").is_ok_and(|value| value == "wayland") {
        return Err("Device button actions require an X11 session.".into());
    }
    let (conn, screen) = x11rb::connect(None).map_err(|e| e.to_string())?;
    xinput::xi_query_version(&conn, 2, 0)
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?;
    let root = conn.setup().roots[screen].root;
    xinput::xi_select_events(
        &conn,
        root,
        &[xinput::EventMask {
            deviceid: 0,
            mask: vec![XIEventMask::HIERARCHY],
        }],
    )
    .map_err(|e| e.to_string())?
    .check()
    .map_err(|e| e.to_string())?;
    let mut grabs = BTreeMap::new();
    let mut held = BTreeSet::new();
    let mut paused = false;
    let mut refresh = true;
    loop {
        while let Ok(command) = incoming.try_recv() {
            match command {
                Command::Update(next, value) => {
                    bindings = next;
                    paused = value;
                    held.clear();
                    refresh = true;
                }
                Command::Stop => return Ok(()), // Closing the connection releases all grabs.
            }
        }
        if refresh {
            let status = refresh_grabs(&conn, root, &bindings, paused, &mut grabs)?;
            held.retain(|key| grabs.contains_key(key));
            sender.send(Event::DeviceButtonsStatus(status));
            refresh = false;
        }
        while let Some(event) = conn.poll_for_event().map_err(|e| e.to_string())? {
            match event {
                XEvent::XinputButtonPress(event) => {
                    let key = (event.deviceid, event.detail);
                    if grabs.contains_key(&key) && !paused {
                        held.insert(key);
                    }
                }
                XEvent::XinputButtonRelease(event) => {
                    let key = (event.deviceid, event.detail);
                    if let Some(binding) = released_binding(key, &mut held, &grabs, paused) {
                        sender.send(Event::DeviceButton(binding));
                    }
                }
                XEvent::XinputHierarchy(event) if relevant_hierarchy(event.flags) => refresh = true,
                _ => {}
            }
        }
        if refresh {
            continue;
        }
        conn.flush().map_err(|e| e.to_string())?;
        let mut fds = [
            libc::pollfd {
                fd: conn.stream().as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: wake.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: fds is a live array of two pollfd values for the call duration.
        if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, -1) } < 0 {
            let error = std::io::Error::last_os_error();
            if error.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(error.to_string());
        }
        let mut bytes = [0u8; 256];
        while wake.read(&mut bytes).is_ok_and(|count| count > 0) {}
    }
}

fn released_binding(
    key: (u16, u32),
    held: &mut BTreeSet<(u16, u32)>,
    grabs: &BTreeMap<(u16, u32), DeviceBinding>,
    paused: bool,
) -> Option<DeviceBinding> {
    if held.remove(&key) && !paused {
        grabs.get(&key).cloned()
    } else {
        None
    }
}

fn relevant_hierarchy(flags: HierarchyMask) -> bool {
    // Attached/detached events also happen for every passive grab; rebuilding
    // then would remove a grab before its matching release arrives.
    u32::from(flags)
        & u32::from(
            HierarchyMask::SLAVE_ADDED
                | HierarchyMask::SLAVE_REMOVED
                | HierarchyMask::DEVICE_ENABLED
                | HierarchyMask::DEVICE_DISABLED
                | HierarchyMask::MASTER_ADDED
                | HierarchyMask::MASTER_REMOVED,
        )
        != 0
}

fn hidpp_binding(binding: &DeviceBinding) -> bool {
    binding.vendor_id == 0x046d
        && (binding.product_id == 0xbc00
            || (binding.product_id == 0xb042 && binding.control_id == "hidpp:416"))
}

fn control_label(binding: &DeviceBinding) -> Option<&'static str> {
    if binding.vendor_id != 0x046d {
        return None;
    }
    let code = match (binding.product_id, binding.control_id.as_str()) {
        (0xc07e, "button:4") => 275,
        (0xc07e, "button:5") => 276,
        (0xc07e, _) => return None,
        // The dialpad's assigned controls use HID++ diversion; its existing
        // evdev remapper may be consuming ordinary mouse button events.
        (0xbc00, _) => return None,
        (_, text) => text.strip_prefix("key:")?.parse().ok()?,
    };
    match code {
        272 => Some("Button Left"),
        273 => Some("Button Right"),
        274 => Some("Button Middle"),
        275 => Some("Button Side"),
        276 => Some("Button Extra"),
        277 => Some("Button Forward"),
        278 => Some("Button Back"),
        _ => None,
    }
}

fn binding_json(binding: &DeviceBinding) -> Value {
    json!({"vendor_id": binding.vendor_id, "product_id": binding.product_id,
           "control_id": binding.control_id})
}

fn refresh_grabs(
    conn: &RustConnection,
    root: u32,
    bindings: &[DeviceBinding],
    paused: bool,
    grabs: &mut BTreeMap<(u16, u32), DeviceBinding>,
) -> Result<Value, String> {
    let modifiers = [u32::from(ModifierMask::ANY)];
    for &(device, button) in grabs.keys() {
        if let Ok(cookie) = xinput::xi_passive_ungrab_device(
            conn,
            root,
            button,
            device,
            GrabType::BUTTON,
            &modifiers,
        ) {
            let _ = cookie.check(); // A removed device is expected during hotplug.
        }
    }
    grabs.clear();
    if paused || bindings.is_empty() {
        return Ok(
            json!({"available": true, "backend": "x11", "active": [], "errors": [], "paused": paused}),
        );
    }
    let mut errors = Vec::new();
    let product_atom = conn
        .intern_atom(false, b"Device Product ID")
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?
        .atom;
    let devices = xinput::xi_query_device(conn, 0u16)
        .map_err(|e| e.to_string())?
        .reply()
        .map_err(|e| e.to_string())?;
    let mut found = BTreeSet::new();
    for device in devices.infos {
        if !device.enabled || device.type_ != DeviceType::SLAVE_POINTER {
            continue;
        }
        let Ok(property) = xinput::xi_get_property(
            conn,
            device.deviceid,
            false,
            product_atom,
            AtomEnum::INTEGER.into(),
            0,
            2,
        ) else {
            continue;
        };
        let Ok(property) = property.reply() else {
            continue;
        };
        let Some(ids) = property.items.as_data32() else {
            continue;
        };
        if ids.len() != 2 {
            continue;
        }
        let Some(buttons) = device.classes.iter().find_map(|class| match &class.data {
            DeviceClassData::Button(buttons) => Some(buttons),
            _ => None,
        }) else {
            continue;
        };
        // XI1's map translates physical button indices in the class into the
        // logical numbers used by passive grabs, including custom user maps.
        let Ok(device_id) = u8::try_from(device.deviceid) else {
            continue;
        };
        let Ok(mapping) = xinput::get_device_button_mapping(conn, device_id) else {
            continue;
        };
        let Ok(mapping) = mapping.reply() else {
            continue;
        };
        for (index, binding) in bindings.iter().enumerate() {
            if hidpp_binding(binding) {
                continue;
            }
            if ids != &[u32::from(binding.vendor_id), u32::from(binding.product_id)] {
                continue;
            }
            let Some(label) = control_label(binding) else {
                continue;
            };
            let atom = conn
                .intern_atom(false, label.as_bytes())
                .map_err(|e| e.to_string())?
                .reply()
                .map_err(|e| e.to_string())?
                .atom;
            let Some(position) = buttons.labels.iter().position(|value| *value == atom) else {
                continue;
            };
            let Some(&button) = mapping.map.get(position) else {
                continue;
            };
            if button == 0 {
                continue;
            }
            found.insert(index);
            let key = (device.deviceid, u32::from(button));
            if grabs.contains_key(&key) {
                continue;
            }
            let result = (|| -> Result<(), String> {
                let reply = xinput::xi_passive_grab_device(
                    conn,
                    CURRENT_TIME,
                    root,
                    NONE,
                    key.1,
                    key.0,
                    GrabType::BUTTON,
                    GrabMode22::ASYNC,
                    GrabMode::ASYNC,
                    GrabOwner::NO_OWNER,
                    &[u32::from(
                        XIEventMask::BUTTON_PRESS | XIEventMask::BUTTON_RELEASE,
                    )],
                    &modifiers,
                )
                .map_err(|e| e.to_string())?
                .reply()
                .map_err(|e| e.to_string())?;
                if reply
                    .modifiers
                    .iter()
                    .any(|value| value.status != GrabStatus::SUCCESS)
                {
                    return Err("another application already owns this button".into());
                }
                Ok(())
            })();
            match result {
                Ok(()) => {
                    grabs.insert(key, binding.clone());
                }
                Err(error) => errors.push(format!("{}: {error}", binding.control_id)),
            }
        }
    }
    for (index, binding) in bindings.iter().enumerate() {
        if hidpp_binding(binding) {
            continue;
        }
        if !found.contains(&index) {
            errors.push(format!(
                "{:04x}:{:04x} {}: {}",
                binding.vendor_id,
                binding.product_id,
                binding.control_id,
                if control_label(binding).is_none() {
                    "this control has no supported X11 mouse button mapping"
                } else {
                    "device disconnected or its button is unavailable"
                }
            ));
        }
    }
    Ok(
        json!({"available": true, "backend": "x11", "active": grabs.values().map(binding_json).collect::<Vec<_>>(),
              "errors": errors, "paused": false}),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn binding(product_id: u16, control_id: &str) -> DeviceBinding {
        DeviceBinding {
            vendor_id: 0x046d,
            product_id,
            control_id: control_id.into(),
        }
    }

    #[test]
    fn only_known_button_identities_are_grabbed() {
        assert_eq!(
            control_label(&binding(0xc07e, "button:4")),
            Some("Button Side")
        );
        assert_eq!(
            control_label(&binding(0xc07e, "button:5")),
            Some("Button Extra")
        );
        assert_eq!(control_label(&binding(0xc07e, "button:1")), None);
        assert_eq!(control_label(&binding(0xc07e, "key:272")), None);
        assert_eq!(control_label(&binding(0xbc00, "button:1")), None);
        assert!(hidpp_binding(&binding(0xb042, "hidpp:416")));
        assert!(!hidpp_binding(&binding(0xb042, "key:278")));
        assert!(!hidpp_binding(&binding(0xb042, "hidpp:196")));
        assert!(!hidpp_binding(&binding(0xc07e, "hidpp:416")));
        assert_eq!(
            control_label(&binding(0xc001, "key:277")),
            Some("Button Forward")
        );
        assert_eq!(control_label(&binding(0xc001, "key:30")), None);
        assert_eq!(control_label(&binding(0xc001, "rel:8")), None);
    }

    #[test]
    fn passive_grab_hierarchy_changes_do_not_rebuild_grabs() {
        assert!(!relevant_hierarchy(
            HierarchyMask::SLAVE_DETACHED | HierarchyMask::SLAVE_ATTACHED
        ));
        assert!(relevant_hierarchy(HierarchyMask::SLAVE_REMOVED));
        assert!(relevant_hierarchy(HierarchyMask::DEVICE_ENABLED));
    }

    #[test]
    fn releases_keep_device_identity_and_ignore_unheld_removed_or_paused_grabs() {
        let side = binding(0xc07e, "button:4");
        let forward = binding(0xc001, "key:277");
        let mut grabs = BTreeMap::from([((10, 8), side.clone()), ((11, 8), forward.clone())]);
        let mut held = BTreeSet::from([(10, 8), (11, 8)]);
        assert_eq!(
            released_binding((11, 8), &mut held, &grabs, false),
            Some(forward)
        );
        assert_eq!(released_binding((11, 8), &mut held, &grabs, false), None);
        assert_eq!(released_binding((10, 8), &mut held, &grabs, true), None);
        held.insert((10, 8));
        grabs.remove(&(10, 8));
        assert_eq!(released_binding((10, 8), &mut held, &grabs, false), None);
        assert!(held.is_empty());
    }
}
