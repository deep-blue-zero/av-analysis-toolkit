#!/usr/bin/env python3
"""Run regression tests using generated media only. Never delete a user-chosen workdir."""
import argparse
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", help="Optional NEW directory for machine-readable test results")
    args = parser.parse_args()
    report = None
    if args.report_dir:
        report = Path(args.report_dir).resolve()
        if report.exists():
            parser.error("Report directory already exists; use a new directory")
        report.mkdir(parents=True)
    started = datetime.now(timezone.utc).isoformat()
    from avevidence.common import environment, sha256
    before = environment()
    test_hashes = {path.name: sha256(path) for path in sorted((ROOT / "tests").glob("test_*.py"))}
    class Tee(io.StringIO):
        def write(self, message):
            sys.stdout.write(message)
            sys.stdout.flush()
            return super().write(message)
    log = Tee()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    result = unittest.TextTestRunner(stream=log, verbosity=2).run(suite)
    output = log.getvalue()
    after = environment()
    stable = before["implementation_sha256"] == after["implementation_sha256"] and test_hashes == {
        path.name: sha256(path) for path in sorted((ROOT / "tests").glob("test_*.py"))}
    successful = result.wasSuccessful() and stable
    if not stable:
        print("ERROR: Source/test files changed during validation", file=sys.stderr)
    if report:
        data = {"started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
                "tests_run": result.testsRun, "failures": len(result.failures), "errors": len(result.errors),
                "skipped": len(result.skipped), "passed": successful, "implementation_unchanged_during_tests": stable,
                "environment": before, "test_source_sha256": test_hashes,
                "scope": "Generated synthetic fixtures and record validation only; no real-media or perceptual review."}
        (report / "test_results.json").write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        (report / "test_output.txt").write_text(output, encoding="utf-8")
    return 0 if successful else 1


if __name__ == "__main__":
    raise SystemExit(main())
