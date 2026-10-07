#!/usr/bin/env python3
"""Conservative public-source scan; reports file names, never matched secrets."""
import argparse
import json
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = {'.mkv', '.mp4', '.flac', '.wav', '.whl', '.zip', '.sqlite', '.safetensors', '.bin', '.exe', '.dll'}
PRIVATE = re.compile(r'(?:[A-Z]:[/\\]Users[/\\](?!Public\b)[^/\\\s]+|H:[/\\]Anime (?:Storage|Media Extraction|Media Analysis))', re.I)
KEY = re.compile(r'\b(?:sk-(?:proj-)?[A-Za-z0-9_-]{30,}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{35,})\b')
PAYLOAD = re.compile(r'(?:data:(?:audio|video|image)/[^;]+;base64,|[A-Za-z0-9+/]{4096,}={0,2})')

def scan(root):
    result = subprocess.run(['git', 'ls-files', '-z'], cwd=root, check=True, capture_output=True)
    names = sorted(n for n in result.stdout.decode('utf-8').split('\0') if n)
    findings = []
    for name in names:
        path = root / name
        if path.is_symlink() or path.stat().st_size > 5 * 1024 * 1024 or path.suffix.lower() in FORBIDDEN:
            findings.append({'path': name, 'reason': 'binary, media, symlink or oversized source'})
            continue
        if any(part in {'.venv', 'wheelhouse', 'models', 'runs', 'cache'} for part in path.relative_to(root).parts):
            findings.append({'path': name, 'reason': 'runtime/dependency artifact'})
        try:
            content = path.read_text(encoding='utf-8-sig')
        except UnicodeError:
            findings.append({'path': name, 'reason': 'unexpected non-UTF8 artifact'})
            continue
        # The scanner's own patterns are detection code, not secret values.
        for pattern, reason in ((PRIVATE, 'personal path'), (KEY, 'credential-shaped value'), (PAYLOAD, 'embedded media or large base64')):
            if pattern.search(content):
                findings.append({'path': name, 'reason': reason})
    return {'schema': 'ave.public-source-scan.v1', 'files_scanned': len(names), 'passed': not findings,
            'findings': findings, 'scope': 'Tracked-source heuristic plus manual review; no credential values returned'}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = scan(args.root.resolve())
    text = json.dumps(report, indent=2) + '\n'
    if args.output:
        args.output.write_text(text, encoding='utf-8')
    print(text)
    return 0 if report['passed'] else 1

if __name__ == '__main__':
    raise SystemExit(main())
