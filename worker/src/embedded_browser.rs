//! Supervisor for the embedded, worker-owned accessibility observer. One child,
//! one bounded latest-report mailbox, no public IPC reporting endpoint.
use crate::events::{Event, Sender};
use mx_creative_worker::embedded::{Observation, Report, Target};
use std::{
    io::{self, Read, Write},
    os::{fd::AsRawFd, unix::net::UnixStream},
    process::{Child, ChildStdin, ChildStdout, Command, Stdio},
    sync::{mpsc, Arc, Mutex},
    time::{Duration, Instant},
};

const HELPER: &str = include_str!("../../src/mxcc/embedded_browser.py");
const RETRY: Duration = Duration::from_secs(5);
const MAX_LINE: usize = 4096;
const MAX_SILENCE: Duration = Duration::from_secs(3);

enum Control {
    Target(Target),
    Stop,
}

pub struct Handle {
    commands: mpsc::Sender<Control>,
    wake: UnixStream,
    latest: Arc<Mutex<Option<Observation>>>,
    thread: Option<std::thread::JoinHandle<()>>,
}

impl Handle {
    pub fn target(&self, target: Target) {
        if self.commands.send(Control::Target(target)).is_ok() {
            let _ = (&self.wake).write(&[1]);
        }
    }
    pub fn take(&self) -> Option<Observation> {
        self.latest.lock().expect("embedded mailbox").take()
    }
}

impl Drop for Handle {
    fn drop(&mut self) {
        let _ = self.commands.send(Control::Stop);
        let _ = (&self.wake).write(&[1]);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

pub fn start(sender: Sender) -> Handle {
    let (wake, mut reader) = UnixStream::pair().expect("embedded helper wake socket");
    wake.set_nonblocking(true)
        .expect("embedded helper wake socket");
    reader
        .set_nonblocking(true)
        .expect("embedded helper wake socket");
    let (commands, incoming) = mpsc::channel();
    let latest = Arc::new(Mutex::new(None));
    let mailbox = latest.clone();
    let thread = std::thread::spawn(move || run(sender, incoming, &mut reader, mailbox));
    Handle {
        commands,
        wake,
        latest,
        thread: Some(thread),
    }
}

fn deliver(sender: &Sender, mailbox: &Mutex<Option<Observation>>, observation: Observation) {
    let mut latest = mailbox.lock().expect("embedded mailbox");
    let notify = latest.is_none();
    *latest = Some(observation);
    if notify {
        sender.send(Event::EmbeddedBrowser);
    }
}

fn nonblocking(fd: libc::c_int) -> io::Result<()> {
    // SAFETY: Caller owns this live pipe descriptor; preserve existing flags.
    unsafe {
        let flags = libc::fcntl(fd, libc::F_GETFL);
        if flags < 0 || libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) < 0 {
            return Err(io::Error::last_os_error());
        }
    }
    Ok(())
}

struct Helper {
    child: Child,
    stdin: ChildStdin,
    stdout: ChildStdout,
    buffer: Vec<u8>,
    last_output: Instant,
    closed: bool,
    last_diagnostic: Option<Report>,
}

impl Drop for Helper {
    fn drop(&mut self) {
        // Kill/wait only the child we created; no process-name matching or global signals.
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

impl Helper {
    fn spawn(target: &Target) -> io::Result<Self> {
        Self::spawn_script(HELPER, target)
    }

    fn spawn_script(script: &str, target: &Target) -> io::Result<Self> {
        let mut child = Command::new("/usr/bin/python3")
            .args(["-I", "-u", "-c", script])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let stdin = child.stdin.take().expect("piped helper input");
        let stdout = child.stdout.take().expect("piped helper output");
        let mut helper = Self {
            child,
            stdin,
            stdout,
            buffer: Vec::new(),
            last_output: Instant::now(),
            closed: false,
            last_diagnostic: None,
        };
        nonblocking(helper.stdin.as_raw_fd())?;
        nonblocking(helper.stdout.as_raw_fd())?;
        helper.target(target)?;
        Ok(helper)
    }

    fn target(&mut self, target: &Target) -> io::Result<()> {
        let mut message = serde_json::to_value(target)?;
        message["command"] = serde_json::json!("target");
        let mut bytes = serde_json::to_vec(&message)?;
        bytes.push(b'\n');
        if bytes.len() > 65536 {
            return Err(io::Error::other(
                "Browser accessibility target is too large",
            ));
        }
        let deadline = Instant::now() + Duration::from_millis(100);
        let mut sent = 0;
        while sent < bytes.len() {
            match self.stdin.write(&bytes[sent..]) {
                Ok(0) => {
                    return Err(io::Error::new(
                        io::ErrorKind::WriteZero,
                        "Browser accessibility helper closed its input",
                    ))
                }
                Ok(count) => sent += count,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                    if Instant::now() >= deadline {
                        return Err(io::Error::new(
                            io::ErrorKind::TimedOut,
                            "Browser accessibility helper stopped reading targets",
                        ));
                    }
                    let mut fd = libc::pollfd {
                        fd: self.stdin.as_raw_fd(),
                        events: libc::POLLOUT,
                        revents: 0,
                    };
                    // SAFETY: The owned child input remains live during this short poll.
                    unsafe {
                        libc::poll(&mut fd, 1, 10);
                    }
                }
                Err(error) => return Err(error),
            }
        }
        Ok(())
    }

    fn read_latest(&mut self) -> io::Result<Option<Report>> {
        let mut latest: Option<Report> = None;
        let mut bytes = [0u8; 1024];
        // A defective observer cannot monopolize this thread or allocate an unbounded queue.
        for _ in 0..64 {
            let count = match self.stdout.read(&mut bytes) {
                Ok(0) => {
                    self.closed = true;
                    if latest.is_none() {
                        latest = self.last_diagnostic.clone();
                    }
                    if latest.as_ref().is_some_and(terminal_diagnostic) {
                        return Ok(latest);
                    }
                    return Err(io::Error::new(
                        io::ErrorKind::UnexpectedEof,
                        "Browser accessibility helper disconnected",
                    ));
                }
                Ok(count) => count,
                Err(error) if error.kind() == io::ErrorKind::WouldBlock => break,
                Err(error) => return Err(error),
            };
            for byte in &bytes[..count] {
                if *byte == b'\n' {
                    latest = Some(serde_json::from_slice::<Report>(&self.buffer).map_err(
                        |error| {
                            io::Error::other(format!(
                                "Invalid Browser accessibility helper report: {error}"
                            ))
                        },
                    )?);
                    self.last_diagnostic = latest
                        .as_ref()
                        .filter(|report| terminal_diagnostic(report))
                        .cloned();
                    self.buffer.clear();
                    self.last_output = Instant::now();
                } else {
                    if self.buffer.len() >= MAX_LINE {
                        return Err(io::Error::other(
                            "Browser accessibility helper report exceeded 4096 bytes",
                        ));
                    }
                    self.buffer.push(*byte);
                }
            }
        }
        Ok(latest)
    }
}

fn terminal_diagnostic(report: &Report) -> bool {
    !report.ready && report.domain.is_none() && report.error.is_some()
}

fn run(
    sender: Sender,
    incoming: mpsc::Receiver<Control>,
    wake: &mut UnixStream,
    latest: Arc<Mutex<Option<Observation>>>,
) {
    let mut target = Target::default();
    let mut helper: Option<Helper> = None;
    let mut generation = 0;
    let mut retry_at = Instant::now();
    loop {
        let mut changed = false;
        while let Ok(command) = incoming.try_recv() {
            match command {
                Control::Stop => return,
                Control::Target(next) => {
                    target = next;
                    changed = true;
                }
            }
        }
        if changed {
            retry_at = Instant::now();
            if let Some(child) = helper.as_mut() {
                if let Err(error) = child.target(&target) {
                    deliver(
                        &sender,
                        &latest,
                        Observation {
                            generation,
                            received: Instant::now(),
                            result: Err(error.to_string()),
                        },
                    );
                    helper = None;
                    retry_at = Instant::now() + RETRY;
                }
            }
        }
        if helper.is_none() && Instant::now() >= retry_at {
            generation = generation.wrapping_add(1);
            match Helper::spawn(&target) {
                Ok(child) => helper = Some(child),
                Err(error) => {
                    deliver(
                        &sender,
                        &latest,
                        Observation {
                            generation,
                            received: Instant::now(),
                            result: Err(format!(
                                "Browser accessibility detection is unavailable: {error}"
                            )),
                        },
                    );
                    retry_at = Instant::now() + RETRY;
                }
            }
        }
        if let Some(child) = helper.as_mut() {
            let result = child.read_latest().and_then(|mut report| {
                child.closed |= child.child.try_wait()?.is_some();
                if child.closed && report.is_none() {
                    report = child.last_diagnostic.clone();
                }
                if child.closed && !report.as_ref().is_some_and(terminal_diagnostic) {
                    return Err(io::Error::other("Browser accessibility helper exited"));
                }
                if child.last_output.elapsed() > MAX_SILENCE {
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "Browser accessibility helper stopped responding",
                    ));
                }
                Ok(report)
            });
            let closed = child.closed;
            match result {
                Ok(Some(report)) => {
                    deliver(
                        &sender,
                        &latest,
                        Observation {
                            generation,
                            received: Instant::now(),
                            result: Ok(report),
                        },
                    );
                    if closed {
                        helper = None;
                        retry_at = Instant::now() + RETRY;
                    }
                }
                Ok(None) => {}
                Err(error) => {
                    deliver(
                        &sender,
                        &latest,
                        Observation {
                            generation,
                            received: Instant::now(),
                            result: Err(error.to_string()),
                        },
                    );
                    helper = None;
                    retry_at = Instant::now() + RETRY;
                }
            }
        }
        let timeout = if helper.is_some() {
            100
        } else {
            retry_at
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(5000) as i32
        };
        let mut fds = [
            libc::pollfd {
                fd: wake.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: helper
                    .as_ref()
                    .map_or(-1, |helper| helper.stdout.as_raw_fd()),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: Owned wake/child pipe descriptors remain live during this poll.
        let result = unsafe { libc::poll(fds.as_mut_ptr(), 2, timeout) };
        if result < 0 && io::Error::last_os_error().kind() != io::ErrorKind::Interrupted {
            return;
        }
        if fds[0].revents != 0 {
            let mut bytes = [0u8; 128];
            while wake.read(&mut bytes).is_ok_and(|count| count > 0) {}
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn wait_report(helper: &mut Helper) -> io::Result<Report> {
        let deadline = Instant::now() + Duration::from_secs(2);
        loop {
            if let Some(report) = helper.read_latest()? {
                return Ok(report);
            }
            assert!(Instant::now() < deadline, "fake helper did not report");
            let mut fd = libc::pollfd {
                fd: helper.stdout.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            };
            // SAFETY: Test owns the pipe for the entire short poll.
            unsafe {
                libc::poll(&mut fd, 1, 10);
            }
        }
    }

    #[test]
    fn private_child_echoes_targets_and_drop_kills_and_reaps_only_owned_process() {
        let script="import sys,json\nfor line in sys.stdin:\n t=json.loads(line); assert t['browser'] in ('chatgpt','firefox',''); print(json.dumps(dict(window=t['window'],pid=t['pid'],target_revision=t['target_revision'],revision=t['target_revision'],domain=None,ready=True,error=None)),flush=True)\n";
        let target = Target {
            browser: "firefox".into(),
            window: 42,
            pid: 1000,
            domains: vec!["10.64.192.14".into()],
            target_revision: 8,
        };
        let mut helper = Helper::spawn_script(script, &target).unwrap();
        let pid = helper.child.id();
        let first = wait_report(&mut helper).unwrap();
        assert_eq!(first.target_revision, 8);
        assert_eq!(first.window, 42);
        helper
            .target(&Target {
                target_revision: 9,
                ..Target::default()
            })
            .unwrap();
        let second = wait_report(&mut helper).unwrap();
        assert_eq!(second.target_revision, 9);
        assert_eq!(second.window, 0);
        drop(helper);
        // SAFETY: Signal zero only checks existence of the child PID after wait.
        assert_eq!(unsafe { libc::kill(pid as i32, 0) }, -1);
        assert_eq!(io::Error::last_os_error().raw_os_error(), Some(libc::ESRCH));
    }

    #[test]
    fn oversized_and_malformed_child_output_is_rejected() {
        for script in [
            "import sys,time\nsys.stdin.readline()\nprint('x'*5000,flush=True)\ntime.sleep(60)",
            "import sys,time\nsys.stdin.readline()\nprint('{}',flush=True)\ntime.sleep(60)",
        ] {
            let mut helper = Helper::spawn_script(script, &Target::default()).unwrap();
            assert!(wait_report(&mut helper).is_err());
        }
    }

    #[test]
    fn terminal_startup_diagnostic_survives_eof_but_positive_context_does_not() {
        let prefix = "import sys,json\nsys.stdin.readline()\n";
        for ready in [false, true] {
            let report = serde_json::json!({"window":0,"pid":0,"target_revision":0,"revision":0,
                "domain":null,"ready":ready,"error":if ready {None}else{Some("Install python3-gi and gir1.2-atspi-2.0")} });
            let script = format!("{prefix}print({:?},flush=True)\n", report.to_string());
            let mut helper = Helper::spawn_script(&script, &Target::default()).unwrap();
            // Wait for exit so this exercises the EOF path, not a pipe timing race.
            helper.child.wait().unwrap();
            let result = helper.read_latest();
            if ready {
                assert!(result.is_err());
            } else {
                assert_eq!(
                    result.unwrap().unwrap().error.as_deref(),
                    Some("Install python3-gi and gir1.2-atspi-2.0")
                );
            }
        }
    }

    #[test]
    fn terminal_diagnostic_is_retained_when_report_and_exit_arrive_in_different_reads() {
        let report = serde_json::json!({"window":0,"pid":0,"target_revision":0,"revision":0,
            "domain":null,"ready":false,"error":"GI is missing"});
        let script = format!(
            "import sys,time\nsys.stdin.readline()\nprint({:?},flush=True)\ntime.sleep(0.05)\n",
            report.to_string()
        );
        let mut helper = Helper::spawn_script(&script, &Target::default()).unwrap();
        let first = wait_report(&mut helper).unwrap();
        assert_eq!(first.error.as_deref(), Some("GI is missing"));
        helper.child.wait().unwrap();
        let terminal = helper.read_latest().unwrap().unwrap();
        assert_eq!(terminal.error, first.error);
        assert!(helper.closed);
    }

    #[test]
    fn mailbox_coalesces_reports_and_has_one_pending_wakeup() {
        let (tx, rx) = mpsc::channel();
        let (wake, _reader) = UnixStream::pair().unwrap();
        let sender = Sender::new(tx, wake);
        let mailbox = Mutex::new(None);
        for generation in 1..=1000 {
            deliver(
                &sender,
                &mailbox,
                Observation {
                    generation,
                    received: Instant::now(),
                    result: Err("offline".into()),
                },
            );
        }
        assert_eq!(mailbox.lock().unwrap().as_ref().unwrap().generation, 1000);
        assert!(matches!(rx.try_recv(), Ok(Event::EmbeddedBrowser)));
        assert!(rx.try_recv().is_err());
        mailbox.lock().unwrap().take();
        deliver(
            &sender,
            &mailbox,
            Observation {
                generation: 1001,
                received: Instant::now(),
                result: Err("offline".into()),
            },
        );
        assert!(matches!(rx.try_recv(), Ok(Event::EmbeddedBrowser)));
    }
}
