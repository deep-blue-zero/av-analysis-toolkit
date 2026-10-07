#!/usr/bin/env python3
"""Verify bundled bytes and exact source/wheel agreement using the standard library."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def member(root, name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or '..' in path.parts or '\\' in name
            or ':' in name or path.as_posix() != name):
        raise ValueError(f'Unsafe package member: {name}')
    target = root.joinpath(*path.parts)
    if not target.resolve().is_relative_to(root) or target.is_symlink() or not target.is_file():
        raise ValueError(f'Missing or unsafe package member: {name}')
    return target


def verify(root):
    root = root.resolve()
    manifest = json.loads((root / 'TRANSFER_MANIFEST.json').read_text(encoding='utf-8'))
    if manifest.get('schema') != 'ave.portable.transfer.v1':
        raise ValueError('Unknown transfer manifest')
    rows = manifest['members']
    names = [r['path'] for r in rows]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError('Duplicate or case-colliding package members')
    for row in rows:
        path = member(root, row['path'])
        if path.stat().st_size != row['bytes'] or sha256(path) != row['sha256']:
            raise ValueError(f'Package hash/size mismatch: {row["path"]}')
    wheel = member(root, manifest['toolkit_wheel'])
    source_names = {n for n in names if n.startswith('avevidence/') and n.endswith('.py')}
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise ValueError('Wheel CRC check failed')
        modules = {n for n in archive.namelist() if n.startswith('avevidence/') and n.endswith('.py')}
        if modules != source_names:
            raise ValueError('Wheel module membership differs from bundled source')
        for name in sorted(modules):
            if hashlib.sha256(archive.read(name)).hexdigest() != sha256(root / name):
                raise ValueError(f'Wheel/source disagreement: {name}')
    # The outer archive's generated inventory also binds this manifest. It is
    # present after ZIP extraction, but absent during pre-archive build validation.
    checksum = root / 'SHA256SUMS.txt'
    inventory_checked = False
    if checksum.is_file():
        expected = {}
        prefix = manifest['archive_root'] + '/'
        for line in checksum.read_text(encoding='utf-8').splitlines():
            digest, name = line.split('  ', 1)
            if not name.startswith(prefix) or name in expected:
                raise ValueError('Unexpected checksum inventory member')
            expected[name] = digest
        if set(expected) != {prefix + n for n in names} | {prefix + 'TRANSFER_MANIFEST.json'}:
            raise ValueError('Checksum inventory differs from transfer manifest')
        for name, digest in expected.items():
            if sha256(member(root, name[len(prefix):])) != digest:
                raise ValueError(f'Checksum mismatch: {name}')
        inventory_checked = True
    return {'passed': True, 'version': manifest['version'], 'bundled_files': len(rows),
            'wheel_modules_equal_source': len(source_names),
            'outer_checksum_inventory_checked': inventory_checked,
            'scope': 'Byte integrity and source/wheel correspondence; not perception or source authenticity'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(verify(args.root), indent=2))


if __name__ == '__main__':
    main()
