from pathlib import Path

from mxcc.applications import application_directories, discover_applications


def desktop(root, name, fields="", *, title="App"):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"[Desktop Entry]\nType=Application\nName={title}\nExec=example %U\nIcon=original-icon\n{fields}")
    return path


def test_user_overrides_and_hidden_tombstones_mask_system_entries(tmp_path):
    user, system = tmp_path / "user", tmp_path / "system"
    desktop(system, "keep.desktop", title="System")
    override = desktop(user, "keep.desktop", title="User")
    desktop(system, "hide.desktop")
    desktop(user, "hide.desktop", "Hidden=true\n")
    desktop(user, "private.desktop", "NoDisplay=true\n")
    desktop(user, "bad.desktop").write_text("not a desktop file")
    desktop(system, "bad.desktop")
    apps = discover_applications([user, system])
    assert [(app.id, app.name, app.icon, app.path) for app in apps] == [("keep.desktop", "User", "original-icon", override)]


def test_desktop_visibility_tryexec_locale_and_nested_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    monkeypatch.setenv("LC_ALL", "fr_FR.UTF-8")
    monkeypatch.delenv("LANGUAGE", raising=False)
    desktop(tmp_path, "tools/editor.desktop", "Name[fr]=Éditeur\nName[fr_FR]=Éditeur français\nOnlyShowIn=GNOME;\nStartupWMClass=MyEditor\n")
    desktop(tmp_path, "excluded.desktop", "NotShowIn=GNOME;\n")
    desktop(tmp_path, "other-desktop.desktop", "OnlyShowIn=KDE;\n")
    desktop(tmp_path, "missing-executable.desktop", "TryExec=/definitely-not-installed/mxcc-test\n")
    executable = tmp_path / "executable"
    executable.write_text("unused")
    executable.chmod(0o700)
    desktop(tmp_path, "available.desktop", f"TryExec={executable}\n", title="Available")
    apps = discover_applications([tmp_path])
    assert [app.id for app in apps] == ["available.desktop", "tools-editor.desktop"]
    assert apps[1].name == "Éditeur français"
    assert apps[1].wm_class == "MyEditor"
    assert apps[1].exec_line == "example %U"


def test_dbus_launch_and_absolute_original_icon_do_not_require_exec(tmp_path):
    path = tmp_path / "org.example.App.desktop"
    path.write_text("[Desktop Entry]\nType=Application\nName=App\nDBusActivatable=true\nIcon=/opt/app/original.png\n")
    app, = discover_applications([tmp_path])
    assert app.exec_line == ""
    assert app.icon == "/opt/app/original.png"


def test_xdg_and_packaged_export_directories(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "user"))
    monkeypatch.setenv("XDG_DATA_DIRS", f"{tmp_path}/first:{tmp_path}/second:{tmp_path}/first:relative")
    roots = application_directories()
    assert roots[:3] == [tmp_path / "user/applications", tmp_path / "first/applications", tmp_path / "second/applications"]
    assert len(roots) == len(set(roots))
    assert Path("/var/lib/snapd/desktop/applications") in roots
    assert Path("/var/lib/flatpak/exports/share/applications") in roots
