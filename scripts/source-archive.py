#!/usr/bin/env python3
"""Create a deterministic source archive without prototypes, binaries or user data."""
import gzip
import hashlib
import os
from pathlib import Path
import tarfile
import tomllib

root = Path(__file__).resolve().parents[1]
with (root / "worker/Cargo.toml").open("rb") as stream:
    version = tomllib.load(stream)["package"]["version"]
prefix = f"mx-creative-control-{version}"
epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "0"))
excluded = {"__pycache__", ".pytest_cache", ".ruff_cache", "target", ".git"}
inputs = ["README.md", "LICENSE", "pyproject.toml", ".gitignore", "src", "worker",
          "shortcuts", "packaging", "scripts", "tests", "docs", "browser-extension"]
paths = []
for name in inputs:
    candidate = root / name
    if candidate.exists():
        paths.append(candidate)
        if candidate.is_dir():
            paths.extend(candidate.rglob("*"))
paths = sorted(path for path in paths if not excluded.intersection(path.relative_to(root).parts)
               and not any(part.endswith(".egg-info") for part in path.relative_to(root).parts)
               and path.suffix != ".pyc" and path.name != ".DS_Store")
output = root / "dist" / f"{prefix}.tar.gz"
output.parent.mkdir(exist_ok=True)
with output.open("wb") as raw:
    with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=epoch) as compressed:
        with tarfile.open(fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT) as archive:
            for path in paths:
                info = archive.gettarinfo(str(path), f"{prefix}/{path.relative_to(root)}")
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = epoch
                info.mode = 0o755 if info.isdir() or path.stat().st_mode & 0o111 else 0o644
                if info.isfile():
                    with path.open("rb") as source:
                        archive.addfile(info, source)
                else:
                    archive.addfile(info)
checksum = hashlib.sha256(output.read_bytes()).hexdigest()
output.with_suffix(output.suffix + ".sha256").write_text(f"{checksum}  {output.name}\n")
print(output)
