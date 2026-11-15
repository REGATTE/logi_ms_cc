//! A ring is an on-demand child process with an immutable action snapshot.
//! The UI can select a slot, never submit an arbitrary action for execution.
use crate::{
    events::{Event, Sender},
    focus,
};
use mx_creative_worker::{
    config::{Action, ActionRing, RingLayout},
    control::Foreground,
};
use serde_json::{json, Value};
use std::{
    fs::File,
    io::Read,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    path::Path,
    process::{Command, Stdio},
    time::{Duration, Instant},
};

const SESSION_LIMIT: Duration = Duration::from_secs(120);

/// The pidfd remains tied to this child even after exit, so cancelling a stale
/// session cannot signal an unrelated process that reused its PID.
struct OverlayProcess(OwnedFd);
impl Drop for OverlayProcess {
    fn drop(&mut self) {
        // SAFETY: the pidfd is owned and live; a null siginfo requests a normal
        // SIGTERM. Child.wait in the supervisor always reaps the child.
        unsafe {
            libc::syscall(
                libc::SYS_pidfd_send_signal,
                self.0.as_raw_fd(),
                libc::SIGTERM,
                std::ptr::null::<libc::siginfo_t>(),
                0,
            )
        };
        let mut fd = libc::pollfd {
            fd: self.0.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        // A following keypad action must not race the old popup's input grab.
        // SAFETY: this owned pidfd remains live until Drop completes.
        unsafe { libc::poll(&mut fd, 1, 1000) };
    }
}

pub struct Session {
    token: String,
    pub foreground: Foreground,
    layout: RingLayout,
    expires: Instant,
    _process: Option<OverlayProcess>,
}
impl Session {
    pub fn action(&self, slot: usize) -> Result<Action, String> {
        if slot >= 7 {
            return Err("Action Ring has at most seven actions".into());
        }
        self.layout
            .slots
            .get(slot)
            .and_then(Option::as_ref)
            .map(|entry| entry.action.clone())
            .ok_or_else(|| "This ring slot is empty or invalid".into())
    }

    fn configuration_command(&self, gui: &Path) -> Command {
        let mut command = Command::new(gui);
        command.args([
            "configure",
            "--ring-layout",
            &self.layout.id,
            "--add-ring-action",
        ]);
        command.stdin(Stdio::null());
        command
    }

    /// Only the session's validated layout identity crosses into the settings
    /// process. The IPC caller cannot choose another layout or supply arguments.
    pub fn configure(&self, gui: &Path) -> Result<(), String> {
        let mut child = self
            .configuration_command(gui)
            .spawn()
            .map_err(|error| format!("Cannot open the Action Ring editor: {error}"))?;
        std::thread::spawn(move || match child.wait() {
            Ok(status) if status.success() => {}
            Ok(status) => eprintln!("Action Ring editor exited with {status}"),
            Err(error) => eprintln!("Could not monitor Action Ring editor: {error}"),
        });
        Ok(())
    }
}

#[derive(Default)]
pub struct Ring {
    session: Option<Session>,
    finishing: Vec<Session>,
}

pub fn choose_layout<'a>(
    config: &'a ActionRing,
    foreground: &Foreground,
) -> Option<&'a RingLayout> {
    config
        .layouts
        .iter()
        .find(|layout| {
            layout.id != "default"
                && foreground
                    .identities
                    .iter()
                    .chain(std::iter::once(&foreground.name))
                    .any(|identity| {
                        !identity.is_empty()
                            && (layout.id.eq_ignore_ascii_case(identity)
                                || layout
                                    .id
                                    .strip_suffix(".desktop")
                                    .is_some_and(|id| id.eq_ignore_ascii_case(identity))
                                || layout
                                    .wm_classes
                                    .iter()
                                    .any(|class| class.eq_ignore_ascii_case(identity)))
                    })
        })
        .or_else(|| config.layouts.iter().find(|layout| layout.id == "default"))
}

impl Ring {
    pub fn is_open(&self) -> bool {
        self.session.is_some()
    }
    pub fn deadline(&self) -> Option<Instant> {
        self.session
            .iter()
            .chain(self.finishing.iter())
            .map(|session| session.expires)
            .min()
    }
    pub fn tick(&mut self) -> bool {
        let now = Instant::now();
        let expired = self
            .session
            .as_ref()
            .is_some_and(|session| now >= session.expires);
        if expired {
            self.session = None;
        }
        self.finishing.retain(|session| now < session.expires);
        expired
    }
    pub fn close(&mut self) {
        self.session = None;
        self.finishing.clear();
    }
    pub fn dismiss(&mut self) -> Option<Foreground> {
        let foreground = self
            .session
            .as_ref()
            .map(|session| session.foreground.clone());
        if foreground.is_some() {
            self.close();
        }
        foreground
    }
    /// Execution and cancellation return their IPC response before the Qt
    /// process exits naturally. Keep a bounded watchdog for error dialogs or
    /// a stuck frontend, without terminating it before the response is read.
    pub fn finish(&mut self, mut session: Session) {
        session.expires = Instant::now() + SESSION_LIMIT;
        if self.finishing.len() >= 8 {
            self.finishing.remove(0);
        }
        self.finishing.push(session);
    }
    pub fn exited(&mut self, token: &str) -> bool {
        self.finishing.retain(|session| session.token != token);
        if self
            .session
            .as_ref()
            .is_some_and(|session| session.token == token)
        {
            self.session = None;
            true
        } else {
            false
        }
    }
    pub fn take(&mut self, token: &str) -> Result<Session, String> {
        if self
            .session
            .as_ref()
            .is_some_and(|session| session.token == token && Instant::now() < session.expires)
        {
            Ok(self.session.take().unwrap())
        } else {
            Err("The action ring session has expired".into())
        }
    }
    pub fn context(&self) -> Result<Value, String> {
        let session = self
            .session
            .as_ref()
            .filter(|session| Instant::now() < session.expires)
            .ok_or("There is no open action ring")?;
        Ok(
            json!({"ok":true,"token":session.token,"layout":session.layout,"foreground":session.foreground}),
        )
    }
    pub fn toggle(
        &mut self,
        config: &ActionRing,
        gui: &Path,
        sender: &Sender,
        session_type: &str,
        paused: bool,
    ) -> Result<bool, String> {
        if self.is_open() {
            self.close();
            return Ok(false);
        }
        if session_type != "x11" {
            return Err("Action Ring requires an Ubuntu on Xorg session in this version".into());
        }
        if paused {
            return Err("Resume device actions before opening the ring".into());
        }
        let foreground = focus::current()?;
        if focus::is_ring(&foreground) {
            return Err("The previous ring is still closing; try again".into());
        }
        let layout = choose_layout(config, &foreground)
            .ok_or("Configure an Action Ring layout first")?
            .clone();
        let mut random = [0u8; 16];
        File::open("/dev/urandom")
            .and_then(|mut file| file.read_exact(&mut random))
            .map_err(|e| e.to_string())?;
        let token = random
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        let mut child = Command::new(gui)
            .arg("ring")
            .stdin(Stdio::null())
            .spawn()
            .map_err(|error| format!("Cannot open Action Ring: {error}"))?;
        // SAFETY: pidfd_open accepts the live child's ID and no flags.
        let pidfd = unsafe { libc::syscall(libc::SYS_pidfd_open, child.id(), 0) };
        if pidfd < 0 {
            let error = std::io::Error::last_os_error();
            let _ = child.kill();
            let _ = child.wait();
            return Err(format!("Cannot supervise Action Ring: {error}"));
        }
        // SAFETY: pidfd_open returned a fresh descriptor owned by this process.
        let process = OverlayProcess(unsafe { OwnedFd::from_raw_fd(pidfd as i32) });
        let child_token = token.clone();
        let child_sender = sender.clone();
        std::thread::spawn(move || {
            let error=match child.wait() {
                Ok(status) if status.success() => None,
                Ok(status) => Some(format!("Action Ring exited with {status}. Reopen settings and check the app installation.")),
                Err(error) => Some(format!("Could not monitor Action Ring: {error}")),
            };
            child_sender.send(Event::RingExited(child_token, error));
        });
        self.session = Some(Session {
            token,
            foreground,
            layout,
            expires: Instant::now() + SESSION_LIMIT,
            _process: Some(process),
        });
        Ok(true)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn exact_application_identity_selects_layout_then_falls_back() {
        let layout = |id: &str, classes: &[&str]| RingLayout {
            id: id.into(),
            wm_classes: classes.iter().map(|s| (*s).into()).collect(),
            slots: vec![],
        };
        let config = ActionRing {
            layouts: vec![layout("default", &[]), layout("code.desktop", &["Code"])],
            bindings: vec![],
        };
        for identity in ["CODE", "code.desktop", "code"] {
            assert_eq!(
                choose_layout(
                    &config,
                    &Foreground {
                        window: 42,
                        name: String::new(),
                        identities: vec![identity.into()]
                    }
                )
                .unwrap()
                .id,
                "code.desktop"
            );
        }
        assert_eq!(
            choose_layout(
                &config,
                &Foreground {
                    window: 7,
                    name: "VSCodium".into(),
                    identities: vec![]
                }
            )
            .unwrap()
            .id,
            "default"
        );
    }
    #[test]
    fn empty_and_stale_sessions_cannot_execute_or_close_a_new_ring() {
        let mut ring = Ring::default();
        assert!(ring.context().is_err());
        assert!(ring.take("invented-token").is_err());
        assert!(!ring.exited("old-token"));
        assert!(!ring.is_open());
    }
    fn active_ring() -> Ring {
        Ring {
            finishing: vec![],
            session: Some(Session {
                token: "current-token".into(),
                foreground: Foreground::default(),
                layout: RingLayout {
                    id: "default".into(),
                    wm_classes: vec![],
                    slots: vec![Some(mx_creative_worker::config::RingSlot {
                        label: "Copy".into(),
                        icon: "edit-copy".into(),
                        action: Action::Shortcut {
                            keys: "Ctrl+C".into(),
                        },
                    })],
                },
                expires: Instant::now() + Duration::from_secs(30),
                _process: None,
            }),
        }
    }
    #[test]
    fn stale_requests_cannot_change_current_session_and_actions_are_single_use() {
        let mut ring = active_ring();
        assert!(ring.take("old-token").is_err());
        assert!(!ring.exited("old-token"));
        assert!(ring.is_open());
        let session = ring.take("current-token").unwrap();
        assert!(matches!(session.action(0),Ok(Action::Shortcut{keys}) if keys=="Ctrl+C"));
        assert!(session.action(1).is_err());
        assert!(session.action(7).is_err());
        assert!(session.action(8).is_err());
        assert!(ring.take("current-token").is_err());
        assert!(!ring.is_open());
    }
    #[test]
    fn expired_ring_does_not_return_context_or_actions() {
        let mut ring = active_ring();
        ring.session.as_mut().unwrap().expires = Instant::now() - Duration::from_secs(1);
        assert!(ring.context().is_err());
        assert!(ring.take("current-token").is_err());
        assert!(ring.tick());
        assert!(!ring.is_open());
    }

    #[test]
    fn finishing_waits_for_child_exit_and_does_not_reuse_its_token() {
        let mut ring = active_ring();
        let session = ring.take("current-token").unwrap();
        ring.finish(session);
        assert!(!ring.is_open());
        assert_eq!(ring.finishing.len(), 1);
        assert!(ring.take("current-token").is_err());
        assert!(!ring.exited("current-token"));
        assert!(ring.finishing.is_empty());
    }

    #[test]
    fn configure_opens_the_captured_layout_without_accepting_caller_arguments() {
        let mut ring = active_ring();
        ring.session.as_mut().unwrap().layout.id = "org.example.Editor.desktop".into();
        let session = ring.take("current-token").unwrap();
        let command = session.configuration_command(Path::new("/usr/bin/mx-creative-control"));
        assert_eq!(command.get_program(), "/usr/bin/mx-creative-control");
        assert_eq!(
            command.get_args().collect::<Vec<_>>(),
            [
                "configure",
                "--ring-layout",
                "org.example.Editor.desktop",
                "--add-ring-action"
            ]
        );
        assert!(ring.take("current-token").is_err());
    }
}
