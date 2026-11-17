use mx_creative_worker::{
    config::{Action, Config, DeviceBinding},
    control::{Foreground, Request, State},
    website::{BrowserReport, CONTEXT_TTL},
};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

const SOURCE: &str = "0123456789abcdef0123456789abcdef";
const OTHER: &str = "abcdef0123456789abcdef0123456789";
fn send(
    state: &mut State,
    browser: &str,
    source: &str,
    domain: Option<&str>,
    focused: bool,
    foreground: Foreground,
    now: Instant,
) -> Result<(), String> {
    state.browser_context(
        &BrowserReport {
            browser: browser.into(),
            session: source.into(),
            domain: domain.map(str::to_string),
            focused,
            window_id: domain.map(|_| foreground.window as i32),
        },
        foreground,
        now,
    )
}
fn target(control: &str) -> DeviceBinding {
    DeviceBinding {
        vendor_id: 1133,
        product_id: 48128,
        control_id: control.into(),
    }
}
fn mapping(control: &str, keys: &str) -> Value {
    json!({"vendor_id":1133,"product_id":48128,"control_id":control,"action":{"type":"shortcut","keys":keys}})
}
fn profile(domain: &str) -> Value {
    json!({"domain":domain,"device_actions":[mapping("button:1","Ctrl+V"),mapping("button:2","Ctrl+S")],"ring":{"id":format!("website:{domain}"),"wm_classes":[],"slots":[{"label":"Find","icon":"edit-find","action":{"type":"shortcut","keys":"Ctrl+F"}}]}})
}
fn base() -> Value {
    json!({"version":1,"auto_switch":true,"pages":[{"name":"Apps","keys":[null,null,null,null,null,null,null,null,null]},{"name":"Shortcuts","keys":[null,null,null,null,null,null,null,null,null]}],"profiles":[],"device_actions":[mapping("button:1","Ctrl+C")],"website_profiles":[profile("example.com")]})
}
fn read(value: Value) -> Result<Config, String> {
    let result: Config = serde_json::from_value(value).map_err(|e| e.to_string())?;
    result.validate()?;
    Ok(result)
}
fn foreground(window: u32, name: &str) -> Foreground {
    Foreground {
        window,
        name: name.into(),
        identities: vec![name.to_ascii_lowercase()],
    }
}
fn state() -> State {
    State::new(read(base()).unwrap(), "x11".into())
}
fn keys(state: &State, control: &str) -> Option<String> {
    match state.device_action(&target(control)) {
        Some(Action::Shortcut { keys }) => Some(keys),
        _ => None,
    }
}

#[test]
fn website_overrides_and_capture_follow_exact_active_domain_only() {
    let mut state = state();
    let now = Instant::now();
    let firefox = foreground(20, "Firefox");
    assert_eq!(keys(&state, "button:1").as_deref(), Some("Ctrl+C"));
    assert_eq!(state.configured_bindings(), vec![target("button:1")]);
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    assert_eq!(state.status.active_website.as_deref(), Some("example.com"));
    assert_eq!(state.status.website_profile.as_deref(), Some("example.com"));
    assert_eq!(keys(&state, "button:1").as_deref(), Some("Ctrl+V"));
    assert_eq!(keys(&state, "button:2").as_deref(), Some("Ctrl+S"));
    assert_eq!(
        state.configured_bindings(),
        vec![target("button:1"), target("button:2")]
    );
    let snapshot = state.website_snapshot(&firefox, now).unwrap();
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now + Duration::from_secs(1),
    )
    .unwrap();
    assert_eq!(
        state.website_snapshot(&firefox, now),
        Some(snapshot.clone()),
        "heartbeat must not reset the button/ring context"
    );
    for domain in ["other.test", "sub.example.com", "example.com.evil.test"] {
        send(
            &mut state,
            "firefox",
            SOURCE,
            Some(domain),
            true,
            firefox.clone(),
            now,
        )
        .unwrap();
        assert!(!state.website_snapshot_current(&snapshot, now));
        assert_eq!(keys(&state, "button:1").as_deref(), Some("Ctrl+C"));
        assert!(keys(&state, "button:2").is_none());
        assert_eq!(state.configured_bindings(), vec![target("button:1")]);
    }
}

#[test]
fn changed_window_or_private_window_needs_fresh_focused_report() {
    let mut state = state();
    let now = Instant::now();
    let first = foreground(20, "Firefox");
    let second = foreground(21, "Firefox");
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        first.clone(),
        now,
    )
    .unwrap();
    let original = state.website_snapshot(&first, now).unwrap();
    state.focus_changed(second.clone(), now);
    assert!(state.status.active_website.is_none());
    assert!(state.website_snapshot(&second, now).is_none());
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        false,
        second.clone(),
        now,
    )
    .unwrap();
    assert!(
        state.website_snapshot(&second, now).is_none(),
        "background normal tab cannot bind to private window"
    );
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        second.clone(),
        now,
    )
    .unwrap();
    assert!(state.website_snapshot(&second, now).is_some());
    assert!(!state.website_snapshot_current(&original, now));
    state.focus_changed(foreground(99, "Editor"), now);
    assert!(state.status.active_website.is_none());
    assert_eq!(keys(&state, "button:1").as_deref(), Some("Ctrl+C"));
    state.focus_changed(second.clone(), now);
    assert!(
        state.website_snapshot(&second, now).is_none(),
        "returning focus waits for extension report"
    );
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        foreground(22, "not-firefox"),
        now,
    )
    .unwrap();
    assert!(state.status.active_website.is_none());
}

#[test]
fn ring_snapshot_survives_overlay_blur_but_not_navigation_disconnect_or_expiry() {
    let mut state = state();
    let now = Instant::now();
    let firefox = foreground(20, "Firefox");
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    let original = state.website_snapshot(&firefox, now).unwrap();
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        false,
        firefox.clone(),
        now + Duration::from_secs(5),
    )
    .unwrap();
    assert!(state.website_snapshot_current(&original, now + Duration::from_secs(5)));
    send(
        &mut state,
        "firefox",
        OTHER,
        None,
        false,
        firefox.clone(),
        now,
    )
    .unwrap();
    assert!(
        state.website_snapshot_current(&original, now),
        "unrelated host disconnect cannot erase the active ring"
    );
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("other.test"),
        false,
        firefox.clone(),
        now,
    )
    .unwrap();
    assert!(!state.website_snapshot_current(&original, now));
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    assert!(
        !state.website_snapshot_current(&original, now),
        "navigating away and back invalidates old snapshot"
    );
    send(
        &mut state,
        "firefox",
        SOURCE,
        None,
        false,
        firefox.clone(),
        now,
    )
    .unwrap();
    assert!(state.status.active_website.is_none());
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    state.tick(now + CONTEXT_TTL);
    assert!(!state.status.browser_connected);
    assert!(state.status.active_website.is_none());
    assert!(keys(&state, "button:2").is_none());
    assert!(state.deadline().is_none());
}

#[test]
fn competing_connected_firefox_profiles_fail_closed_and_recover_after_expiry() {
    let mut state = state();
    let now = Instant::now();
    let firefox = foreground(20, "Firefox");
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    assert!(send(
        &mut state,
        "firefox",
        OTHER,
        Some("other.test"),
        true,
        firefox.clone(),
        now
    )
    .is_err());
    assert!(state.status.active_website.is_none());
    assert!(state.status.browser_error.is_some());
    assert!(keys(&state, "button:2").is_none());
    state.tick(now + CONTEXT_TTL);
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox,
        now + CONTEXT_TTL,
    )
    .unwrap();
    assert_eq!(state.status.website_profile.as_deref(), Some("example.com"));
    assert!(state.status.browser_error.is_none());
}

#[test]
fn removed_profile_and_paused_actions_cannot_execute_website_overrides() {
    let mut state = state();
    let now = Instant::now();
    let firefox = foreground(20, "Firefox");
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox,
        now,
    )
    .unwrap();
    state.status.paused = true;
    assert!(keys(&state, "button:1").is_none());
    state.status.paused = false;
    let mut replacement = base();
    replacement["website_profiles"] = json!([]);
    state.reload(read(replacement).unwrap());
    assert!(state.status.website_profile.is_none());
    assert_eq!(keys(&state, "button:1").as_deref(), Some("Ctrl+C"));
    assert!(keys(&state, "button:2").is_none());
}

#[test]
fn profile_validation_rejects_ambiguous_domains_duplicate_targets_and_ring_recursion() {
    for domain in [
        "",
        "Example.com",
        "https://example.com",
        "example.com:80",
        "*.example.com",
        "example.com.",
        "-bad.test",
        "bad-.test",
        "foo..bar",
        "foo/bar",
        "é.test",
    ] {
        let mut value = base();
        value["website_profiles"] = json!([profile(domain)]);
        assert!(read(value).is_err(), "{domain}");
    }
    for domain in ["localhost", "127.0.0.1", "xn--caf-dma.test"] {
        let mut value = base();
        value["website_profiles"] = json!([profile(domain)]);
        read(value).unwrap();
    }
    let mut value = base();
    value["website_profiles"] = json!([profile("example.com"), profile("example.com")]);
    assert!(read(value).is_err());
    let mut value = base();
    value["website_profiles"][0]["device_actions"] =
        json!([mapping("button:1", "Ctrl+C"), mapping("button:1", "Ctrl+V")]);
    assert!(read(value).is_err());
    let mut value = base();
    value["website_profiles"][0]["device_actions"][0]["control_id"] = json!("wheel:roller");
    assert!(read(value).is_err());
    let mut value = base();
    value["website_profiles"][0]["ring"]["id"] = json!("website:other.test");
    assert!(read(value).is_err());
    let mut value = base();
    value["website_profiles"][0]["ring"]["slots"][0]["action"] = json!({"type":"open_ring"});
    assert!(read(value).is_err());
    let mut value = base();
    value.as_object_mut().unwrap().remove("website_profiles");
    assert!(read(value).unwrap().website_profiles.is_empty());
}

#[test]
fn native_context_requires_explicit_focus_and_rejects_urls_or_arbitrary_browsers() {
    let mut state = state();
    let now = Instant::now();
    let firefox = foreground(20, "Firefox");
    for (browser, source, domain) in [
        ("chrome", SOURCE, "example.com"),
        ("firefox", "bad", "example.com"),
        ("firefox", SOURCE, "https://example.com/path"),
    ] {
        assert!(send(
            &mut state,
            browser,
            source,
            Some(domain),
            true,
            firefox.clone(),
            now
        )
        .is_err());
    }
    assert!(serde_json::from_value::<Request>(json!({"command":"browser_context","browser":"firefox","session":SOURCE,"domain":"example.com"})).is_err());
    assert!(serde_json::from_value::<Request>(json!({"command":"browser_context","browser":"firefox","session":SOURCE,"domain":"example.com","focused":true,"execute":"command"})).is_err());
}

#[test]
fn clean_disconnect_allows_immediate_reconnect_and_late_old_disconnect_is_ignored() {
    let mut state = state();
    let now = Instant::now();
    let firefox = foreground(20, "Firefox");
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    state.browser_disconnect("firefox", SOURCE, now).unwrap();
    assert!(!state.status.browser_connected);
    assert!(state.status.active_website.is_none());
    send(
        &mut state,
        "firefox",
        OTHER,
        Some("example.com"),
        true,
        firefox.clone(),
        now,
    )
    .unwrap();
    let snapshot = state.website_snapshot(&firefox, now).unwrap();
    state.browser_disconnect("firefox", SOURCE, now).unwrap();
    assert!(state.website_snapshot_current(&snapshot, now));
    assert!(state.status.browser_error.is_none());
    assert_eq!(keys(&state, "button:2").as_deref(), Some("Ctrl+S"));
    state.status.session = "wayland".into();
    assert!(send(
        &mut state,
        "firefox",
        OTHER,
        Some("example.com"),
        true,
        firefox,
        now
    )
    .is_err());
}

#[test]
fn queued_report_from_known_old_browser_window_cannot_bind_to_new_foreground() {
    let mut state = state();
    let now = Instant::now();
    let mut report = BrowserReport {
        browser: "firefox".into(),
        session: SOURCE.into(),
        domain: Some("example.com".into()),
        focused: true,
        window_id: Some(1),
    };
    state
        .browser_context(&report, foreground(20, "Firefox"), now)
        .unwrap();
    // The old report was queued before the OS switched windows.
    assert!(state
        .browser_context(&report, foreground(21, "Firefox"), now)
        .is_err());
    assert!(state.status.active_website.is_none());
    assert!(keys(&state, "button:2").is_none());
    report.window_id = Some(2);
    report.domain = Some("other.test".into());
    state
        .browser_context(&report, foreground(21, "Firefox"), now)
        .unwrap();
    report.window_id = Some(1);
    report.domain = Some("example.com".into());
    assert!(state
        .browser_context(&report, foreground(21, "Firefox"), now)
        .is_err());
    assert_eq!(state.status.active_website.as_deref(), Some("other.test"));
    assert!(keys(&state, "button:2").is_none());
    // Another new browser ID cannot claim an already-associated X11 window.
    report.window_id = Some(3);
    assert!(state
        .browser_context(&report, foreground(21, "Firefox"), now)
        .is_err());
    report.window_id = Some(1);
    state
        .browser_context(&report, foreground(20, "Firefox"), now)
        .unwrap();
    assert_eq!(state.status.website_profile.as_deref(), Some("example.com"));
}

#[test]
fn browser_window_identity_table_is_bounded_and_cleared_on_disconnect() {
    let mut state = state();
    let now = Instant::now();
    for id in 1..=32 {
        send(
            &mut state,
            "firefox",
            SOURCE,
            Some("example.com"),
            true,
            foreground(id, "Firefox"),
            now,
        )
        .unwrap();
    }
    assert!(send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        foreground(33, "Firefox"),
        now
    )
    .is_err());
    assert!(state.status.active_website.is_none());
    state.browser_disconnect("firefox", SOURCE, now).unwrap();
    send(
        &mut state,
        "firefox",
        SOURCE,
        Some("example.com"),
        true,
        foreground(33, "Firefox"),
        now,
    )
    .unwrap();
    for window_id in [None, Some(0), Some(-1)] {
        let report = BrowserReport {
            browser: "firefox".into(),
            session: SOURCE.into(),
            domain: Some("example.com".into()),
            focused: true,
            window_id,
        };
        assert!(state
            .browser_context(&report, foreground(33, "Firefox"), now)
            .is_err());
    }
}

#[test]
fn native_bridge_wire_messages_parse_with_flat_fields() {
    let request: Request = serde_json::from_value(json!({"command":"browser_context", "browser":"firefox", "session":SOURCE, "domain":"example.com", "focused":true, "window_id":17})).unwrap();
    assert!(
        matches!(request,Request::BrowserContext(report) if report.window_id==Some(17) && report.domain.as_deref()==Some("example.com"))
    );
    let request: Request = serde_json::from_value(
        json!({"command":"browser_disconnect","browser":"firefox","session":SOURCE}),
    )
    .unwrap();
    assert!(
        matches!(request,Request::BrowserDisconnect{browser,session} if browser=="firefox" && session==SOURCE)
    );
}
