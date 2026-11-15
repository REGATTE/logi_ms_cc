//! MX Creative Keypad vendor protocol, adapted from OpenLogi v0.9.0 (MIT).
//! See THIRD_PARTY.md and licenses/OpenLogi-MIT.txt.
use std::collections::BTreeSet;

pub const REPORT_SIZE: usize = 4095;
pub const PAGE_CONTROLS: [u16; 2] = [0x01a1, 0x01a2];

#[derive(Default)]
pub struct Edges {
    grid: BTreeSet<u8>,
    pages: BTreeSet<u16>,
}

#[derive(Debug, PartialEq)]
pub enum Press {
    Key(usize),
    Page(usize),
}

impl Edges {
    pub fn report(&mut self, bytes: &[u8], control_feature: Option<u8>) -> Vec<Press> {
        if bytes.len() >= 7 && bytes[..4] == [0x13, 0xff, 2, 0] && bytes[5] == 1 {
            let held: BTreeSet<_> = bytes[6..]
                .iter()
                .copied()
                .take_while(|c| *c != 0)
                .filter(|c| (1..=9).contains(c))
                .collect();
            let presses = held
                .difference(&self.grid)
                .map(|k| Press::Key(usize::from(*k - 1)))
                .collect();
            self.grid = held;
            return presses;
        }
        if bytes.len() >= 12
            && matches!(bytes[0], 0x10 | 0x11)
            && bytes[1] == 0xff
            && Some(bytes[2]) == control_feature
            && bytes[3] == 0
        {
            let held: BTreeSet<_> = bytes[4..12]
                .chunks_exact(2)
                .map(|c| u16::from_be_bytes([c[0], c[1]]))
                .filter(|c| PAGE_CONTROLS.contains(c))
                .collect();
            let presses = held
                .difference(&self.pages)
                .map(|c| Press::Page(PAGE_CONTROLS.iter().position(|v| v == c).unwrap()))
                .collect();
            self.pages = held;
            return presses;
        }
        Vec::new()
    }
}

pub fn image_packets(slot: usize, jpeg: &[u8]) -> Result<Vec<Vec<u8>>, String> {
    if slot >= 9
        || jpeg.len() < 4
        || jpeg.len() > 65535
        || jpeg[..2] != [0xff, 0xd8]
        || jpeg[jpeg.len() - 2..] != [0xff, 0xd9]
    {
        return Err("invalid slot or JPEG (maximum 65535 bytes)".into());
    }
    let first_size = jpeg.len().min(REPORT_SIZE - 20);
    let count = 1 + (jpeg.len() - first_size).div_ceil(REPORT_SIZE - 5);
    let part = |i: usize| -> u8 {
        i as u8 | 0x20 | if i == 1 { 0x80 } else { 0 } | if i == count { 0x40 } else { 0 }
    };
    let mut first = vec![0; REPORT_SIZE];
    first[..5].copy_from_slice(&[0x14, 0xff, 2, 0x2b, part(1)]);
    first[5..9].copy_from_slice(&[1, 0, 1, 0]);
    let x = 23 + (slot % 3) as u16 * 158;
    let y = 6 + (slot / 3) as u16 * 158;
    for (offset, value) in [
        (9, x),
        (11, y),
        (13, 118),
        (15, 118),
        (18, jpeg.len() as u16),
    ] {
        first[offset..offset + 2].copy_from_slice(&value.to_be_bytes());
    }
    first[20..20 + first_size].copy_from_slice(&jpeg[..first_size]);
    let mut packets = vec![first];
    for (i, chunk) in jpeg[first_size..].chunks(REPORT_SIZE - 5).enumerate() {
        let mut packet = vec![0; REPORT_SIZE];
        packet[..5].copy_from_slice(&[0x14, 0xff, 2, 0x2b, part(i + 2)]);
        packet[5..5 + chunk.len()].copy_from_slice(chunk);
        packets.push(packet);
    }
    Ok(packets)
}

#[derive(Debug, PartialEq, Eq)]
pub enum ImageAck {
    Accepted,
    Busy,
    Rejected([u8; 2]),
}

pub fn image_ack(report: &[u8], output_part: u8) -> Option<ImageAck> {
    // Each packet, including intermediate parts, gets its own ACK. The part
    // index is echoed with 0xc0 flags. Only byte5 is the status: byte6 is not
    // an error code. See the original node library and THIRD_PARTY.md.
    if report.len() < 7
        || report[..4] != [0x13, 0xff, 2, 0x2b]
        || report[4] != ((output_part & 0x1f) | 0xc0)
    {
        return None;
    }
    Some(match report[5] {
        0 => ImageAck::Accepted,
        0xfb => ImageAck::Busy,
        _ => ImageAck::Rejected([report[5], report[6]]),
    })
}
