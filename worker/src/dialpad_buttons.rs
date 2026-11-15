//! MX Dialpad HID++ button diversion, independent of its ordinary scroll input.
//!
//! Only assigned controls have their temporary diversion bit changed. Persistent
//! mappings, wheel reports, and the user's existing evdev remapper are untouched.
//! Normal shutdown/pause restores the previous temporary diversion bit; a forced
//! kill or unplug may require cycling the dialpad's power to reset that bit.

use crate::events::{Event, Sender};
use mx_creative_worker::config::DeviceBinding;
use serde_json::{json, Value};
use std::{
    collections::BTreeSet,
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::{
        fd::AsRawFd,
        unix::{fs::OpenOptionsExt, net::UnixStream},
    },
    path::{Path, PathBuf},
    sync::mpsc,
    time::{Duration, Instant},
};

const VENDOR: u16 = 0x046d;
const PRODUCT: u16 = 0xbc00;
const CONTROL_IDS: [u16; 4] = [0x0053, 0x0056, 0x0059, 0x005a];
const RETRY: Duration = Duration::from_secs(5);

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
    let (wake, mut reader) = UnixStream::pair().expect("dialpad button wake socket");
    wake.set_nonblocking(true)
        .expect("dialpad button wake socket");
    reader
        .set_nonblocking(true)
        .expect("dialpad button wake socket");
    let (commands, incoming) = mpsc::channel();
    let thread = std::thread::spawn(move || run(sender, bindings, incoming, &mut reader));
    Handle {
        commands,
        wake,
        thread: Some(thread),
    }
}

fn controls(bindings: &[DeviceBinding]) -> BTreeSet<u16> {
    bindings
        .iter()
        .filter(|binding| binding.vendor_id == VENDOR && binding.product_id == PRODUCT)
        .filter_map(|binding| control_id(&binding.control_id))
        .collect()
}

fn control_id(identifier: &str) -> Option<u16> {
    match identifier {
        "button:1" => Some(CONTROL_IDS[0]),
        "button:2" => Some(CONTROL_IDS[1]),
        "button:3" => Some(CONTROL_IDS[2]),
        "button:4" => Some(CONTROL_IDS[3]),
        _ => None,
    }
}

fn status(assigned: &BTreeSet<u16>, active: bool, error: Option<&str>, permission: bool) -> Value {
    let controls: Vec<_> = if active {
        assigned
            .iter()
            .filter_map(|cid| CONTROL_IDS.iter().position(|value| value == cid))
            .map(|index| {
                json!({"vendor_id": VENDOR, "product_id": PRODUCT,
                "control_id": format!("button:{}", index + 1)})
            })
            .collect()
    } else {
        Vec::new()
    };
    json!({"available": error.is_none(), "backend": "hidpp", "active": controls,
           "permission_required": permission, "errors": error.into_iter().collect::<Vec<_>>()})
}

fn run(
    sender: Sender,
    bindings: Vec<DeviceBinding>,
    incoming: mpsc::Receiver<Command>,
    wake: &mut UnixStream,
) {
    let mut assigned = controls(&bindings);
    let mut paused = false;
    let mut device: Option<Dialpad> = None;
    let mut retry_at = Instant::now();
    let mut previous = Value::Null;
    let mut report = |value: Value| {
        if value != previous {
            previous = value.clone();
            sender.send(Event::DialpadButtonsStatus(value));
        }
    };
    loop {
        while let Ok(command) = incoming.try_recv() {
            match command {
                Command::Stop => return, // Drop restores the temporary diversion bits.
                Command::Update(bindings, next_paused) => {
                    let next = controls(&bindings);
                    if assigned != next || paused != next_paused {
                        device = None;
                        assigned = next;
                        paused = next_paused;
                        retry_at = Instant::now();
                    }
                }
            }
        }
        if paused || assigned.is_empty() {
            device = None;
            report(status(&assigned, false, None, false));
        } else if device.is_none() && Instant::now() >= retry_at {
            let result = discover().and_then(|path| {
                let path = path.ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::NotFound,
                        "Connect or wake the MX Dialpad to use its Action Ring buttons.",
                    )
                })?;
                if let Some(conflict) = crate::hardware::conflict(&path) {
                    return Err(io::Error::other(conflict.replace("keypad", "dialpad")));
                }
                Dialpad::open(&path, &assigned)
            });
            match result {
                Ok(opened) => {
                    device = Some(opened);
                    report(status(&assigned, true, None, false));
                }
                Err(error) => {
                    let permission = error.kind() == io::ErrorKind::PermissionDenied;
                    let message = if permission {
                        "MX Dialpad access is required. Open Settings → Permissions and allow device access.".into()
                    } else {
                        error.to_string()
                    };
                    report(status(&assigned, false, Some(&message), permission));
                    retry_at = Instant::now() + RETRY;
                }
            }
        }
        let timeout = if device.is_none() && !paused && !assigned.is_empty() {
            retry_at
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(5000) as i32
        } else {
            -1
        };
        let mut fds = [
            libc::pollfd {
                fd: wake.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: device.as_ref().map_or(-1, |device| device.file.as_raw_fd()),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: fds contains valid pollfd entries; -1 marks an inactive device.
        if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, timeout) } < 0 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            report(status(&assigned, false, Some(&error.to_string()), false));
            return;
        }
        let mut bytes = [0u8; 128];
        while wake.read(&mut bytes).is_ok_and(|count| count > 0) {}
        if fds[1].revents != 0 {
            let result = device.as_mut().map(|device| device.receive(&sender));
            if let Some(Err(error)) = result {
                report(status(&assigned, false, Some(&error.to_string()), false));
                device = None;
                retry_at = Instant::now() + RETRY;
            }
        }
    }
}

fn discover() -> io::Result<Option<PathBuf>> {
    let Ok(entries) = fs::read_dir("/sys/class/hidraw") else {
        return Ok(None);
    };
    let mut candidates = Vec::new();
    for entry in entries.flatten() {
        let device = entry.path().join("device");
        let uevent = fs::read_to_string(device.join("uevent")).unwrap_or_default();
        if uevent.lines().any(|line| {
            line.strip_prefix("HID_ID=")
                .is_some_and(|id| id.eq_ignore_ascii_case("0005:0000046d:0000bc00"))
        }) {
            candidates.push(Path::new("/dev").join(entry.file_name()));
        }
    }
    if candidates.len() > 1 {
        return Err(io::Error::other(
            "More than one MX Dialpad is connected; connect one to assign its buttons.",
        ));
    }
    Ok(candidates.pop())
}

struct Dialpad {
    file: File,
    feature: u8,
    original: Vec<(u16, bool)>,
    assigned: BTreeSet<u16>,
    held: BTreeSet<u16>,
}

impl Drop for Dialpad {
    fn drop(&mut self) {
        for (cid, diverted) in std::mem::take(&mut self.original) {
            let [hi, lo] = cid.to_be_bytes();
            if let Err(error) = self.request(
                self.feature,
                3,
                &[hi, lo, 2 | u8::from(diverted), 0, 0],
                Duration::from_millis(250),
            ) {
                eprintln!("Restore MX Dialpad button {cid:04x}: {error}");
            }
        }
    }
}

impl Dialpad {
    fn open(path: &Path, assigned: &BTreeSet<u16>) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(path)?;
        let mut device = Self {
            file,
            feature: 0,
            original: Vec::new(),
            assigned: assigned.clone(),
            held: BTreeSet::new(),
        };
        let timeout = Duration::from_millis(1000);
        let reply = device.request(0, 0, &[0x1b, 0x04, 0], timeout)?;
        device.feature = reply[4];
        if device.feature == 0 {
            return Err(io::Error::other(
                "MX Dialpad has no reprogrammable button feature.",
            ));
        }
        let count = device.request(device.feature, 0, &[], timeout)?[4];
        if count > 32 {
            return Err(io::Error::other("Unexpected MX Dialpad control count."));
        }
        let mut divertable = BTreeSet::new();
        for index in 0..count {
            let info = device.request(device.feature, 1, &[index], timeout)?;
            if info.len() >= 13 && info[8] & 0x20 != 0 {
                divertable.insert(u16::from_be_bytes([info[4], info[5]]));
            }
        }
        for cid in assigned {
            if !divertable.contains(cid) {
                return Err(io::Error::other(
                    "An assigned dialpad button does not support temporary diversion.",
                ));
            }
            let [hi, lo] = cid.to_be_bytes();
            let current = device.request(device.feature, 2, &[hi, lo], timeout)?;
            if current[4..6] != [hi, lo] {
                return Err(io::Error::other("Unexpected MX Dialpad control response."));
            }
            device.original.push((*cid, current[6] & 1 != 0));
            let changed = device.request(device.feature, 3, &[hi, lo, 3, 0, 0], timeout)?;
            if changed[4..7] != [hi, lo, 3] {
                return Err(io::Error::other(
                    "MX Dialpad did not confirm its temporary button assignment.",
                ));
            }
        }
        Ok(device)
    }

    fn read(&mut self) -> io::Result<Option<Vec<u8>>> {
        let mut bytes = [0u8; 64];
        match self.file.read(&mut bytes) {
            Ok(0) => Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "MX Dialpad disconnected.",
            )),
            Ok(count) => Ok(Some(bytes[..count].to_vec())),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(error) => Err(error),
        }
    }

    fn receive(&mut self, sender: &Sender) -> io::Result<()> {
        while let Some(report) = self.read()? {
            for control in released(&report, self.feature, &self.assigned, &mut self.held) {
                if let Some(index) = CONTROL_IDS.iter().position(|cid| *cid == control) {
                    sender.send(Event::DeviceButton(DeviceBinding {
                        vendor_id: VENDOR,
                        product_id: PRODUCT,
                        control_id: format!("button:{}", index + 1),
                    }));
                }
            }
        }
        Ok(())
    }

    fn request(
        &mut self,
        feature: u8,
        function: u8,
        payload: &[u8],
        timeout: Duration,
    ) -> io::Result<Vec<u8>> {
        let mut packet = [0u8; 20];
        packet[..4].copy_from_slice(&[0x11, 0xff, feature, (function << 4) | 0x0d]);
        packet[4..4 + payload.len()].copy_from_slice(payload);
        if self.file.write(&packet)? != packet.len() {
            return Err(io::Error::other("Partial MX Dialpad HID++ request."));
        }
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(report) = self.read()? {
                if let Some(result) = response(&report, feature, packet[3]) {
                    result?;
                    return Ok(report);
                }
                // Keep press state while settings replies and input are interleaved.
                // Input during a configuration change must never invoke an action.
                let _ = released(&report, self.feature, &self.assigned, &mut self.held);
            } else {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "Wake the MX Dialpad to apply its button actions.",
                    ));
                }
                let mut fd = libc::pollfd {
                    fd: self.file.as_raw_fd(),
                    events: libc::POLLIN,
                    revents: 0,
                };
                // SAFETY: fd is valid for the entire bounded poll call.
                if unsafe { libc::poll(&mut fd, 1, remaining.as_millis().min(1000) as i32) } < 0 {
                    let error = io::Error::last_os_error();
                    if error.kind() != io::ErrorKind::Interrupted {
                        return Err(error);
                    }
                }
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "MX Dialpad did not acknowledge the button request.",
                ));
            }
        }
    }
}

fn response(report: &[u8], feature: u8, command: u8) -> Option<io::Result<()>> {
    // The dialpad replies to these requests with a 20-byte HID++ long report.
    // Mouse, wheel, truncated and other software clients' reports may be on
    // the same hidraw stream, and cannot acknowledge a configuration change.
    if report.len() < 20 || report[0] != 0x11 {
        return None;
    }
    if report[1..5] == [0xff, 0xff, feature, command] {
        return Some(Err(io::Error::other(format!(
            "MX Dialpad refused button request ({:02x}).",
            report[5]
        ))));
    }
    (report[1..4] == [0xff, feature, command]).then_some(Ok(()))
}

fn released(
    report: &[u8],
    feature: u8,
    assigned: &BTreeSet<u16>,
    held: &mut BTreeSet<u16>,
) -> Vec<u16> {
    if report.len() < 12 || report[0] != 0x11 || report[1..4] != [0xff, feature, 0] {
        return Vec::new();
    }
    let next: BTreeSet<_> = report[4..12]
        .chunks_exact(2)
        .map(|bytes| u16::from_be_bytes([bytes[0], bytes[1]]))
        .filter(|cid| assigned.contains(cid))
        .collect();
    let result = held.difference(&next).copied().collect();
    *held = next;
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_assigned_button_release_triggers_and_unrelated_reports_do_not_reset_state() {
        let assigned = BTreeSet::from([0x0053]);
        let mut held = BTreeSet::new();
        let mut report = [0u8; 20];
        report[..6].copy_from_slice(&[0x11, 0xff, 0x0a, 0, 0, 0x53]);
        assert!(released(&report, 0x0a, &assigned, &mut held).is_empty());
        assert!(released(&report, 0x0a, &assigned, &mut held).is_empty());
        assert!(released(&[0x02, 0, 0], 0x0a, &assigned, &mut held).is_empty());
        assert_eq!(held, assigned);
        report[5] = 0x56;
        assert_eq!(released(&report, 0x0a, &assigned, &mut held), [0x0053]);
        assert!(released(&report, 0x0a, &assigned, &mut held).is_empty());
        report[5] = 0;
        assert!(released(&report, 0x0a, &assigned, &mut held).is_empty());
    }

    #[test]
    fn simultaneous_dialpad_releases_keep_each_button_identity() {
        let assigned = BTreeSet::from([0x0053, 0x0056, 0x0059]);
        let mut held = BTreeSet::new();
        let mut report = [0u8; 20];
        report[..10].copy_from_slice(&[0x11, 0xff, 0x0a, 0, 0, 0x53, 0, 0x56, 0, 0x59]);
        assert!(released(&report, 0x0a, &assigned, &mut held).is_empty());
        report[5] = 0;
        report[9] = 0;
        assert_eq!(
            released(&report, 0x0a, &assigned, &mut held),
            [0x0053, 0x0059]
        );
        assert_eq!(held, BTreeSet::from([0x0056]));
        report[7] = 0;
        assert_eq!(released(&report, 0x0a, &assigned, &mut held), [0x0056]);
        assert!(released(&report, 0x0a, &assigned, &mut held).is_empty());
    }

    #[test]
    fn wheel_and_other_device_assignments_cannot_divert_controls() {
        let bindings = vec![
            DeviceBinding {
                vendor_id: VENDOR,
                product_id: PRODUCT,
                control_id: "button:3".into(),
            },
            DeviceBinding {
                vendor_id: VENDOR,
                product_id: PRODUCT,
                control_id: "wheel:roller".into(),
            },
            DeviceBinding {
                vendor_id: VENDOR,
                product_id: 0xc07e,
                control_id: "button:4".into(),
            },
        ];
        assert_eq!(controls(&bindings), BTreeSet::from([0x0059]));
    }

    #[test]
    fn only_complete_matching_hidpp_replies_acknowledge_assignment_changes() {
        let mut report = [0u8; 20];
        report[..4].copy_from_slice(&[0x11, 0xff, 0x0a, 0x3d]);
        assert!(response(&report, 0x0a, 0x3d).unwrap().is_ok());
        assert!(response(&report[..7], 0x0a, 0x3d).is_none());
        assert!(response(&report, 0x0b, 0x3d).is_none());
        assert!(response(&report, 0x0a, 0x3e).is_none());
        report[0] = 0x02;
        assert!(response(&report, 0x0a, 0x3d).is_none());
        report[..6].copy_from_slice(&[0x11, 0xff, 0xff, 0x0a, 0x3d, 0x09]);
        assert!(response(&report, 0x0a, 0x3d).unwrap().is_err());
        assert!(response(&report, 0x0a, 0x3e).is_none());
    }
}
