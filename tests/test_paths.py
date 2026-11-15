import os
import stat

import pytest

from mxcc import paths


def test_runtime_directory_is_private_and_refuses_symlinked_locations(monkeypatch, tmp_path):
    runtime = tmp_path / "runtime"
    runtime.mkdir(mode=0o777)
    runtime.chmod(0o777)
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))

    directory = paths.prepare_runtime_dir()
    assert directory == runtime / paths.APP_ID
    for path in (runtime, directory):
        assert path.stat().st_uid == os.getuid()
        assert stat.S_IMODE(path.stat().st_mode) == 0o700

    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    outside.chmod(0o755)
    directory.rmdir()
    directory.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="real directory owned by you"):
        paths.prepare_runtime_dir()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755

    directory.unlink()
    runtime.rmdir()
    runtime.symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="real directory owned by you"):
        paths.prepare_runtime_dir()
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755
    assert not (outside / paths.APP_ID).exists()
