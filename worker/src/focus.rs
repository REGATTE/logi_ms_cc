use crate::events::{Event, Sender};
use mx_creative_worker::control::Foreground;
use x11rb::{
    connection::Connection,
    errors::ReplyError,
    protocol::{
        xproto::{Atom, AtomEnum, ChangeWindowAttributesAux, ConnectionExt, EventMask, Window},
        ErrorKind, Event as XEvent,
    },
    rust_connection::RustConnection,
};

fn foreground(window: Window, class: &[u8], gtk_id: &[u8], desktop_file: &[u8]) -> Foreground {
    if window == 0 {
        return Foreground::default();
    }
    let classes: Vec<_> = class
        .split(|byte| *byte == 0)
        .filter(|value| !value.is_empty())
        .map(|value| String::from_utf8_lossy(value).into_owned())
        .collect();
    let name = classes.last().cloned().unwrap_or_default();
    let mut identities = Vec::new();
    let mut insert = |value: &str| {
        let value = value.to_ascii_lowercase();
        if !value.is_empty() && !identities.contains(&value) {
            identities.push(value);
        }
    };
    for class in &classes {
        insert(class);
    }
    for bytes in [gtk_id, desktop_file] {
        let value =
            String::from_utf8_lossy(bytes.split(|byte| *byte == 0).next().unwrap_or_default());
        let value = value.rsplit('/').next().unwrap_or_default();
        insert(value.strip_suffix(".desktop").unwrap_or(value));
    }
    Foreground {
        window,
        name,
        identities,
    }
}

fn read_foreground(
    conn: &RustConnection,
    window: Window,
    class: Atom,
    gtk_id: Atom,
    desktop_file: Atom,
) -> Result<Foreground, ReplyError> {
    if window == 0 {
        return Ok(Foreground::default());
    }
    // Queue independent reads together; disappearing windows are handled by
    // the caller, while connection failures disable focus tracking accurately.
    let class = conn.get_property(false, window, class, AtomEnum::STRING, 0, 1024)?;
    let gtk_id = conn.get_property(false, window, gtk_id, AtomEnum::ANY, 0, 1024)?;
    let desktop_file = conn.get_property(false, window, desktop_file, AtomEnum::ANY, 0, 1024)?;
    Ok(foreground(
        window,
        &class.reply()?.value,
        &gtk_id.reply()?.value,
        &desktop_file.reply()?.value,
    ))
}

pub fn start(sender: Sender) {
    std::thread::spawn(move || {
        let result = (|| -> Result<(), Box<dyn std::error::Error>> {
            let (conn, screen) = x11rb::connect(None)?;
            let root = conn.setup().roots[screen].root;
            let active = conn
                .intern_atom(false, b"_NET_ACTIVE_WINDOW")?
                .reply()?
                .atom;
            let class = conn.intern_atom(false, b"WM_CLASS")?.reply()?.atom;
            let gtk_id = conn
                .intern_atom(false, b"_GTK_APPLICATION_ID")?
                .reply()?
                .atom;
            let desktop_file = conn
                .intern_atom(false, b"_NET_WM_DESKTOP_FILE")?
                .reply()?
                .atom;
            let pid = conn.intern_atom(false, b"_NET_WM_PID")?.reply()?.atom;
            conn.change_window_attributes(
                root,
                &ChangeWindowAttributesAux::new().event_mask(EventMask::PROPERTY_CHANGE),
            )?
            .check()?;
            conn.flush()?;
            let mut window = 0;
            let mut update = || -> Result<(), Box<dyn std::error::Error>> {
                let property = conn
                    .get_property(false, root, active, AtomEnum::WINDOW, 0, 1)?
                    .reply()?;
                let next = property
                    .value32()
                    .and_then(|mut value| value.next())
                    .unwrap_or(0);
                if next != window {
                    if window != 0 {
                        let _ = conn.change_window_attributes(
                            window,
                            &ChangeWindowAttributesAux::new().event_mask(EventMask::NO_EVENT),
                        );
                    }
                    window = next;
                    if window != 0 {
                        let _ = conn.change_window_attributes(
                            window,
                            &ChangeWindowAttributesAux::new().event_mask(
                                EventMask::PROPERTY_CHANGE | EventMask::STRUCTURE_NOTIFY,
                            ),
                        );
                    }
                }
                let snapshot = match read_foreground(&conn, window, class, gtk_id, desktop_file) {
                    Ok(snapshot) => snapshot,
                    Err(ReplyError::X11Error(error)) if error.error_kind == ErrorKind::Window => {
                        Foreground::default()
                    }
                    Err(error) => return Err(error.into()),
                };
                sender.send(Event::Focus(snapshot));
                conn.flush()?;
                Ok(())
            };
            update()?;
            sender.send(Event::FocusSupport(true));
            loop {
                match conn.wait_for_event()? {
                    XEvent::PropertyNotify(event)
                        if [active, class, gtk_id, desktop_file, pid].contains(&event.atom) =>
                    {
                        update()?
                    }
                    XEvent::DestroyNotify(_) => update()?,
                    _ => {}
                }
            }
        })();
        if let Err(error) = result {
            eprintln!("X11 focus tracking unavailable: {error}");
        }
        sender.send(Event::Focus(Foreground::default()));
        sender.send(Event::FocusSupport(false));
    });
}

/// Read focus synchronously for a user-triggered ring. The watcher may still
/// have an older event queued when the button is pressed.
pub fn current() -> Result<Foreground, String> {
    let read = || -> Result<Foreground, Box<dyn std::error::Error>> {
        let (conn, screen) = x11rb::connect(None)?;
        let root = conn.setup().roots[screen].root;
        let atom = |name: &[u8]| -> Result<Atom, Box<dyn std::error::Error>> {
            Ok(conn.intern_atom(false, name)?.reply()?.atom)
        };
        let active = atom(b"_NET_ACTIVE_WINDOW")?;
        let window = conn
            .get_property(false, root, active, AtomEnum::WINDOW, 0, 1)?
            .reply()?
            .value32()
            .and_then(|mut values| values.next())
            .unwrap_or(0);
        Ok(read_foreground(
            &conn,
            window,
            atom(b"WM_CLASS")?,
            atom(b"_GTK_APPLICATION_ID")?,
            atom(b"_NET_WM_DESKTOP_FILE")?,
        )?)
    };
    read().map_err(|error| format!("Cannot read the active application: {error}"))
}

pub fn is_ring(foreground: &Foreground) -> bool {
    foreground
        .identities
        .iter()
        .chain(std::iter::once(&foreground.name))
        .any(|identity| identity.eq_ignore_ascii_case("mx-creative-control-ring"))
}

/// Read the owner of a freshly observed X11 window; never infer process identity
/// from a title or from the accessibility tree itself.
pub fn window_pid(window: u32) -> Result<u32, String> {
    let read = || -> Result<u32, Box<dyn std::error::Error>> {
        if window == 0 {
            return Ok(0);
        }
        let (conn, _) = x11rb::connect(None)?;
        let atom = conn.intern_atom(false, b"_NET_WM_PID")?.reply()?.atom;
        let property = conn
            .get_property(false, window, atom, AtomEnum::CARDINAL, 0, 1)?
            .reply()?;
        Ok(property
            .value32()
            .and_then(|mut values| values.next())
            .unwrap_or(0))
    };
    read().map_err(|error| format!("Cannot read the active application's process: {error}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn retains_display_class_and_normalizes_distinct_app_identities() {
        let snapshot = foreground(
            42,
            b"firefox\0Firefox\0",
            b"org.mozilla.Firefox\0",
            b"/var/lib/snapd/desktop/applications/firefox_firefox.desktop\0",
        );
        assert_eq!(snapshot.window, 42);
        assert_eq!(snapshot.name, "Firefox");
        assert_eq!(
            snapshot.identities,
            ["firefox", "org.mozilla.firefox", "firefox_firefox"]
        );
    }

    #[test]
    fn ids_match_without_class_and_empty_focus_discards_stale_identity() {
        let snapshot = foreground(9, b"", b"org.example.App\0", b"org.example.App.desktop\0");
        assert_eq!(snapshot.name, "");
        assert_eq!(snapshot.identities, ["org.example.app"]);
        let empty = foreground(0, b"OldApp\0", b"org.old.App\0", b"old.desktop\0");
        assert_eq!(empty.window, 0);
        assert!(empty.name.is_empty());
        assert!(empty.identities.is_empty());
    }
}
