use mx_creative_worker::{
    config::Config,
    control::{Foreground, State},
};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

fn config(auto_switch: bool) -> Config {
    let keys = vec![Value::Null; 9];
    let config: Config = serde_json::from_value(json!({
        "version": 1, "auto_switch": auto_switch,
        "pages": [{"name": "Apps", "keys": keys}, {"name": "General", "keys": keys}],
        "profiles": [
            {"id": "firefox", "wm_classes": ["firefox_firefox"],
             "page": {"name": "Firefox", "keys": keys}},
            {"id": "editor", "wm_classes": ["code"],
             "page": {"name": "Editor", "keys": keys}}
        ]
    }))
    .unwrap();
    config.validate().unwrap();
    config
}

fn firefox() -> Foreground {
    Foreground {
        window: 42,
        name: "Navigator".into(),
        identities: vec!["navigator".into(), "FIREFOX_FIREFOX".into()],
    }
}

fn focus(name: &str) -> Foreground {
    Foreground {
        window: 43,
        name: name.into(),
        identities: vec![name.into()],
    }
}

fn state() -> State {
    let mut state = State::new(config(true), "x11".into());
    state.set_focus_supported(true);
    state
}

#[test]
fn externally_focused_apps_show_their_profiles_without_keypad_activation() {
    let now = Instant::now();
    let mut state = state();
    assert!(state.focus_changed(firefox(), now));
    assert_eq!(state.status.page, 1);
    assert_eq!(state.page().name, "Firefox");
    assert!(!state.focus_changed(firefox(), now));

    state.focus_changed(focus("Code"), now);
    assert_eq!(state.page().name, "Editor");
    state.focus_changed(focus("Unknown"), now);
    assert_eq!(state.page().name, "Apps");
    state.focus_changed(firefox(), now);
    assert_eq!(state.page().name, "Firefox");
    state.focus_changed(Foreground::default(), now);
    assert_eq!(state.page().name, "Apps");
}

#[test]
fn initial_focus_and_tracking_recovery_select_shortcuts() {
    let now = Instant::now();
    let mut state = State::new(config(true), "x11".into());
    state.focus_changed(firefox(), now);
    assert_eq!(state.page().name, "Apps");
    assert!(state.set_focus_supported(true));
    assert_eq!(state.page().name, "Firefox");
    assert!(state.set_focus_supported(false));
    assert_eq!(state.page().name, "Apps");
    assert!(state.set_focus_supported(true));
    assert_eq!(state.page().name, "Firefox");
}

#[test]
fn physical_page_choice_survives_refreshes_until_focus_changes() {
    let now = Instant::now();
    let mut state = state();
    state.focus_changed(firefox(), now);
    state.select_page(0).unwrap();
    state.focus_changed(firefox(), now);
    state.tick(now + Duration::from_secs(30));
    state.reload(config(true));
    assert_eq!(state.page().name, "Apps");
    state.focus_changed(focus("Unknown"), now);
    state.focus_changed(firefox(), now);
    assert_eq!(state.page().name, "Firefox");
}

#[test]
fn manual_profiles_stay_pinned_until_follow_active_app_is_selected() {
    let now = Instant::now();
    let mut state = state();
    state.select_profile(Some("editor".into())).unwrap();
    state.select_page(1).unwrap();
    state.focus_changed(firefox(), now);
    assert_eq!(state.page().name, "Editor");
    state.focus_changed(focus("Unknown"), now);
    assert_eq!(state.page().name, "Editor");
    state.select_profile(None).unwrap();
    assert_eq!(state.page().name, "Apps");
    state.focus_changed(firefox(), now);
    state.select_page(0).unwrap();
    state.select_profile(None).unwrap();
    assert_eq!(state.page().name, "Firefox");
}

#[test]
fn disabled_automation_and_wayland_leave_manual_navigation_available() {
    let now = Instant::now();
    for session in ["x11", "wayland"] {
        let mut state = State::new(config(false), session.into());
        state.set_focus_supported(true);
        state.focus_changed(firefox(), now);
        assert_eq!(state.page().name, "Apps");
        state.select_page(1).unwrap();
        assert_eq!(state.page().name, "General");
        state.reload(config(true));
        assert_eq!(
            state.page().name,
            if session == "x11" {
                "Firefox"
            } else {
                "General"
            }
        );
        state.reload(config(false));
        assert_eq!(
            state.page().name,
            if session == "x11" { "Apps" } else { "General" }
        );
    }
}

#[test]
fn profile_changes_apply_to_the_current_foreground_on_reload() {
    let now = Instant::now();
    let mut state = state();
    state.focus_changed(firefox(), now);
    let mut updated = config(true);
    updated.profiles[0].page.name = "Updated Firefox".into();
    state.reload(updated);
    assert_eq!(state.page().name, "Updated Firefox");
    let mut removed = config(true);
    removed.profiles.remove(0);
    state.reload(removed);
    assert_eq!(state.page().name, "Apps");
}

#[test]
fn website_launcher_keeps_externally_selected_browser_shortcuts() {
    let now = Instant::now();
    let mut state = state();
    state.focus_changed(firefox(), now);
    assert!(!state.launch_succeeded("firefox-site.desktop", &[], None, None, false, now));
    assert_eq!(state.page().name, "Firefox");
    state.focus_changed(focus("Unknown"), now);
    assert_eq!(state.page().name, "Apps");
}
