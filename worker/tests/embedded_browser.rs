use mx_creative_worker::{
    config::Config,
    control::{Foreground, State},
    embedded::{Context, Observation, Report, Target, CONTEXT_TTL},
    website::BrowserReport,
};
use serde_json::json;
use std::time::{Duration, Instant};

fn app(window: u32, name: &str) -> Foreground {
    Foreground {
        window,
        name: name.into(),
        identities: vec![name.to_lowercase()],
    }
}
fn target(revision: u64) -> Target {
    Target {
        window: 42,
        pid: 1000,
        domains: vec!["10.64.192.14".into()],
        target_revision: revision,
    }
}
fn observation(
    generation: u64,
    target_revision: u64,
    revision: u64,
    domain: Option<&str>,
    now: Instant,
) -> Observation {
    Observation {
        generation,
        received: now,
        result: Ok(Report {
            window: 42,
            pid: 1000,
            target_revision,
            revision,
            domain: domain.map(str::to_string),
            ready: true,
            error: None,
        }),
    }
}
fn context() -> Context {
    let mut context = Context::default();
    context.target(target(1));
    context
}
fn state() -> State {
    let config:Config=serde_json::from_value(json!({"version":1,"auto_switch":true,"profiles":[],"pages":[{"name":"Apps","keys":[null,null,null,null,null,null,null,null,null]},{"name":"Shortcuts","keys":[null,null,null,null,null,null,null,null,null]}],"website_profiles":[{"domain":"10.64.192.14","device_actions":[{"vendor_id":1133,"product_id":48128,"control_id":"dial:main","action":{"type":"dial_shortcut","clockwise":"Right","counterclockwise":"Left"}}],"ring":null}]})).unwrap();
    config.validate().unwrap();
    State::new(config, "x11".into())
}

#[test]
fn only_current_exact_chatgpt_window_pid_and_allowed_host_create_context() {
    let now = Instant::now();
    let chat = app(42, "Chatgpt");
    for (foreground, pid) in [
        (app(42, "Firefox"), 1000),
        (app(43, "Chatgpt"), 1000),
        (chat.clone(), 1001),
        (chat.clone(), 0),
        (app(42, "Chatgpt-other"), 1000),
    ] {
        let mut context = context();
        context
            .update(
                observation(1, 1, 1, Some("10.64.192.14"), now),
                &foreground,
                pid,
                now,
            )
            .unwrap();
        assert!(context.snapshot(&chat, now).is_none());
    }
    for host in [
        "evil.test",
        "sub.10.64.192.14",
        "http://10.64.192.14:8800",
        "10.64.192.14.evil.test",
    ] {
        let mut context = context();
        assert!(context
            .update(observation(1, 1, 1, Some(host), now), &chat, 1000, now)
            .is_err());
        assert!(context.snapshot(&chat, now).is_none());
    }
    let mut context = context();
    context
        .update(
            observation(1, 1, 1, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    let snapshot = context.snapshot(&chat, now).unwrap();
    assert_eq!(snapshot.source, "chatgpt");
    assert!(snapshot.matches_process(1000));
    assert!(!snapshot.matches_process(1001));
}

#[test]
fn target_tokens_child_generation_document_revision_and_expiry_reject_stale_context() {
    let now = Instant::now();
    let chat = app(42, "Chatgpt");
    let mut context = context();
    context
        .update(
            observation(1, 1, 5, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    let snapshot = context.snapshot(&chat, now).unwrap();
    context
        .update(
            observation(
                1,
                1,
                5,
                Some("10.64.192.14"),
                now + Duration::from_millis(100),
            ),
            &chat,
            1000,
            now + Duration::from_millis(100),
        )
        .unwrap();
    assert_eq!(
        context.snapshot(&chat, now),
        Some(snapshot.clone()),
        "heartbeat must retain capture identity"
    );
    context
        .update(observation(1, 1, 6, None, now), &chat, 1000, now)
        .unwrap();
    assert!(context.snapshot(&chat, now).is_none());
    context
        .update(
            observation(1, 1, 7, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    assert_ne!(
        context.snapshot(&chat, now),
        Some(snapshot.clone()),
        "returning to same host after chat focus must be new"
    );
    assert!(context
        .update(
            observation(1, 1, 6, Some("10.64.192.14"), now),
            &chat,
            1000,
            now
        )
        .is_err());
    assert!(context.snapshot(&chat, now).is_none());
    context.target(Target {
        target_revision: 2,
        ..Target::default()
    });
    context.target(target(3));
    context
        .update(
            observation(1, 1, 99, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    assert!(context.snapshot(&chat, now).is_none());
    context
        .update(
            observation(2, 3, 1, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    let restarted = context.snapshot(&chat, now).unwrap();
    assert_ne!(restarted, snapshot);
    context
        .update(observation(1, 3, 999, None, now), &chat, 1000, now)
        .unwrap();
    assert_eq!(context.snapshot(&chat, now), Some(restarted));
    assert!(context.snapshot(&chat, now + CONTEXT_TTL).is_none());
    context.tick(now + CONTEXT_TTL);
    assert!(!context.status(now + CONTEXT_TTL).connected);
}

#[test]
fn disconnected_not_ready_bad_revision_and_delayed_reports_fail_closed() {
    let now = Instant::now();
    let chat = app(42, "Chatgpt");
    let mut context = context();
    context
        .update(
            observation(1, 1, 1, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    context
        .update(
            Observation {
                generation: 1,
                received: now,
                result: Err("helper exited".into()),
            },
            &chat,
            1000,
            now,
        )
        .unwrap();
    assert!(context.snapshot(&chat, now).is_none());
    assert!(!context.status(now).connected);
    context
        .update(
            observation(2, 1, 1, Some("10.64.192.14"), now),
            &chat,
            1000,
            now + CONTEXT_TTL,
        )
        .unwrap();
    assert!(context.snapshot(&chat, now + CONTEXT_TTL).is_none());
    let startup = Observation {
        generation: 3,
        received: now,
        result: Ok(Report {
            window: 0,
            pid: 0,
            target_revision: 0,
            revision: 0,
            domain: None,
            ready: false,
            error: Some("GI is missing".into()),
        }),
    };
    context.update(startup, &chat, 1000, now).unwrap();
    assert_eq!(context.status(now).errors, vec!["GI is missing"]);
    assert!(context.snapshot(&chat, now).is_none());
    context
        .update(
            observation(3, 1, 4, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    assert!(context
        .update(observation(3, 1, 4, None, now), &chat, 1000, now)
        .is_err());
    assert!(context.snapshot(&chat, now).is_none());
}

#[test]
fn firefox_heartbeats_coexist_without_changing_embedded_provider_or_bypassing_firefox_gate() {
    let now = Instant::now();
    let chat = app(42, "Chatgpt");
    let mut state = state();
    state.focus_changed(chat.clone(), now);
    state.embedded_target(target(1), now);
    state
        .embedded_report(
            observation(1, 1, 1, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    let capture = state.dial_capture(now).unwrap();
    assert_eq!(state.status.website_source.as_deref(), Some("chatgpt"));
    let firefox_report = BrowserReport {
        browser: "firefox".into(),
        session: "0123456789abcdef0123456789abcdef".into(),
        domain: None,
        focused: false,
        window_id: None,
    };
    state
        .browser_context(&firefox_report, chat.clone(), now)
        .unwrap();
    assert_eq!(state.dial_capture(now), Some(capture.clone()));
    assert!(state.status.browser_connected);
    let mut spoof = firefox_report.clone();
    spoof.browser = "chatgpt".into();
    spoof.domain = Some("10.64.192.14".into());
    spoof.window_id = Some(1);
    spoof.focused = true;
    assert!(state.browser_context(&spoof, chat.clone(), now).is_err());
    assert_eq!(state.status.website_source.as_deref(), Some("chatgpt"));
    state
        .embedded_report(observation(1, 1, 2, None, now), &chat, 1000, now)
        .unwrap();
    assert!(state.dial_capture(now).is_none());
    state
        .embedded_report(
            observation(1, 1, 3, Some("10.64.192.14"), now),
            &chat,
            1000,
            now,
        )
        .unwrap();
    assert!(!capture.current(state.dial_capture(now).as_ref(), now, now));
    assert!(state.dial_capture(now + CONTEXT_TTL).is_none());
    let firefox = app(55, "Firefox");
    let mut report = firefox_report;
    report.domain = Some("10.64.192.14".into());
    report.focused = true;
    report.window_id = Some(2);
    state
        .browser_context(&report, firefox.clone(), now)
        .unwrap();
    assert_eq!(state.status.website_source.as_deref(), Some("firefox"));
    state
        .embedded_report(observation(1, 1, 4, None, now), &firefox, 0, now)
        .unwrap();
    assert_eq!(state.status.website_source.as_deref(), Some("firefox"));
}
