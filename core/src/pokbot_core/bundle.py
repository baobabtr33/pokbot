"""Bot bundles: a single file or a directory with pokbot.toml, zipped for upload."""

from __future__ import annotations

import io
import tomllib
import zipfile
from pathlib import Path

MAX_BUNDLE_BYTES = 20 * 1024 * 1024
RUNTIMES = {"python": (".py",), "node": (".js", ".mjs")}
MANIFEST = "pokbot.toml"


class BundleError(ValueError):
    pass


def load_manifest(directory: Path) -> dict:
    path = directory / MANIFEST
    if not path.exists():
        raise BundleError(f"{directory} has no {MANIFEST}")
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise BundleError(f"{MANIFEST}: {e}") from None
    bot = data.get("bot", data)
    for key in ("name", "entrypoint", "runtime"):
        if key not in bot:
            raise BundleError(f"{MANIFEST} is missing `{key}`")
    if bot["runtime"] not in RUNTIMES:
        raise BundleError(f"runtime must be one of {', '.join(RUNTIMES)}")
    if not (directory / bot["entrypoint"]).is_file():
        raise BundleError(f"entrypoint {bot['entrypoint']} not found")
    return bot


def manifest_for_file(path: Path) -> dict:
    runtime = next((r for r, exts in RUNTIMES.items() if path.suffix in exts), None)
    if runtime is None:
        raise BundleError(f"uploads support {', '.join(RUNTIMES)} bots; got {path.name}")
    return {"name": path.stem, "entrypoint": path.name, "runtime": runtime}


def pack(target: str) -> tuple[bytes, dict]:
    """Zip a bot file or directory. Returns (zip bytes, manifest)."""
    path = Path(target.split(":")[0])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if path.is_dir():
            manifest = load_manifest(path)
            for f in sorted(path.rglob("*")):
                rel = f.relative_to(path)
                if f.is_file() and not any(p.startswith(".") or p == "__pycache__" for p in rel.parts):
                    zf.write(f, rel.as_posix())
        else:
            manifest = manifest_for_file(path)
            zf.write(path, path.name)
            zf.writestr(
                MANIFEST,
                f'name = "{manifest["name"]}"\nentrypoint = "{manifest["entrypoint"]}"\n'
                f'runtime = "{manifest["runtime"]}"\n',
            )
    data = buf.getvalue()
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError(f"bundle is {len(data)} bytes; limit is {MAX_BUNDLE_BYTES}")
    return data, manifest


def unpack(data: bytes, dest: Path) -> dict:
    """Extract an uploaded bundle safely (size cap, no path traversal)."""
    if len(data) > MAX_BUNDLE_BYTES:
        raise BundleError("bundle too large")
    dest.mkdir(parents=True, exist_ok=True)
    root = dest.resolve()
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise BundleError("upload is not a zip file") from None
    with zf:
        total = 0
        for info in zf.infolist():
            total += info.file_size
            if total > MAX_BUNDLE_BYTES * 5:
                raise BundleError("bundle expands too large")
            target = (root / info.filename).resolve()
            if not target.is_relative_to(root):
                raise BundleError(f"bad path in bundle: {info.filename}")
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(info))
    return load_manifest(root)
