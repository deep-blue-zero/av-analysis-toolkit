#!/usr/bin/env python3
"""Record source-bound regression results without treating them as listening tests."""
import argparse
import json
from pathlib import Path
import sys
import time
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/"tests"))
from avevidence.common import environment,sha256


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("output",type=Path)
    a=parser.parse_args();target=a.output.resolve();target.mkdir(parents=True,exist_ok=False)
    started=time.perf_counter()
    code_before={p.relative_to(ROOT/"avevidence").as_posix():sha256(p) for p in sorted((ROOT/"avevidence").rglob("*.py"))}
    suite=unittest.defaultTestLoader.discover(str(ROOT/"tests"))
    with (target/"regression.txt").open("x",encoding="utf-8") as log:
        result=unittest.TextTestRunner(stream=log,verbosity=2).run(suite)
    env=environment()
    code_unchanged=env["implementation_sha256"]==code_before
    receipt={"schema":"ave.claim-directed-regression.v1","passed":result.wasSuccessful() and code_unchanged,"tests":result.testsRun,
        "failures":[str(t) for t,_ in result.failures],"errors":[str(t) for t,_ in result.errors],
        "skipped":[{"test":str(t),"reason":why} for t,why in result.skipped],"wall_seconds":time.perf_counter()-started,
        "environment":env,"runtime_unchanged_during_tests":code_unchanged,"test_source_sha256":{p.name:sha256(p) for p in sorted((ROOT/"tests").glob("test_*.py"))},
        "scope":"Decoding, source-clock, signal, reuse, evidence-routing and contract regressions. No real auditory backend or interpretation benchmark is certified."}
    (target/"regression.json").write_text(json.dumps(receipt,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({k:receipt[k] for k in ("passed","tests","failures","errors","skipped","wall_seconds")},indent=2))
    return 0 if result.wasSuccessful() else 1

if __name__=="__main__":raise SystemExit(main())
