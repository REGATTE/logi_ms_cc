use mx_creative_worker::{
    config::Config,
    control::{Request, State},
    input::parse_chord,
    protocol::{image_ack, image_packets, Edges, ImageAck, Press},
};
use serde_json::{json, Value};

fn config_value() -> Value {
    json!({"version":1,"auto_switch":true,"pages":[{"name":"Apps","keys":[null,null,null,null,null,null,null,null,null]},{"name":"Shortcuts","keys":[null,null,null,null,null,null,null,null,null]}],"profiles":[]})
}
fn valid(value: Value) -> Result<(), String> {
    let config: Config = serde_json::from_value(value).map_err(|e| e.to_string())?;
    config.validate()
}

#[test]
fn rejects_unsafe_or_malformed_runtime_without_executing() {
    assert!(valid(config_value()).is_ok());
    for image in [
        "../private.jpg",
        "/tmp/key.jpg",
        "sub/key.jpg",
        "link\\key.jpg",
        ".hidden.jpg",
        "-flag.jpg",
        "key.png",
    ] {
        let mut data = config_value();
        data["pages"][0]["keys"][0] =
            json!({"label":"Copy","image":image,"action":{"type":"shortcut","keys":"Ctrl+C"}});
        assert!(valid(data).is_err(), "{image}");
    }
    for id in [
        "/tmp/foo.desktop",
        "--help.desktop",
        ".hidden.desktop",
        "firefox",
    ] {
        let mut data = config_value();
        data["pages"][0]["keys"][0] =
            json!({"label":"App","image":"icon.jpg","action":{"type":"launch","desktop_id":id}});
        assert!(valid(data).is_err(), "{id}");
    }
    let mut data = config_value();
    data["pages"][0]["keys"] = json!([null]);
    assert!(valid(data).is_err());
    let mut data = config_value();
    data["version"] = json!(2);
    assert!(valid(data).is_err());
    let mut data = config_value();
    data["execute"] = json!("anything");
    assert!(valid(data).is_err());
}

#[test]
fn shortcut_parser_accepts_modifiers_and_media_and_rejects_sequences() {
    assert_eq!(parse_chord("Control+Shift+P").unwrap(), [29, 42, 25]);
    assert_eq!(parse_chord("Super+Return").unwrap(), [125, 28]);
    assert_eq!(parse_chord("Ctrl+Plus").unwrap(), [29, 42, 13]);
    assert_eq!(parse_chord("F24").unwrap(), [194]);
    assert_eq!(parse_chord("VolumeUp").unwrap(), [115]);
    assert_eq!(parse_chord("Ctrl+\\").unwrap(), [29, 43]);
    for bad in [
        "",
        "Ctrl",
        "Ctrl++",
        "Ctrl+Ctrl+C",
        "Ctrl+C+V",
        "Ctrl+C; rm -rf",
        "F25",
    ] {
        assert!(parse_chord(bad).is_err(), "{bad}");
    }
}

fn grid(seq: u8, keys: &[u8]) -> Vec<u8> {
    let mut packet = vec![0x13, 0xff, 2, 0, seq, 1];
    packet.extend_from_slice(keys);
    packet.resize(32, 0);
    packet
}

#[test]
fn grid_frames_emit_only_new_presses_and_ignore_acknowledgements() {
    let mut edges = Edges::default();
    assert_eq!(edges.report(&grid(0xce, &[8]), None), [Press::Key(7)]);
    assert!(edges.report(&grid(0xcf, &[8]), None).is_empty());
    assert!(edges
        .report(&[0x13, 0xff, 2, 0x2b, 0xc1, 0, 1], None)
        .is_empty());
    assert_eq!(edges.report(&grid(0xc0, &[3, 8]), None), [Press::Key(2)]);
    assert!(edges.report(&grid(0xc1, &[]), None).is_empty());
    assert_eq!(edges.report(&grid(0xc2, &[8]), None), [Press::Key(7)]);
    let mut wrong = grid(0xc3, &[2]);
    wrong[5] = 2;
    assert!(edges.report(&wrong, None).is_empty());
}

#[test]
fn navigation_is_deduplicated_and_feature_scoped() {
    let mut edges = Edges::default();
    let packet = [0x11, 0xff, 0x0b, 0, 1, 0xa1, 0, 0, 0, 0, 0, 0];
    assert!(edges.report(&packet, Some(0x0c)).is_empty());
    assert_eq!(edges.report(&packet, Some(0x0b)), [Press::Page(0)]);
    assert!(edges.report(&packet, Some(0x0b)).is_empty());
    assert!(edges
        .report(&[0x11, 0xff, 0x0b, 0, 0, 0, 0, 0, 0, 0, 0, 0], Some(0x0b))
        .is_empty());
    assert_eq!(edges.report(&packet, Some(0x0b)), [Press::Page(0)]);
}

fn jpeg(size: usize) -> Vec<u8> {
    let mut bytes = vec![0x55; size];
    bytes[..2].copy_from_slice(&[0xff, 0xd8]);
    bytes[size - 2..].copy_from_slice(&[0xff, 0xd9]);
    bytes
}

#[test]
fn image_framing_preserves_payload_across_packet_boundaries() {
    for size in [4, 4075, 4076, 8165, 65535] {
        let image = jpeg(size);
        let packets = image_packets(8, &image).unwrap();
        assert_eq!(&packets[0][..4], &[0x14, 0xff, 2, 0x2b]);
        assert_eq!(&packets[0][9..13], &[1, 83, 1, 66]); // x339, y322
        assert_eq!(&packets[0][13..17], &[0, 118, 0, 118]);
        assert_eq!(&packets[0][18..20], &(size as u16).to_be_bytes());
        assert!(packets.iter().all(|p| p.len() == 4095));
        assert!(packets[0][4] & 0x80 != 0);
        assert!(packets.last().unwrap()[4] & 0x40 != 0);
        let mut decoded = packets[0][20..].to_vec();
        for p in &packets[1..] {
            decoded.extend_from_slice(&p[5..]);
        }
        decoded.truncate(size);
        assert_eq!(decoded, image);
        let part = (packets.last().unwrap()[4] & !0x20) | 0x80;
        assert_eq!(
            image_ack(&[0x13, 0xff, 2, 0x2b, part, 0, 1], part),
            Some(ImageAck::Accepted)
        );
        assert_eq!(
            image_ack(&[0x13, 0xff, 2, 0x2b, part, 5, 0], part),
            Some(ImageAck::Rejected([5, 0]))
        );
        assert_eq!(
            image_ack(&[0x13, 0xff, 2, 0x2b, part ^ 1, 0, 1], part),
            None
        );
    }
    assert!(image_packets(9, &jpeg(10)).is_err());
    assert!(image_packets(0, &jpeg(65536)).is_err());
    assert!(image_packets(0, b"not jpeg").is_err());
}

#[test]
fn multipart_image_ack_matches_real_device_progress_and_final_reports() {
    let packets = image_packets(0, &jpeg(4175)).unwrap();
    assert_eq!(packets[0][4], 0xa1);
    assert_eq!(packets[1][4], 0x62);
    let expected = packets[1][4] & !0x20;
    assert_eq!(
        image_ack(&[0x13, 0xff, 2, 0x2b, 0xc1, 0, 0], expected),
        None
    );
    assert_eq!(
        image_ack(&[0x13, 0xff, 2, 0x2b, 0xc2, 0, 1], expected),
        Some(ImageAck::Accepted)
    );
    // Every packet's acceptance is consumed before sending the next packet.
    assert_eq!(
        image_ack(&[0x13, 0xff, 2, 0x2b, 0xc1, 0, 0], 0xa1),
        Some(ImageAck::Accepted)
    );
    assert_eq!(
        image_ack(&[0x13, 0xff, 2, 0x2b, 0xc1, 0, 1], 0xc1),
        Some(ImageAck::Accepted)
    );
    assert_eq!(
        image_ack(&[0x13, 0xff, 2, 0x2b, 0xc1, 5, 0], 0xc1),
        Some(ImageAck::Rejected([5, 0]))
    );
    assert_eq!(
        image_ack(&[0x13, 0xff, 2, 0x2b, 0xc1, 0xfb, 0], 0xe1),
        Some(ImageAck::Busy)
    );
}

#[test]
fn contextual_page_matches_class_without_replacing_app_launchers() {
    let mut value = config_value();
    let profile = json!({"id":"code","wm_classes":["Code"],"page":{"name":"VS Code","keys":[null,null,null,null,null,null,null,null,null]}});
    value["profiles"] = json!([profile]);
    let mut state = State::new(serde_json::from_value(value).unwrap(), "x11".into());
    state.status.active_app = "code".into();
    assert_eq!(state.page().name, "Apps");
    state.select_page(1).unwrap();
    assert_eq!(state.page().name, "VS Code");
    assert!(state.select_page(2).is_err());
    assert_eq!(state.status.page, 1);
    state.config.auto_switch = false;
    assert_eq!(state.page().name, "Shortcuts");
    state.config.auto_switch = true;
    state.status.active_app = "unknown".into();
    assert_eq!(state.page().name, "Shortcuts");
}

#[test]
fn manual_profile_works_without_focus_and_invalid_reload_returns_to_auto() {
    let mut value = config_value();
    value["profiles"] = json!([{"id":"code","wm_classes":["Code"],"page":{"name":"VS Code","keys":[null,null,null,null,null,null,null,null,null]}}]);
    let mut state = State::new(serde_json::from_value(value).unwrap(), "wayland".into());
    state.config.auto_switch = false;
    assert!(state.select_profile(Some("missing".into())).is_err());
    state.select_profile(Some("code".into())).unwrap();
    assert_eq!(state.page().name, "Apps");
    state.select_page(1).unwrap();
    assert_eq!(state.page().name, "VS Code");
    state.select_profile(None).unwrap();
    assert_eq!(state.page().name, "Shortcuts");
    state.select_profile(Some("default".into())).unwrap();
    state.reload(state.config.clone());
    assert_eq!(state.status.manual_profile.as_deref(), Some("default"));
    assert_eq!(state.page().name, "Shortcuts");
    state.select_profile(Some("code".into())).unwrap();
    state.reload(serde_json::from_value(config_value()).unwrap());
    assert!(state.status.manual_profile.is_none());
    assert_eq!(state.page().name, "Shortcuts");
}

#[test]
fn ipc_requests_are_typed_and_cannot_smuggle_shell_actions() {
    assert!(matches!(
        serde_json::from_value::<Request>(json!({"command":"pause","value":true})).unwrap(),
        Request::Pause { value: true }
    ));
    for invalid in [
        json!({"command":"exec","cmd":"sh"}),
        json!({"command":"page","index":-1}),
        json!({"command":"pause","value":"yes"}),
        json!({"command":"status","extra":1}),
    ] {
        assert!(serde_json::from_value::<Request>(invalid).is_err());
    }
}
