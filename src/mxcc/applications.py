"""Discover launchable desktop applications without executing their commands.

Desktop IDs, visibility and precedence follow the freedesktop Desktop Entry
specification. Launching is delegated to the desktop, never to a shell parser.
"""

from __future__ import annotations

import configparser
import logging
import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class Application:
    id: str
    name: str
    icon: str
    exec_line: str
    wm_class: str
    path: Path


def _unescape(value: str) -> str:
    replacements = {"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}
    return re.sub(r"\\([sntr\\])", lambda m: replacements[m.group(1)], value)


def _locale_candidates() -> list[str]:
    language = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "")
    languages = os.environ.get("LANGUAGE", "").split(":") if language not in ("C", "POSIX") else []
    languages.append(language)
    candidates: list[str] = []
    for value in languages:
        if not value or value in ("C", "POSIX"):
            continue
        locale, _, modifier = value.partition("@")
        locale = locale.split(".", 1)[0]
        lang, _, territory = locale.partition("_")
        variants = []
        if territory and modifier:
            variants.append(f"{locale}@{modifier}")
        if territory:
            variants.append(locale)
        if modifier:
            variants.append(f"{lang}@{modifier}")
        variants.append(lang)
        candidates.extend(v for v in variants if v and v not in candidates)
    return candidates


def _visible(entry: configparser.SectionProxy, desktops: Iterable[str]) -> bool:
    only = {v for v in entry.get("OnlyShowIn", "").split(";") if v}
    excluded = {v for v in entry.get("NotShowIn", "").split(";") if v}
    for desktop in desktops:
        if desktop in only:
            return True
        if desktop in excluded:
            return False
    return not only


def application_directories() -> list[Path]:
    """Return application directories in desktop-file override order."""
    home = Path.home()
    user_data = Path(os.environ.get("XDG_DATA_HOME", str(home / ".local/share")))
    if not user_data.is_absolute():
        user_data = home / ".local/share"
    roots = [user_data / "applications"]
    for value in os.environ.get("XDG_DATA_DIRS", "/usr/local/share:/usr/share").split(":"):
        if value and Path(value).is_absolute():
            roots.append(Path(value) / "applications")
    # Some sessions do not add these exporters to XDG_DATA_DIRS.
    roots.extend([
        home / ".local/share/flatpak/exports/share/applications",
        Path("/var/lib/flatpak/exports/share/applications"),
        Path("/var/lib/snapd/desktop/applications"),
    ])
    return list(dict.fromkeys(roots))


def discover_applications(directories: Iterable[Path] | None = None) -> list[Application]:
    """Scan installed desktop entries, preserving their original icon names.

    The optional directory list makes discovery testable and useful for a
    controlled installation prefix. Invalid/hidden entries mask lower-priority
    copies of the same ID, as they do in the desktop menu.
    """
    roots = application_directories() if directories is None else [Path(p) for p in directories]
    desktops = os.environ.get("XDG_CURRENT_DESKTOP", "").split(":")
    locales = _locale_candidates()
    seen: set[str] = set()
    apps: list[Application] = []
    for root in roots:
        if not root.is_dir():
            continue
        try:
            files = sorted(root.rglob("*.desktop"))
        except OSError as error:
            LOG.warning("Cannot scan applications in %s: %s", root, error)
            continue
        for path in files:
            desktop_id = path.relative_to(root).as_posix().replace("/", "-")
            if desktop_id in seen:
                continue
            seen.add(desktop_id)
            parser = configparser.ConfigParser(interpolation=None, strict=False)
            parser.optionxform = str
            try:
                # Do not read arbitrary large files under an applications dir.
                if path.stat().st_size > 1024 * 1024:
                    continue
                parser.read_string(path.read_text(encoding="utf-8"))
                entry = parser["Desktop Entry"]
                if entry.get("Type") != "Application":
                    continue
                if entry.get("Hidden", "false").lower() == "true" or entry.get("NoDisplay", "false").lower() == "true":
                    continue
                if not _visible(entry, desktops):
                    continue
                try_exec = _unescape(entry.get("TryExec", ""))
                if try_exec:
                    executable = (Path(try_exec).is_file() and os.access(try_exec, os.X_OK)) if Path(try_exec).is_absolute() else shutil.which(try_exec)
                    if not executable:
                        continue
                exec_line = entry.get("Exec", "").strip()
                if not exec_line and entry.get("DBusActivatable", "false").lower() != "true":
                    continue
                name = next((entry[f"Name[{loc}]"] for loc in locales if f"Name[{loc}]" in entry), entry.get("Name", ""))
                name = _unescape(name).strip()
                if not name:
                    continue
                apps.append(Application(
                    id=desktop_id,
                    name=name,
                    icon=_unescape(entry.get("Icon", "")).strip(),
                    exec_line=exec_line,
                    wm_class=_unescape(entry.get("StartupWMClass", "")).strip(),
                    path=path,
                ))
            except (OSError, UnicodeError, configparser.Error, KeyError) as error:
                LOG.debug("Ignoring invalid desktop entry %s: %s", path, error)
    return sorted(apps, key=lambda app: (app.name.casefold(), app.id))
