mod activation;
mod device_buttons;
mod dialpad_buttons;
mod events;
mod focus;
mod hardware;
mod ipc;
mod launcher;
mod ring;
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
    path::{Path, PathBuf},
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
    no_keypad: bool,
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
            no_keypad: false,
            check: false,
        };
        let mut args = env::args_os().skip(1);
        while let Some(arg) = args.next() {
            match arg.to_str() {
                Some("--no-tray") => result.no_tray = true,
                Some("--no-keypad") => result.no_keypad = true,
                Some("--help") => {
                    println!("mxcc-worker [--config PATH] [--cache PATH] [--socket PATH] [--gui-command PATH] [--no-tray] [--no-keypad]\nmxcc-worker --check-config PATH");
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
        Action::Launch { .. } | Action::OpenRing {} => {
            Err("Launcher actions require desktop activation".into())
        }
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

fn execute_action(
    action_value: &Action,
    state: &mut State,
    launcher: &mut launcher::Launcher,
    keyboard: &mut Option<Keyboard>,
) -> Result<bool, String> {
    if let Action::Launch {
        desktop_id,
        wm_classes,
        profile_id,
    } = action_value
    {
        let now = Instant::now();
        let mut redraw = false;
        match launcher.activate(desktop_id, wm_classes, state.status.session == "x11", now)? {
            launcher::Outcome::Existing(window) => {
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
            }
            launcher::Outcome::Started => {
                redraw |= state.launch_succeeded(
                    desktop_id,
                    wm_classes,
                    profile_id.as_deref(),
                    None,
                    false,
                    now,
                );
            }
            launcher::Outcome::Pending => {}
        }
        Ok(redraw)
    } else {
        action(action_value, keyboard).map(|_| false)
    }
}

/// Keypad keys and intercepted device buttons share the same focus-safe dispatch.
fn trigger_action(
    action_value: &Action,
    state: &mut State,
    launcher: &mut launcher::Launcher,
    keyboard: &mut Option<Keyboard>,
    ring: &mut ring::Ring,
    gui: &Path,
    sender: &Sender,
) -> Result<bool, String> {
    if matches!(action_value, Action::OpenRing {}) {
        let result = ring.toggle(
            &state.config.action_ring,
            gui,
            sender,
            &state.status.session,
            state.status.paused,
        );
        state.status.ring_error = result.as_ref().err().cloned();
        state.status.ring_open = ring.is_open();
        return result.map(|_| false);
    }
    let foreground = ring.dismiss();
    state.status.ring_open = ring.is_open();
    let mut redraw = false;
    if let Some(foreground) = foreground {
        if matches!(
            action_value,
            Action::Shortcut { .. } | Action::Screenshot {}
        ) {
            activation::restore_exact(&foreground)?;
            redraw |= state.focus_changed(foreground, Instant::now());
        }
    }
    execute_action(action_value, state, launcher, keyboard).map(|changed| redraw | changed)
}

fn refresh_focus(state: &mut State) -> bool {
    match focus::current() {
        Ok(foreground) if !focus::is_ring(&foreground) => {
            state.focus_changed(foreground, Instant::now())
        }
        _ => false,
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
    let mut ring = ring::Ring::default();
    let buttons = if session == "x11" {
        Some(device_buttons::start(
            sender.clone(),
            state.config.configured_bindings(),
        ))
    } else {
        None
    };
    let dialpad = if session == "x11" {
        Some(dialpad_buttons::start(
            sender.clone(),
            state.config.configured_bindings(),
        ))
    } else {
        None
    };
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
        if ring.tick() {
            redraw |= refresh_focus(&mut state);
        }
        state.status.ring_open = ring.is_open();
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
            if keypad.is_none() && !options.no_keypad {
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
                    let mut ending_ring = None;
                    let result: Result<(), String> = match request {
                        Request::Status {} => {
                            let _ = reply.send(serde_json::to_value(&state.status)?);
                            continue;
                        }
                        Request::RingContext {} => {
                            let response = ring.context().unwrap_or_else(
                                |error| serde_json::json!({"ok":false,"error":error}),
                            );
                            let _ = reply.send(response);
                            continue;
                        }
                        Request::RingToggle {} => {
                            let response = match ring.toggle(
                                &state.config.action_ring,
                                &options.gui,
                                &sender,
                                session,
                                state.status.paused,
                            ) {
                                Ok(open) => {
                                    state.status.ring_error = None;
                                    state.status.ring_open = open;
                                    serde_json::json!({"ok":true,"open":open})
                                }
                                Err(error) => {
                                    state.status.ring_error = Some(error.clone());
                                    serde_json::json!({"ok":false,"error":error})
                                }
                            };
                            let _ = reply.send(response);
                            continue;
                        }
                        Request::RingCancel { token } => match ring.take(&token) {
                            Ok(session) => {
                                ending_ring = Some(session);
                                Ok(())
                            }
                            Err(error) => Err(error),
                        },
                        Request::RingConfigure { token } => match ring.take(&token) {
                            Ok(session) => {
                                let result = session.configure(&options.gui);
                                ending_ring = Some(session);
                                result
                            }
                            Err(error) => Err(error),
                        },
                        Request::RingExecute { token, slot } => match ring.take(&token) {
                            Ok(session) => {
                                let result = (|| -> Result<(), String> {
                                    if state.status.paused {
                                        return Err("Device actions are paused".into());
                                    }
                                    let selected = session.action(slot)?;
                                    if matches!(
                                        selected,
                                        Action::Shortcut { .. } | Action::Screenshot {}
                                    ) {
                                        activation::restore_exact(&session.foreground)?;
                                        redraw |= state.focus_changed(
                                            session.foreground.clone(),
                                            Instant::now(),
                                        );
                                    }
                                    redraw |= execute_action(
                                        &selected,
                                        &mut state,
                                        &mut launcher,
                                        &mut keyboard,
                                    )?;
                                    Ok(())
                                })();
                                ending_ring = Some(session);
                                result
                            }
                            Err(error) => Err(error),
                        },
                        Request::Reload {} => match Config::load(&options.config) {
                            Ok(config) => {
                                ring.close();
                                state.reload(config);
                                if let Some(dialpad) = &dialpad {
                                    dialpad.update(
                                        state.config.configured_bindings(),
                                        state.status.paused,
                                    );
                                }
                                if let Some(buttons) = &buttons {
                                    buttons.update(
                                        state.config.configured_bindings(),
                                        state.status.paused,
                                    );
                                }
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
                            if value {
                                ring.close();
                            }
                            if let Some(dialpad) = &dialpad {
                                dialpad.update(state.config.configured_bindings(), value);
                            }
                            if let Some(buttons) = &buttons {
                                buttons.update(state.config.configured_bindings(), value);
                            }
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
                    if quit || ending_ring.is_some() {
                        let _ = written.recv_timeout(Duration::from_secs(1));
                    }
                    if let Some(session) = ending_ring {
                        ring.finish(session);
                        redraw |= refresh_focus(&mut state);
                    }
                    state.status.ring_open = ring.is_open();
                }
                Event::Focus(foreground) => {
                    if !focus::is_ring(&foreground) && !ring.is_open() {
                        redraw |= state.focus_changed(foreground, Instant::now());
                    }
                }
                Event::FocusSupport(value) => redraw |= state.set_focus_supported(value),
                Event::Tray(value) => state.status.tray_available = value,
                Event::Page(page) => {
                    let _ = state.select_page(page);
                    redraw = true;
                }
                Event::Pause(value) => {
                    state.status.paused = value;
                    if value {
                        ring.close();
                    }
                    if let Some(dialpad) = &dialpad {
                        dialpad.update(state.config.configured_bindings(), value);
                    }
                    if let Some(buttons) = &buttons {
                        buttons.update(state.config.configured_bindings(), value);
                    }
                }
                Event::DeviceButton(binding) => {
                    let Some(action_value) = state.device_action(&binding) else {
                        continue;
                    };
                    match trigger_action(
                        &action_value,
                        &mut state,
                        &mut launcher,
                        &mut keyboard,
                        &mut ring,
                        &options.gui,
                        &sender,
                    ) {
                        Ok(changed) => {
                            redraw |= changed;
                            state.status.last_error = None;
                        }
                        Err(error) => state.status.last_error = Some(error),
                    }
                }
                Event::RingExited(token, error) => {
                    if ring.exited(&token) {
                        state.status.ring_error = error;
                        redraw |= refresh_focus(&mut state);
                    }
                    state.status.ring_open = ring.is_open();
                }
                Event::DeviceButtonsStatus(status) => state.status.device_buttons = status,
                Event::DialpadButtonsStatus(status) => state.status.dialpad_buttons = status,
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
                            match trigger_action(
                                &key.action,
                                &mut state,
                                &mut launcher,
                                &mut keyboard,
                                &mut ring,
                                &options.gui,
                                &sender,
                            ) {
                                Ok(changed) => {
                                    redraw |= changed;
                                    state.status.last_error = None;
                                }
                                Err(error) => state.status.last_error = Some(error),
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
        let mut timeout = if (keypad.is_none() && !options.no_keypad) || keyboard.is_none() {
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
        if let Some(deadline) = state.deadline().into_iter().chain(ring.deadline()).min() {
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
    ring.close();
    drop(buttons);
    drop(dialpad);
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
