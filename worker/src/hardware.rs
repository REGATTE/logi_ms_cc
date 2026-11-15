use mx_creative_worker::protocol::{self, Edges, Press, PAGE_CONTROLS};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, Read, Write},
    os::{fd::AsRawFd, unix::fs::OpenOptionsExt},
    path::{Path, PathBuf},
    time::{Duration, Instant},
};

pub struct Keypad {
    file: File,
    feature: Option<u8>,
    originals: Vec<(u16, bool)>,
    edges: Edges,
    pub pending: Vec<Press>,
    displayed: [Option<String>; 9],
    next_draw: Instant,
    busy_since: Option<Instant>,
    busy_image: Option<(usize, String)>,
}

impl Drop for Keypad {
    fn drop(&mut self) {
        self.restore();
    }
}

pub fn discover() -> io::Result<Option<PathBuf>> {
    let Ok(entries) = fs::read_dir("/sys/class/hidraw") else {
        return Ok(None);
    };
    for entry in entries.flatten() {
        let device = entry.path().join("device");
        let uevent = fs::read_to_string(device.join("uevent")).unwrap_or_default();
        let matches = uevent
            .lines()
            .find_map(|l| l.strip_prefix("HID_ID="))
            .map(|s| s.to_ascii_lowercase().ends_with(":0000046d:0000c354"))
            .unwrap_or(false);
        if !matches {
            continue;
        }
        let descriptor = fs::read(device.join("report_descriptor"))?;
        // The ff43 HID++ interface contains the vendor output report 0x14.
        if descriptor.windows(3).any(|w| w == [0x06, 0x43, 0xff])
            && descriptor.windows(2).any(|w| w == [0x85, 0x14])
        {
            return Ok(Some(Path::new("/dev").join(entry.file_name())));
        }
    }
    Ok(None)
}

/// Check only known competing managers which actually hold this keypad node.
/// A running Solaar instance controlling some other mouse is not a conflict.
pub fn conflict(path: &Path) -> Option<String> {
    conflict_in(Path::new("/proc"), path)
}

fn conflict_in(proc_root: &Path, path: &Path) -> Option<String> {
    let processes = fs::read_dir(proc_root).ok()?;
    for entry in processes.flatten() {
        let name = entry.file_name();
        let pid = name.to_str().and_then(|name| name.parse::<u32>().ok());
        if pid.is_none() || pid == Some(std::process::id()) {
            continue;
        }
        let cmdline = fs::read(entry.path().join("cmdline")).unwrap_or_default();
        let manager = cmdline.split(|b| *b == 0).take(2).find_map(|arg| {
            let text = std::str::from_utf8(arg).ok()?;
            let program = Path::new(text).file_name()?.to_str()?;
            match program {
                "openlogi-agent" => Some("OpenLogi"),
                "solaar" => Some("Solaar"),
                _ => None,
            }
        });
        let Some(manager) = manager else { continue };
        let Ok(fds) = fs::read_dir(entry.path().join("fd")) else {
            continue;
        };
        if fds
            .flatten()
            .any(|fd| fs::read_link(fd.path()).is_ok_and(|target| target == path))
        {
            return Some(format!("{manager} (PID {}) is using the keypad. Quit that manager before connecting MX Creative Control.",pid.unwrap()));
        }
    }
    None
}

impl Keypad {
    pub fn open(path: &Path) -> io::Result<Self> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
            .open(path)?;
        Ok(Self {
            file,
            feature: None,
            originals: Vec::new(),
            edges: Edges::default(),
            pending: Vec::new(),
            displayed: Default::default(),
            next_draw: Instant::now(),
            busy_since: None,
            busy_image: None,
        })
    }
    pub fn fd(&self) -> libc::c_int {
        self.file.as_raw_fd()
    }
    pub fn initialise(&mut self) -> io::Result<()> {
        let reply = self.request(0, 0, &[0x1b, 0x04, 0])?;
        if reply[4] == 0 {
            return Err(io::Error::other(
                "keypad has no reprogrammable button feature",
            ));
        }
        self.feature = Some(reply[4]);
        for cid in PAGE_CONTROLS {
            let [hi, lo] = cid.to_be_bytes();
            let current = self.request(reply[4], 2, &[hi, lo, 0])?;
            if current[4..6] != [hi, lo] {
                return Err(io::Error::other("unexpected navigation control response"));
            }
            self.originals.push((cid, current[6] & 1 != 0));
            self.request(reply[4], 3, &[hi, lo, 3, 0, 0, 0])?;
        }
        Ok(())
    }
    pub fn restore(&mut self) {
        if let Some(feature) = self.feature {
            for (cid, diverted) in std::mem::take(&mut self.originals) {
                let [hi, lo] = cid.to_be_bytes();
                if let Err(e) = self.request(feature, 3, &[hi, lo, 2 | u8::from(diverted), 0, 0, 0])
                {
                    eprintln!("restore navigation button: {e}");
                }
            }
        }
    }
    fn write(&mut self, bytes: &[u8]) -> io::Result<()> {
        let start = Instant::now();
        loop {
            match self.file.write(bytes) {
                Ok(n) if n == bytes.len() => return Ok(()),
                Ok(_) => return Err(io::Error::other("partial HID output report")),
                Err(e)
                    if e.kind() == io::ErrorKind::WouldBlock
                        && start.elapsed() < Duration::from_secs(2) =>
                {
                    poll(self.fd(), libc::POLLOUT, 100)?;
                }
                Err(e) => return Err(e),
            }
        }
    }
    fn read(&mut self) -> io::Result<Option<Vec<u8>>> {
        let mut bytes = [0u8; 4096];
        match self.file.read(&mut bytes) {
            Ok(0) => Err(io::Error::new(
                io::ErrorKind::UnexpectedEof,
                "keypad disconnected",
            )),
            Ok(n) => Ok(Some(bytes[..n].to_vec())),
            Err(e) if e.kind() == io::ErrorKind::WouldBlock => Ok(None),
            Err(e) => Err(e),
        }
    }
    pub fn receive(&mut self) -> io::Result<()> {
        while let Some(report) = self.read()? {
            self.pending
                .extend(self.edges.report(&report, self.feature));
        }
        Ok(())
    }
    fn wait_report(
        &mut self,
        mut predicate: impl FnMut(&[u8]) -> Option<io::Result<()>>,
    ) -> io::Result<Vec<u8>> {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "keypad did not acknowledge request",
                ));
            }
            if let Some(report) = self.read()? {
                if let Some(result) = predicate(&report) {
                    result?;
                    return Ok(report);
                }
                self.pending
                    .extend(self.edges.report(&report, self.feature));
            } else {
                let remaining = deadline.saturating_duration_since(Instant::now());
                if remaining.is_zero() {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "keypad did not acknowledge request",
                    ));
                }
                poll(
                    self.fd(),
                    libc::POLLIN,
                    remaining.as_millis().min(2000) as i32,
                )?;
            }
        }
    }
    fn request(&mut self, feature: u8, function: u8, payload: &[u8]) -> io::Result<Vec<u8>> {
        let mut packet = [0u8; 20];
        packet[..4].copy_from_slice(&[0x11, 0xff, feature, (function << 4) | 0x0e]);
        packet[4..4 + payload.len()].copy_from_slice(payload);
        self.write(&packet)?;
        self.wait_report(|r| {
            if r.len() >= 7 && r[1] == 0xff && r[2] == 0xff && r[3] == feature && r[4] == packet[3]
            {
                return Some(Err(io::Error::other(format!(
                    "HID++ request refused ({:02x})",
                    r[5]
                ))));
            }
            (r.len() >= 7 && r[1] == 0xff && r[2] == feature && r[3] == packet[3]).then_some(Ok(()))
        })
    }
    pub fn invalidate(&mut self) {
        self.displayed = Default::default();
    }
    pub fn draw_delay_ms(&self) -> i32 {
        let delay = self.next_draw.saturating_duration_since(Instant::now());
        if delay.is_zero() {
            0
        } else {
            delay.as_millis().saturating_add(1).min(1000) as i32
        }
    }

    /// Send at most one changed tile. Returning to the event loop between
    /// tiles coalesces queued page/focus changes before starting another image.
    /// False means there is work remaining (including a bounded busy retry).
    pub fn display_next(
        &mut self,
        keys: &[Option<mx_creative_worker::config::Key>],
        cache: &Path,
    ) -> io::Result<bool> {
        let Some((slot, key)) = keys.iter().enumerate().find(|(slot, key)| {
            self.displayed[*slot].as_deref()
                != Some(key.as_ref().map(|k| k.image.as_str()).unwrap_or("__blank"))
        }) else {
            self.busy_since = None;
            self.busy_image = None;
            return Ok(true);
        };
        let name = key.as_ref().map(|k| k.image.as_str()).unwrap_or("__blank");
        if self
            .busy_image
            .as_ref()
            .is_none_or(|(old_slot, old_name)| *old_slot != slot || old_name != name)
        {
            self.busy_since = None;
            self.busy_image = Some((slot, name.into()));
        }
        let jpeg = if let Some(key) = key {
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(cache.join(&key.image))?;
            let mut bytes = Vec::new();
            file.take(65536).read_to_end(&mut bytes)?;
            bytes
        } else {
            include_bytes!("blank.jpg").to_vec()
        };
        let packets = protocol::image_packets(slot, &jpeg).map_err(io::Error::other)?;
        for packet in packets {
            self.write(&packet)?;
            // The protocol acknowledges every fragment; sending the next
            // before that ACK can overflow its image-assembly queue.
            let report = self.wait_report(|r| protocol::image_ack(r, packet[4]).map(|_| Ok(())))?;
            match protocol::image_ack(&report, packet[4]).unwrap() {
                protocol::ImageAck::Accepted => {}
                protocol::ImageAck::Busy => {
                    let now = Instant::now();
                    let since = *self.busy_since.get_or_insert(now);
                    if now.duration_since(since) >= Duration::from_secs(1) {
                        self.busy_since = None;
                        return Err(io::Error::new(
                            io::ErrorKind::TimedOut,
                            "keypad image queue stayed busy (fb 00) for one second",
                        ));
                    }
                    self.next_draw = now + Duration::from_millis(10);
                    return Ok(false); // Retry the whole image on a later turn.
                }
                protocol::ImageAck::Rejected([a, b]) => {
                    return Err(io::Error::other(format!(
                        "keypad rejected image: {a:02x} {b:02x}"
                    )))
                }
            }
        }
        self.displayed[slot] = Some(name.into());
        self.busy_since = None;
        self.busy_image = None;
        // Only active updates are paced; idle workers still block indefinitely.
        self.next_draw = Instant::now() + Duration::from_millis(8);
        Ok(false)
    }
}

pub fn poll(fd: libc::c_int, events: libc::c_short, timeout: i32) -> io::Result<()> {
    let mut p = libc::pollfd {
        fd,
        events,
        revents: 0,
    };
    // SAFETY: p points to one valid pollfd for the duration of poll.
    if unsafe { libc::poll(&mut p, 1, timeout) } < 0 {
        let e = io::Error::last_os_error();
        if e.kind() != io::ErrorKind::Interrupted {
            return Err(e);
        }
    }
    if p.revents & (libc::POLLHUP | libc::POLLERR | libc::POLLNVAL) != 0 {
        return Err(io::Error::other("keypad disconnected"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::{fd::OwnedFd, unix::net::UnixStream};

    fn simulated_keypad(stream: UnixStream) -> Keypad {
        stream.set_nonblocking(true).unwrap();
        Keypad {
            file: File::from(OwnedFd::from(stream)),
            feature: None,
            originals: Vec::new(),
            edges: Edges::default(),
            pending: Vec::new(),
            displayed: std::array::from_fn(|_| Some("__blank".into())),
            next_draw: Instant::now(),
            busy_since: None,
            busy_image: None,
        }
    }

    fn image_keys(name: &str) -> Vec<Option<mx_creative_worker::config::Key>> {
        let mut keys = vec![None; 9];
        keys[0] = Some(mxcc_key(name));
        keys
    }
    fn mxcc_key(name: &str) -> mx_creative_worker::config::Key {
        mx_creative_worker::config::Key {
            label: "Test".into(),
            image: name.into(),
            action: mx_creative_worker::config::Action::None {},
        }
    }
    fn fake_jpeg(size: usize) -> Vec<u8> {
        let mut bytes = vec![0; size];
        bytes[..2].copy_from_slice(&[0xff, 0xd8]);
        bytes[size - 2..].copy_from_slice(&[0xff, 0xd9]);
        bytes
    }

    #[test]
    fn busy_images_retry_whole_and_new_targets_replace_pending_work() {
        for changed in [false, true] {
            let cache = std::env::temp_dir()
                .join(format!("mxcc-image-test-{}-{changed}", std::process::id()));
            fs::create_dir_all(&cache).unwrap();
            fs::write(cache.join("old.jpg"), fake_jpeg(4175)).unwrap();
            fs::write(cache.join("new.jpg"), fake_jpeg(120)).unwrap();
            let (stream, mut peer) = UnixStream::pair().unwrap();
            peer.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
            let transport = std::thread::spawn(move || {
                let sequence = if changed {
                    vec![(0xa1, 0), (0x62, 0xfb), (0xe1, 0)]
                } else {
                    vec![(0xa1, 0), (0x62, 0xfb), (0xa1, 0), (0x62, 0)]
                };
                for (part, status) in sequence {
                    let mut packet = [0; 4095];
                    peer.read_exact(&mut packet).unwrap();
                    assert_eq!(packet[4], part);
                    let mut ack = [0; 32];
                    ack[..7].copy_from_slice(&[
                        0x13,
                        0xff,
                        2,
                        0x2b,
                        (part & 0x1f) | 0xc0,
                        status,
                        0,
                    ]);
                    peer.write_all(&ack).unwrap();
                }
                peer
            });
            let mut keypad = simulated_keypad(stream);
            assert!(!keypad.display_next(&image_keys("old.jpg"), &cache).unwrap());
            assert_ne!(keypad.displayed[0].as_deref(), Some("old.jpg"));
            assert!(keypad.draw_delay_ms() > 0);
            let latest = if changed { "new.jpg" } else { "old.jpg" };
            assert!(!keypad.display_next(&image_keys(latest), &cache).unwrap());
            assert!(keypad.display_next(&image_keys(latest), &cache).unwrap());
            assert_eq!(keypad.displayed[0].as_deref(), Some(latest));
            transport.join().unwrap();
            fs::remove_dir_all(cache).unwrap();
        }
    }

    #[test]
    fn persistent_busy_is_reported_without_marking_the_image_uploaded() {
        let cache = std::env::temp_dir().join(format!("mxcc-busy-test-{}", std::process::id()));
        fs::create_dir_all(&cache).unwrap();
        fs::write(cache.join("old.jpg"), fake_jpeg(120)).unwrap();
        let (stream, mut peer) = UnixStream::pair().unwrap();
        peer.set_read_timeout(Some(Duration::from_secs(2))).unwrap();
        let transport = std::thread::spawn(move || {
            let mut packet = [0; 4095];
            peer.read_exact(&mut packet).unwrap();
            let mut ack = [0; 32];
            ack[..7].copy_from_slice(&[0x13, 0xff, 2, 0x2b, 0xc1, 0xfb, 0]);
            peer.write_all(&ack).unwrap();
            peer
        });
        let mut keypad = simulated_keypad(stream);
        keypad.busy_since = Some(Instant::now() - Duration::from_secs(2));
        keypad.busy_image = Some((0, "old.jpg".into()));
        assert!(keypad
            .display_next(&image_keys("old.jpg"), &cache)
            .unwrap_err()
            .to_string()
            .contains("stayed busy"));
        assert_ne!(keypad.displayed[0].as_deref(), Some("old.jpg"));
        transport.join().unwrap();
        fs::remove_dir_all(cache).unwrap();
    }
    #[test]
    fn other_managers_are_conflicts_only_for_this_device() {
        let root = std::env::temp_dir().join(format!("mxcc-proc-test-{}", std::process::id()));
        let process = root.join("424242");
        fs::create_dir_all(process.join("fd")).unwrap();
        fs::write(
            process.join("cmdline"),
            b"/usr/bin/python3\0/usr/bin/solaar\0",
        )
        .unwrap();
        let node = Path::new("/dev/hidraw24");
        std::os::unix::fs::symlink("/dev/hidraw20", process.join("fd/3")).unwrap();
        assert!(conflict_in(&root, node).is_none());
        std::os::unix::fs::symlink(node, process.join("fd/4")).unwrap();
        assert!(conflict_in(&root, node).unwrap().contains("Solaar"));
        fs::write(process.join("cmdline"), b"/usr/bin/openlogi-agent\0").unwrap();
        assert!(conflict_in(&root, node).unwrap().contains("OpenLogi"));
        fs::remove_dir_all(root).unwrap();
    }
}
