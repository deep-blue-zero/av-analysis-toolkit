#!/usr/bin/env python3
"""Explicitly authorized synthetic-only hosted compatibility test, capped at $0.25.

Never called by the normal regression suite or GitHub Actions. No retry, model
fallback, episode input or credential persistence is supported.
"""
import argparse
import json
from pathlib import Path
import sys
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--allow-remote-media", choices=["openai"])
    parser.add_argument("--credential-env", default="OPENAI_API_KEY")
    parser.add_argument("--budget-usd", type=float, default=.25)
    parser.add_argument("--request-budget-usd", type=float, default=.025)
    args = parser.parse_args()
    from avevidence.common import AVError, read_json, sha256, write_json
    from avevidence.audio_witness import audio_witness, provider_witness
    from avevidence.auditory_observer import make_probe
    from avevidence.observer_execution import estimate_observation, observe_request, probe_backend
    from avevidence.providers.openai_audio import OpenAIAudioObserver
    from avevidence.providers.openai_common import authorize
    import math
    if (not math.isfinite(args.budget_usd) or not 0 < args.budget_usd <= .25
        or not math.isfinite(args.request_budget_usd) or not 0 < args.request_budget_usd <= args.budget_usd):
        parser.error("Initial synthetic integration ceilings must be positive and at most $0.25 cumulative")
    if args.execute:
        authorize(args.allow_remote_media, args.credential_env)
    root = Path(args.output).resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root/"generated-tone.wav"
    import numpy as np
    rate = 16000
    samples = (.15*np.sin(2*np.pi*330*np.arange(rate)/rate)*32767).astype("<i2")
    with wave.open(str(source), "wb") as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(rate); stream.writeframes(samples.tobytes())
    audio_witness(source, root/"forensic", start=0, end=1.)
    provider_witness(root/"forensic", root/"submission")
    make_probe(root/"fixtures", trials=24, protocol="v2")
    backend = OpenAIAudioObserver(credential_env=args.credential_env, allow_remote_media=args.allow_remote_media)
    request = root/"request.json"
    write_json(request, {"request_id": "synthetic-submission-compatibility", "source_sha256": sha256(source),
        "stage": 1, "task_profile": "SOUNDSCAPE", "observation_lane": "EXPERIMENTAL",
        "media_category": "GENERATED_SYNTHETIC_TONE", "question": "Describe only the audible sound and any changes within this bounded clip."})
    execution = dict(budget_usd=args.budget_usd, request_budget_usd=args.request_budget_usd,
                     queue_ledger=root/"cumulative-usage.jsonl", queue_budget_usd=args.budget_usd)
    probe_backend(root/"fixtures", root/"probe", backend=backend, dry_run=not args.execute, **execution)
    if not args.execute:
        report = estimate_observation(request, witness_run=root/"submission", backend=backend)
        write_json(root/"integration-summary.json", {"executed": False, "synthetic_only": True,
            "estimate": report, "api_spend_usd": 0, "cumulative_cap_usd": args.budget_usd})
        print("Prepared generated witnesses and estimates; no submission or API spend.")
        return 0
    probe_cost = read_json(root/"probe"/"cost-summary.json")
    if probe_cost["billing_unresolved"]:
        write_json(root/"integration-summary.json", {"executed": True, "synthetic_only": True,
            "status": "PROBE_USAGE_UNRESOLVED_NO_FURTHER_SUBMISSION", "probe_cost": probe_cost,
            "cumulative_cap_usd": args.budget_usd, "natural_audio_tested": False})
        print("Provider usage unresolved; retained the receipt and stopped without retry.")
        return 2
    # Influence failure does not prevent this explicitly experimental, authorized
    # format/collection test. Both outputs retain UNQUALIFIED natural tasks.
    observe_request(request, root/"observation", witness_run=root/"submission", backend=backend, **execution)
    obs = read_json(root/"observation"/"observation.json")
    observation_cost = read_json(root/"observation"/"cost-summary.json")
    costs = [probe_cost, observation_cost]
    known = not any(c["billing_unresolved"] for c in costs)
    total = round(sum(c["actual_cost_usd"] for c in costs), 9) if known else None
    summary = {"executed": True, "synthetic_only": True, "natural_audio_tested": False,
        "real_hosted_route": backend.identity["execution_mode"] == "HOSTED_LIVE",
        "probe_status": read_json(root/"probe"/"capability.json")["status"],
        "observation_validation_status": obs["validation_status"],
        "admission_dimensions": obs["admission_dimensions"], "natural_task_qualification": "UNQUALIFIED",
        "api_spend_usd": total, "cumulative_cap_usd": args.budget_usd, "usage_receipts": costs,
        "authority": "Format/transport and bounded synthetic-input testing only; no acting/music competence"}
    write_json(root/"integration-summary.json", summary)
    print(json.dumps({key: summary[key] for key in ("real_hosted_route", "probe_status", "observation_validation_status", "natural_task_qualification", "api_spend_usd", "cumulative_cap_usd")}))
    return 0 if obs["validation_status"] == "VALID" and known and total <= args.budget_usd else 2


if __name__ == "__main__":
    raise SystemExit(main())
