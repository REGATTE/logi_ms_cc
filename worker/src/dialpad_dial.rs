//! Native MultiRoller diversion for the large MX Creative Dialpad dial only.
//! Roller zero (the small roller) and reprogrammable buttons are never changed.
use crate::{
    dialpad_buttons,
    events::{Event, Sender},
};
use mx_creative_worker::config::DeviceBinding;
use serde_json::{json, Value};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::{
        fd::AsRawFd,
        unix::{fs::OpenOptionsExt, net::UnixStream},
    },
    path::Path,
    sync::{
        atomic::{AtomicUsize, Ordering},
        mpsc, Arc,
    },
    time::{Duration, Instant},
};

const MAIN_ROLLER: u8 = 1;
const SOFTWARE_ID: u8 = 0x0c; // Button handles use 0x0d on the same hidraw node.
const RETRY: Duration = Duration::from_secs(5);
const REQUEST_TIMEOUT: Duration = Duration::from_secs(1);
const RESTORE_TIMEOUT: Duration = Duration::from_millis(250);
const MAX_QUEUED_BURSTS: usize = 8;
const MAX_BURST_STEPS: i16 = 16;

/// Queue permits bound both pending reports and the number of injected shortcuts.
/// Each step is a complete press/release; a stale burst is dropped at dispatch.
pub struct Rotation {
    pub delta: i8,
    pub generation: u64,
    pub captured: Instant,
    pending: Arc<AtomicUsize>,
}

impl Drop for Rotation {
    fn drop(&mut self) {
        self.pending.fetch_sub(1, Ordering::AcqRel);
    }
}

fn queue_rotation(sender: &Sender, pending: &Arc<AtomicUsize>, delta: i8, generation: u64) {
    let captured = Instant::now();
    let mut remaining = i16::from(delta);
    while remaining != 0 {
        if pending
            .fetch_update(Ordering::AcqRel, Ordering::Acquire, |count| {
                (count < MAX_QUEUED_BURSTS).then_some(count + 1)
            })
            .is_err()
        {
            break;
        }
        let steps = remaining.clamp(-MAX_BURST_STEPS, MAX_BURST_STEPS);
        sender.send(Event::DeviceRotate(Rotation {
            delta: steps as i8,
            generation,
            captured,
            pending: pending.clone(),
        }));
        remaining -= steps;
    }
}

enum Command {
    Update(bool, u64),
    Stop,
}

pub struct Handle {
    commands: mpsc::Sender<Command>,
    wake: UnixStream,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Handle {
    pub fn update(&self, enabled: bool, generation: u64) {
        if self
            .commands
            .send(Command::Update(enabled, generation))
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

pub fn start(sender: Sender) -> Handle {
    let (wake, mut reader) = UnixStream::pair().expect("HID++ dial wake socket");
    wake.set_nonblocking(true).expect("HID++ dial wake socket");
    reader
        .set_nonblocking(true)
        .expect("HID++ dial wake socket");
    let (commands, incoming) = mpsc::channel();
    let thread = std::thread::spawn(move || run(sender, incoming, &mut reader));
    Handle {
        commands,
        wake,
        thread: Some(thread),
    }
}

fn status(active: bool, error: Option<&io::Error>) -> Value {
    let controls = if active {
        vec![DeviceBinding::main_dial()]
    } else {
        Vec::new()
    };
    let permission = error.is_some_and(|error| error.kind() == io::ErrorKind::PermissionDenied);
    let errors: Vec<_> = error.into_iter().map(|error| if permission {
        "MX Dialpad access is required. Open Settings → Permissions and allow device access.".into()
    } else { error.to_string() }).collect();
    json!({"available":error.is_none(),"backend":"hidpp","active":controls,
        "permission_required":permission,"errors":errors})
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct Recovery {
    original_mode: u8,
    identity: String,
}

fn restore_or_remember(device: &mut Dial, recovery: &mut Option<Recovery>) -> io::Result<()> {
    let result = device.restore();
    if let Some(original_mode) = device.original_mode.take() {
        *recovery = Some(Recovery {
            original_mode,
            identity: device.identity.clone(),
        });
    }
    result
}

fn close(device: &mut Option<Dial>, recovery: &mut Option<Recovery>) -> io::Result<()> {
    device.take().map_or(Ok(()), |mut device| {
        restore_or_remember(&mut device, recovery)
    })
}

fn discover() -> io::Result<std::path::PathBuf> {
    let path = dialpad_buttons::discover(dialpad_buttons::Target::Dialpad)?.ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::NotFound,
            "Connect or wake the MX Dialpad to use or restore its large dial.",
        )
    })?;
    if let Some(conflict) = crate::hardware::conflict(&path) {
        return Err(io::Error::other(conflict.replace("keypad", "MX Dialpad")));
    }
    Ok(path)
}

fn identity(path: &Path) -> io::Result<String> {
    let name = path
        .file_name()
        .ok_or_else(|| io::Error::other("Invalid MX Dialpad node."))?;
    let uevent = fs::read_to_string(
        Path::new("/sys/class/hidraw")
            .join(name)
            .join("device/uevent"),
    )?;
    uevent
        .lines()
        .find_map(|line| line.strip_prefix("HID_UNIQ="))
        .filter(|id| !id.is_empty())
        .map(str::to_string)
        .ok_or_else(|| io::Error::other("MX Dialpad Bluetooth identity is unavailable."))
}

fn recover(recovery: &Recovery) -> io::Result<()> {
    let path = discover()?;
    if identity(&path)? != recovery.identity {
        return Err(io::Error::other(
            "Reconnect the original MX Dialpad to restore its large dial mode.",
        ));
    }
    let mut dial = Dial::transport(&path)?;
    dial.find_feature()?;
    dial.original_mode = Some(recovery.original_mode);
    let result = dial.restore();
    // The caller retains the recovery record and retries even outside the website.
    dial.original_mode = None;
    result
}

fn run(sender: Sender, incoming: mpsc::Receiver<Command>, wake: &mut UnixStream) {
    let mut enabled = false;
    let mut generation = 0;
    let mut device: Option<Dial> = None;
    let mut recovery = None;
    let mut retry_at = Instant::now();
    let mut last_error = None;
    let mut previous = Value::Null;
    let pending = Arc::new(AtomicUsize::new(0));
    loop {
        while let Ok(command) = incoming.try_recv() {
            match command {
                Command::Stop => {
                    let _ = close(&mut device, &mut recovery);
                    if let Some(recovery) = &recovery {
                        if let Err(error) = recover(recovery) {
                            eprintln!("Restore MX Dialpad large dial on shutdown: {error}");
                        }
                    }
                    return;
                }
                Command::Update(next_enabled, next_generation) => {
                    if enabled != next_enabled || generation != next_generation {
                        if let Err(error) = close(&mut device, &mut recovery) {
                            last_error = Some(error);
                        }
                        enabled = next_enabled;
                        generation = next_generation;
                        retry_at = Instant::now();
                    }
                }
            }
        }
        if let Some(pending_restore) = recovery.as_ref().filter(|_| Instant::now() >= retry_at) {
            match recover(pending_restore) {
                Ok(()) => {
                    recovery = None;
                    last_error = None;
                }
                Err(error) => {
                    last_error = Some(error);
                    retry_at = Instant::now() + RETRY;
                }
            }
        }
        if enabled && recovery.is_none() && device.is_none() && Instant::now() >= retry_at {
            let opened = discover().and_then(|path| Dial::open(&path, &mut recovery));
            match opened {
                Ok(opened) => {
                    device = Some(opened);
                    last_error = None;
                }
                Err(error) => {
                    last_error = Some(error);
                    retry_at = Instant::now() + RETRY;
                }
            }
        }
        let next = status(device.is_some(), last_error.as_ref());
        if next != previous {
            previous = next.clone();
            sender.send(Event::DialpadDialStatus(next));
        }
        let timeout = if recovery.is_some() || enabled && device.is_none() {
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
                fd: device.as_ref().map_or(-1, |dial| dial.file.as_raw_fd()),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: Both entries are valid for this bounded/interruptible poll; -1 is ignored.
        if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as _, timeout) } < 0 {
            let error = io::Error::last_os_error();
            if error.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            sender.send(Event::DialpadDialStatus(status(false, Some(&error))));
            return;
        }
        if fds[0].revents != 0 {
            let mut bytes = [0u8; 128];
            while wake.read(&mut bytes).is_ok_and(|count| count > 0) {}
            continue; // Apply disable/context changes before reading more input.
        }
        if fds[1].revents != 0 {
            let received = if fds[1].revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0
            {
                Err(io::Error::new(
                    io::ErrorKind::NotConnected,
                    "MX Dialpad disconnected.",
                ))
            } else {
                device
                    .as_mut()
                    .expect("polled dial")
                    .receive(&sender, &pending, generation)
            };
            if let Err(error) = received {
                let _ = close(&mut device, &mut recovery);
                last_error = Some(error);
                retry_at = Instant::now() + RETRY;
            }
        }
    }
}

struct Dial {
    file: File,
    feature: u8,
    original_mode: Option<u8>,
    identity: String,
}

impl Drop for Dial {
    fn drop(&mut self) {
        if let Err(error) = self.restore() {
            eprintln!("Restore MX Dialpad large dial: {error}");
        }
    }
}

impl Dial {
    fn transport(path: &Path) -> io::Result<Self> {
        let identity = identity(path)?;
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(path)?;
        Ok(Self {
            file,
            feature: 0,
            original_mode: None,
            identity,
        })
    }

    fn open(path: &Path, recovery: &mut Option<Recovery>) -> io::Result<Self> {
        let mut dial = Self::transport(path)?;
        if let Err(error) = dial.initialise() {
            let _ = restore_or_remember(&mut dial, recovery);
            return Err(error);
        }
        Ok(dial)
    }

    fn find_feature(&mut self) -> io::Result<()> {
        self.feature = self.request(0, 0, &[0x46, 0x10, 0], REQUEST_TIMEOUT)?[4];
        if self.feature == 0 {
            return Err(io::Error::other("MX Dialpad has no MultiRoller feature."));
        }
        Ok(())
    }

    fn initialise(&mut self) -> io::Result<()> {
        let dial = self;
        dial.find_feature()?;
        let count = dial.request(dial.feature, 0, &[], REQUEST_TIMEOUT)?[4] & 0x0f;
        if count <= MAIN_ROLLER {
            return Err(io::Error::other(
                "MX Dialpad did not report its large dial.",
            ));
        }
        let capabilities = dial.request(dial.feature, 1, &[MAIN_ROLLER], REQUEST_TIMEOUT)?;
        if capabilities[4] == 0 {
            return Err(io::Error::other(
                "MX Dialpad large dial has invalid increments per revolution.",
            ));
        }
        let original = dial.mode(REQUEST_TIMEOUT)?;
        dial.original_mode = Some(original); // Roll back even if setMode or verification fails.
        dial.request(
            dial.feature,
            3,
            &[MAIN_ROLLER, original | 1],
            REQUEST_TIMEOUT,
        )?;
        if dial.mode(REQUEST_TIMEOUT)? != original | 1 {
            return Err(io::Error::other(
                "MX Dialpad did not enable large dial diversion.",
            ));
        }
        // Configuration input has no current capture context and must never execute.
        for _ in 0..256 {
            if dial.read()?.is_none() {
                return Ok(());
            }
        }
        Err(io::Error::other(
            "MX Dialpad input did not settle after configuring its dial.",
        ))
    }

    fn mode(&mut self, timeout: Duration) -> io::Result<u8> {
        Ok(self.request(self.feature, 2, &[MAIN_ROLLER], timeout)?[4])
    }

    fn restore(&mut self) -> io::Result<()> {
        if let Some(original) = self.original_mode {
            self.request(self.feature, 3, &[MAIN_ROLLER, original], RESTORE_TIMEOUT)?;
            if self.mode(RESTORE_TIMEOUT)? != original {
                return Err(io::Error::other(
                    "MX Dialpad did not restore its large dial mode.",
                ));
            }
            self.original_mode = None;
        }
        Ok(())
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

    fn receive(
        &mut self,
        sender: &Sender,
        pending: &Arc<AtomicUsize>,
        generation: u64,
    ) -> io::Result<()> {
        // Bound time before checking pause/context commands, even on a noisy device.
        for _ in 0..256 {
            let Some(report) = self.read()? else {
                break;
            };
            if let Some(delta) = rotation(&report, self.feature) {
                queue_rotation(sender, pending, delta, generation);
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
        packet[..4].copy_from_slice(&[0x11, 0xff, feature, (function << 4) | SOFTWARE_ID]);
        packet[4..4 + payload.len()].copy_from_slice(payload);
        if self.file.write(&packet)? != packet.len() {
            return Err(io::Error::other("Partial MX Dialpad dial request."));
        }
        let deadline = Instant::now() + timeout;
        loop {
            if let Some(report) = self.read()? {
                if let Some(result) = response(&report, feature, packet[3]) {
                    result?;
                    return Ok(report);
                }
                // Ignore notifications and replies to the separate button handle.
            } else {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if !remaining.is_zero() {
                    let mut fd = libc::pollfd {
                        fd: self.file.as_raw_fd(),
                        events: libc::POLLIN,
                        revents: 0,
                    };
                    // SAFETY: fd remains open for this bounded poll.
                    if unsafe { libc::poll(&mut fd, 1, remaining.as_millis().min(1000) as i32) } < 0
                    {
                        let error = io::Error::last_os_error();
                        if error.kind() != io::ErrorKind::Interrupted {
                            return Err(error);
                        }
                    }
                }
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "Wake the MX Dialpad to apply or restore its large dial action.",
                ));
            }
        }
    }
}

fn response(report: &[u8], feature: u8, command: u8) -> Option<io::Result<()>> {
    if report.len() < 20 || report[0] != 0x11 || !matches!(report[1], 0x00 | 0xff) {
        return None;
    }
    if report[2..5] == [0xff, feature, command] {
        return Some(Err(io::Error::other(format!(
            "Device refused HID++ dial request ({:02x}).",
            report[5]
        ))));
    }
    (report[2..4] == [feature, command]).then_some(Ok(()))
}

fn rotation(report: &[u8], feature: u8) -> Option<i8> {
    if report.len() < 20
        || report[0] != 0x11
        || !matches!(report[1], 0x00 | 0xff)
        || report[2..4] != [feature, 0]
        || report[4] & 0x0f != MAIN_ROLLER
    {
        return None;
    }
    let delta = report[5] as i8;
    (delta != 0).then_some(delta)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::{fd::OwnedFd, unix::net::UnixDatagram};

    struct Step {
        feature: u8,
        function: u8,
        payload: Vec<u8>,
        reply: Result<Vec<u8>, u8>,
    }
    fn step(feature: u8, function: u8, payload: &[u8], reply: &[u8]) -> Step {
        Step {
            feature,
            function,
            payload: payload.into(),
            reply: Ok(reply.into()),
        }
    }
    fn setup(original: u8) -> Vec<Step> {
        vec![
            step(0, 0, &[0x46, 0x10, 0], &[0x13]),
            step(0x13, 0, &[], &[2]),
            step(0x13, 1, &[1], &[180, 0, 31, 0]),
            step(0x13, 2, &[1], &[original]),
            step(0x13, 3, &[1, original | 1], &[original | 1]),
            step(0x13, 2, &[1], &[original | 1]),
        ]
    }
    fn restore(original: u8) -> Vec<Step> {
        vec![
            step(0x13, 3, &[1, original], &[original]),
            step(0x13, 2, &[1], &[original]),
        ]
    }
    fn fake(steps: Vec<Step>) -> (Dial, std::thread::JoinHandle<()>) {
        let (client, server) = UnixDatagram::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        server
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        let thread = std::thread::spawn(move || {
            for step in steps {
                let mut packet = [0u8; 64];
                assert_eq!(server.recv(&mut packet).unwrap(), 20);
                assert_eq!(
                    &packet[..4],
                    &[0x11, 0xff, step.feature, (step.function << 4) | SOFTWARE_ID]
                );
                assert_eq!(&packet[4..4 + step.payload.len()], step.payload.as_slice());
                assert!(packet[4 + step.payload.len()..20]
                    .iter()
                    .all(|byte| *byte == 0));
                // Replies to the simultaneous button handle and small-roller input
                // must not acknowledge this request or produce a large dial action.
                let mut other = [0u8; 20];
                other[..4].copy_from_slice(&[0x11, 0, step.feature, (step.function << 4) | 0x0d]);
                server.send(&other).unwrap();
                let mut reply = [0u8; 20];
                reply[..4].copy_from_slice(&[0x11, 0, step.feature, packet[3]]);
                match step.reply {
                    Ok(payload) => reply[4..4 + payload.len()].copy_from_slice(&payload),
                    Err(code) => {
                        reply[2..6].copy_from_slice(&[0xff, step.feature, packet[3], code])
                    }
                }
                server.send(&reply).unwrap();
            }
        });
        let fd: OwnedFd = client.into();
        (
            Dial {
                file: fd.into(),
                feature: 0,
                original_mode: None,
                identity: "test-device".into(),
            },
            thread,
        )
    }

    #[test]
    fn only_large_roller_signed_notifications_are_accepted() {
        let mut packet = [0u8; 20];
        packet[..6].copy_from_slice(&[0x11, 0, 0x13, 0, 1, 3]);
        assert_eq!(rotation(&packet, 0x13), Some(3));
        packet[5] = (-128i8) as u8;
        assert_eq!(rotation(&packet, 0x13), Some(-128));
        packet[4] = 0;
        assert_eq!(rotation(&packet, 0x13), None);
        packet[4] = 1;
        packet[3] = SOFTWARE_ID;
        assert_eq!(rotation(&packet, 0x13), None);
        packet[3] = 0;
        packet[1] = 2;
        assert_eq!(rotation(&packet, 0x13), None);
        packet[1] = 0xff;
        assert_eq!(rotation(&packet[..19], 0x13), None);
        assert_eq!(rotation(&packet, 0x14), None);
        packet[5] = 0;
        assert_eq!(rotation(&packet, 0x13), None);
    }

    #[test]
    fn configuration_and_drop_restore_exact_mode_without_touching_small_roller() {
        for original in [0, 1, 0x80, 0x81] {
            let mut steps = setup(original);
            steps.extend(restore(original));
            let (mut dial, server) = fake(steps);
            dial.initialise().unwrap();
            assert_eq!(dial.original_mode, Some(original));
            drop(dial);
            server.join().unwrap();
        }
    }

    #[test]
    fn rejected_or_unverified_diversion_rolls_back() {
        for reject in [true, false] {
            let mut steps = setup(0);
            if reject {
                steps.truncate(5);
                steps[4].reply = Err(0x02);
            } else {
                steps[5].reply = Ok(vec![0]);
            }
            steps.extend(restore(0));
            let (mut dial, server) = fake(steps);
            assert!(dial.initialise().is_err());
            let mut recovery = None;
            restore_or_remember(&mut dial, &mut recovery).unwrap();
            assert!(recovery.is_none());
            drop(dial);
            server.join().unwrap();
        }
    }

    #[test]
    fn failed_restore_remembers_original_until_recovery_then_reacquires_cleanly() {
        let mut steps = setup(0);
        let mut rejected = step(0x13, 3, &[1, 0], &[]);
        rejected.reply = Err(0x02);
        steps.push(rejected);
        let (mut dial, server) = fake(steps);
        dial.initialise().unwrap();
        let mut recovery = None;
        assert!(restore_or_remember(&mut dial, &mut recovery).is_err());
        assert_eq!(
            recovery,
            Some(Recovery {
                original_mode: 0,
                identity: "test-device".into()
            })
        );
        assert_eq!(
            dial.original_mode, None,
            "Drop must not lose/rewrite the recovery record"
        );
        drop(dial);
        server.join().unwrap();

        // Reopen restores the saved zero, even though the hardware was left at one.
        let (mut recovered, server) = fake(restore(0));
        recovered.feature = 0x13;
        recovered.original_mode = Some(recovery.as_ref().unwrap().original_mode);
        recovered.restore().unwrap();
        assert_eq!(recovered.original_mode, None);
        drop(recovered);
        server.join().unwrap();
        let mut steps = setup(0);
        steps.extend(restore(0));
        let (mut dial, server) = fake(steps);
        dial.initialise().unwrap();
        assert_eq!(dial.original_mode, Some(0));
        drop(dial);
        server.join().unwrap();
    }

    #[test]
    fn disconnect_preserves_a_pending_restore_and_eof_is_an_error() {
        let (client, server) = UnixStream::pair().unwrap();
        client.set_nonblocking(true).unwrap();
        drop(server);
        let fd: OwnedFd = client.into();
        let mut dial = Dial {
            file: fd.into(),
            feature: 0x13,
            original_mode: Some(0),
            identity: "test-device".into(),
        };
        assert_eq!(
            dial.read().unwrap_err().kind(),
            io::ErrorKind::UnexpectedEof
        );
        let mut recovery = None;
        assert!(restore_or_remember(&mut dial, &mut recovery).is_err());
        assert_eq!(recovery.unwrap().original_mode, 0);
    }

    #[test]
    fn queue_is_bounded_and_signed_minimum_splits_without_overflow() {
        let (tx, rx) = mpsc::channel();
        let (wake, _reader) = UnixStream::pair().unwrap();
        wake.set_nonblocking(true).unwrap();
        let sender = Sender::new(tx, wake);
        let pending = Arc::new(AtomicUsize::new(0));
        queue_rotation(&sender, &pending, -128, 42);
        queue_rotation(&sender, &pending, 127, 42);
        let mut sum = 0i16;
        let mut held = Vec::new();
        while let Ok(Event::DeviceRotate(rotation)) = rx.try_recv() {
            assert_eq!(rotation.generation, 42);
            assert_eq!(rotation.delta, -16);
            sum += i16::from(rotation.delta);
            held.push(rotation);
        }
        assert_eq!(sum, -128);
        assert_eq!(held.len(), MAX_QUEUED_BURSTS);
        assert_eq!(pending.load(Ordering::Acquire), MAX_QUEUED_BURSTS);
        held.clear();
        assert_eq!(pending.load(Ordering::Acquire), 0);
        queue_rotation(&sender, &pending, 1, 43);
        assert!(
            matches!(rx.try_recv(),Ok(Event::DeviceRotate(rotation)) if rotation.delta==1 && rotation.generation==43)
        );
        assert_eq!(pending.load(Ordering::Acquire), 0);
    }
}
