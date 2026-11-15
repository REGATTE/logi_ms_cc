use mx_creative_worker::{
    config::{Action, Config, DeviceBinding},
    control::State,
};
use serde_json::{json, Value};
use std::time::Instant;

fn base() -> Value {
    json!({"version":1,"auto_switch":true,
        "pages":[{"name":"Apps","keys":[null,null,null,null,null,null,null,null,null]},
                 {"name":"Shortcuts","keys":[null,null,null,null,null,null,null,null,null]}],
        "profiles":[],
        "action_ring":{"layouts":[],"bindings":[
            {"vendor_id":1133,"product_id":49278,"control_id":"button:4"}]}})
}

fn mapping(action: Value) -> Value {
    json!({"vendor_id":1133,"product_id":48128,"control_id":"button:1","action":action})
}

fn read(value: Value) -> Result<Config, String> {
    let config: Config = serde_json::from_value(value).map_err(|e| e.to_string())?;
    config.validate()?;
    Ok(config)
}

fn button(product_id: u16, control_id: &str) -> DeviceBinding {
    DeviceBinding {
        vendor_id: 1133,
        product_id,
        control_id: control_id.into(),
    }
}

#[test]
fn supported_actions_share_capture_with_legacy_ring_bindings() {
    for action in [
        json!({"type":"launch","desktop_id":"editor.desktop"}),
        json!({"type":"shortcut","keys":"Ctrl+Shift+P"}),
        json!({"type":"screenshot"}),
        json!({"type":"open_ring"}),
    ] {
        let mut value = base();
        value["device_actions"] = json!([mapping(action.clone())]);
        let config = read(value).unwrap();
        assert_eq!(
            config.configured_bindings(),
            [button(49278, "button:4"), button(48128, "button:1")]
        );
        assert!(matches!(
            config.device_action(&button(49278, "button:4")),
            Some(Action::OpenRing {})
        ));
        let selected =
            serde_json::to_value(config.device_action(&button(48128, "button:1")).unwrap())
                .unwrap();
        assert_eq!(selected["type"], action["type"]);
    }
    assert_eq!(
        read(base()).unwrap().configured_bindings(),
        [button(49278, "button:4")]
    );
}

#[test]
fn removed_or_paused_buttons_cannot_replay_queued_actions() {
    let mut value = base();
    value["device_actions"] = json!([mapping(json!({"type":"shortcut","keys":"Ctrl+C"}))]);
    let mut state = State::new(read(value.clone()).unwrap(), "x11".into());
    let target = button(48128, "button:1");
    assert!(
        matches!(state.device_action(&target), Some(Action::Shortcut { keys }) if keys == "Ctrl+C")
    );
    assert!(state.device_action(&button(48128, "button:2")).is_none());
    assert!(state.device_action(&button(48129, "button:1")).is_none());
    state.status.paused = true;
    assert!(state.device_action(&target).is_none());
    assert!(state.device_action(&button(49278, "button:4")).is_none());
    state.status.paused = false;
    value["device_actions"][0]["action"] = json!({"type":"screenshot"});
    state.reload(read(value).unwrap());
    assert!(matches!(
        state.device_action(&target),
        Some(Action::Screenshot {})
    ));
    state.reload(read(base()).unwrap());
    assert!(state.device_action(&target).is_none());
    assert!(state
        .status
        .capabilities
        .iter()
        .any(|capability| capability == "device_actions_v1"));
}

#[test]
fn device_launcher_context_survives_unrelated_reload_but_not_removed_mapping() {
    let mut value = base();
    value["device_actions"] = json!([mapping(json!({"type":"launch",
        "desktop_id":"editor.desktop","wm_classes":["editor"],"profile_id":"default"}))]);
    let config = read(value).unwrap();
    let mut state = State::new(config.clone(), "x11".into());
    state.status.focus_supported = true;
    assert!(state.launch_succeeded(
        "editor.desktop",
        &["editor".into()],
        Some("default"),
        Some(100),
        true,
        Instant::now()
    ));
    assert_eq!(state.status.page, 1);
    state.reload(config);
    assert_eq!(state.status.page, 1);
    state.reload(read(base()).unwrap());
    assert_eq!(state.status.page, 0);
}

#[test]
fn device_actions_reject_unsupported_targets_duplicates_and_executable_input() {
    for (product, control) in [
        (0, "button:1"),
        (48128, "button:5"),
        (48128, "wheel:roller"),
        (49278, "button:1"),
        (49278, "key:272"),
        (50000, "key:30"),
        (50000, "key:279"),
        (50000, "key:0272"),
        (50000, "button:1"),
    ] {
        let mut item = mapping(json!({"type":"screenshot"}));
        item["product_id"] = json!(product);
        item["control_id"] = json!(control);
        let mut value = base();
        value["device_actions"] = json!([item]);
        assert!(read(value).is_err(), "{product} {control}");
    }
    for action in [
        json!({"type":"command","command":"sh"}),
        json!({"type":"shortcut","keys":"Ctrl+C; echo test"}),
        json!({"type":"launch","desktop_id":"../editor.desktop"}),
        json!({"type":"screenshot","command":"sh"}),
        json!({"type":"none"}),
    ] {
        let mut value = base();
        value["device_actions"] = json!([mapping(action)]);
        assert!(read(value).is_err());
    }
    let mut value = base();
    let item = mapping(json!({"type":"screenshot"}));
    value["device_actions"] = json!([item.clone(), item]);
    assert!(read(value).is_err());
    let mut value = base();
    value["device_actions"] = json!([{"vendor_id":1133,"product_id":49278,
                                     "control_id":"button:4","action":{"type":"screenshot"}}]);
    assert!(read(value).is_err());
}

#[test]
fn binding_limit_and_profile_references_cover_every_assignment_source() {
    let mut value = base();
    let mut mappings: Vec<_> = (1..=255)
        .map(|product| {
            json!({"vendor_id":1133,
        "product_id":product,"control_id":"key:272","action":{"type":"screenshot"}})
        })
        .collect();
    value["device_actions"] = json!(mappings);
    assert!(read(value.clone()).is_ok());
    mappings.push(json!({"vendor_id":1133,"product_id":256,"control_id":"key:272","action":{"type":"screenshot"}}));
    value["device_actions"] = json!(mappings);
    assert!(read(value).is_err());
    let mut value = base();
    value["device_actions"] = json!([mapping(
        json!({"type":"launch","desktop_id":"editor.desktop","profile_id":"missing"})
    )]);
    assert!(read(value.clone()).is_err());
    value["device_actions"][0]["action"]["profile_id"] = json!("default");
    assert!(read(value).is_ok());
}
