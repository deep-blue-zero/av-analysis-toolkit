#!/usr/bin/env python3
"""Native host release-build regression. Output must be new and outside the tree."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from avevidence.common import sha256
from avevidence.packaging import verify_archive
from avevidence import __version__


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('output'); p.add_argument('--require-windows',action='store_true')
    a=p.parse_args()
    if a.require_windows and sys.platform != 'win32':
        p.error('This validation explicitly requires native Windows')
    out=Path(a.output).resolve()
    if out.exists() or out == ROOT or ROOT in out.parents:
        p.error('Choose a NEW directory outside the source tree')
    out.mkdir(parents=True)
    command=[sys.executable,str(ROOT/'scripts/build_release.py')]
    for name in ('one.zip','two.zip'):
        subprocess.run(command+[str(out/name)],check=True,stdout=(out/(name+'.log')).open('w',encoding='utf-8'))
    if sha256(out/'one.zip') != sha256(out/'two.zip'):
        raise RuntimeError('Independent source builds differ')
    verify_archive(out/'one.zip')
    with zipfile.ZipFile(out/'one.zip') as z: z.extractall(out/'clean')
    clean=next((out/'clean').iterdir())
    subprocess.run([sys.executable,str(clean/'scripts/self_test.py'),'--report-dir',str(out/'regression')],check=True)
    subprocess.run([sys.executable,str(clean/'scripts/build_release.py'),str(out/'rebuilt.zip')],check=True,
                   stdout=(out/'rebuilt.log').open('w',encoding='utf-8'))
    if sha256(out/'one.zip') != sha256(out/'rebuilt.zip'):
        raise RuntimeError('Clean-extraction rebuild differs')
    (out/'native_release_validation.json').write_text(json.dumps({'passed':True,'version':__version__,
        'platform':sys.platform,'archive_sha256':sha256(out/'one.zip'),
        'two_builds_identical':True,'clean_rebuild_identical':True},indent=2)+'\n',encoding='utf-8')
    return 0

if __name__ == '__main__': raise SystemExit(main())
