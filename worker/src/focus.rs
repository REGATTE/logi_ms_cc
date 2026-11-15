use crate::events::{Event, Sender};
use x11rb::{
    connection::Connection,
    protocol::{
        xproto::{AtomEnum, ChangeWindowAttributesAux, ConnectionExt, EventMask},
        Event as XEvent,
    },
};

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
                let next = property.value32().and_then(|mut v| v.next()).unwrap_or(0);
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
                            &ChangeWindowAttributesAux::new()
                                .event_mask(EventMask::PROPERTY_CHANGE),
                        );
                    }
                }
                let name = if window == 0 {
                    String::new()
                } else {
                    match conn
                        .get_property(false, window, class, AtomEnum::STRING, 0, 1024)?
                        .reply()
                    {
                        Ok(p) => p
                            .value
                            .split(|b| *b == 0)
                            .rfind(|s| !s.is_empty())
                            .map(|s| String::from_utf8_lossy(s).into_owned())
                            .unwrap_or_default(),
                        Err(_) => String::new(),
                    }
                };
                sender.send(Event::Focus(name));
                conn.flush()?;
                Ok(())
            };
            update()?;
            sender.send(Event::FocusSupport(true));
            loop {
                if let XEvent::PropertyNotify(event) = conn.wait_for_event()? {
                    if event.atom == active || event.atom == class {
                        update()?;
                    }
                }
            }
        })();
        if let Err(e) = result {
            eprintln!("X11 focus tracking unavailable: {e}");
        }
        sender.send(Event::FocusSupport(false));
    });
}
