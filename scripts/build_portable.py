#!/usr/bin/env python3
"""Build an optional offline ZIP from first-party distributions and external wheels.

No downloads, installation, media access, observers, or paid API calls occur.
The existing standard-library private-runtime installer remains unchanged.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from email.parser import Parser
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
DEPENDENCIES = {'numpy', 'pillow', 'praat-parselmouth', 'scipy'}
TARGETS = {'linux-cp311': ('cp311', 'linux'), 'linux-cp312': ('cp312', 'linux'),
           'windows-cp312': ('cp312', 'windows')}


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def row(path, name):
    return {'path': name, 'bytes': path.stat().st_size, 'sha256': digest(path)}


def metadata(wheel):
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise ValueError(f'Wheel CRC failed: {wheel.name}')
        seen = set()
        for entry in archive.infolist():
            path = PurePosixPath(entry.filename)
            if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename or ((entry.external_attr >> 16) & 0o170000) == 0o120000:
                raise ValueError('Unsafe wheel member or symlink')
            if entry.filename.casefold() in seen:
                raise ValueError('Duplicate or case-colliding wheel member')
            seen.add(entry.filename.casefold())
        entries = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        wheels = [name for name in archive.namelist() if name.endswith('.dist-info/WHEEL')]
        if len(entries) != 1 or len(wheels) != 1:
            raise ValueError('Wheel metadata is ambiguous')
        info = Parser().parsestr(archive.read(entries[0]).decode('utf-8'))
        wheel_info = Parser().parsestr(archive.read(wheels[0]).decode('utf-8'))
        if not info['Name'] or not info['Version']:
            raise ValueError('Wheel name/version metadata is missing')
        return {'name': info['Name'].lower().replace('_', '-'), 'version': info['Version'],
                'tags': wheel_info.get_all('Tag', [])}


def locked_dependencies(source, abi, operating_system):
    profile = 'cloud' if operating_system == 'linux' else 'windows-offline'
    path = source / f'requirements-{profile}-{abi}.txt'
    expected = {}
    for line in path.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.count('==') != 1:
            raise ValueError('Offline dependency lock must contain exact versions')
        name, version = line.split('==')
        expected[name.lower().replace('_', '-')] = version
    if set(expected) != DEPENDENCIES:
        raise ValueError('Offline installer requires the four-package validated native profile')
    return expected


def copy_source(sdist, destination):
    seen = set()
    root_name = None
    with tarfile.open(sdist, 'r:gz') as archive:
        for item in archive.getmembers():
            path = PurePosixPath(item.name)
            if path.is_absolute() or '..' in path.parts or '\\' in item.name or ':' in item.name:
                raise ValueError('Unsafe source-distribution member')
            if item.issym() or item.islnk() or not (item.isdir() or item.isfile()):
                raise ValueError('Source-distribution links/devices are not supported')
            if not path.parts:
                continue
            if root_name is None:
                root_name = path.parts[0]
            if path.parts[0] != root_name:
                raise ValueError('Source distribution must have a single archive root')
            if len(path.parts) < 2 or not item.isfile():
                continue
            name = '/'.join(path.parts[1:])
            if name.casefold() in seen:
                raise ValueError('Case-colliding source-distribution members')
            seen.add(name.casefold())
            if any(part in {'wheelhouse', 'dependency-sources', '.venv', '.cloud-runtime', 'media', 'runs', 'models'}
                   for part in PurePosixPath(name).parts) or name.endswith(('.mkv', '.mp4', '.flac', '.wav', '.whl', '.sqlite')):
                raise ValueError('Unexpected private/runtime payload in source distribution')
            output = destination.joinpath(*PurePosixPath(name).parts)
            output.parent.mkdir(parents=True, exist_ok=True)
            stream = archive.extractfile(item)
            if stream is None:
                raise ValueError('Unreadable source-distribution member')
            with stream, output.open('xb') as target:
                shutil.copyfileobj(stream, target)


def select_wheels(wheelhouse, abi, operating_system, expected):
    directory = wheelhouse / 'linux' / abi if operating_system == 'linux' else wheelhouse
    found = {}
    for wheel in sorted(directory.glob('*.whl')):
        if wheel.is_symlink():
            raise ValueError('Wheelhouse symlinks are not supported')
        info = metadata(wheel)
        name = info['name']
        if name not in DEPENDENCIES:
            continue
        if name in found or info['version'] != expected[name]:
            raise ValueError(f'Duplicate or unlocked dependency wheel: {name}')
        platform_ok = (lambda tag: tag in {abi + '-' + abi + '-manylinux_2_17_x86_64',
                       abi + '-' + abi + '-manylinux2014_x86_64'}) if operating_system == 'linux' else (
                      lambda tag: tag == abi + '-' + abi + '-win_amd64')
        if not any(platform_ok(tag) for tag in info['tags']):
            raise ValueError(f'Wheel does not match selected target: {wheel.name}')
        found[name] = (wheel, info)
    if set(found) != DEPENDENCIES:
        raise ValueError(f'Incomplete external wheelhouse: missing {sorted(DEPENDENCIES - set(found))}')
    return found


def zip_tree(source, output, prefix):
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(source.rglob('*')):
            if not path.is_file():
                continue
            name = prefix + '/' + path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    with zipfile.ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Portable ZIP failed CRC verification')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdist', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--build-receipt', type=Path, help='Defaults to BUILD_PROVENANCE.json alongside the sdist; hashes must match both inputs')
    parser.add_argument('--wheelhouse', type=Path, required=True, help='External existing wheelhouse; Linux subdirectories linux/cp311 or linux/cp312')
    parser.add_argument('--target', choices=sorted(TARGETS), required=True)
    parser.add_argument('--parselmouth-source', type=Path, required=True, help='Corresponding unmodified GPL dependency source archive')
    parser.add_argument('--output', type=Path, required=True, help='NEW ZIP outside the repository')
    args = parser.parse_args()
    output = args.output.resolve()
    receipt_path = output.with_suffix('.build.json')
    if output.is_relative_to(ROOT) or output.exists() or receipt_path.exists():
        parser.error('Output ZIP and adjacent receipt must be NEW and outside the repository')
    try:
        inputs = [args.sdist.resolve(), args.wheel.resolve(), args.parselmouth_source.resolve()]
        if any(path.is_symlink() or not path.is_file() for path in inputs):
            raise ValueError('Inputs must be existing regular files')
        toolkit = metadata(inputs[1])
        if toolkit['name'] != 'av-evidence-toolkit' or 'py3-none-any' not in toolkit['tags']:
            raise ValueError('Expected a first-party platform-independent toolkit wheel')
        build_receipt = args.build_receipt.resolve() if args.build_receipt else inputs[0].parent / 'BUILD_PROVENANCE.json'
        provenance = json.loads(build_receipt.read_text(encoding='utf-8'))
        expected_artifacts = {item['name']: item['sha256'] for item in provenance.get('artifacts', [])}
        if provenance.get('schema') != 'ave.distribution.build.v1' or provenance.get('passed') is not True or not provenance.get('source', {}).get('git_commit'):
            raise ValueError('A passing commit-bound distribution build receipt is required')
        if any(expected_artifacts.get(path.name) != digest(path) for path in inputs[:2]):
            raise ValueError('Distribution build receipt does not bind supplied wheel/sdist bytes')
        output.parent.mkdir(parents=True, exist_ok=True)
        abi, operating_system = TARGETS[args.target]
        prefix = 'av-evidence-toolkit-' + toolkit['version']
        with tempfile.TemporaryDirectory(prefix='ave-portable-', dir=output.parent) as temporary:
            source = Path(temporary) / prefix
            source.mkdir()
            copy_source(inputs[0], source)
            shutil.copyfile(build_receipt, source / 'BUILD_PROVENANCE.json')
            expected = locked_dependencies(source, abi, operating_system)
            dependencies = select_wheels(args.wheelhouse.resolve(), abi, operating_system, expected)
            required_source = 'praat_parselmouth-' + expected['praat-parselmouth'] + '.tar.gz'
            if inputs[2].name != required_source:
                raise ValueError('Parselmouth source archive must match the pinned dependency version')
            # A source archive is retained intact; this builder does not make a
            # legal determination or claim that the archive was built into a wheel.
            with tarfile.open(inputs[2], 'r:gz') as archive:
                if not archive.getmembers():
                    raise ValueError('Empty Parselmouth source archive')
            wheel_root = source / 'wheelhouse'
            wheel_root.mkdir()
            toolkit_wheel = wheel_root / inputs[1].name
            shutil.copyfile(inputs[1], toolkit_wheel)
            dependency_rows = []
            for name, (wheel, info) in sorted(dependencies.items()):
                relative = ('wheelhouse/linux/' + abi + '/' if operating_system == 'linux' else 'wheelhouse/') + wheel.name
                destination = source / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(wheel, destination)
                dependency_rows.append({**info, **row(destination, relative)})
            dependency_source = source / 'dependency-sources' / inputs[2].name
            dependency_source.parent.mkdir()
            shutil.copyfile(inputs[2], dependency_source)
            profile = {'schema': 'ave.portable.profile.v1', 'version': toolkit['version'],
                       'edition': args.target, 'native_bundled_target': args.target,
                       'native_wheels': dependency_rows,
                       'ffmpeg_bundled': False, 'model_weights_bundled': False,
                       'episode_media_bundled': False, 'real_observer_configured': False,
                       'native_target_execution_checked_by_builder': False,
                       'host_dependency_mode_available': True, 'VM_required': False}
            (source / 'DELIVERY_PROFILE.json').write_text(json.dumps(profile, indent=2) + '\n', encoding='utf-8')
            (source / 'DEPENDENCIES.json').write_text(json.dumps({'packages': dependency_rows,
                 'parselmouth_source': row(dependency_source, 'dependency-sources/' + inputs[2].name)}, indent=2) + '\n', encoding='utf-8')
            members = [row(path, path.relative_to(source).as_posix())
                       for path in sorted(source.rglob('*')) if path.is_file()]
            manifest = {'schema': 'ave.portable.transfer.v1', 'version': toolkit['version'],
                        'archive_root': prefix, 'toolkit_wheel': 'wheelhouse/' + inputs[1].name,
                        'members': members}
            (source / 'TRANSFER_MANIFEST.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
            checksums = members + [row(source / 'TRANSFER_MANIFEST.json', 'TRANSFER_MANIFEST.json')]
            (source / 'SHA256SUMS.txt').write_text(''.join(item['sha256'] + '  ' + prefix + '/' + item['path'] + '\n'
                                                       for item in checksums), encoding='utf-8')
            validation = subprocess.run([sys.executable, str(source / 'scripts/verify_transfer.py'), '--root', str(source)],
                                        check=True, capture_output=True, text=True, encoding='utf-8')
            zip_tree(source, output, prefix)
            receipt = {'schema': 'ave.portable.build.v1', 'passed': True,
                       'created_at': datetime.now(timezone.utc).isoformat(), 'target': args.target,
                       'first_party_inputs': [row(inputs[0], inputs[0].name), row(inputs[1], inputs[1].name)],
                       'source': provenance['source'],
                       'parselmouth_source': row(inputs[2], inputs[2].name),
                       'dependency_wheels': dependency_rows, 'transfer_validation': json.loads(validation.stdout),
                       'artifact': row(output, output.name), 'network_used': False,
                       'paid_api_calls': 0, 'native_target_execution_checked': False,
                       'scope': 'Artifact construction, byte/source consistency and profile tags; target install must be tested separately'}
            receipt_path.write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'passed': True, 'target': args.target, 'artifact': receipt['artifact']}, indent=2))
        return 0
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile, subprocess.CalledProcessError) as error:
        print(f'Portable build failed: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stdout, file=sys.stderr)
            print(error.stderr, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
