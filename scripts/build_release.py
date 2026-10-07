#!/usr/bin/env python3
"""Build a source-bound deterministic ZIP; --candidate is explicitly unvalidated."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
from avevidence import __release_label__, __version__
from avevidence.common import AVError, sha256
from avevidence.packaging import deterministic_zip, verify_archive

TOP = {"README.md", "AGENT_START_HERE.md", "PROMPT_FOR_OTHER_AGENTS.md",
       "HARDENING_SPEC_1.1.0_RC2.md", "AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md", "LICENSE", "pyproject.toml", "avtool.py"}
DIRECTORIES = {
    "avevidence": {".py"}, "tests": {".py"}, "scripts": {".py", ".sh", ".ps1"},
    "docs": {".md"}, "examples": {".json", ".md"}, "LICENSES": {".txt"},
    "reports": {".md", ".json", ".txt", ".log"},
}
REQUIRED_REPORTS = {
    "reports/RELEASE_VALIDATION.md", "reports/IMPLEMENTATION_CONFORMANCE.md",
    "reports/REGRESSION_LINUX.json", "reports/REGRESSION_LINUX.txt",
    "reports/CLEAN_EXTRACTION_REGRESSION.json", "reports/CLEAN_EXTRACTION_REGRESSION.txt",
    "reports/WHEEL_SMOKE.json", "reports/BUILD_REPRODUCIBILITY.json", "reports/REAL_MEDIA_SMOKE.json",
}
STALE_RELEASE_FILES = {
    "TRIAL_PATCH.md", "reports/TRIAL_REGRESSION.log", "reports/TRIAL_REGRESSION_STATUS.json",
    "reports/VALIDATION.md", "reports/test_results.json", "reports/test_output.txt",
    "reports/wheel_smoke.json",
}


def stored_files(root):
    """Stored names, NOT case-insensitive Path.exists lookups for retired names."""
    root = Path(root)
    # Models, virtual environments and media caches are not release inputs.
    # Enumerating them can dominate a local build after corpus processing.
    files = [p for p in root.iterdir() if p.is_file()]
    for name in DIRECTORIES:
        files.extend(p for p in (root/name).rglob('*') if p.is_file() and '__pycache__' not in p.relative_to(root).parts)
    return {p.relative_to(root).as_posix() for p in files}


def select_members(root=None):
    root = Path(root or ROOT)
    actual = stored_files(root)
    stale = actual & STALE_RELEASE_FILES
    if stale:
        raise AVError(f'Stale trial/base release files must be removed: {sorted(stale)}')
    result = []
    for name in sorted(TOP):
        if name not in actual:
            raise AVError(f'Required release member missing or wrong-case: {name}')
        result.append((name, root/name))
    for folder, extensions in DIRECTORIES.items():
        for path in sorted((root/folder).rglob('*')):
            relative = path.relative_to(root)
            if '__pycache__' in relative.parts or path.is_dir():
                continue
            if path.is_symlink():
                raise AVError('Release members must not be symlinks')
            if path.suffix in extensions:
                result.append((relative.as_posix(), path))
    names = [n for n,_ in result]
    if len({n.casefold() for n in names}) != len(names):
        raise AVError('Casefold-colliding release names are not portable')
    for name,path in result:
        if path.is_symlink():
            raise AVError('Release members must not be symlinks')
        if path.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8-sig'), filename=name, feature_version=(3,10))
    required = {'docs/COMMANDS.md', 'docs/METHODS.md', 'docs/REVIEW_RECORDS.md',
        'docs/HOW_AGENTS_ANALYZE_MEDIA_WITH_THIS_TOOLKIT.md', 'docs/ORIGINS_AND_CHANGES.md',
        'examples/EXAMPLE_GIRLS_BAND_CRY_WORKFLOW.md', 'scripts/self_test.py',
        'LICENSES/GBC_TOOLKIT_MIT.txt', 'avevidence/__init__.py'}
    if required-set(names):
        raise AVError(f'Required documentation/code missing: {sorted(required-set(names))}')
    if f'version = "{__version__}"' not in (root/'pyproject.toml').read_text(encoding='utf-8'):
        raise AVError('pyproject version disagrees with runtime')
    if not (root/'README.md').read_text(encoding='utf-8').startswith(f'# AV Evidence Toolkit {__release_label__}\n'):
        raise AVError('README release label disagrees with runtime')
    if __release_label__ not in (root/'AUDIO_ANALYSIS_SPEC_1.2.0_RC1.md').read_text(encoding='utf-8'):
        raise AVError('Active specification release label disagrees with runtime')
    return sorted(result)


def source_binding(files):
    entries = [{'path': n, 'sha256': sha256(p)} for n,p in sorted(files)
               if not n.startswith('reports/') and n != 'SHA256SUMS.txt']
    digest = hashlib.sha256(''.join(x['path']+'\0'+x['sha256']+'\n' for x in entries).encode('utf-8')).hexdigest()
    return {'algorithm': 'SHA-256 over sorted UTF-8 path NUL file-sha256 LF records; reports and generated SHA256SUMS excluded',
            'members': entries, 'member_count': len(entries), 'digest': digest}


def _json_report(root, name):
    try:
        return json.loads((root/name).read_text(encoding='utf-8'))
    except (ValueError,OSError) as exc:
        raise AVError(f'Unreadable release report {name}: {exc}') from exc


def _validate_current_reports(files, root=None):
    root = Path(root or ROOT)
    names = {n for n,_ in files if n.startswith('reports/')}
    if names != REQUIRED_REPORTS:
        raise AVError(f'Release reports do not match current allowlist; missing={sorted(REQUIRED_REPORTS-names)}, extra={sorted(names-REQUIRED_REPORTS)}')
    binding = source_binding(files)
    runtime = {n.removeprefix('avevidence/'): sha256(p) for n,p in files if n.startswith('avevidence/') and n.endswith('.py')}
    tests = {Path(n).name: sha256(p) for n,p in files if n.startswith('tests/test_') and n.endswith('.py')}
    for name in ('REGRESSION_LINUX', 'CLEAN_EXTRACTION_REGRESSION'):
        value = _json_report(root, f'reports/{name}.json')
        if (value.get('passed') is not True or value.get('environment',{}).get('tool_version') != __version__
            or value.get('environment',{}).get('implementation_sha256') != runtime
            or value.get('test_source_sha256') != tests):
            raise AVError(f'{name} is not bound to the selected current runtime/tests')
    for name in ('WHEEL_SMOKE', 'REAL_MEDIA_SMOKE'):
        value = _json_report(root, f'reports/{name}.json')
        if (value.get('passed') is not True or value.get('release',{}).get('version') != __version__
            or value.get('implementation_sha256') != runtime):
            raise AVError(f'{name} is not a passing report bound to this runtime')
    reproducibility = _json_report(root, 'reports/BUILD_REPRODUCIBILITY.json')
    if (reproducibility.get('passed') is not True or reproducibility.get('release_label') != __release_label__
        or reproducibility.get('source_binding') != binding):
        raise AVError('Reproducibility report source binding differs from exact selected release members')
    for name in ('RELEASE_VALIDATION', 'IMPLEMENTATION_CONFORMANCE'):
        if __release_label__ not in (root/f'reports/{name}.md').read_text(encoding='utf-8'):
            raise AVError(f'{name} identifies a different release')


def members(candidate=False, root=None):
    files = select_members(root)
    if not candidate:
        _validate_current_reports(files, root)
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', help='NEW ZIP outside source directory')
    parser.add_argument('--candidate', action='store_true', help='Unvalidated staging archive; NOT a release-validation pass')
    args = parser.parse_args()
    target = Path(args.destination).resolve()
    if target == ROOT or ROOT in target.parents:
        parser.error('Build outside the source directory')
    try:
        files = members(candidate=args.candidate)
        result = deterministic_zip(files,target,prefix=f'av-evidence-toolkit-{__release_label__}/',
                                   expected_hashes={n:sha256(p) for n,p in files})
        verify_archive(target)
        result['release_validation'] = 'CANDIDATE_UNVALIDATED' if args.candidate else 'CURRENT_REPORT_BINDINGS_VERIFIED'
        result['source_binding'] = source_binding(files)
        print(json.dumps(result,indent=2))
        return 0
    except (AVError, OSError, ValueError) as exc:
        print(f'ERROR: {exc}',file=sys.stderr)
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
