use mx_creative_worker::{
    config::Config,
    control::{Foreground, State},
};
use serde_json::{json, Value};
use std::time::{Duration, Instant};

fn runtime() -> Value {
    let empty = vec![Value::Null; 9];
    let mut apps = empty.clone();
    apps[0] = json!({"label":"Code","image":"code.jpg","action":{
        "type":"launch","desktop_id":"code.desktop","wm_classes":["Code"],"profile_id":"editor"
    }});
    json!({
        "version":1,"auto_switch":true,
        "pages":[{"name":"Apps","keys":apps},{"name":"General","keys":empty}],
        "profiles":[
            {"id":"editor","wm_classes":["Code","VSCodium"],"page":{"name":"Editor","keys":empty}},
            {"id":"other","wm_classes":["Browser"],"page":{"name":"Browser","keys":empty}}
        ]
    })
}

fn config(value: Value) -> Config {
    let config: Config = serde_json::from_value(value).unwrap();
    config.validate().unwrap();
    config
}

fn state() -> State {
    let mut state = State::new(config(runtime()), "x11".into());
    state.set_focus_supported(true);
    state
}

fn focus(name: &str) -> Foreground {
    Foreground {
        window: 1,
        name: name.into(),
        identities: vec![name.into()],
    }
}

fn launch(state: &mut State, now: Instant) {
    let confirmed_active = state.status.active_app.eq_ignore_ascii_case("code");
    assert!(state.launch_succeeded(
        "code.desktop",
        &["Code".into()],
        Some("editor"),
        None,
        confirmed_active,
        now
    ));
}

#[test]
fn waits_for_target_focus_then_follows_other_apps_when_it_leaves() {
    let mut state = state();
    let now = Instant::now();
    state.focus_changed(focus("Browser"), now);
    launch(&mut state, now);
    assert_eq!(state.page().name, "Apps");
    assert!(state.deadline().is_some());
    // Unrelated transitions while a new process starts must not choose shortcuts.
    state.focus_changed(focus("Launcher"), now + Duration::from_secs(1));
    assert_eq!(state.page().name, "Apps");
    assert!(state.focus_changed(focus("cOdE"), now + Duration::from_secs(2)));
    assert_eq!(state.status.page, 1);
    assert_eq!(state.page().name, "Editor");
    assert!(state.deadline().is_none());
    assert!(!state.tick(now + Duration::from_secs(60)));
    assert_eq!(state.page().name, "Editor");
    state.focus_changed(focus("Browser"), now + Duration::from_secs(61));
    assert_eq!(state.status.page, 1);
    assert_eq!(state.page().name, "Browser");
    // Returning to the app selects its profile without another keypad launch.
    state.focus_changed(focus("Code"), now + Duration::from_secs(62));
    assert_eq!(state.page().name, "Editor");
}

#[test]
fn already_active_application_enters_shortcuts_without_another_focus_event() {
    let mut state = state();
    let now = Instant::now();
    state.focus_changed(focus("Code"), now);
    state.select_profile(Some("other".into())).unwrap();
    launch(&mut state, now);
    assert_eq!(state.page().name, "Editor");
    assert_eq!(state.status.manual_profile, None);
    assert_eq!(state.deadline(), None);
    // Another window or dialog reporting the same class keeps its app profile.
    assert!(!state.focus_changed(focus("Code"), now));
    assert_eq!(state.page().name, "Editor");
}

#[test]
fn unprofiled_launch_keeps_active_shortcuts_until_focus_leaves() {
    let now = Instant::now();
    for target_window in [None, Some(1)] {
        let mut state = state();
        state.focus_changed(focus("Code"), now);
        launch(&mut state, now);

        // URL/document launchers can open content in the current application
        // without having a shortcut profile of their own.
        let redraw = state.launch_succeeded(
            "open-document.desktop",
            &["open-document".into()],
            None,
            target_window,
            target_window.is_some(),
            now,
        );
        assert_eq!(state.page().name, "Editor");
        assert!(!redraw);
        assert_eq!(state.deadline(), None);
        assert!(!state.tick(now + Duration::from_secs(20)));
        let mut same_app = focus("Code");
        same_app.window = 2;
        state.focus_changed(same_app, now + Duration::from_secs(21));
        assert_eq!(state.page().name, "Editor");

        state.focus_changed(focus("Unassigned"), now + Duration::from_secs(22));
        assert_eq!(state.page().name, "Apps");
        state.focus_changed(focus("Code"), now + Duration::from_secs(23));
        assert_eq!(state.page().name, "Editor");
    }
}

#[test]
fn unprofiled_launch_cancels_an_unconfirmed_launch() {
    let now = Instant::now();
    let mut state = state();
    state.focus_changed(focus("Browser"), now);
    launch(&mut state, now);
    assert!(state.deadline().is_some());

    assert!(state.launch_succeeded("open-document.desktop", &[], None, None, false, now));
    assert_eq!(state.page().name, "Browser");
    assert_eq!(state.deadline(), None);
    state.focus_changed(focus("Code"), now);
    assert_eq!(state.page().name, "Editor");
}

#[test]
fn shared_profile_alias_cannot_confirm_a_launch_but_can_select_its_own_profile() {
    let mut state = state();
    let now = Instant::now();
    launch(&mut state, now);
    state.focus_changed(focus("VSCodium"), now);
    assert_eq!(state.page().name, "Apps");
    state.focus_changed(focus("Code"), now);
    assert_eq!(state.page().name, "Editor");
    state.focus_changed(focus("VSCodium"), now);
    assert_eq!(state.page().name, "Editor");
}

#[test]
fn pending_launch_expires_then_focus_uses_normal_profiles() {
    let mut state = state();
    let now = Instant::now();
    launch(&mut state, now);
    let deadline = state.deadline().unwrap();
    assert!(!state.tick(deadline - Duration::from_millis(1)));
    assert!(state.tick(deadline));
    assert_eq!(state.deadline(), None);
    state.focus_changed(focus("Code"), deadline);
    assert_eq!(state.page().name, "Editor");

    state.focus_changed(focus("Browser"), now);
    launch(&mut state, now);
    state.focus_changed(focus("Code"), now + Duration::from_secs(13));
    assert_eq!(state.page().name, "Editor");
    assert_eq!(state.deadline(), None);
}

#[test]
fn explicit_navigation_and_profile_selection_cancel_pending_or_active_context() {
    let now = Instant::now();
    for active in [false, true] {
        let mut state = state();
        launch(&mut state, now);
        if active {
            state.focus_changed(focus("Code"), now);
        }
        state.select_page(0).unwrap();
        state.focus_changed(focus("Code"), now);
        assert_eq!(state.page().name, if active { "Apps" } else { "Editor" });
        assert_eq!(state.deadline(), None);

        launch(&mut state, now);
        state.select_profile(Some("other".into())).unwrap();
        state.select_page(1).unwrap();
        state.focus_changed(focus("Browser"), now);
        assert_eq!(state.page().name, "Browser");
        assert_eq!(state.status.manual_profile.as_deref(), Some("other"));
    }
}

#[test]
fn invalid_manual_selection_does_not_cancel_active_context() {
    let mut state = state();
    let now = Instant::now();
    state.focus_changed(focus("Code"), now);
    launch(&mut state, now);
    assert!(state.select_page(3).is_err());
    assert!(state.select_profile(Some("missing".into())).is_err());
    assert_eq!(state.page().name, "Editor");
    state.focus_changed(focus("Browser"), now);
    assert_eq!(state.page().name, "Browser");
}

#[test]
fn launches_without_profiles_or_tracking_respect_automatic_paging_settings() {
    let now = Instant::now();
    for mode in [
        "no-profile",
        "invalid-profile",
        "auto-off",
        "wayland",
        "tracking-off",
    ] {
        let mut state = state();
        let profile = match mode {
            "no-profile" => None,
            "invalid-profile" => Some("absent"),
            "auto-off" => {
                state.config.auto_switch = false;
                Some("editor")
            }
            "wayland" => {
                state.status.session = "wayland".into();
                Some("editor")
            }
            _ => {
                state.set_focus_supported(false);
                Some("editor")
            }
        };
        state.focus_changed(focus("Code"), now);
        assert!(!state.launch_succeeded(
            "code.desktop",
            &["Code".into()],
            profile,
            None,
            false,
            now
        ));
        assert_eq!(
            state.page().name,
            if matches!(mode, "no-profile" | "invalid-profile") {
                "Editor"
            } else {
                "Apps"
            },
            "{mode}"
        );
        assert_eq!(state.deadline(), None);
    }
}

#[test]
fn tracking_failure_and_empty_foreground_clear_launch_shortcuts() {
    let now = Instant::now();
    let mut state = state();
    state.focus_changed(focus("Code"), now);
    launch(&mut state, now);
    assert!(state.set_focus_supported(false));
    assert_eq!(state.page().name, "Apps");
    state.set_focus_supported(true);
    launch(&mut state, now);
    state.focus_changed(Foreground::default(), now);
    assert_eq!(state.page().name, "Apps");
}

#[test]
fn default_profile_is_supported_and_desktop_stem_can_identify_the_app() {
    let mut state = state();
    let now = Instant::now();
    assert!(state.launch_succeeded("code.desktop", &[], Some("default"), None, false, now));
    state.focus_changed(focus("CODE"), now);
    assert_eq!(state.page().name, "General");
    state.focus_changed(focus("Browser"), now);
    assert_eq!(state.page().name, "Browser");
}

#[test]
fn reload_keeps_valid_context_or_falls_back_to_the_foreground_profile() {
    let now = Instant::now();
    let mut state = state();
    state.focus_changed(focus("Code"), now);
    launch(&mut state, now);
    let mut updated = runtime();
    updated["profiles"][0]["page"]["name"] = json!("Updated editor");
    state.reload(config(updated));
    assert_eq!(state.page().name, "Updated editor");

    for removal in ["profile", "launcher", "auto-switch", "assignment"] {
        let mut updated = runtime();
        state.reload(config(updated.clone()));
        launch(&mut state, now);
        match removal {
            "profile" => {
                updated["profiles"] = json!([]);
                updated["pages"][0]["keys"][0]["action"]["profile_id"] = Value::Null;
            }
            "launcher" => updated["pages"][0]["keys"][0] = Value::Null,
            "auto-switch" => updated["auto_switch"] = json!(false),
            _ => {
                updated["pages"][0]["keys"][0]["action"]["profile_id"] = json!("other");
                updated["profiles"][0]["wm_classes"] = json!(["VSCodium"]);
                updated["profiles"][1]["wm_classes"] = json!(["Browser", "Code"]);
            }
        }
        state.reload(config(updated));
        assert_eq!(
            state.page().name,
            match removal {
                "launcher" => "Editor",
                "assignment" => "Browser",
                _ => "Apps",
            },
            "{removal}"
        );
        assert_eq!(state.deadline(), None);
    }
}

#[test]
fn target_window_fallback_does_not_reuse_a_generic_class_for_another_app() {
    let mut state = state();
    let now = Instant::now();
    state.launch_succeeded(
        "code.desktop",
        &["Code".into()],
        Some("editor"),
        Some(41),
        false,
        now,
    );
    let generic = |window| Foreground {
        window,
        name: "Google-chrome".into(),
        identities: vec!["Google-chrome".into()],
    };
    state.focus_changed(generic(42), now);
    assert_eq!(state.page().name, "Apps");
    state.focus_changed(generic(41), now);
    assert_eq!(state.page().name, "Editor");
    // A second application sharing that same toolkit/browser class is distinct.
    assert!(state.focus_changed(generic(42), now));
    assert_eq!(state.status.active_app, "Google-chrome");
    assert_eq!(state.page().name, "Apps");
}

#[test]
fn app_id_properties_confirm_pending_launch_and_identity_changes_trigger_redraw() {
    let mut state = state();
    let now = Instant::now();
    state.launch_succeeded("code.desktop", &[], Some("editor"), None, false, now);
    let mut foreground = Foreground {
        window: 51,
        name: "GenericToolkit".into(),
        identities: vec!["code.desktop".into()],
    };
    assert!(state.focus_changed(foreground.clone(), now));
    assert_eq!(state.page().name, "Editor");
    assert_eq!(state.status.active_app, "GenericToolkit");
    foreground.identities = vec!["another-app.desktop".into()];
    assert!(state.focus_changed(foreground, now));
    assert_eq!(state.page().name, "Apps");
}

#[test]
fn prior_focus_snapshot_does_not_count_as_fresh_activation_confirmation() {
    let mut state = state();
    let now = Instant::now();
    state.focus_changed(focus("Code"), now);
    state.launch_succeeded(
        "code.desktop",
        &["Code".into()],
        Some("editor"),
        Some(61),
        false,
        now,
    );
    assert_eq!(state.page().name, "Apps");
    assert!(state.deadline().is_some());
    state.focus_changed(focus("Browser"), now);
    assert_eq!(state.page().name, "Apps");
    let mut confirmed = focus("Code");
    confirmed.window = 61;
    assert!(state.focus_changed(confirmed, now));
    assert_eq!(state.page().name, "Editor");
}

#[test]
fn repeated_focus_snapshot_still_redraws_when_it_confirms_pending_launch() {
    let mut state = state();
    let now = Instant::now();
    let foreground = focus("Code");
    state.focus_changed(foreground.clone(), now);
    state.launch_succeeded(
        "code.desktop",
        &["Code".into()],
        Some("editor"),
        None,
        false,
        now,
    );
    assert_eq!(state.page().name, "Apps");
    assert!(state.focus_changed(foreground, now));
    assert_eq!(state.page().name, "Editor");
}
