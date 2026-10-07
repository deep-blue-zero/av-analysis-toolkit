"""Run a config's local performance jobs sequentially and record cold/warm wall times."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from avevidence.common import read_json, write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config")
    parser.add_argument("output", help="New benchmark directory; media files are not copied")
    args = parser.parse_args()
    config = read_json(args.config)
    folder = Path(args.output).resolve()
    folder.mkdir(parents=True, exist_ok=False)
    env = dict(os.environ, PYTHONUTF8="1", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    results = []
    for job in config["jobs"]:
        for phase in ("cold", "warm"):
            name = job["name"] + "-" + phase
            command = [sys.executable, "-m", "avevidence", "performance", job["input"], str(folder/name),
                       "--cache-dir", str(folder/"cache"), "--model-dir", config["model_dir"], *job["arguments"]]
            print(f"Running {name}", flush=True)
            before = time.perf_counter()
            result = subprocess.run(command, env=env, capture_output=True, encoding="utf-8", cwd=ROOT)
            elapsed = time.perf_counter() - before
            (folder/(name+".log")).write_text(result.stdout + "\n" + result.stderr, encoding="utf-8")
            record = {"name": name, "wall_seconds": elapsed, "exit_code": result.returncode,
                      "command": command, "source": job["input"]}
            if result.returncode:
                write_json(folder/(name+"-failure.json"), record)
                print(result.stderr, file=sys.stderr)
                return result.returncode
            report = read_json(folder/name/"performance.json")
            record.update(duration_seconds=report["source"]["duration_seconds"],
                speed_ratio=report["source"]["duration_seconds"]/elapsed,
                stages_seconds=report["timings_s"], cache=report["cache"], alignment_counts=report["alignment_counts"],
                performance_key=report["performance_key"])
            results.append(record)
            write_json(folder/(name+"-timing.json"), record)
            print(json.dumps({k:record[k] for k in ("name", "wall_seconds", "speed_ratio", "alignment_counts")}), flush=True)
    write_json(folder/"benchmark.json", {"schema":"ave.performance-benchmark.v1", "results":results,
        "scope":"Single-machine sequential wall-clock benchmark, including fresh Python startup and final source verification; model download and text preparation excluded. Cold means an empty toolkit cache, not empty operating-system disk caches.",
        "manual_word_boundary_validation":"NOT_PERFORMED"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
