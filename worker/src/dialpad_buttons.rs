//! Model-scoped HID++ diversion for MX Dialpad buttons and the MX Master 4 panel.
//!
//! Only assigned controls have their temporary diversion bit changed. Persistent
//! mappings, wheel reports, and the user's existing evdev remapper are untouched.
//! Normal shutdown/pause restores the previous temporary diversion bit; a forced
//! kill or unplug may require cycling the device's power to reset that bit.

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
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Target {
    Dialpad,
    Master4,
}

impl Target {
    fn product(self) -> u16 {
        match self {
            Self::Dialpad => 0xbc00,
            Self::Master4 => 0xb042,
        }
    }

    fn name(self) -> &'static str {
        match self {
            Self::Dialpad => "MX Dialpad",
            Self::Master4 => "MX Master 4",
        }
    }

    fn controls(self) -> &'static [(u16, &'static str)] {
        match self {
            Self::Dialpad => &[
                (0x0053, "button:1"),
                (0x0056, "button:2"),
                (0x0059, "button:3"),
                (0x005a, "button:4"),
            ],
            Self::Master4 => &[(0x01a0, "hidpp:416")],
        }
    }

    fn binding(self, cid: u16) -> Option<DeviceBinding> {
        self.controls()
            .iter()
            .find(|(value, _)| *value == cid)
            .map(|(_, identifier)| DeviceBinding {
                vendor_id: VENDOR,
                product_id: self.product(),
                control_id: (*identifier).into(),
            })
    }

    fn event(self, value: Value) -> Event {
        match self {
            Self::Dialpad => Event::DialpadButtonsStatus(value),
            Self::Master4 => Event::Master4ButtonsStatus(value),
        }
    }
}
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

pub fn start(sender: Sender, bindings: Vec<DeviceBinding>, target: Target) -> Handle {
    let (wake, mut reader) = UnixStream::pair().expect("HID++ button wake socket");
    wake.set_nonblocking(true)
        .expect("HID++ button wake socket");
    reader
        .set_nonblocking(true)
        .expect("HID++ button wake socket");
    let (commands, incoming) = mpsc::channel();
    let thread = std::thread::spawn(move || run(sender, bindings, incoming, &mut reader, target));
    Handle {
        commands,
        wake,
        thread: Some(thread),
    }
}

fn controls(bindings: &[DeviceBinding], target: Target) -> BTreeSet<u16> {
    bindings
        .iter()
        .filter(|binding| binding.vendor_id == VENDOR && binding.product_id == target.product())
        .filter_map(|binding| {
            target
                .controls()
                .iter()
                .find(|(_, identifier)| *identifier == binding.control_id)
                .map(|(cid, _)| *cid)
        })
        .collect()
}

fn status(
    target: Target,
    assigned: &BTreeSet<u16>,
    active: bool,
    error: Option<&str>,
    permission: bool,
) -> Value {
    let controls: Vec<_> = if active {
        assigned
            .iter()
            .filter_map(|cid| target.binding(*cid))
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
    target: Target,
) {
    let mut assigned = controls(&bindings, target);
    let mut paused = false;
    let mut device: Option<Buttons> = None;
    let mut retry_at = Instant::now();
    let mut previous = Value::Null;
    let mut report = |value: Value| {
        if value != previous {
            previous = value.clone();
            sender.send(target.event(value));
        }
    };
    loop {
        while let Ok(command) = incoming.try_recv() {
            match command {
                Command::Stop => return, // Drop restores the temporary diversion bits.
                Command::Update(bindings, next_paused) => {
                    let next = controls(&bindings, target);
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
            report(status(target, &assigned, false, None, false));
        } else if device.is_none() && Instant::now() >= retry_at {
            let result = discover(target).and_then(|path| {
                let path = path.ok_or_else(|| {
                    io::Error::new(
                        io::ErrorKind::NotFound,
                        format!(
                            "Connect or wake the {} to use its assigned buttons.",
                            target.name()
                        ),
                    )
                })?;
                if let Some(conflict) = crate::hardware::conflict(&path) {
                    return Err(io::Error::other(conflict.replace("keypad", target.name())));
                }
                Buttons::open(&path, &assigned, target)
            });
            match result {
                Ok(opened) => {
                    device = Some(opened);
                    report(status(target, &assigned, true, None, false));
                }
                Err(error) => {
                    let permission = error.kind() == io::ErrorKind::PermissionDenied;
                    let message = if permission {
                        format!("{} access is required. Open Settings → Permissions and allow device access.", target.name())
                    } else {
                        error.to_string()
                    };
                    report(status(target, &assigned, false, Some(&message), permission));
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
            report(status(
                target,
                &assigned,
                false,
                Some(&error.to_string()),
                false,
            ));
            return;
        }
        let mut bytes = [0u8; 128];
        while wake.read(&mut bytes).is_ok_and(|count| count > 0) {}
        if fds[1].revents != 0 {
            let result = device.as_mut().map(|device| device.receive(&sender));
            if let Some(Err(error)) = result {
                report(status(
                    target,
                    &assigned,
                    false,
                    Some(&error.to_string()),
                    false,
                ));
                device = None;
                retry_at = Instant::now() + RETRY;
            }
        }
    }
}

pub(super) fn discover(target: Target) -> io::Result<Option<PathBuf>> {
    let Ok(entries) = fs::read_dir("/sys/class/hidraw") else {
        return Ok(None);
    };
    let mut candidates = Vec::new();
    for entry in entries.flatten() {
        let device = entry.path().join("device");
        let uevent = fs::read_to_string(device.join("uevent")).unwrap_or_default();
        if uevent.lines().any(|line| {
            line.strip_prefix("HID_ID=").is_some_and(|id| {
                id.eq_ignore_ascii_case(&format!("0005:0000046d:0000{:04x}", target.product()))
            })
        }) {
            candidates.push(Path::new("/dev").join(entry.file_name()));
        }
    }
    if candidates.len() > 1 {
        return Err(io::Error::other(format!(
            "More than one {} is connected; connect one to assign its buttons.",
            target.name()
        )));
    }
    Ok(candidates.pop())
}

struct Buttons {
    target: Target,
    file: File,
    feature: u8,
    original: Vec<(u16, bool)>,
    assigned: BTreeSet<u16>,
    held: BTreeSet<u16>,
}

impl Drop for Buttons {
    fn drop(&mut self) {
        for (cid, diverted) in std::mem::take(&mut self.original) {
            let [hi, lo] = cid.to_be_bytes();
            if let Err(error) = self.request(
                self.feature,
                3,
                &[hi, lo, 2 | u8::from(diverted), 0, 0],
                Duration::from_millis(250),
            ) {
                eprintln!("Restore {} button {cid:04x}: {error}", self.target.name());
            }
        }
    }
}

impl Buttons {
    fn open(path: &Path, assigned: &BTreeSet<u16>, target: Target) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(path)?;
        Self::configure(file, assigned, target)
    }

    fn configure(file: File, assigned: &BTreeSet<u16>, target: Target) -> io::Result<Self> {
        let mut device = Self {
            target,
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
            return Err(io::Error::other(format!(
                "{} has no reprogrammable button feature.",
                target.name()
            )));
        }
        let count = device.request(device.feature, 0, &[], timeout)?[4];
        if count > 32 {
            return Err(io::Error::other(format!(
                "Unexpected {} control count.",
                target.name()
            )));
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
                return Err(io::Error::other(format!(
                    "An assigned {} button does not support temporary diversion.",
                    target.name()
                )));
            }
            let [hi, lo] = cid.to_be_bytes();
            let current = device.request(device.feature, 2, &[hi, lo], timeout)?;
            if current[4..6] != [hi, lo] {
                return Err(io::Error::other(format!(
                    "Unexpected {} control response.",
                    target.name()
                )));
            }
            device.original.push((*cid, current[6] & 1 != 0));
            let changed = device.request(device.feature, 3, &[hi, lo, 3, 0, 0], timeout)?;
            if changed[4..7] != [hi, lo, 3] {
                return Err(io::Error::other(format!(
                    "{} did not confirm its temporary button assignment.",
                    target.name()
                )));
            }
        }
        device.held.clear(); // A press during setup cannot execute on a later release.
        Ok(device)
    }

    fn read(&mut self) -> io::Result<Option<Vec<u8>>> {
        let mut bytes = [0u8; 64];
        match self.file.read(&mut bytes) {
            Ok(0) => Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                format!("{} disconnected.", self.target.name()),
            )),
            Ok(count) => Ok(Some(bytes[..count].to_vec())),
            Err(error) if error.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(error) => Err(error),
        }
    }

    fn receive(&mut self, sender: &Sender) -> io::Result<()> {
        while let Some(report) = self.read()? {
            for control in released(&report, self.feature, &self.assigned, &mut self.held) {
                if let Some(binding) = self.target.binding(control) {
                    sender.send(Event::DeviceButton(binding));
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
            return Err(io::Error::other(format!(
                "Partial {} HID++ request.",
                self.target.name()
            )));
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
                        format!(
                            "Wake the {} to apply its button actions.",
                            self.target.name()
                        ),
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
                    format!(
                        "{} did not acknowledge the button request.",
                        self.target.name()
                    ),
                ));
            }
        }
    }
}

fn response(report: &[u8], feature: u8, command: u8) -> Option<io::Result<()>> {
    // Both supported devices reply to these requests with a 20-byte HID++ long report.
    // Mouse, wheel, truncated and other software clients' reports may be on
    // the same hidraw stream, and cannot acknowledge a configuration change.
    // Discovery accepts only a direct Bluetooth device, whose response address
    // may be either the requested 0xff or 0x00 (never a receiver slot).
    if report.len() < 20 || report[0] != 0x11 || !matches!(report[1], 0x00 | 0xff) {
        return None;
    }
    if report[2..5] == [0xff, feature, command] {
        return Some(Err(io::Error::other(format!(
            "Device refused HID++ button request ({:02x}).",
            report[5]
        ))));
    }
    (report[2..4] == [feature, command]).then_some(Ok(()))
}

fn released(
    report: &[u8],
    feature: u8,
    assigned: &BTreeSet<u16>,
    held: &mut BTreeSet<u16>,
) -> Vec<u16> {
    if report.len() < 12
        || report[0] != 0x11
        || !matches!(report[1], 0x00 | 0xff)
        || report[2..4] != [feature, 0]
    {
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
    use std::os::{fd::OwnedFd, unix::net::UnixDatagram};

    // A packet-preserving fake hidraw transport verifies the actual configure
    // and Drop requests, including rollback after a rejected assignment.
    fn fake_master4(
        original_flags: u8,
        reject_assignment: bool,
    ) -> (File, std::thread::JoinHandle<()>) {
        let (client, server) = UnixDatagram::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        server
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let thread = std::thread::spawn(move || {
            let steps: &[(u8, u8, &[u8], &[u8])] = &[
                (0, 0, &[0x1b, 0x04, 0], &[0x0d]),
                (0x0d, 0, &[], &[1]),
                (0x0d, 1, &[0], &[0x01, 0xa0, 0, 0, 0x31]),
                (0x0d, 2, &[0x01, 0xa0], &[0x01, 0xa0, original_flags]),
                (0x0d, 3, &[0x01, 0xa0, 3, 0, 0], &[0x01, 0xa0, 3]),
                (
                    0x0d,
                    3,
                    &[0x01, 0xa0, 2 | (original_flags & 1), 0, 0],
                    &[0x01, 0xa0, 2 | (original_flags & 1)],
                ),
            ];
            for (index, (feature, function, payload, data)) in steps.iter().enumerate() {
                let mut received = [0; 64];
                assert_eq!(server.recv(&mut received).unwrap(), 20);
                let mut expected = [0; 20];
                expected[..4].copy_from_slice(&[0x11, 0xff, *feature, (function << 4) | 0x0d]);
                expected[4..4 + payload.len()].copy_from_slice(payload);
                assert_eq!(&received[..20], expected, "request {index}");
                let mut reply = [0; 20];
                reply[..4].copy_from_slice(&[0x11, 0, *feature, (function << 4) | 0x0d]);
                reply[4..4 + data.len()].copy_from_slice(data);
                if index == 4 {
                    if reject_assignment {
                        reply[..6].copy_from_slice(&[0x11, 0, 0xff, *feature, 0x3d, 9]);
                    } else {
                        // The press arriving during setup must not execute later.
                        let mut press = [0; 20];
                        press[..6].copy_from_slice(&[0x11, 0, 0x0d, 0, 0x01, 0xa0]);
                        server.send(&press).unwrap();
                    }
                }
                server.send(&reply).unwrap();
            }
        });
        (File::from(OwnedFd::from(client)), thread)
    }

    #[test]
    fn master4_assignment_and_drop_only_modify_temporary_diversion() {
        for original in [0x50, 0x51] {
            let (file, thread) = fake_master4(original, false);
            let device =
                Buttons::configure(file, &BTreeSet::from([0x01a0]), Target::Master4).unwrap();
            assert!(device.held.is_empty());
            drop(device);
            thread.join().unwrap();
        }
    }

    #[test]
    fn failed_master4_assignment_restores_original_reporting() {
        let (file, thread) = fake_master4(0x50, true);
        let result = Buttons::configure(file, &BTreeSet::from([0x01a0]), Target::Master4);
        assert!(result.is_err());
        thread.join().unwrap();
    }

    #[test]
    fn master4_panel_release_is_once_and_other_features_addresses_and_cids_are_ignored() {
        let assigned = BTreeSet::from([0x01a0]);
        let mut held = BTreeSet::new();
        let mut report = [0; 20];
        report[..6].copy_from_slice(&[0x11, 0, 0x0d, 0, 0x01, 0xa0]);
        assert!(released(&report, 0x0d, &assigned, &mut held).is_empty());
        report[5] = 0xc4;
        for (address, feature, function) in [(1, 0x0d, 0), (0, 0x0e, 0), (0, 0x0d, 1)] {
            report[1..4].copy_from_slice(&[address, feature, function]);
            assert!(released(&report, 0x0d, &assigned, &mut held).is_empty());
            assert_eq!(held, assigned);
        }
        report[1..4].copy_from_slice(&[0xff, 0x0d, 0]);
        assert_eq!(released(&report, 0x0d, &assigned, &mut held), [0x01a0]);
        assert!(released(&report, 0x0d, &assigned, &mut held).is_empty());
        let binding = Target::Master4.binding(0x01a0).unwrap();
        assert_eq!(binding.product_id, 0xb042);
        assert_eq!(binding.control_id, "hidpp:416");
        assert!(Target::Dialpad.binding(0x01a0).is_none());
        assert!(Target::Master4.binding(0x00c4).is_none());
        assert_eq!(
            controls(std::slice::from_ref(&binding), Target::Master4),
            assigned
        );
        assert!(controls(&[binding], Target::Dialpad).is_empty());
    }

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
                product_id: Target::Dialpad.product(),
                control_id: "button:3".into(),
            },
            DeviceBinding {
                vendor_id: VENDOR,
                product_id: Target::Dialpad.product(),
                control_id: "wheel:roller".into(),
            },
            DeviceBinding {
                vendor_id: VENDOR,
                product_id: 0xc07e,
                control_id: "button:4".into(),
            },
        ];
        assert_eq!(
            controls(&bindings, Target::Dialpad),
            BTreeSet::from([0x0059])
        );
    }

    #[test]
    fn only_complete_matching_hidpp_replies_acknowledge_assignment_changes() {
        let mut report = [0u8; 20];
        report[..4].copy_from_slice(&[0x11, 0xff, 0x0a, 0x3d]);
        assert!(response(&report, 0x0a, 0x3d).unwrap().is_ok());
        report[1] = 0;
        assert!(response(&report, 0x0a, 0x3d).unwrap().is_ok());
        report[1] = 1;
        assert!(response(&report, 0x0a, 0x3d).is_none());
        report[1] = 0xff;
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
