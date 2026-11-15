//! Focus an existing desktop application through the window manager.
//!
//! EWMH requests intentionally leave deiconification, raising and focus policy
//! to the desktop. Sending a request does not prove it was accepted; the focus
//! observer confirms the foreground app. This module never launches a process.

use std::error::Error;
use x11rb::{
    connection::Connection,
    errors::ReplyError,
    protocol::{
        xproto::{
            Atom, AtomEnum, ClientMessageEvent, ConnectionExt, CreateWindowAux, EventMask,
            GetPropertyReply, PropMode, Window, WindowClass,
        },
        ErrorKind, Event,
    },
    rust_connection::RustConnection,
    wrapper::ConnectionExt as _,
};

type Result<T> = std::result::Result<T, Box<dyn Error>>;

#[derive(Debug)]
pub struct Activation {
    pub window: Window,
    pub wm_class: String,
    pub already_active: bool,
}

struct Atoms {
    active: Atom,
    clients: Atom,
    stacking: Atom,
    supported: Atom,
    class: Atom,
    gtk_app_id: Atom,
    desktop_file: Atom,
    window_type: Atom,
    normal: Atom,
    dialog: Atom,
    desktop: Atom,
    current_desktop: Atom,
    timestamp: Atom,
}

impl Atoms {
    fn new(conn: &RustConnection) -> Result<Self> {
        let atom =
            |name: &[u8]| -> Result<Atom> { Ok(conn.intern_atom(false, name)?.reply()?.atom) };
        Ok(Self {
            active: atom(b"_NET_ACTIVE_WINDOW")?,
            clients: atom(b"_NET_CLIENT_LIST")?,
            stacking: atom(b"_NET_CLIENT_LIST_STACKING")?,
            supported: atom(b"_NET_SUPPORTED")?,
            class: atom(b"WM_CLASS")?,
            gtk_app_id: atom(b"_GTK_APPLICATION_ID")?,
            desktop_file: atom(b"_NET_WM_DESKTOP_FILE")?,
            window_type: atom(b"_NET_WM_WINDOW_TYPE")?,
            normal: atom(b"_NET_WM_WINDOW_TYPE_NORMAL")?,
            dialog: atom(b"_NET_WM_WINDOW_TYPE_DIALOG")?,
            desktop: atom(b"_NET_WM_DESKTOP")?,
            current_desktop: atom(b"_NET_CURRENT_DESKTOP")?,
            timestamp: atom(b"_MXCC_ACTIVATION_TIME")?,
        })
    }
}

#[derive(Debug)]
struct Candidate {
    window: Window,
    classes: Vec<String>,
    gtk_app_id: String,
    desktop_file: String,
    desktop: Option<u32>,
    eligible: bool,
}

fn desktop_stem(value: &str) -> &str {
    let value = value.rsplit('/').next().unwrap_or(value);
    value.strip_suffix(".desktop").unwrap_or(value)
}

fn matches(candidate: &Candidate, desktop_id: &str, classes: &[String]) -> bool {
    if !candidate.eligible {
        return false;
    }
    let stem = desktop_stem(desktop_id);
    candidate.classes.iter().any(|actual| {
        actual.eq_ignore_ascii_case(stem)
            || classes
                .iter()
                .any(|expected| actual.eq_ignore_ascii_case(expected))
    }) || (!candidate.gtk_app_id.is_empty()
        && desktop_stem(&candidate.gtk_app_id).eq_ignore_ascii_case(stem))
        || (!candidate.desktop_file.is_empty()
            && desktop_stem(&candidate.desktop_file).eq_ignore_ascii_case(stem))
}

fn choose<'a>(
    candidates: &'a [Candidate],
    active: Window,
    desktop_id: &str,
    classes: &[String],
) -> Option<&'a Candidate> {
    // _NET_CLIENT_LIST_STACKING is bottom-to-top. Keep the already active app
    // window when possible; otherwise select its highest eligible window.
    candidates
        .iter()
        .find(|candidate| candidate.window == active && matches(candidate, desktop_id, classes))
        .or_else(|| {
            candidates
                .iter()
                .rev()
                .find(|candidate| matches(candidate, desktop_id, classes))
        })
}

fn property(
    conn: &RustConnection,
    window: Window,
    atom: Atom,
    kind: AtomEnum,
) -> std::result::Result<GetPropertyReply, ReplyError> {
    // Bound reads even if another client publishes a malformed property.
    conn.get_property(false, window, atom, kind, 0, 4096)?
        .reply()
}

fn numbers(reply: &GetPropertyReply) -> Vec<u32> {
    reply.value32().map(Iterator::collect).unwrap_or_default()
}

fn text(reply: &GetPropertyReply) -> String {
    String::from_utf8_lossy(
        reply
            .value
            .split(|byte| *byte == 0)
            .next()
            .unwrap_or_default(),
    )
    .into_owned()
}

fn candidate(
    conn: &RustConnection,
    window: Window,
    atoms: &Atoms,
) -> std::result::Result<Candidate, ReplyError> {
    let attributes = conn.get_window_attributes(window)?.reply()?;
    let types = numbers(&property(conn, window, atoms.window_type, AtomEnum::ATOM)?);
    let class = property(conn, window, atoms.class, AtomEnum::STRING)?;
    let classes = class
        .value
        .split(|byte| *byte == 0)
        .filter(|value| !value.is_empty())
        .map(|value| String::from_utf8_lossy(value).into_owned())
        .collect();
    Ok(Candidate {
        window,
        classes,
        gtk_app_id: text(&property(conn, window, atoms.gtk_app_id, AtomEnum::ANY)?),
        desktop_file: text(&property(conn, window, atoms.desktop_file, AtomEnum::ANY)?),
        desktop: numbers(&property(conn, window, atoms.desktop, AtomEnum::CARDINAL)?)
            .first()
            .copied(),
        // Missing _NET_WM_WINDOW_TYPE denotes a normal window (or a transient
        // dialog). Unmapped/minimized windows remain eligible for activation.
        eligible: !attributes.override_redirect
            && attributes.class == WindowClass::INPUT_OUTPUT
            && (types.is_empty() || types.contains(&atoms.normal) || types.contains(&atoms.dialog)),
    })
}

fn request(
    conn: &RustConnection,
    root: Window,
    window: Window,
    atom: Atom,
    data: [u32; 5],
) -> Result<()> {
    let event = ClientMessageEvent::new(32, window, atom, data);
    conn.send_event(
        false,
        root,
        EventMask::SUBSTRUCTURE_NOTIFY | EventMask::SUBSTRUCTURE_REDIRECT,
        event,
    )?
    .check()?;
    Ok(())
}

fn server_timestamp(conn: &RustConnection, root: Window, atom: Atom) -> Result<u32> {
    // A hardware key press has no X event timestamp. Request one from the
    // server using an unmapped input-only window; no screen content changes.
    let window = conn.generate_id()?;
    conn.create_window(
        0,
        window,
        root,
        0,
        0,
        1,
        1,
        0,
        WindowClass::INPUT_ONLY,
        0,
        &CreateWindowAux::new().event_mask(EventMask::PROPERTY_CHANGE),
    )?
    .check()?;
    let result = (|| -> Result<u32> {
        conn.change_property8(PropMode::REPLACE, window, atom, AtomEnum::STRING, b"time")?
            .check()?;
        conn.flush()?;
        loop {
            if let Event::PropertyNotify(event) = conn.wait_for_event()? {
                if event.window == window && event.atom == atom {
                    return Ok(event.time);
                }
            }
        }
    })();
    let _ = conn.destroy_window(window);
    let _ = conn.flush();
    result
}

/// Return an existing target after requesting its activation, or None only when
/// no eligible match exists. Errors must not be treated as permission to create
/// a duplicate window. The caller should use its focus observer to confirm.
pub fn activate_existing(
    desktop_id: &str,
    wm_classes: &[String],
) -> std::result::Result<Option<Activation>, String> {
    activate(desktop_id, wm_classes)
        .map_err(|error| format!("Cannot activate {desktop_id}: {error}"))
}

fn activate(desktop_id: &str, wm_classes: &[String]) -> Result<Option<Activation>> {
    let (conn, screen) = x11rb::connect(None)?;
    let root = conn.setup().roots[screen].root;
    let atoms = Atoms::new(&conn)?;
    let supported = numbers(&property(&conn, root, atoms.supported, AtomEnum::ATOM)?);
    if !supported.contains(&atoms.active) {
        return Err("the window manager does not support application activation".into());
    }
    let mut list = property(&conn, root, atoms.stacking, AtomEnum::WINDOW)?;
    if list.type_ == u32::from(AtomEnum::NONE) {
        list = property(&conn, root, atoms.clients, AtomEnum::WINDOW)?;
    }
    if list.type_ == u32::from(AtomEnum::NONE) || list.bytes_after != 0 {
        return Err("the window manager did not provide a complete application window list".into());
    }
    let active = numbers(&property(&conn, root, atoms.active, AtomEnum::WINDOW)?)
        .first()
        .copied()
        .unwrap_or(0);
    let mut candidates = Vec::new();
    for window in numbers(&list) {
        match candidate(&conn, window, &atoms) {
            Ok(candidate) => candidates.push(candidate),
            // A window can close between the root snapshot and its properties.
            Err(ReplyError::X11Error(error)) if error.error_kind == ErrorKind::Window => {}
            Err(error) => return Err(error.into()),
        }
    }
    let Some(target) = choose(&candidates, active, desktop_id, wm_classes) else {
        return Ok(None);
    };
    let already_active = target.window == active;
    if !already_active {
        let timestamp = server_timestamp(&conn, root, atoms.timestamp)?;
        let current = numbers(&property(
            &conn,
            root,
            atoms.current_desktop,
            AtomEnum::CARDINAL,
        )?)
        .first()
        .copied();
        if let Some(desktop) = target
            .desktop
            .filter(|desktop| *desktop != u32::MAX && Some(*desktop) != current)
        {
            request(
                &conn,
                root,
                root,
                atoms.current_desktop,
                [desktop, timestamp, 0, 0, 0],
            )?;
        }
        // Source 2 identifies a taskbar/pager-style user activation. The window
        // manager restores minimized windows and raises/focuses the target.
        request(
            &conn,
            root,
            target.window,
            atoms.active,
            [2, timestamp, 0, 0, 0],
        )?;
        conn.flush()?;
    }
    Ok(Some(Activation {
        window: target.window,
        wm_class: target.classes.last().cloned().unwrap_or_default(),
        already_active,
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn window(id: Window, instance: &str, class: &str) -> Candidate {
        Candidate {
            window: id,
            classes: vec![instance.into(), class.into()],
            gtk_app_id: String::new(),
            desktop_file: String::new(),
            desktop: None,
            eligible: true,
        }
    }

    #[test]
    fn matches_exact_class_or_instance_without_fuzzy_application_titles() {
        let candidate = window(1, "editor-instance", "Code");
        assert!(matches(&candidate, "code.desktop", &[]));
        assert!(matches(
            &candidate,
            "vendor-editor.desktop",
            &["EDITOR-INSTANCE".into()]
        ));
        assert!(!matches(&candidate, "codium.desktop", &[]));
        assert!(!matches(&candidate, "editor.desktop", &[]));
    }

    #[test]
    fn matches_gtk_and_desktop_file_ids_without_requiring_window_class() {
        let mut candidate = window(1, "", "");
        candidate.gtk_app_id = "org.example.App".into();
        assert!(matches(&candidate, "org.example.App.desktop", &[]));
        candidate.gtk_app_id.clear();
        candidate.desktop_file =
            "/var/lib/snapd/desktop/applications/firefox_firefox.desktop".into();
        assert!(matches(&candidate, "firefox_firefox.desktop", &[]));
        assert!(!matches(&candidate, "firefox.desktop", &[]));
    }

    #[test]
    fn chooses_active_match_then_topmost_matching_application() {
        let windows = [
            window(1, "code", "Code"),
            window(2, "browser", "Firefox"),
            window(3, "code", "Code"),
        ];
        assert_eq!(choose(&windows, 1, "code.desktop", &[]).unwrap().window, 1);
        assert_eq!(choose(&windows, 2, "code.desktop", &[]).unwrap().window, 3);
        assert!(choose(&windows, 1, "missing.desktop", &[]).is_none());
    }

    #[test]
    fn excluded_window_types_do_not_hide_real_or_minimized_applications() {
        let mut windows = [window(1, "app", "App"), window(2, "app", "App")];
        windows[1].eligible = false;
        windows[0].desktop = Some(2); // Another workspace remains eligible.
        assert_eq!(choose(&windows, 2, "app.desktop", &[]).unwrap().window, 1);
    }
}
