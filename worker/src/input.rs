use std::{
    fs::{File, OpenOptions},
    io::{self, Write},
    os::fd::AsRawFd,
};

/// Linux evdev codes. These are physical US-layout key names, not text input.
pub fn parse_chord(text: &str) -> Result<Vec<u16>, String> {
    if text.len() > 128 {
        return Err("shortcut is too long".into());
    }
    let mut mods = Vec::new();
    let mut key = None;
    for token in text.split('+') {
        let s = token.trim().to_ascii_lowercase();
        let modifier = match s.as_str() {
            "ctrl" | "control" => Some(29),
            "shift" => Some(42),
            "alt" => Some(56),
            "super" | "meta" => Some(125),
            _ => None,
        };
        if let Some(m) = modifier {
            if mods.contains(&m) {
                return Err("duplicate modifier".into());
            }
            mods.push(m);
            continue;
        }
        if key.is_some() {
            return Err("shortcut requires exactly one key".into());
        }
        let code = match s.as_str() {
            "a" => 30,
            "b" => 48,
            "c" => 46,
            "d" => 32,
            "e" => 18,
            "f" => 33,
            "g" => 34,
            "h" => 35,
            "i" => 23,
            "j" => 36,
            "k" => 37,
            "l" => 38,
            "m" => 50,
            "n" => 49,
            "o" => 24,
            "p" => 25,
            "q" => 16,
            "r" => 19,
            "s" => 31,
            "t" => 20,
            "u" => 22,
            "v" => 47,
            "w" => 17,
            "x" => 45,
            "y" => 21,
            "z" => 44,
            "1" => 2,
            "2" => 3,
            "3" => 4,
            "4" => 5,
            "5" => 6,
            "6" => 7,
            "7" => 8,
            "8" => 9,
            "9" => 10,
            "0" => 11,
            "enter" | "return" => 28,
            "tab" => 15,
            "space" => 57,
            "escape" | "esc" => 1,
            "backspace" => 14,
            "delete" => 111,
            "insert" => 110,
            "home" => 102,
            "end" => 107,
            "pageup" => 104,
            "pagedown" => 109,
            "up" => 103,
            "down" => 108,
            "left" => 105,
            "right" => 106,
            "print" => 99,
            "volumeup" => 115,
            "volumedown" => 114,
            "mute" => 113,
            "playpause" => 164,
            "nexttrack" => 163,
            "prevtrack" => 165,
            "`" | "grave" => 41,
            "-" | "minus" => 12,
            "=" | "equal" => 13,
            "[" | "bracketleft" => 26,
            "]" | "bracketright" => 27,
            "\\" | "backslash" => 43,
            ";" | "semicolon" => 39,
            "'" | "apostrophe" => 40,
            "," | "comma" => 51,
            "." | "period" => 52,
            "/" | "slash" => 53,
            "plus" => {
                if !mods.contains(&42) {
                    mods.push(42);
                }
                13
            }
            _ => match s.strip_prefix('f').and_then(|v| v.parse::<u16>().ok()) {
                Some(n @ 1..=10) => 58 + n,
                Some(11) => 87,
                Some(12) => 88,
                Some(n @ 13..=24) => 170 + n,
                _ => return Err(format!("unsupported shortcut key: {token}")),
            },
        };
        key = Some(code);
    }
    mods.push(key.ok_or("shortcut requires a nonmodifier key")?);
    Ok(mods)
}

const UI_SET_EVBIT: libc::c_ulong = 0x40045564;
const UI_SET_KEYBIT: libc::c_ulong = 0x40045565;
const UI_DEV_CREATE: libc::c_ulong = 0x5501;
const UI_DEV_DESTROY: libc::c_ulong = 0x5502;

pub struct Keyboard(File);
impl Keyboard {
    pub fn open() -> io::Result<Self> {
        let mut file = OpenOptions::new().write(true).open("/dev/uinput")?;
        // SAFETY: documented uinput integer ioctls on this live descriptor.
        unsafe {
            if libc::ioctl(file.as_raw_fd(), UI_SET_EVBIT, 1) < 0 {
                return Err(io::Error::last_os_error());
            }
            for code in 1..=194 {
                if libc::ioctl(file.as_raw_fd(), UI_SET_KEYBIT, code) < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
        }
        // Legacy uinput_user_dev ABI: 80-byte name, input_id, ff_effects_max,
        // followed by four 64-entry i32 ABS tables. All fields initially zero.
        let mut setup = [0u8; 1116];
        let name = b"MX Creative Control shortcuts";
        setup[..name.len()].copy_from_slice(name);
        setup[80..82].copy_from_slice(&3u16.to_ne_bytes());
        setup[82..84].copy_from_slice(&0x046du16.to_ne_bytes());
        setup[84..86].copy_from_slice(&0xc354u16.to_ne_bytes());
        file.write_all(&setup)?;
        // SAFETY: UI_DEV_CREATE takes no third argument.
        if unsafe { libc::ioctl(file.as_raw_fd(), UI_DEV_CREATE) } < 0 {
            return Err(io::Error::last_os_error());
        }
        Ok(Self(file))
    }
    fn event(&mut self, kind: u16, code: u16, value: i32) -> io::Result<()> {
        #[repr(C)]
        struct Event {
            time: libc::timeval,
            kind: u16,
            code: u16,
            value: i32,
        }
        let event = Event {
            time: libc::timeval {
                tv_sec: 0,
                tv_usec: 0,
            },
            kind,
            code,
            value,
        };
        // SAFETY: C input_event has no uninitialised padding with these fields.
        let bytes = unsafe {
            std::slice::from_raw_parts(
                (&event as *const Event).cast::<u8>(),
                std::mem::size_of::<Event>(),
            )
        };
        self.0.write_all(bytes)
    }
    pub fn chord(&mut self, keys: &[u16]) -> io::Result<()> {
        // Release every key even after a failed press to avoid stuck modifiers.
        let mut result = Ok(());
        for key in keys {
            if let Err(e) = self.event(1, *key, 1) {
                result = Err(e);
                break;
            }
        }
        if let Err(e) = self.event(0, 0, 0) {
            result = Err(e);
        }
        for key in keys.iter().rev() {
            if let Err(e) = self.event(1, *key, 0) {
                result = Err(e);
            }
        }
        if let Err(e) = self.event(0, 0, 0) {
            result = Err(e);
        }
        result
    }
}
impl Drop for Keyboard {
    fn drop(&mut self) {
        unsafe {
            libc::ioctl(self.0.as_raw_fd(), UI_DEV_DESTROY);
        }
    }
}
