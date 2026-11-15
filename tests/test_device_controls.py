"""Physical model layouts and Linux-advertised controls, without device access."""

from pathlib import Path

import pytest

from mxcc.device_controls import DeviceControl, controls_for_device


def bitmap(codes, *, word_bits=64):
    value = sum(1 << code for code in codes)
    words = []
    while value:
        words.append(format(value & ((1 << word_bits) - 1), "x"))
        value >>= word_bits
    return " ".join(reversed(words)) or "0"


def input_node(tmp_path, name="input1", *, keys=(), rel=(), word_bits=64):
    path = tmp_path / name
    capabilities = path / "capabilities"
    capabilities.mkdir(parents=True)
    (capabilities / "key").write_text(bitmap(keys, word_bits=word_bits))
    (capabilities / "rel").write_text(bitmap(rel, word_bits=word_bits))
    return path


def generic(*paths, kind="mouse", word_bits=64):
    return controls_for_device(vendor_id=0x046d, product_id=0xbeef, kind=kind,
                               input_paths=paths, word_bits=word_bits)


@pytest.mark.parametrize(("product", "kind", "buttons", "wheels", "dials"),
                         [(0xbc00, "dialpad", 4, 1, 1), (0xc07e, "mouse", 2, 0, 0)])
def test_known_models_do_not_invent_physical_controls_from_oversized_capabilities(tmp_path, product, kind, buttons, wheels, dials):
    node = input_node(tmp_path, keys=range(0x110, 0x120), rel=(0, 1, 6, 8, 11, 12))
    controls = controls_for_device(vendor_id=0x046d, product_id=product, kind=kind, input_paths=[node])
    assert sum(control.kind == "button" for control in controls) == buttons
    assert sum(control.kind == "wheel" for control in controls) == wheels
    assert sum(control.kind == "dial" for control in controls) == dials
    assert len({control.id for control in controls}) == len(controls)
    assert {control.source for control in controls} == {"model"}
    assert controls_for_device(vendor_id=0x046d, product_id=product, kind=kind) == controls
    if product == 0xc07e:
        assert [(control.id, control.label) for control in controls] == [
            ("button:4", "Back button"), ("button:5", "Forward button")]
    else:
        assert [(control.id, control.label) for control in controls[-2:]] == [
            ("dial:main", "Dial"), ("wheel:roller", "Roller")]


@pytest.mark.parametrize("word_bits", [32, 64])
def test_new_mouse_gets_reported_buttons_and_one_slot_per_wheel(tmp_path, word_bits):
    pointer = input_node(tmp_path, keys=(0x110, 0x112, 0x119), rel=(0, 1, 6, 8, 11, 12), word_bits=word_bits)
    keyboard = input_node(tmp_path, "input2", keys=(30, 115, 0x110), rel=(6, 12), word_bits=word_bits)
    controls = generic(pointer, keyboard, pointer, word_bits=word_bits)
    assert [control.id for control in controls] == ["key:272", "key:274", "key:281", "rel:8", "rel:6"]
    assert controls[0].label == "Left button"
    assert controls[2].label == "Button 10"
    assert [control.label for control in controls[-2:]] == ["Scroll wheel", "Horizontal scroll wheel"]
    assert {control.source for control in controls} == {"linux"}


def test_keyboard_and_consumer_controls_are_discovered_without_model_support(tmp_path):
    node = input_node(tmp_path, keys=(0, 30, 115, 164, 212, 0x148, 0x14a, 0x14b, 0x220), rel=(0, 1, 7))
    controls = generic(node, kind="device")
    assert [control.label for control in controls] == ["A", "Volume up", "Play / pause", "Camera", "Stylus button", "D-pad up", "Dial"]
    assert [control.kind for control in controls] == ["key"] * 4 + ["button"] * 2 + ["dial"]
    assert controls[-1].id == "rel:7"


def test_reported_controls_are_stable_across_interface_order_and_high_resolution_aliases(tmp_path):
    first = input_node(tmp_path, keys=(0x110, 0x111), rel=(8,))
    second = input_node(tmp_path, "input2", keys=(0x111, 0x113), rel=(11,))
    assert generic(first, second) == generic(second, first)
    assert generic(first)[-1] == generic(second)[-1]


def test_missing_malformed_or_unreadable_capabilities_do_not_break_valid_interfaces(tmp_path, monkeypatch):
    missing = tmp_path / "missing"
    invalid = input_node(tmp_path, "invalid")
    (invalid / "capabilities/key").write_text("f" * 4097)
    (invalid / "capabilities/rel").write_text("not-hex")
    valid = input_node(tmp_path, "valid", keys=(0x110,), rel=(8,))
    denied = input_node(tmp_path, "denied", keys=(0x111,))
    original = Path.open
    def guarded(path, *args, **kwargs):
        if denied in path.parents:
            raise PermissionError("Unavailable metadata")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", guarded)
    assert generic(missing, invalid, valid, denied) == generic(valid)
    assert generic(missing, invalid, denied) == ()


def test_keypad_and_absent_capabilities_do_not_gain_placeholder_controls(tmp_path):
    node = input_node(tmp_path, keys=(115, 116))
    assert controls_for_device(vendor_id=0x046d, product_id=0xc354, kind="keypad", input_paths=[node]) == ()
    assert generic() == ()
    assert generic(node, kind="receiver") == ()
    assert isinstance(generic(node, kind="headset")[0], DeviceControl)
