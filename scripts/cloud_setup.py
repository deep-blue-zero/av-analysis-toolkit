#!/usr/bin/env python3
"""Install matching bundled wheels into a private target without pip, root or network."""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=ROOT / '.cloud-runtime', help='NEW private runtime directory')
    parser.add_argument('--use-host-dependencies', action='store_true',
                        help='Use compatible preinstalled native packages, including on Python 3.13; no downloads or global changes')
    args = parser.parse_args()
    runtime = args.runtime.resolve()
    if runtime.exists():
        parser.error('Runtime already exists. Use cloud_run.py or choose another NEW --runtime directory')
    if runtime == ROOT or runtime in ROOT.parents:
        parser.error('Runtime cannot replace the toolkit or its parents')
    is_linux = (sys.platform.startswith('linux') and sys.version_info[:2] in {(3, 11), (3, 12)})
    is_windows = (sys.platform == 'win32' and sys.version_info[:2] == (3, 12))
    if not args.use_host_dependencies and not ((is_linux or is_windows) and sys.maxsize > 2**32
            and platform.machine().lower() in {'x86_64', 'amd64'} and sys.implementation.name == 'cpython'):
        parser.error('Bundled native wheels target Linux x64 CPython 3.11/3.12 or Windows x64 CPython 3.12. Compatible preinstalled dependencies can be tested with --use-host-dependencies')
    if sys.version_info < (3,10):
        parser.error('Toolkit requires Python 3.10 or newer')
    subprocess.run([sys.executable, '-X', 'utf8', str(ROOT / 'scripts/verify_transfer.py')], check=True)
    abi = f'cp{sys.version_info.major}{sys.version_info.minor}'
    dependency_root = ROOT / 'wheelhouse' / 'linux' / abi if is_linux else ROOT / 'wheelhouse'
    wheels = [] if args.use_host_dependencies else sorted(w for w in dependency_root.glob('*.whl') if not w.name.startswith('av_evidence_toolkit-'))
    wheels += sorted((ROOT / 'wheelhouse').glob('av_evidence_toolkit-*.whl'))
    if len(wheels) != (1 if args.use_host_dependencies else 5):
        parser.error('Expected four matching dependency wheels and one toolkit wheel. Use the complete cloud ZIP')
    glibc, glibc_version = platform.libc_ver()
    if not args.use_host_dependencies and is_linux and (glibc != 'glibc' or tuple(map(int, glibc_version.split('.')[:2])) < (2, 17)):
        parser.error('Bundled wheels require glibc 2.17 or newer; musl/Alpine needs a different installation')
    # Validate all member names before writing. Pure/platlib layouts are installed
    # into the same private import directory. Scripts are not needed: cloud_run
    # calls the toolkit module directly. No .pth file is executed by this installer.
    entries = []
    seen = set()
    skipped = []
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            if archive.testzip() is not None:
                raise ValueError(f'Invalid wheel CRC: {wheel.name}')
            for info in archive.infolist():
                name = info.filename
                path = PurePosixPath(name)
                if path.is_absolute() or '..' in path.parts or '\\' in name or ':' in name:
                    raise ValueError(f'Unsafe wheel path: {name}')
                if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                    raise ValueError('Wheel symlinks are not accepted')
                if info.is_dir():
                    continue
                parts = path.parts
                if parts[0].endswith('.data'):
                    if len(parts) > 2 and parts[1] in {'purelib', 'platlib'}:
                        parts = parts[2:]
                    else:
                        skipped.append({'wheel': wheel.name, 'member': name, 'reason': 'Non-import installation data; CLI uses module entry point'})
                        continue
                target = '/'.join(parts)
                if target in seen:
                    raise ValueError(f'Colliding wheel installation path: {target}')
                seen.add(target)
                entries.append((wheel, name, target))
    runtime.mkdir(parents=True, exist_ok=False)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as archive:
            for owner, name, target_name in entries:
                if owner != wheel:
                    continue
                target = runtime / target_name
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(name) as source, target.open('xb') as destination:
                    shutil.copyfileobj(source, destination)
    probe = subprocess.run([sys.executable, '-I', '-X', 'utf8', str(ROOT / 'scripts/cloud_run.py'),
                            '--runtime', str(runtime), '--where'], capture_output=True,
                           text=True, encoding='utf-8')
    if probe.returncode:
        failure = {'schema': 'ave.cloud.setup-failure.v1', 'passed': False,
                   'phase': 'native_dependency_execution', 'python': sys.version,
                   'runtime': str(runtime), 'returncode': probe.returncode,
                   'stdout': probe.stdout, 'stderr': probe.stderr,
                   'dependency_mode': 'compatible_preinstalled_host' if args.use_host_dependencies else 'matching_bundled_native_wheels'}
        (runtime / 'CLOUD_SETUP_FAILURE.json').write_text(json.dumps(failure, indent=2) + '\n', encoding='utf-8')
        raise SystemExit('Native dependency execution failed:\n' + probe.stderr.strip()
                         + '\nIncomplete private installation retained at ' + str(runtime)
                         + '\nUse installed compatible dependencies or a supported execution image, then choose a NEW runtime.')
    receipt = {'schema': 'ave.cloud.setup.v1', 'passed': True, 'installer': 'stdlib wheel extraction',
               'pip_required': False, 'network_used': False, 'root_required': False,
               'python': sys.version, 'platform': platform.platform(), 'abi': abi, 'target_os': sys.platform,
               'dependency_mode': 'compatible_preinstalled_host' if args.use_host_dependencies else 'matching_bundled_native_wheels',
               'libc': [glibc, glibc_version], 'runtime': str(runtime),
               'wheels': [{'name': w.name, 'sha256': hashlib.sha256(w.read_bytes()).hexdigest()} for w in wheels],
               'installed_files': len(entries), 'skipped_nonimport_members': skipped,
               'import_check': json.loads(probe.stdout),
               'ffmpeg': shutil.which('ffmpeg'), 'ffprobe': shutil.which('ffprobe')}
    (runtime / 'CLOUD_SETUP_RECEIPT.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({k: receipt[k] for k in ['passed', 'installer', 'network_used', 'root_required', 'abi', 'runtime', 'installed_files', 'ffmpeg', 'ffprobe']}, indent=2))
    if not receipt['ffmpeg'] or not receipt['ffprobe']:
        print('Python installation succeeded. Media commands also require FFmpeg and ffprobe on PATH; see CLOUD_START_HERE.md.')


if __name__ == '__main__':
    main()
