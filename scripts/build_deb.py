#!/usr/bin/env python3
"""Assemble an offline-installable Ubuntu package from pinned local inputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tomllib

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "mx-creative-control"
DEPENDENCIES = (
    "python3 (>= 3.12), python3-gi, gir1.2-atspi-2.0, libc6 (>= 2.39), libstdc++6, libgcc-s1, "
    "libdbus-1-3, libudev1, libglib2.0-0t64, libgl1, libegl1, libopengl0, "
    "libfontconfig1, libfreetype6, libx11-6, libx11-xcb1, libxcb1, "
    "libxcb-cursor0, libxcb-icccm4, libxcb-image0, libxcb-keysyms1, "
    "libxcb-render-util0, libxcb-randr0, libxcb-shape0, libxcb-shm0, "
    "libxcb-sync1, libxcb-xfixes0, libxcb-render0, libxcb-xkb1, "
    "libxkbcommon0, libxkbcommon-x11-0, libwayland-client0, libwayland-cursor0, "
    "libwayland-egl1, libzstd1, zlib1g, libgssapi-krb5-2, libbrotli1, "
    "libgtk-3-bin, pkexec, polkitd, udev, kmod, systemd"
)


def copy_file(source: Path, stage: Path, destination: str, mode: int = 0o644) -> None:
    target = stage / destination.lstrip("/")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    target.chmod(mode)


def copy_tree(source: Path, destination: Path) -> None:
    shutil.copytree(source, destination, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))


def version() -> str:
    with (ROOT / "worker/Cargo.toml").open("rb") as stream:
        return tomllib.load(stream)["package"]["version"]


def copy_rust_notices(stage: Path) -> None:
    """Preserve the license texts of the linked platform's cached crates."""
    compiler = subprocess.check_output(["rustc", "-vV"], text=True)
    host = next(line.split(": ", 1)[1] for line in compiler.splitlines() if line.startswith("host:"))
    metadata = json.loads(subprocess.check_output(
        ["cargo", "metadata", "--format-version", "1", "--locked", "--offline", "--filter-platform", host],
        cwd=ROOT / "worker", text=True))
    selected = {node["id"] for node in metadata["resolve"]["nodes"]}
    packages = sorted((item for item in metadata["packages"] if item["id"] in selected and item.get("source")),
                      key=lambda item: (item["name"], item["version"]))
    base = f"/usr/share/doc/{PACKAGE}/licenses/rust"
    index = ["# Rust dependency notices", "", "Package | Version | Declared license", "--- | --- | ---"]
    for package in packages:
        directory = Path(package["manifest_path"]).parent
        notices = [path for path in directory.iterdir() if path.is_file()
                   and path.name.lower().startswith(("license", "licence", "copying", "notice", "copyright", "unlicense"))]
        if not notices:
            raise RuntimeError(f"No dependency license text found for {package['name']} {package['version']}")
        name = f"{package['name']}-{package['version']}"
        for notice in sorted(notices):
            copy_file(notice, stage, f"{base}/{name}/{notice.name}")
        index.append(f"{package['name']} | {package['version']} | {package.get('license') or 'See notice files'}")
    destination = stage / base.lstrip("/") / "README.md"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text("\n".join(index) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-dir", type=Path, default=ROOT / "build/runtime",
                        help="Directory containing pinned PySide6 and shiboken6 packages")
    parser.add_argument("--skip-worker-build", action="store_true")
    parser.add_argument("--version", default=version())
    parser.add_argument("--output-dir", type=Path, default=ROOT / "dist")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9][0-9A-Za-z.+~]*", args.version):
        parser.error("Version must be a simple Debian upstream version")
    for package in ("PySide6", "shiboken6"):
        if not (args.runtime_dir / package).is_dir():
            parser.error("Qt runtime absent; run scripts/prepare-runtime.sh --download first")
    runtime_metadata = []
    for name in ("pyside6_essentials", "shiboken6"):
        matches = [path for path in args.runtime_dir.glob("*.dist-info")
                   if path.name.lower() == f"{name}-6.10.2.dist-info"]
        if len(matches) != 1:
            parser.error(f"Expected pinned {name} 6.10.2 metadata in runtime directory")
        runtime_metadata.extend(matches)
    environment = os.environ.copy()
    epoch = int(environment.setdefault("SOURCE_DATE_EPOCH", "0"))
    # Path remapping keeps build machine paths out of release diagnostics.
    if "RUSTFLAGS" not in environment and "CARGO_ENCODED_RUSTFLAGS" not in environment:
        environment["CARGO_ENCODED_RUSTFLAGS"] = f"--remap-path-prefix={ROOT}=."
    if not args.skip_worker_build:
        subprocess.run(["cargo", "build", "--release", "--locked", "--offline"],
                       cwd=ROOT / "worker", env=environment, check=True)
    worker = ROOT / "worker/target/release/mxcc-worker"
    if not worker.is_file():
        parser.error("Worker binary absent; build worker/ with cargo build --release --locked")
    architecture = subprocess.check_output(["dpkg", "--print-architecture"], text=True).strip()
    stage = ROOT / "build/deb-root"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    private = stage / "usr/lib" / PACKAGE / "python"
    for package in ("PySide6", "shiboken6"):
        copy_tree(args.runtime_dir / package, private / package)
    for metadata in runtime_metadata:
        copy_tree(metadata, private / metadata.name)
    copy_tree(ROOT / "src/mxcc", private / "mxcc")
    copy_tree(ROOT / "browser-extension/firefox", stage / "usr/share" / PACKAGE / "browser-extension/firefox")
    copy_tree(ROOT / "shortcuts/config", stage / "usr/share" / PACKAGE / "shortcuts/config")
    assets = (
        ("mx-creative-control", "/usr/bin/mx-creative-control", 0o755),
        ("setup-permissions", "/usr/lib/mx-creative-control/setup-permissions", 0o755),
        ("mx-creative-control.service", "/usr/lib/systemd/user/mx-creative-control.service", 0o644),
        ("mx-creative-control.desktop", "/usr/share/applications/mx-creative-control.desktop", 0o644),
        ("mx-creative-control.svg", "/usr/share/icons/hicolor/scalable/apps/mx-creative-control.svg", 0o644),
        ("70-mx-creative-control.rules", "/usr/lib/udev/rules.d/70-mx-creative-control.rules", 0o644),
        ("org.mxcreativecontrol.setup-permissions.policy", "/usr/share/polkit-1/actions/org.mxcreativecontrol.setup-permissions.policy", 0o644),
        ("postinst", "/DEBIAN/postinst", 0o755),
        ("postrm", "/DEBIAN/postrm", 0o755),
    )
    for source, target, mode in assets:
        copy_file(ROOT / "packaging" / source, stage, target, mode)
    copy_file(worker, stage, "/usr/lib/mx-creative-control/mxcc-worker", 0o755)
    for document in ("README.md", "docs/INSTALL.md", "docs/PROFILES.md", "docs/ACTION-RING.md", "docs/WEBSITE-SHORTCUTS.md", "docs/VALIDATION.md", "docs/UI-REFERENCES.md", "LICENSE", "worker/THIRD_PARTY.md"):
        if (ROOT / document).is_file():
            copy_file(ROOT / document, stage, f"/usr/share/doc/{PACKAGE}/{Path(document).name}")
    if (ROOT / "worker/licenses").is_dir():
        copy_tree(ROOT / "worker/licenses", stage / "usr/share/doc" / PACKAGE / "licenses")
    if (ROOT / "worker/README.md").is_file():
        copy_file(ROOT / "worker/README.md", stage, f"/usr/share/doc/{PACKAGE}/WORKER.md")
    copy_tree(ROOT / "packaging/licenses", stage / "usr/share/doc" / PACKAGE / "licenses")
    copy_rust_notices(stage)
    copy_file(ROOT / "packaging/THIRD_PARTY.md", stage, f"/usr/share/doc/{PACKAGE}/QT-THIRD-PARTY.md")
    installed_size = sum(path.stat().st_size for path in stage.rglob("*") if path.is_file()) // 1024
    control = (
        f"Package: {PACKAGE}\nVersion: {args.version}\nArchitecture: {architecture}\n"
        "Maintainer: MX Creative Control contributors <maintainers@mxcreativecontrol.invalid>\n"
        f"Installed-Size: {installed_size}\nSection: utils\nPriority: optional\n"
        f"Depends: {DEPENDENCIES}\n"
        "Description: Ubuntu MX Creative Keypad launcher and shortcuts\n"
        " A small Rust tray worker with on-demand Qt settings, installed application\n"
        " discovery, local shortcut profiles and active-session device permissions.\n"
    )
    (stage / "DEBIAN/control").write_text(control)
    # Consistent modes and timestamps; never preserve workstation owners or caches.
    for path in sorted(stage.rglob("*")):
        if path.is_dir():
            path.chmod(0o755)
        elif path.is_file():
            path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        os.utime(path, (epoch, epoch), follow_symlinks=False)
    os.utime(stage, (epoch, epoch))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / f"{PACKAGE}_{args.version}_{architecture}.deb"
    subprocess.run(["dpkg-deb", "--root-owner-group", "--uniform-compression", "-Zxz",
                    "--threads-max=2", "--build", str(stage), str(output)],
                   env=environment, check=True)
    checksum = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n")
    print(output)


if __name__ == "__main__":
    main()
