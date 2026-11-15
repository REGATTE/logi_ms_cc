use mx_creative_worker::{
    config::{Action, Config},
    control::Request,
};
use serde_json::{json, Value};

fn config() -> Value {
    json!({"version":1,"auto_switch":true,"pages":[{"name":"Apps","keys":[null,null,null,null,null,null,null,null,null]},{"name":"Shortcuts","keys":[null,null,null,null,null,null,null,null,null]}],"profiles":[],"action_ring":{"layouts":[{"id":"default","wm_classes":[],"slots":[{"label":"Copy","icon":"edit-copy","action":{"type":"shortcut","keys":"Ctrl+C"}}]}],"bindings":[{"vendor_id":1133,"product_id":49278,"control_id":"button:4"}]}})
}
fn valid(value: Value) -> bool {
    serde_json::from_value::<Config>(value).is_ok_and(|config| config.validate().is_ok())
}

#[test]
fn portable_ring_layouts_and_legacy_config_are_valid() {
    let mut data = config();
    assert!(valid(data.clone()));
    data.as_object_mut().unwrap().remove("action_ring");
    assert!(valid(data));
    assert!(matches!(
        serde_json::from_value::<Action>(json!({"type":"open_ring"})).unwrap(),
        Action::OpenRing {}
    ));
}

#[test]
fn runtime_ring_actions_are_compact_and_limited_to_seven() {
    let mut data = config();
    let action = data["action_ring"]["layouts"][0]["slots"][0].clone();
    for count in 0..=7 {
        data["action_ring"]["layouts"][0]["slots"] = json!(vec![action.clone(); count]);
        assert!(
            valid(data.clone()),
            "{count} configured actions should be accepted"
        );
    }
    data["action_ring"]["layouts"][0]["slots"] = json!(vec![action; 8]);
    let config: Config = serde_json::from_value(data).unwrap();
    assert!(config.validate().unwrap_err().contains("Open settings"));
}

#[test]
fn malformed_and_recursive_ring_actions_are_rejected() {
    for action in [
        json!({"type":"open_ring"}),
        json!({"type":"command","command":"sh"}),
        json!({"type":"shortcut","keys":"Ctrl"}),
        json!({"type":"launch","desktop_id":"../bad.desktop"}),
    ] {
        let mut data = config();
        data["action_ring"]["layouts"][0]["slots"][0]["action"] = action;
        assert!(!valid(data));
    }
    let mut data = config();
    data["action_ring"]["layouts"][0]["slots"] = json!([null]);
    assert!(!valid(data));
    let mut data = config();
    data["action_ring"]["layouts"][0]["id"] = json!("app.desktop");
    assert!(!valid(data));
    let mut data = config();
    data["action_ring"]["layouts"][0]["slots"][0]["label"] = json!("");
    assert!(!valid(data));
    let mut data = config();
    data["action_ring"]["layouts"][0]["slots"][0]["icon"] = json!("bad\nicon");
    assert!(!valid(data));
}

#[test]
fn device_bindings_are_scoped_bounded_and_unique() {
    for field in ["vendor_id", "product_id"] {
        let mut data = config();
        data["action_ring"]["bindings"][0][field] = json!(0);
        assert!(!valid(data));
    }
    let mut data = config();
    data["action_ring"]["bindings"][0]["control_id"] = json!("../anything");
    assert!(!valid(data));
    let mut data = config();
    let binding = data["action_ring"]["bindings"][0].clone();
    data["action_ring"]["bindings"]
        .as_array_mut()
        .unwrap()
        .push(binding);
    assert!(!valid(data));
}

#[test]
fn ring_ipc_only_selects_worker_owned_slots() {
    assert!(matches!(
        serde_json::from_value::<Request>(
            json!({"command":"ring_execute","token":"session","slot":0})
        )
        .unwrap(),
        Request::RingExecute { slot: 0, .. }
    ));
    assert!(matches!(
        serde_json::from_value::<Request>(json!({"command":"ring_configure","token":"session"})).unwrap(),
        Request::RingConfigure { token } if token == "session"
    ));
    for value in [
        json!({"command":"ring_execute","token":"session","slot":-1}),
        json!({"command":"ring_execute","token":"session","slot":0,"action":{"type":"shortcut","keys":"Ctrl+Q"}}),
        json!({"command":"ring_cancel","token":7}),
        json!({"command":"ring_configure","token":"session","layout_id":"different.desktop"}),
    ] {
        assert!(serde_json::from_value::<Request>(value).is_err());
    }
}
