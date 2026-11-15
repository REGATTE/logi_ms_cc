"""Device inventory uses metadata only: no hardware/input handles or pairing."""

import json
from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from mxcc import devices


@pytest.fixture
def inventory(tmp_path, monkeypatch):
    usb, hid = tmp_path / 'usb', tmp_path / 'hid'
    usb.mkdir()
    hid.mkdir()
    monkeypatch.setattr(devices, '_read_bluez', lambda: None)
    return usb, hid


def usb_node(root, port='1-4', vendor='046d', product='c354', name='MX Creative Keypad', serial='ABC123'):
    path = root / port
    path.mkdir()
    for key, value in {'idVendor': vendor, 'idProduct': product, 'product': name, 'serial': serial}.items():
        (path / key).write_text(value)
    return path


def hid_node(root, *, bus='0003', product='C354', name='Logitech MX Creative Keypad', unique='', phys='usb-1-4/input0', parent=None, suffix='0001'):
    target = (parent or root) / f'{bus}:046D:{product}.{suffix}'
    target.mkdir()
    (target / 'uevent').write_text(f'HID_ID={bus}:0000046D:0000{product}\nHID_NAME={name}\nHID_UNIQ={unique}\nHID_PHYS={phys}\n')
    if parent:
        (root / target.name).symlink_to(target)
    return target


def input_node(hid, index, *, keys=(), relative=()):
    path = hid / 'input' / f'input{index}' / 'capabilities'
    path.mkdir(parents=True)
    for name, bits in (('ev', (1, 2)), ('key', keys), ('rel', relative)):
        value = sum(1 << bit for bit in bits)
        words = [f'{(value >> shift) & ((1 << 64) - 1):x}'
                 for shift in reversed(range(0, max(1, value.bit_length()), 64))]
        (path / name).write_text(' '.join(words))
    return path.parent


def bluez(address='AA:BB:CC:DD:EE:FF', *, connected=True, modalias='usb:v046DpBC00d0016', name='MX Dialpad', battery=None):
    entry = {'org.bluez.Device1': {'Address': address, 'Connected': connected, 'Name': name, 'Modalias': modalias}}
    if battery is not None:
        entry['org.bluez.Battery1'] = {'Percentage': battery}
    return {f'/org/bluez/hci0/dev_{address.replace(":", "_")}': entry}


def discover(inventory, **values):
    usb, hid = inventory
    return devices.discover_devices(usb_root=usb, hid_root=hid, **values)


def test_composite_usb_device_is_one_card_and_non_logitech_is_excluded(inventory):
    usb, hid = inventory
    keypad = usb_node(usb)
    mouse = usb_node(usb, '1-2', product='c07e', name='Gaming Mouse G402', serial='MOUSE')
    usb_node(usb, '1-3', vendor='1234', product='5678', name='Other mouse')
    hid_node(hid, parent=keypad)
    hid_node(hid, parent=mouse, product='C07E', name='Logitech Gaming Mouse G402', suffix='0002')
    hid_node(hid, parent=mouse, product='C07E', name='Logitech Gaming Mouse G402', phys='usb-1-2/input1', suffix='0003')
    result = discover(inventory, bluez_objects={})
    assert [(device.name, device.kind, device.supported) for device in result] == [
        ('MX Creative Keypad', 'keypad', True), ('Gaming Mouse G402', 'mouse', False)]
    assert all(device.connection == 'USB' and device.connected is True for device in result)
    assert all(device.battery_percent is None for device in result)


def test_serial_keeps_identity_stable_across_usb_port_change(inventory):
    usb, _ = inventory
    path = usb_node(usb)
    before, = discover(inventory, bluez_objects={})
    path.rename(usb / '2-9')
    after, = discover(inventory, bluez_objects={})
    assert before.id == after.id == 'usb:046d:c354:ABC123'
    usb_node(usb, '1-5', serial='SECOND')
    assert len(discover(inventory, bluez_objects={})) == 2


def test_bluetooth_hid_and_bluez_merge_and_only_report_real_battery(inventory):
    _, hid = inventory
    hid_node(hid, bus='0005', product='BC00', name='MX Dialpad', unique='aa:bb:cc:dd:ee:ff')
    result, = discover(inventory, bluez_objects=bluez(battery=72))
    assert result.id == 'bluetooth:aa:bb:cc:dd:ee:ff'
    assert result.connection == 'Bluetooth'
    assert result.connected is True
    assert result.kind == 'dialpad'
    assert not result.supported
    assert result.battery_percent == 72
    for invalid in (True, -1, 101, '80'):
        result, = discover(inventory, bluez_objects=bluez(battery=invalid))
        assert result.battery_percent is None


def test_multiple_bluetooth_interfaces_preserve_bluez_name_and_battery(inventory):
    _, hid = inventory
    hid_node(hid, bus='0005', product='B034', name='Logitech Mouse',
             unique='aa:bb:cc:dd:ee:ff')
    hid_node(hid, bus='0005', product='B034', name='HID consumer control',
             unique='AA:BB:CC:DD:EE:FF', suffix='0002')
    result, = discover(inventory, bluez_objects=bluez(
        modalias='usb:v046DpB034d0001', name='Logitech MX Master 3S', battery=64))
    assert result.id == 'bluetooth:aa:bb:cc:dd:ee:ff'
    assert result.name == 'MX Master 3S'
    assert result.kind == 'mouse'
    assert result.battery_percent == 64
    assert result.connected is True


def test_disconnected_or_absent_bluez_device_does_not_leave_a_stale_card(inventory):
    _, hid = inventory
    hid_node(hid, bus='0005', product='BC00', name='MX Dialpad', unique='aa:bb:cc:dd:ee:ff')
    assert discover(inventory, bluez_objects=bluez(connected=False)) == []
    assert discover(inventory, bluez_objects={}) == []
    # Optional BlueZ tools missing: a live HID node remains usable evidence.
    result, = discover(inventory)
    assert result.connected is True
    assert result.battery_percent is None


def test_bluez_can_report_connected_non_hid_logitech_devices(inventory):
    entries = bluez(modalias='usb:v046Dp1234d0001', name='Logitech Zone headset', battery=35)
    entries.update(bluez('11:22:33:44:55:66', modalias='usb:v1234p1234d0001', name='Other headset'))
    result, = discover(inventory, bluez_objects=entries)
    assert result.name == 'Zone headset'
    assert result.kind == 'headset'
    assert result.battery_percent == 35
    assert not result.supported


def test_hid_vendor_evidence_survives_missing_bluez_modalias(inventory):
    _, hid = inventory
    hid_node(hid, bus='0005', product='BC00', name='MX Dialpad', unique='aa:bb:cc:dd:ee:ff')
    result, = discover(inventory, bluez_objects=bluez(modalias=''))
    assert result.name == 'MX Dialpad'
    assert result.connected is True


def test_receiver_is_not_a_mouse_and_paired_child_link_is_unknown(inventory):
    usb, hid = inventory
    receiver = usb_node(usb, product='c52b', name='USB Receiver', serial='RECEIVER')
    hid_node(hid, parent=receiver, product='C52B', name='Logitech USB Receiver')
    hid_node(hid, parent=receiver, product='4082', name='Logitech MX Master 3', unique='MOUSE', suffix='0002')
    result = discover(inventory, bluez_objects={})
    assert len(result) == 2
    mouse = next(device for device in result if device.kind == 'mouse')
    receiver = next(device for device in result if device.kind == 'receiver')
    assert receiver.connection == 'USB' and receiver.connected is True
    assert mouse.connection == 'USB receiver' and mouse.connected is None
    assert 'not reported' in mouse.detail
    assert not mouse.supported and not receiver.supported


def test_missing_or_removed_sysfs_nodes_are_safe(tmp_path, inventory):
    assert devices.discover_devices(usb_root=tmp_path / 'missing', hid_root=tmp_path / 'also-missing', bluez_objects={}) == []
    usb, hid = inventory
    usb_node(usb, product='broken')
    path = hid / 'removed'
    path.symlink_to(tmp_path / 'gone')
    assert discover(inventory, bluez_objects={}) == []


def test_bluez_reader_uses_bounded_read_only_queries(monkeypatch):
    path = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF'
    responses = [
        'Device AA:BB:CC:DD:EE:FF MX Dialpad\n',
        path + '\n' + path + '/service0001\n',
        '\n'.join(json.dumps({'type': kind, 'data': value}) for kind, value in [('b', True), ('s', 'usb:v046DpBC00d0016'), ('s', 'MX Dialpad')]),
        json.dumps({'type': 'y', 'data': 63}),
    ]
    run = Mock(side_effect=[subprocess.CompletedProcess([], 0, response, '') for response in responses])
    monkeypatch.setattr(devices.subprocess, 'run', run)
    result = devices._read_bluez()
    parsed, connected = devices._bluez_devices(result)
    assert parsed['aa:bb:cc:dd:ee:ff'].battery_percent == 63
    assert connected == {'aa:bb:cc:dd:ee:ff'}
    for call in run.call_args_list:
        assert call.kwargs['timeout'] <= 1
        assert call.kwargs.get('shell') is None
        assert not {'scan', 'pair', 'connect', 'disconnect', 'remove', 'set-property'} & set(call.args[0])
    assert run.call_args_list[0].args[0] == ['bluetoothctl', 'devices', 'Connected']


@pytest.mark.parametrize('failure', ['battery_timeout', 'device_timeout', 'deadline'])
def test_bluez_property_failures_keep_gathered_devices_and_connected_addresses(monkeypatch, failure):
    first = '/org/bluez/hci0/dev_AA_BB_CC_DD_EE_FF'
    second = '/org/bluez/hci0/dev_11_22_33_44_55_66'
    clock = [0]
    monkeypatch.setattr(devices.time, 'monotonic', lambda: clock[0])

    def run(arguments, **_):
        if arguments[0] == 'bluetoothctl':
            output = 'Device AA:BB:CC:DD:EE:FF Zone headset\nDevice 11:22:33:44:55:66 Mouse\n'
        elif 'tree' in arguments:
            output = first + '\n' + second + '\n'
        elif 'org.bluez.Device1' in arguments:
            if second in arguments and failure == 'device_timeout':
                raise subprocess.TimeoutExpired(arguments, 1)
            if first in arguments and failure == 'deadline':
                clock[0] = 4
            name = 'Logitech Zone headset' if first in arguments else 'Logitech Mouse'
            output = '\n'.join(json.dumps({'type': kind, 'data': value}) for kind, value in
                               [('b', True), ('s', 'usb:v046Dp1234d0001'), ('s', name)])
        else:
            if first in arguments and failure == 'battery_timeout':
                raise subprocess.TimeoutExpired(arguments, 1)
            output = json.dumps({'type': 'y', 'data': 63})
        return subprocess.CompletedProcess(arguments, 0, output, '')

    monkeypatch.setattr(devices.subprocess, 'run', run)
    result = devices._read_bluez()
    parsed, connected = devices._bluez_devices(result)
    assert connected == {'aa:bb:cc:dd:ee:ff', '11:22:33:44:55:66'}
    assert parsed['aa:bb:cc:dd:ee:ff'].name == 'Zone headset'
    if failure == 'battery_timeout':
        assert parsed['aa:bb:cc:dd:ee:ff'].battery_percent is None
        assert parsed['11:22:33:44:55:66'].battery_percent == 63
    elif failure == 'device_timeout':
        assert parsed['aa:bb:cc:dd:ee:ff'].battery_percent == 63
    else:
        assert parsed['aa:bb:cc:dd:ee:ff'].battery_percent is None


def test_unavailable_bluetooth_tools_do_not_break_usb(monkeypatch, tmp_path):
    usb, hid = tmp_path / 'usb', tmp_path / 'hid'
    usb.mkdir()
    hid.mkdir()
    usb_node(usb)
    monkeypatch.setattr(devices.subprocess, 'run', Mock(side_effect=FileNotFoundError('bluetoothctl')))
    result, = devices.discover_devices(usb_root=usb, hid_root=hid)
    assert result.kind == 'keypad'


def test_new_usb_device_combines_only_its_own_input_interfaces(inventory):
    usb, hid = inventory
    first = usb_node(usb, product='f001', name='Future Mouse', serial='FIRST')
    second = usb_node(usb, '1-5', product='f001', name='Future Mouse', serial='SECOND')
    a = hid_node(hid, parent=first, product='F001')
    b = hid_node(hid, parent=first, product='F001', suffix='0002')
    other = hid_node(hid, parent=second, product='F001', suffix='0003')
    input_node(a, 1, keys=(272, 273), relative=(0, 1, 8))
    input_node(b, 2, keys=(272, 274), relative=(8, 11))
    input_node(other, 3, keys=(275,), relative=(6,))
    result = {device.id.rsplit(':', 1)[-1]: device for device in discover(inventory, bluez_objects={})}
    controls = {control.id for control in result['FIRST'].controls}
    assert controls == {'key:272', 'key:273', 'key:274', 'rel:8'}
    assert {control.id for control in result['SECOND'].controls} == {'key:275', 'rel:6'}
    assert all(control.source == 'linux' for control in result['FIRST'].controls)
    assert 'reported by Ubuntu' in result['FIRST'].controls_note


def test_bluetooth_controls_follow_address_and_keep_bluez_battery(inventory):
    _, hid = inventory
    first = hid_node(hid, bus='0005', product='F001', name='Future Mouse', unique='aa:bb:cc:dd:ee:ff')
    second = hid_node(hid, bus='0005', product='F001', name='Future Mouse', unique='11:22:33:44:55:66', suffix='0002')
    input_node(first, 1, keys=(272,), relative=(8,))
    input_node(second, 2, keys=(273,), relative=())
    objects = bluez(modalias='usb:v046DpF001d0001', name='Future Mouse', battery=73)
    objects.update(bluez('11:22:33:44:55:66', modalias='usb:v046DpF001d0001', name='Future Mouse', battery=60))
    result = {device.id: device for device in discover(inventory, bluez_objects=objects)}
    assert {control.id for control in result['bluetooth:aa:bb:cc:dd:ee:ff'].controls} == {'key:272', 'rel:8'}
    assert result['bluetooth:aa:bb:cc:dd:ee:ff'].battery_percent == 73
    assert {control.id for control in result['bluetooth:11:22:33:44:55:66'].controls} == {'key:273'}


def test_receiver_children_do_not_share_each_others_controls(inventory):
    usb, hid = inventory
    receiver = usb_node(usb, product='c52b', name='USB Receiver')
    for index, key in ((1, 272), (2, 273)):
        child = hid_node(hid, parent=receiver, product='F001', name='Future Mouse', unique=f'MOUSE{index}', suffix=f'{index:04x}')
        input_node(child, index, keys=(key,))
    result = discover(inventory, bluez_objects={})
    children = {device.id.rsplit(':', 1)[-1]: device for device in result if device.kind == 'mouse'}
    assert {control.id for control in children['mouse1'].controls} == {'key:272'}
    assert {control.id for control in children['mouse2'].controls} == {'key:273'}
    assert not next(device for device in result if device.kind == 'receiver').controls


def test_non_hid_controls_under_direct_usb_interfaces_are_discovered(inventory):
    usb, _ = inventory
    camera = usb_node(usb, product='f002', name='Logitech Webcam', serial='CAMERA')
    input_node(camera / f'{camera.name}:1.0', 1, keys=(212,))
    # A nested unrelated USB child is not part of this device's controls.
    input_node(camera / 'nested-device', 2, keys=(115,))
    result, = discover(inventory, bluez_objects={})
    assert result.kind == 'webcam'
    assert [(control.id, control.label) for control in result.controls] == [('key:212', 'Camera')]
