"""Deterministic, allowlisted evidence archives. No source uploads or promotion."""
from __future__ import annotations

import hashlib
from pathlib import Path, PurePosixPath
import re
import uuid
import zipfile
import zlib

from .common import AVError, sha256


def _member_name(name):
    if not isinstance(name, str) or not name:
        raise AVError("Archive member must be a nonempty relative name")
    p = PurePosixPath(name)
    if (p.is_absolute() or ".." in p.parts or "\\" in name or ":" in name
            or str(p) in ("", ".") or p.as_posix() != name
            or any(ord(c) < 32 for c in name)):
        raise AVError(f"Unsafe archive member: {name!r}")
    return name


def deterministic_zip(files, destination, prefix="", *, expected_hashes=None):
    """Pack (relative name, bytes or Path) pairs, streaming file-backed members.

    expected_hashes, when supplied, binds each unprefixed member to a digest
    already verified in its source run. A failed archive remains in staging.
    """
    target = Path(destination).resolve()
    if target.exists():
        raise AVError(f"Archive already exists: {target}")
    if prefix:
        _member_name(prefix.removesuffix("/"))
        if not prefix.endswith("/"):
            raise AVError("Archive prefix must end with /")
    target.parent.mkdir(parents=True, exist_ok=True)
    entries = []
    seen = set()
    for name, data in files:
        final = _member_name(prefix + _member_name(name))
        if final.casefold() in seen:
            raise AVError("Duplicate archive member")
        seen.add(final.casefold())
        if not isinstance(data, (bytes, Path)):
            raise AVError("Archive payload must be bytes or a pathlib.Path")
        if isinstance(data, Path) and (data.is_symlink() or not data.is_file()):
            raise AVError("Archive source must be a regular non-symlink file")
        entries.append((final, data, name))
    checksum_name = prefix + "SHA256SUMS.txt"
    if checksum_name.casefold() in seen:
        raise AVError("Checksum member is reserved")
    if expected_hashes is not None and set(expected_hashes) != {e[2] for e in entries}:
        raise AVError("Expected archive hashes must cover every member exactly")
    staging = target.parent / ("." + target.name + ".staging-" + uuid.uuid4().hex)
    sums = []
    with zipfile.ZipFile(staging, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data, original in sorted(entries):
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            info.file_size = len(data) if isinstance(data, bytes) else data.stat().st_size
            digest = hashlib.sha256()
            with archive.open(info, "w", force_zip64=True) as member:
                if isinstance(data, bytes):
                    member.write(data)
                    digest.update(data)
                else:
                    with data.open("rb") as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            member.write(chunk)
                            digest.update(chunk)
            actual = digest.hexdigest()
            if expected_hashes is not None and expected_hashes[original] != actual:
                raise AVError(f"Artifact changed while archiving: {original}")
            sums.append(f"{actual}  {name}\n")
        info = zipfile.ZipInfo(checksum_name, (1980, 1, 1, 0, 0, 0))
        info.external_attr = 0o100644 << 16
        archive.writestr(info, "".join(sums).encode("utf-8"),
                         compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    if target.exists():
        raise AVError("Archive destination appeared while building; staged archive retained")
    staging.rename(target)
    return {"archive": str(target), "sha256": sha256(target), "members": len(entries) + 1}


def pack_run(run_dir, destination):
    from .inventory import verify_run
    from .common import read_json, safe_member
    root = Path(run_dir).resolve()
    target = Path(destination).resolve()
    if target == root or root in target.parents:
        raise AVError("An archive must be outside its immutable source run")
    verify_run(root)
    manifest = read_json(root / "run.json")
    files = [("run.json", root / "run.json")]
    files += [(e["path"], safe_member(root, e["path"])) for e in manifest["artifacts"]]
    hashes = {e["path"]: e["sha256"] for e in manifest["artifacts"]}
    hashes["run.json"] = sha256(root / "run.json")
    return deterministic_zip(files, target, prefix="evidence/", expected_hashes=hashes)


def verify_archive(path):
    try:
        return _verify_archive(path)
    except (zipfile.BadZipFile, zlib.error, UnicodeError, ValueError, RuntimeError, KeyError, EOFError) as exc:
        if isinstance(exc, AVError):
            raise
        raise AVError(f"Malformed or unsupported evidence archive: {exc}") from exc


def _verify_archive(path):
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if len({n.casefold() for n in names}) != len(names):
            raise AVError("Duplicate archive members")
        for info in z.infolist():
            _member_name(info.filename)
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise AVError("Symlinks are not allowed")
        checksums = [n for n in names if n.endswith("/SHA256SUMS.txt") or n == "SHA256SUMS.txt"]
        if len(checksums) != 1:
            raise AVError("Exactly one checksum inventory is required")
        expected = {}
        for line in z.read(checksums[0]).decode("utf-8").splitlines():
            digest, name = line.split("  ", 1)
            if not re.fullmatch(r"[0-9a-f]{64}", digest) or name in expected:
                raise AVError("Malformed digest or duplicate checksum record")
            expected[name] = digest
        if set(expected) != set(names) - {checksums[0]}:
            raise AVError("Archive membership differs from checksum inventory")
        for name, digest in expected.items():
            h = hashlib.sha256()
            with z.open(name) as member:
                for chunk in iter(lambda: member.read(1024 * 1024), b""):
                    h.update(chunk)
            if h.hexdigest() != digest:
                raise AVError(f"Checksum mismatch: {name}")
    return {"archive": str(Path(path).resolve()), "sha256": sha256(path), "members": len(names),
            "integrity_valid": True, "scope": "Archive byte integrity only; not source authenticity or perceptual review."}
