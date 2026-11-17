use mx_creative_worker::{
    config::{Config, DeviceBinding},
    control::{Foreground, State},
    website::BrowserReport,
};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

fn action() -> Value {
    json!({"type":"dial_shortcut","clockwise":"Right","counterclockwise":"Left"})
}
fn mapping() -> Value {
    json!({"vendor_id":1133,"product_id":48128,"control_id":"dial:main","action":action()})
}
fn base() -> Value {
    json!({"version":1,"auto_switch":true,"pages":[{"name":"Apps","keys":[null,null,null,null,null,null,null,null,null]},{"name":"Shortcuts","keys":[null,null,null,null,null,null,null,null,null]}],"profiles":[],"device_actions":[],"website_profiles":[{"domain":"10.64.192.14","device_actions":[mapping()],"ring":null}]})
}
fn read(value: Value) -> Result<Config, String> {
    let config: Config = serde_json::from_value(value).map_err(|e| e.to_string())?;
    config.validate()?;
    Ok(config)
}
fn firefox() -> Foreground {
    Foreground {
        window: 20,
        name: "Firefox".into(),
        identities: vec!["firefox".into()],
    }
}
fn site(state: &mut State, domain: &str, now: Instant) {
    state
        .browser_context(
            &BrowserReport {
                browser: "firefox".into(),
                session: "0123456789abcdef0123456789abcdef".into(),
                domain: Some(domain.into()),
                focused: true,
                window_id: Some(1),
            },
            firefox(),
            now,
        )
        .unwrap();
}

#[test]
fn only_exact_large_dial_accepts_rotary_actions_in_global_or_website_mappings() {
    let good = base();
    assert!(read(good.clone()).is_ok());
    let mut global = good.clone();
    global["device_actions"] = json!([mapping()]);
    assert!(read(global).is_ok());
    for (vendor, product, control) in [
        (1133, 48128, "button:1"),
        (1133, 0xb042, "hidpp:416"),
        (1133, 0xc07e, "button:4"),
        (1133, 0xb042, "dial:main"),
        (1, 48128, "dial:main"),
        (1133, 48128, "dial:small"),
    ] {
        let mut invalid = good.clone();
        let mapping = &mut invalid["website_profiles"][0]["device_actions"][0];
        mapping["vendor_id"] = json!(vendor);
        mapping["product_id"] = json!(product);
        mapping["control_id"] = json!(control);
        assert!(read(invalid).is_err(), "{vendor}/{product}/{control}");
    }
    for action in [
        json!({"type":"shortcut","keys":"Right"}),
        json!({"type":"open_ring"}),
        json!({"type":"none"}),
        json!({"type":"dial_shortcut","clockwise":"Ctrl","counterclockwise":"Left"}),
    ] {
        let mut invalid = good.clone();
        invalid["website_profiles"][0]["device_actions"][0]["action"] = action;
        assert!(read(invalid).is_err());
    }
}

#[test]
fn keypad_and_ring_actions_cannot_accept_dial_shortcuts() {
    let mut invalid = base();
    invalid["pages"][0]["keys"][0] = json!({"label":"Dial","image":"dial.jpg","action":action()});
    assert!(read(invalid).is_err());
    let mut invalid = base();
    invalid["website_profiles"][0]["ring"] = json!({"id":"website:10.64.192.14","wm_classes":[],"slots":[{"label":"Dial","icon":"","action":action()}]});
    assert!(read(invalid).is_err());
    let mut invalid = base();
    invalid["action_ring"] = json!({"bindings":[DeviceBinding::main_dial()],"layouts":[]});
    assert!(read(invalid).is_err());
}

#[test]
fn website_capture_requires_exact_live_site_and_rejects_changed_and_returned_contexts() {
    let mut state = State::new(read(base()).unwrap(), "x11".into());
    let now = Instant::now();
    assert!(state.dial_capture(now).is_none());
    site(&mut state, "10.64.192.14", now);
    let capture = state.dial_capture(now).unwrap();
    assert_eq!(capture.keys(1), Some("Right"));
    assert_eq!(capture.keys(-1), Some("Left"));
    assert_eq!(capture.keys(0), None);
    assert!(capture.current(state.dial_capture(now).as_ref(), now, now));
    assert!(!capture.current(
        state.dial_capture(now).as_ref(),
        now,
        now + Duration::from_millis(251)
    ));
    site(&mut state, "other.test", now);
    assert!(state.dial_capture(now).is_none());
    site(&mut state, "10.64.192.14", now);
    assert!(
        !capture.current(state.dial_capture(now).as_ref(), now, now),
        "A to B to A must not replay earlier rotations"
    );
    for outside in ["sub.10.64.192.14", "10.64.192.14.evil.test"] {
        site(&mut state, outside, now);
        assert!(state.dial_capture(now).is_none());
    }
    site(&mut state, "10.64.192.14", now);
    state.status.paused = true;
    assert!(state.dial_capture(now).is_none());
    state.status.paused = false;
    state.focus_changed(
        Foreground {
            window: 99,
            name: "Terminal".into(),
            identities: vec![],
        },
        now,
    );
    assert!(state.dial_capture(now).is_none());
}

#[test]
fn same_binding_with_new_chords_or_removed_profile_invalidates_queued_capture() {
    let mut state = State::new(read(base()).unwrap(), "x11".into());
    let now = Instant::now();
    site(&mut state, "10.64.192.14", now);
    let captured = state.dial_capture(now).unwrap();
    let mut changed = base();
    changed["website_profiles"][0]["device_actions"][0]["action"]["clockwise"] =
        json!("Ctrl+Right");
    state.reload(read(changed).unwrap());
    assert!(!captured.current(state.dial_capture(now).as_ref(), now, now));
    let mut removed = base();
    removed["website_profiles"] = json!([]);
    state.reload(read(removed).unwrap());
    assert!(state.dial_capture(now).is_none());
}
