mod activation;
mod events;
mod focus;
mod hardware;
mod ipc;
mod launcher;
mod tray;
use events::{Event, Sender};
use mx_creative_worker::{
    config::{Action, Config},
    control::{Foreground, Request, State},
    input::{parse_chord, Keyboard},
    protocol::Press,
};
use std::{
    env,
    io::{self, Read},
    os::{fd::AsRawFd, unix::net::UnixStream},
    path::PathBuf,
    sync::mpsc,
    time::{Duration, Instant},
};

struct Options {
    config: PathBuf,
    cache: PathBuf,
    socket: PathBuf,
    runtime_base: Option<PathBuf>,
    gui: PathBuf,
    no_tray: bool,
    check: bool,
}
impl Options {
    fn parse() -> Result<Self, String> {
        let home = PathBuf::from(env::var_os("HOME").ok_or("HOME is unset")?);
        let config = env::var_os("XDG_CONFIG_HOME")
            .map(PathBuf::from)
            .filter(|path| path.is_absolute())
            .unwrap_or_else(|| home.join(".config"))
            .join("mx-creative-control/runtime.json");
        let cache = env::var_os("XDG_CACHE_HOME")
            .map(PathBuf::from)
            .filter(|path| path.is_absolute())
            .unwrap_or_else(|| home.join(".cache"))
            .join("mx-creative-control/keys");
        let runtime = env::var_os("XDG_RUNTIME_DIR")
            .map(PathBuf::from)
            .filter(|path| path.is_absolute())
            .unwrap_or_else(|| PathBuf::from(format!("/tmp/mxcc-{}", unsafe { libc::getuid() })));
        let mut result = Self {
            config,
            cache,
            socket: runtime.join("mx-creative-control/control.sock"),
            runtime_base: Some(runtime),
            gui: "/usr/bin/mx-creative-control".into(),
            no_tray: false,
            check: false,
        };
        let mut args = env::args_os().skip(1);
        while let Some(arg) = args.next() {
            match arg.to_str() {
                Some("--no-tray") => result.no_tray = true,
                Some("--help") => {
                    println!("mxcc-worker [--config PATH] [--cache PATH] [--socket PATH] [--gui-command PATH] [--no-tray]\nmxcc-worker --check-config PATH");
                    std::process::exit(0);
                }
                Some("--version") => {
                    println!("{}", env!("CARGO_PKG_VERSION"));
                    std::process::exit(0);
                }
                Some(
                    flag @ ("--config" | "--cache" | "--socket" | "--gui-command"
                    | "--check-config"),
                ) => {
                    let value = args
                        .next()
                        .ok_or_else(|| format!("{flag} requires a path"))?;
                    match flag {
                        "--config" => result.config = value.into(),
                        "--cache" => result.cache = value.into(),
                        "--socket" => {
                            result.socket = value.into();
                            result.runtime_base = None;
                        }
                        "--gui-command" => result.gui = value.into(),
                        _ => {
                            result.config = value.into();
                            result.check = true;
                        }
                    }
                }
                _ => return Err(format!("unknown option: {}", arg.to_string_lossy())),
            }
        }
        Ok(result)
    }
}

fn action(action: &Action, keyboard: &mut Option<Keyboard>) -> Result<(), String> {
    match action {
        Action::Launch { .. } => Err("Launcher actions require desktop activation".into()),
        Action::None {} => Ok(()),
        Action::Shortcut { .. } | Action::Screenshot {} => {
            let keys = if let Action::Shortcut { keys } = action {
                parse_chord(keys)?
            } else {
                vec![99]
            };
            keyboard
                .as_mut()
                .ok_or("shortcut input permission is required")?
                .chord(&keys)
                .map_err(|e| e.to_string())
        }
    }
}

fn run(options: Options) -> Result<(), Box<dyn std::error::Error>> {
    let config = Config::load(&options.config)?;
    if options.check {
        println!("Runtime configuration is valid");
        return Ok(());
    }
    if let Some(base) = &options.runtime_base {
        ipc::prepare_runtime_base(base)?;
    }
    let guard = ipc::SocketGuard::bind(&options.socket)?;
    let (mut read, wake) = UnixStream::pair()?;
    read.set_nonblocking(true)?;
    wake.set_nonblocking(true)?;
    let (tx, rx) = mpsc::channel();
    let sender = Sender::new(tx, wake);
    guard.serve(sender.clone())?;
    let session = if env::var("XDG_SESSION_TYPE").as_deref() == Ok("wayland")
        || env::var_os("WAYLAND_DISPLAY").is_some()
    {
        "wayland"
    } else {
        "x11"
    };
    let mut state = State::new(config, session.into());
    if session == "x11" {
        focus::start(sender.clone());
    }
    let tray = if options.no_tray {
        None
    } else {
        tray::start(sender.clone(), options.gui.clone())
    };
    state.status.tray_available = tray.is_some();
    let mut signals = signal_hook::iterator::Signals::new([
        signal_hook::consts::SIGINT,
        signal_hook::consts::SIGTERM,
    ])?;
    let signal_sender = sender.clone();
    std::thread::spawn(move || {
        if signals.forever().next().is_some() {
            signal_sender.send(Event::Shutdown);
        }
    });
    let mut keypad: Option<hardware::Keypad> = None;
    let mut keyboard = None;
    let mut launcher = launcher::Launcher::default();
    let mut retry = Instant::now();
    let mut redraw = true;
    let mut quit = false;
    let mut tray_state = None;
    while !quit {
        redraw |= state.tick(Instant::now());
        if Instant::now() >= retry {
            retry = Instant::now() + Duration::from_secs(5);
            if keyboard.is_none() {
                match Keyboard::open() {
                    Ok(k) => {
                        keyboard = Some(k);
                        state.status.input_access = true;
                    }
                    Err(e) => {
                        state.status.input_access = false;
                        if e.kind() != io::ErrorKind::PermissionDenied {
                            eprintln!("uinput unavailable: {e}");
                        }
                    }
                }
            }
            if keypad.is_none() {
                match hardware::discover() {
                    Ok(Some(path)) => {
                        state.status.connected = true;
                        if let Some(conflict) = hardware::conflict(&path) {
                            state.status.last_error = Some(conflict);
                            // Permission status stays separate from ownership conflicts.
                            let name = std::ffi::CString::new(path.as_os_str().as_encoded_bytes())?;
                            state.status.keypad_access =
                                unsafe { libc::access(name.as_ptr(), libc::R_OK | libc::W_OK) }
                                    == 0;
                        } else {
                            match hardware::Keypad::open(&path) {
                                Ok(mut k) => {
                                    state.status.keypad_access = true;
                                    state.status.last_error = None;
                                    if let Err(e) = k.initialise() {
                                        state.status.last_error =
                                            Some(format!("Navigation button setup: {e}"));
                                    }
                                    keypad = Some(k);
                                    redraw = true;
                                }
                                Err(e) => {
                                    state.status.keypad_access = false;
                                    state.status.last_error = Some(format!("Keypad access: {e}"));
                                }
                            }
                        }
                    }
                    Ok(None) => {
                        state.status.connected = false;
                        state.status.keypad_access = false;
                    }
                    Err(e) => state.status.last_error = Some(format!("Keypad detection: {e}")),
                }
            }
        }
        while let Ok(event) = rx.try_recv() {
            match event {
                Event::Request(request, reply, written) => {
                    let result: Result<(), String> = match request {
                        Request::Status {} => {
                            let _ = reply.send(serde_json::to_value(&state.status)?);
                            continue;
                        }
                        Request::Reload {} => match Config::load(&options.config) {
                            Ok(config) => {
                                state.reload(config);
                                state.status.last_error = None;
                                redraw = true;
                                if let Some(k) = keypad.as_mut() {
                                    k.invalidate();
                                }
                                retry = Instant::now();
                                Ok(())
                            }
                            Err(e) => Err(e),
                        },
                        Request::Pause { value } => {
                            state.status.paused = value;
                            Ok(())
                        }
                        Request::Page { index } => {
                            redraw = true;
                            state.select_page(index)
                        }
                        Request::Profile { id } => {
                            redraw = true;
                            state.select_profile(id)
                        }
                        Request::Shutdown {} => {
                            quit = true;
                            Ok(())
                        }
                    };
                    let response = match result {
                        Ok(()) => serde_json::json!({"ok":true}),
                        Err(error) => serde_json::json!({"ok":false,"error":error}),
                    };
                    let _ = reply.send(response);
                    if quit {
                        let _ = written.recv_timeout(Duration::from_secs(1));
                    }
                }
                Event::Focus(foreground) => {
                    redraw |= state.focus_changed(foreground, Instant::now());
                }
                Event::FocusSupport(value) => redraw |= state.set_focus_supported(value),
                Event::Tray(value) => state.status.tray_available = value,
                Event::Page(page) => {
                    let _ = state.select_page(page);
                    redraw = true;
                }
                Event::Pause(value) => state.status.paused = value,
                Event::Shutdown => quit = true,
            }
        }
        if quit {
            break;
        }
        if let Some(k) = keypad.as_mut() {
            for press in std::mem::take(&mut k.pending) {
                match press {
                    Press::Page(page) => {
                        let _ = state.select_page(page);
                        redraw = true;
                    }
                    Press::Key(slot) if !state.status.paused => {
                        if let Some(key) = state.page().keys[slot].clone() {
                            if let Action::Launch {
                                desktop_id,
                                wm_classes,
                                profile_id,
                            } = &key.action
                            {
                                let now = Instant::now();
                                match launcher.activate(
                                    desktop_id,
                                    wm_classes,
                                    session == "x11",
                                    now,
                                ) {
                                    Ok(launcher::Outcome::Existing(window)) => {
                                        if window.already_active {
                                            redraw |= state.focus_changed(
                                                Foreground {
                                                    window: window.window,
                                                    name: window.wm_class.clone(),
                                                    identities: vec![window.wm_class],
                                                },
                                                now,
                                            );
                                        }
                                        redraw |= state.launch_succeeded(
                                            desktop_id,
                                            wm_classes,
                                            profile_id.as_deref(),
                                            Some(window.window),
                                            window.already_active,
                                            now,
                                        );
                                        state.status.last_error = None;
                                    }
                                    Ok(launcher::Outcome::Started) => {
                                        redraw |= state.launch_succeeded(
                                            desktop_id,
                                            wm_classes,
                                            profile_id.as_deref(),
                                            None,
                                            false,
                                            now,
                                        );
                                        state.status.last_error = None;
                                    }
                                    Ok(launcher::Outcome::Pending) => {}
                                    Err(error) => state.status.last_error = Some(error),
                                }
                            } else if let Err(error) = action(&key.action, &mut keyboard) {
                                state.status.last_error = Some(error);
                            }
                        }
                    }
                    _ => {}
                }
            }
            if redraw && k.draw_delay_ms() == 0 {
                match k.display_next(&state.page().keys, &options.cache) {
                    Ok(complete) => redraw = !complete,
                    Err(e) => {
                        state.status.last_error = Some(format!("Display update: {e}"));
                        redraw = false;
                    }
                }
            }
        }
        let current_tray_state = (
            state.status.paused,
            state.status.page,
            state.status.connected,
        );
        if let Some(handle) = tray
            .as_ref()
            .filter(|_| tray_state != Some(current_tray_state))
        {
            handle.update(|t| {
                t.paused = state.status.paused;
                t.page = state.status.page;
                t.connected = state.status.connected;
            });
            tray_state = Some(current_tray_state);
        }
        if keypad.as_ref().is_some_and(|k| !k.pending.is_empty()) {
            continue;
        }
        let mut timeout = if keypad.is_none() || keyboard.is_none() {
            retry
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(5000) as i32
        } else {
            -1
        };
        if redraw {
            if let Some(k) = keypad.as_ref() {
                let draw_timeout = k.draw_delay_ms();
                timeout = if timeout < 0 {
                    draw_timeout
                } else {
                    timeout.min(draw_timeout)
                };
            }
        }
        if let Some(deadline) = state.deadline() {
            let remaining = deadline
                .saturating_duration_since(Instant::now())
                .as_millis()
                .min(i32::MAX as u128) as i32;
            timeout = if timeout < 0 {
                remaining
            } else {
                timeout.min(remaining)
            };
        }
        let mut fds = [
            libc::pollfd {
                fd: read.as_raw_fd(),
                events: libc::POLLIN,
                revents: 0,
            },
            libc::pollfd {
                fd: keypad.as_ref().map(|k| k.fd()).unwrap_or(-1),
                events: libc::POLLIN,
                revents: 0,
            },
        ];
        // SAFETY: fds is a live array of two pollfd values; negative fd is ignored.
        if unsafe { libc::poll(fds.as_mut_ptr(), 2, timeout) } < 0 {
            let e = io::Error::last_os_error();
            if e.kind() != io::ErrorKind::Interrupted {
                return Err(e.into());
            }
        }
        if fds[0].revents != 0 {
            let mut bytes = [0u8; 256];
            while read.read(&mut bytes).is_ok() {}
        }
        if fds[1].revents != 0 {
            let result = keypad.as_mut().unwrap().receive();
            if let Err(e) = result {
                eprintln!("keypad disconnected: {e}");
                keypad = None;
                state.status.connected = false;
                state.status.keypad_access = false;
                retry = Instant::now();
            }
        }
    }
    if let Some(k) = keypad.as_mut() {
        k.restore();
    }
    if let Some(t) = tray {
        t.shutdown();
    }
    drop(guard);
    Ok(())
}

fn main() {
    if let Err(e) = Options::parse()
        .map_err(|e| -> Box<dyn std::error::Error> { e.into() })
        .and_then(run)
    {
        eprintln!("mx-creative-worker: {e}");
        std::process::exit(1);
    }
}
