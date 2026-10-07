#!/usr/bin/env python3
"""Build wheel/sdist and verify installation without network or observer calls.

Install .[dev] beforehand. Outputs are release artifacts outside the source tree.
Dependencies for the installation smoke are inherited from the build interpreter;
CI installs them through project metadata before this script runs.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import site
import subprocess
import sys
import tarfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def run(arguments, cwd=ROOT):
    environment = os.environ.copy()
    environment.pop('OPENAI_API_KEY', None)
    environment['AVE_ENABLE_PAID_TESTS'] = '0'
    return subprocess.run(arguments, cwd=cwd, env=environment, check=True,
                          capture_output=True, text=True, encoding='utf-8')


def source_identity(allow_dirty=False):
    commit = run(['git', 'rev-parse', 'HEAD']).stdout.strip()
    status = run(['git', 'status', '--porcelain']).stdout
    if status and not allow_dirty:
        raise ValueError('Source tree is dirty; commit it or explicitly use --allow-dirty for a candidate build')
    names = run(['git', 'ls-files', '-z', '--cached', '--others', '--exclude-standard']).stdout.split('\0')
    rows = []
    for name in sorted(set(filter(None, names))):
        path = ROOT / name
        if path.is_symlink() or not path.resolve().is_relative_to(ROOT) or not path.is_file():
            raise ValueError(f'Unsafe or missing source member: {name}')
        rows.append({'path': name, 'sha256': digest(path), 'bytes': path.stat().st_size})
    binding = hashlib.sha256(''.join(row['path'] + '\0' + row['sha256'] + '\n'
                                   for row in rows).encode('utf-8')).hexdigest()
    return {'git_commit': commit, 'dirty': bool(status), 'git_status': status,
            'source_binding_sha256': binding, 'members': rows}


def verify_wheel(wheel):
    expected = {path.relative_to(ROOT).as_posix(): digest(path)
                for path in (ROOT / 'avevidence').rglob('*.py')}
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:
            raise ValueError('Wheel has invalid CRC')
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest()
                  for name in archive.namelist()
                  if name.startswith('avevidence/') and name.endswith('.py')}
        if actual != expected:
            raise ValueError('Wheel modules differ from repository source')
    return {'check': 'wheel/source module equality', 'passed': True, 'module_count': len(expected)}


def isolated_source_snapshot(identity, output):
    """Build only reviewed source bytes, never reusable checkout build scratch."""
    snapshot = output / '_source_snapshot'
    snapshot.mkdir()
    for member in identity['members']:
        source = ROOT / member['path']
        target = snapshot / member['path']
        if source.is_symlink() or not source.resolve().is_relative_to(ROOT) or not source.is_file():
            raise ValueError('Source member changed type or escaped the checkout before snapshot')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        if target.stat().st_size != member['bytes'] or digest(target) != member['sha256']:
            raise ValueError(f'Source member changed before snapshot: {member["path"]}')
    return snapshot


def verify_sdist(sdist):
    with tarfile.open(sdist, 'r:gz') as archive:
        names = set()
        for member in archive.getmembers():
            path = PurePosixPath(member.name)
            if path.is_absolute() or '..' in path.parts or '\\' in member.name or member.issym() or member.islnk():
                raise ValueError(f'Unsafe source-distribution member: {member.name}')
            if len(path.parts) < 2 or not member.isfile():
                continue
            relative = '/'.join(path.parts[1:])
            if relative in names:
                raise ValueError('Duplicate source-distribution member')
            names.add(relative)
            if any(part in {'wheelhouse', '.venv', '.cloud-runtime', 'dependency-sources', 'runs', 'media'}
                   for part in PurePosixPath(relative).parts) or relative.endswith(('.whl', '.mkv', '.mp4', '.flac', '.wav', '.sqlite')):
                raise ValueError(f'Forbidden source-distribution payload: {relative}')
            candidate = ROOT / relative
            if candidate.is_file() and not relative.endswith('PKG-INFO') and '.egg-info/' not in relative:
                stream = archive.extractfile(member)
                if stream is None or hashlib.sha256(stream.read()).hexdigest() != digest(candidate):
                    raise ValueError(f'Source-distribution/source disagreement: {relative}')
        required = {'README.md', 'LICENSE', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml',
                    'MANIFEST.in', 'avtool.py', 'scripts/self_test.py',
                    'scripts/cloud_setup.py', 'scripts/cloud_run.py', 'avevidence/__init__.py'}
        if required - names or not any(name.startswith('tests/test_') for name in names):
            raise ValueError(f'Source distribution misses required first-party material: {sorted(required - names)}')
    return {'check': 'sdist source correspondence and content policy', 'passed': True, 'file_count': len(names)}


def install_check(artifact, output):
    work = output / '_install_checks' / ('wheel' if artifact.suffix == '.whl' else 'sdist')
    environment = work / 'environment'
    venv.EnvBuilder(with_pip=True, system_site_packages=False).create(environment)
    python = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    # Nested venvs do not automatically inherit their parent venv's packages.
    # Explicitly expose already installed dependency directories; first-party
    # import assertions below still require this distribution's fresh install.
    dependency_paths = sorted({str(Path(path).resolve()) for path in site.getsitepackages()
                               if Path(path).is_dir() and not Path(path).resolve().is_relative_to(ROOT)})
    purelib = Path(run([str(python), '-I', '-c', "import sysconfig; print(sysconfig.get_path('purelib'))"], cwd=work).stdout.strip())
    (purelib / 'build-interpreter-dependencies.pth').write_text('\n'.join(dependency_paths) + '\n', encoding='utf-8')
    run([str(python), '-I', '-m', 'pip', 'install', '--no-index', '--no-deps',
         '--no-build-isolation', '--force-reinstall', str(artifact)], cwd=work)
    code = ("import json; from pathlib import Path; import avevidence; from PIL import Image; "
            "assert Image.new('RGB',(2,2)).getpixel((0,0)) == (0,0,0); "
            "root=Path(__import__('sys').prefix).resolve(); "
            "module=Path(avevidence.__file__).resolve(); "
            "assert module.is_relative_to(root), 'package import fell back outside fresh environment'; "
            "print(json.dumps({'module':str(module),'version':avevidence.__version__,'pillow_execution_check':True}))")
    probe = run([str(python), '-I', '-c', code], cwd=work)
    run([str(python), '-I', '-m', 'avevidence', '--help'], cwd=work)
    imported = json.loads(probe.stdout)
    imported['module'] = Path(imported['module']).relative_to(environment).as_posix()
    return {'check': artifact.name + ' fresh-install import and CLI', 'passed': True,
            'network_used': False, 'dependency_mode': 'explicitly exposed build interpreter site-package directories',
            'dependency_directory_count': len(dependency_paths),
            'source_tree_import_used': False, 'import': imported}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='NEW directory outside the repository')
    parser.add_argument('--allow-dirty', action='store_true', help='Candidate only; receipt records commit plus changed-source hashes')
    args = parser.parse_args()
    output = args.output.resolve()
    if output == ROOT or output.is_relative_to(ROOT) or ROOT.is_relative_to(output) or output.exists():
        parser.error('Output must be a NEW directory outside the source tree and its parents')
    try:
        identity = source_identity(args.allow_dirty)
        output.mkdir(parents=True)
        snapshot = isolated_source_snapshot(identity, output)
        run([sys.executable, '-m', 'build', '--no-isolation', '--sdist', '--wheel', '--outdir', str(output)], cwd=snapshot)
        wheels = list(output.glob('*.whl'))
        sdists = list(output.glob('*.tar.gz'))
        if len(wheels) != 1 or len(sdists) != 1:
            raise ValueError('Expected exactly one wheel and one source distribution')
        checks = [verify_wheel(wheels[0]), verify_sdist(sdists[0])]
        checks += [install_check(artifact, output) for artifact in (wheels[0], sdists[0])]
        after = source_identity(allow_dirty=True)
        if after['git_commit'] != identity['git_commit'] or after['source_binding_sha256'] != identity['source_binding_sha256']:
            raise ValueError('Source files or commit changed during distribution validation; use a new output directory and retry')
        artifacts = [{'name': path.name, 'sha256': digest(path), 'bytes': path.stat().st_size}
                     for path in (wheels[0], sdists[0])]
        receipt = {'schema': 'ave.distribution.build.v1', 'passed': True,
                   'created_at': datetime.now(timezone.utc).isoformat(),
                   'source': identity, 'python': sys.version, 'platform': sys.platform,
                   'build_scratch': 'new external source snapshot; ignored checkout build caches excluded',
                   'paid_api_calls': 0, 'api_spend_usd': 0, 'checks': checks,
                   'passed_checks': len(checks), 'failed_checks': 0, 'skipped_checks': 0,
                   'artifacts': artifacts,
                   'scope': 'Distribution bytes/source correspondence and installation; not cross-platform or perceptual validation'}
        (output / 'BUILD_PROVENANCE.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
        (output / 'SHA256SUMS').write_text(''.join(row['sha256'] + '  ' + row['name'] + '\n'
                                                for row in artifacts), encoding='utf-8')
        print(json.dumps({'passed': True, 'checks': len(checks), 'artifacts': artifacts}, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'Build failed: {error}', file=sys.stderr)
        if isinstance(error, subprocess.CalledProcessError):
            print(error.stdout, file=sys.stderr)
            print(error.stderr, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
