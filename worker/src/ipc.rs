use crate::events::{Event, Sender};
use mx_creative_worker::control::Request;
use std::{
    fs::{self, File, OpenOptions},
    io::{self, BufRead, BufReader, Write},
    os::{
        fd::AsRawFd,
        unix::{
            fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt},
            net::UnixListener,
        },
    },
    path::{Path, PathBuf},
    sync::mpsc,
    time::Duration,
};

pub struct SocketGuard {
    pub listener: UnixListener,
    _lock: File,
    path: PathBuf,
}

/// The predictable fallback base must itself be private, not only its child.
/// Called for default/XDG runtime paths before creating the socket directory.
pub fn prepare_runtime_base(path: &Path) -> io::Result<()> {
    fs::create_dir_all(path)?;
    let directory = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY | libc::O_CLOEXEC)
        .open(path)?;
    if directory.metadata()?.uid() != unsafe { libc::getuid() } {
        return Err(io::Error::other(
            "runtime directory must be owned by current user",
        ));
    }
    directory.set_permissions(fs::Permissions::from_mode(0o700))
}
impl SocketGuard {
    pub fn bind(path: &Path) -> io::Result<Self> {
        let parent = path
            .parent()
            .ok_or_else(|| io::Error::other("socket path has no directory"))?;
        fs::create_dir_all(parent)?;
        let metadata = fs::symlink_metadata(parent)?;
        // SAFETY: getuid has no preconditions.
        if !metadata.is_dir() || metadata.uid() != unsafe { libc::getuid() } {
            return Err(io::Error::other(
                "socket directory must be owned by current user",
            ));
        }
        fs::set_permissions(parent, fs::Permissions::from_mode(0o700))?;
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(parent.join("worker.lock"))?;
        // SAFETY: flock operates on this live descriptor.
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } != 0 {
            return Err(io::Error::other("worker is already running"));
        }
        if let Ok(meta) = fs::symlink_metadata(path) {
            if !meta.file_type().is_socket() || meta.uid() != unsafe { libc::getuid() } {
                return Err(io::Error::other(
                    "refusing to replace unexpected socket path",
                ));
            }
            fs::remove_file(path)?;
        }
        let listener = UnixListener::bind(path)?;
        fs::set_permissions(path, fs::Permissions::from_mode(0o600))?;
        Ok(Self {
            listener,
            _lock: lock,
            path: path.into(),
        })
    }
    pub fn serve(&self, sender: Sender) -> io::Result<()> {
        let listener = self.listener.try_clone()?;
        std::thread::spawn(move || {
            for connection in listener.incoming() {
                let Ok(mut socket) = connection else { break };
                let _ = socket.set_read_timeout(Some(Duration::from_secs(1)));
                let _ = socket.set_write_timeout(Some(Duration::from_secs(1)));
                let mut written = None;
                let result = (|| -> Result<serde_json::Value, String> {
                    let mut reader = BufReader::new(&socket);
                    let mut line = Vec::new();
                    loop {
                        let available = reader.fill_buf().map_err(|e| e.to_string())?;
                        if available.is_empty() {
                            return Err("request must end with newline".into());
                        }
                        let count = available
                            .iter()
                            .position(|b| *b == b'\n')
                            .map(|n| n + 1)
                            .unwrap_or(available.len());
                        if line.len() + count > 4096 {
                            return Err("request exceeds 4096 bytes".into());
                        }
                        let done = available[count - 1] == b'\n';
                        line.extend_from_slice(&available[..count]);
                        reader.consume(count);
                        if done {
                            break;
                        }
                    }
                    let request: Request =
                        serde_json::from_slice(&line).map_err(|e| e.to_string())?;
                    let (tx, rx) = mpsc::sync_channel(1);
                    let (done_tx, done_rx) = mpsc::sync_channel(1);
                    written = Some(done_tx);
                    sender.send(Event::Request(request, tx, done_rx));
                    rx.recv_timeout(Duration::from_secs(25))
                        .map_err(|e| e.to_string())
                })();
                let response =
                    result.unwrap_or_else(|error| serde_json::json!({"ok":false,"error":error}));
                let _ = writeln!(socket, "{response}");
                if let Some(done) = written {
                    let _ = done.send(());
                }
            }
        });
        Ok(())
    }
}

impl Drop for SocketGuard {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{io::Read, os::unix::net::UnixStream};
    #[test]
    fn socket_is_private_single_instance_and_requests_are_bounded() {
        let dir = std::env::temp_dir().join(format!("mxcc-ipc-test-{}", std::process::id()));
        let path = dir.join("control.sock");
        let guard = SocketGuard::bind(&path).unwrap();
        assert_eq!(
            fs::metadata(&dir).unwrap().permissions().mode() & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o600
        );
        assert!(SocketGuard::bind(&path).is_err());
        let (tx, rx) = mpsc::channel();
        let (_reader, wake) = UnixStream::pair().unwrap();
        guard.serve(Sender::new(tx, wake)).unwrap();
        let responder = std::thread::spawn(move || {
            let Event::Request(Request::Status {}, reply, written) =
                rx.recv_timeout(Duration::from_secs(2)).unwrap()
            else {
                panic!("wrong request")
            };
            reply.send(serde_json::json!({"ok":true,"page":1})).unwrap();
            written.recv_timeout(Duration::from_secs(2)).unwrap();
        });
        let mut client = UnixStream::connect(&path).unwrap();
        client.write_all(b"{\"command\":\"status\"}\n").unwrap();
        let mut response = String::new();
        client.read_to_string(&mut response).unwrap();
        assert_eq!(
            serde_json::from_str::<serde_json::Value>(&response).unwrap()["page"],
            1
        );
        responder.join().unwrap();
        let mut client = UnixStream::connect(&path).unwrap();
        client.write_all(&[b' '; 4097]).unwrap();
        let mut response = String::new();
        client.read_to_string(&mut response).unwrap();
        assert!(response.contains("exceeds 4096 bytes"));
        drop(guard);
        assert!(!path.exists());
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn runtime_base_is_private_and_never_follows_symlinks() {
        let root = std::env::temp_dir().join(format!("mxcc-runtime-test-{}", std::process::id()));
        let real = root.join("real");
        prepare_runtime_base(&real).unwrap();
        assert_eq!(
            fs::metadata(&real).unwrap().permissions().mode() & 0o777,
            0o700
        );
        let link = root.join("symlink");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        assert!(prepare_runtime_base(&link).is_err());
        fs::remove_dir_all(root).unwrap();
    }
}
