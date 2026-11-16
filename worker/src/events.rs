use mx_creative_worker::{
    config::DeviceBinding,
    control::{Foreground, Request},
};
use std::{
    io::Write,
    os::unix::net::UnixStream,
    sync::{mpsc, Arc},
};
pub enum Event {
    Request(
        Request,
        mpsc::SyncSender<serde_json::Value>,
        mpsc::Receiver<()>,
    ),
    Focus(Foreground),
    FocusSupport(bool),
    Tray(bool),
    Page(usize),
    Pause(bool),
    DeviceButton(DeviceBinding),
    RingExited(String, Option<String>),
    DeviceButtonsStatus(serde_json::Value),
    DialpadButtonsStatus(serde_json::Value),
    Master4ButtonsStatus(serde_json::Value),
    Shutdown,
}
#[derive(Clone)]
pub struct Sender {
    tx: mpsc::Sender<Event>,
    wake: Arc<UnixStream>,
}
impl Sender {
    pub fn new(tx: mpsc::Sender<Event>, wake: UnixStream) -> Self {
        Self {
            tx,
            wake: Arc::new(wake),
        }
    }
    pub fn send(&self, event: Event) {
        if self.tx.send(event).is_ok() {
            let _ = (&*self.wake).write(&[1]);
        }
    }
}
