"""Release source archives include build inputs but exclude local generated data."""

from pathlib import Path
import shutil
import subprocess
import sys
import tarfile


def test_source_archive_includes_browser_extension_without_local_generated_data(tmp_path):
    root = tmp_path / "project"
    files = {
        "worker/Cargo.toml": '[package]\nname = "mx-creative-worker"\nversion = "0.4.0"\n',
        "src/mxcc/__init__.py": '__version__ = "0.4.0"\n',
        "browser-extension/firefox/manifest.json": '{"manifest_version": 2}\n',
        "browser-extension/firefox/background.js": "// packaged build input\n",
        "shortcuts/config/default.json": '{"id": "default"}\n',
        "src/mx_creative_control.egg-info/PKG-INFO": "generated metadata\n",
        "src/nested/generated.egg-info/deeper/SOURCES.txt": "generated metadata\n",
        "src/mxcc/__pycache__/module.pyc": "compiled cache\n",
        "worker/target/release/mxcc-worker": "compiled worker\n",
        "build/runtime/private.txt": "build cache\n",
        ".config/mx-creative-control/settings.json": '{"personal": true}\n',
        ".config/mx-creative-control/shortcuts/config/firefox.json": '{"personal": true}\n',
        ".local/share/icons/mxcc-firefox-private.svg": "personal favicon\n",
    }
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
    script = root / "scripts/source-archive.py"
    script.parent.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1] / "scripts/source-archive.py", script)

    subprocess.run([sys.executable, str(script)], check=True, capture_output=True, text=True)

    prefix = "mx-creative-control-0.4.0/"
    with tarfile.open(root / "dist/mx-creative-control-0.4.0.tar.gz") as archive:
        names = {name.removeprefix(prefix) for name in archive.getnames()}
        assert {
            "worker/Cargo.toml", "src/mxcc/__init__.py", "shortcuts/config/default.json",
            "browser-extension/firefox/manifest.json", "browser-extension/firefox/background.js",
        } <= names
        assert not any(part.endswith(".egg-info") for name in names for part in Path(name).parts)
        assert not any(set(Path(name).parts) & {"__pycache__", "target", "build", ".config", ".local"}
                       for name in names)
