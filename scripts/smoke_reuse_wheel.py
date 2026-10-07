"""Install a wheel into a fresh target and exercise reuse without source fallback."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('wheel');p.add_argument('output')
    a=p.parse_args()
    wheel=Path(a.wheel).resolve();out=Path(a.output).resolve()
    out.mkdir(parents=True,exist_ok=False)
    target=out/'installed'
    install=subprocess.run([sys.executable,'-m','pip','install','--no-deps','--target',str(target),str(wheel)],
                           stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    (out/'install.txt').write_text(install.stdout,encoding='utf-8')
    if install.returncode:raise RuntimeError('Wheel installation failed')
    with zipfile.ZipFile(wheel) as archive:
        hashes={n:hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if n.startswith('avevidence/') and n.endswith('.py')}
    for name,digest in hashes.items():
        assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
        assert hashlib.sha256((target/name).read_bytes()).hexdigest()==digest,name
    child="""import json,pathlib,sys,unittest,avevidence
root=pathlib.Path(sys.argv[1]).resolve()
assert pathlib.Path(avevidence.__file__).resolve().is_relative_to(root)
suite=unittest.defaultTestLoader.discover(sys.argv[2],pattern='test_reuse.py')
result=unittest.TextTestRunner(verbosity=2).run(suite)
data={'passed':result.wasSuccessful(),'tests_run':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skipped':len(result.skipped),'imported_from':avevidence.__file__,'version':avevidence.__version__}
pathlib.Path(sys.argv[3]).write_text(json.dumps(data,indent=2),encoding='utf-8')
sys.exit(0 if result.wasSuccessful() else 1)
"""
    env=dict(os.environ,PYTHONPATH=str(target),PYTHONUTF8='1')
    checked=subprocess.run([sys.executable,'-c',child,str(target),str(ROOT/'tests'),str(out/'tests.json')],cwd=out,
                           env=env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace')
    (out/'test_output.txt').write_text(checked.stdout,encoding='utf-8')
    tests=json.loads((out/'tests.json').read_text(encoding='utf-8'))
    report={'passed':checked.returncode==0 and tests['passed'],'wheel_path':str(wheel),
            'wheel_sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
            'runtime_modules_match_source_and_installed_bytes':True,'runtime_sha256':hashes,'tests':tests,
            'scope':'Fresh wheel target; local dependency environment; Windows only; no source package fallback'}
    (out/'validation.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'passed':report['passed'],'wheel_sha256':report['wheel_sha256'],'tests':tests},indent=2))
    return checked.returncode


if __name__=='__main__':raise SystemExit(main())
