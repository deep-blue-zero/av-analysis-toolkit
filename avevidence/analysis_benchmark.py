"""Supplied-context isolation and modality ablation, separate from extraction tests."""
from pathlib import Path
import hashlib

from .common import AVError, file_record, finish_run, output_transaction, read_json, sha256, write_json

FALLBACK_GENERIC_METHOD = """Analyze only the supplied episode or clip. Establish what happens before interpreting why.
Separate text, visible points, verified motion, mixed-signal measurements and attributed listening.
Follow decisions, relationships, institutions, staging, editing and musical dramatic function.
Treat desire, ability, performance occasions and lasting character changes as different claims.
Test plausible alternatives and local counterevidence. Do not infer future membership, cure or resolution.
For songs distinguish lyrics, singer allocation, visual focus, audience, arrangement and before/after state.
Ask consequential bounded questions. Never infer emotion from pitch or soundtrack identity from a camera shot.
Retain OPEN when evidence is insufficient. Series familiarity is not evidence from this source.
"""


def select_analytical_method(method=None):
    """Freeze the explicit UTF-8 file bytes, or select the built-in fallback.

    Semantic isolation is deliberately not an argument to method selection.
    """
    if method is None:
        payload = FALLBACK_GENERIC_METHOD.encode("utf-8")
        sources = []
        record = {"method_source": "builtin:fallback_generic_method",
                  "method_filename": None, "method_selection": "fallback"}
    else:
        path = Path(method).resolve()
        try:
            if not path.is_file():
                raise AVError(f"Explicit analytical method is not a readable file: {path}")
            payload = path.read_bytes()
            payload.decode("utf-8-sig")  # Validate text without changing BOMs or line endings.
            source = file_record(path, kind="analytical-method")
        except (OSError, UnicodeError) as exc:
            raise AVError(f"Cannot read explicit analytical method as UTF-8: {path}: {exc}") from exc
        if hashlib.sha256(payload).hexdigest() != source["sha256"]:
            raise AVError(f"Explicit analytical method changed while being read: {path}")
        sources = [source]
        record = {"method_source": str(path), "method_filename": Path(method).name,
                  "method_selection": "explicit"}
    record["method_sha256"] = hashlib.sha256(payload).hexdigest()
    return payload, record, sources


def stage_analytical_method(stage, payload, record, *, relative_path, episode_isolated):
    """Preserve bytes and record independently selected semantic boundaries."""
    destination = Path(stage)/relative_path
    with destination.open("xb") as stream:
        stream.write(payload)
    copied_hash = sha256(destination)
    if copied_hash != record["method_sha256"]:
        raise AVError("Staged analytical method does not match the selected method bytes")
    receipt = {"schema": "ave.method-selection.v1", **record,
        "method_source_sha256": record["method_sha256"], "method_copied_sha256": copied_hash,
        "method_copy_path": relative_path, "method_copy_matches_source": True,
        "episode_isolated": bool(episode_isolated),
        "semantic_evidence_boundary": "supplied_source_only" if episode_isolated else "owner_defined"}
    write_json(Path(stage)/"method-receipt.json", receipt)
    return receipt


def blind_export(config, output, *, method=None):
    payload, selected, method_sources = select_analytical_method(method)
    config = Path(config).resolve()
    data = read_json(config)
    utterances = data.get("utterances")
    if not isinstance(utterances, list):
        raise AVError("Blind export needs explicit episode-local utterances")
    excluded = set(data.get("exclude_cue_ids", []))
    names = sorted(set(str(r.get("speaker")) for r in utterances if r.get("speaker")))
    mapping = {name: "Person-%02d" % (i+1) for i,name in enumerate(names)}
    replacements = dict(data.get("pseudonyms", {})); replacements.update(mapping)
    neutral = []
    for row in utterances:
        if row.get("cue_id", row.get("cue_index")) in excluded:
            continue
        text = row.get("text", row.get("text_plain", ""))
        for original, replacement in sorted(replacements.items(), key=lambda p: -len(p[0])):
            text = text.replace(original, replacement)
        neutral.append({"cue_id": row.get("cue_id", row.get("cue_index")), "start_s": row.get("start_s", row.get("start_seconds")),
                        "end_s": row.get("end_s", row.get("end_seconds")), "speaker": mapping.get(row.get("speaker")), "text": text})
    with output_transaction(output, [config] + [s["path"] for s in method_sources]) as stage:
        analyst = stage/"analyst-context"; private = stage/"adjudication"
        analyst.mkdir(); private.mkdir()
        write_json(analyst/"source-001.json", {"neutral_source_id": "source-001", "utterances": neutral,
            "scope": "Episode-isolated supplied context; no claim of absence from model pretraining"})
        receipt = stage_analytical_method(stage, payload, selected,
            relative_path="analyst-context/method.txt", episode_isolated=True)
        (analyst/"START_HERE.txt").write_text(
            "Use method.txt as the governing analytical method; its selection is independent of source isolation.\n"
            "Semantic evidence is limited to the supplied episode/clip. Do not use later episodes or external-series outcomes.\n"
            "Method examples and hypotheses are analytical guidance, not observations of this source.\n",
            encoding="utf-8")
        write_json(private/"crosswalk.json", {"speaker_crosswalk": mapping, "pseudonyms": replacements,
            "original_input": str(config), "excluded_cue_ids": list(excluded), "not_for_analyst": True})
        return finish_run(stage, "blind-export", [file_record(config)] + method_sources, metadata={**receipt,
            "result_file": "analyst-context/source-001.json", "method_receipt_file": "method-receipt.json",
            "scope": "Only analyst-context may be supplied to a blind analyst; the full evidence run contains a private crosswalk"})


def modality_ablation(config, output):
    from .evidence_claims import validate_claims
    config = Path(config).resolve()
    data = read_json(config)
    claims, witnesses, deps = validate_claims(data, config.parent)
    passes = {"A_text": {"textual"}, "B_text_visual": {"textual", "observed"},
              "C_plus_signal": {"textual", "observed", "measured"},
              "D_plus_auditory": {"textual", "observed", "measured", "auditory_observation"}}
    with output_transaction(output, [config]) as stage:
        for name, layers in passes.items():
            folder = stage/name; folder.mkdir()
            included = [e for e in witnesses.values() if e["layer"] in layers]
            IDs = {e["id"] for e in included}
            # Do not pass prior verdicts, thesis statements or accumulated prose.
            questions = [{"id": c["id"], "question": c.get("question", c["statement"]),
                          "locators": c["locators"], "required_modalities": c["required_modalities"],
                          "current_evidence_ids": [i for i in c.get("evidence_ids", []) if i in IDs]} for c in claims]
            write_json(folder/"input.json", {"pass": name, "questions": questions, "evidence": included,
                "prior_passes_supplied": False, "needs_independent_reasoning_run": True,
                "warning": "Question wording remains supplied context; use neutral questions for a blind benchmark"})
        write_json(stage/"evaluation-template.json", {"schema": "ave.ablation-results.v1", "runs": [], "human_adjudications": [],
            "metrics_required": ["new_supported_claims", "revised_claims", "false_claims", "abstentions", "speaker_errors", "timing_errors", "wall_seconds", "cost_usd"],
            "evaluation_status": "NOT_RUN", "no_proxy": "Essay length and agreement with a canonical essay are not quality scores"})
        return finish_run(stage, "modality-ablation", [file_record(config)] + deps, metadata={"result_file": "evaluation-template.json"})


def evaluate_ablation(config, output):
    data = read_json(config)
    runs = data.get("runs", [])
    if not isinstance(runs, list) or len(runs) < 2:
        raise AVError("Need at least two independently recorded reasoning passes")
    gold = {r["claim_id"]: r for r in data.get("human_adjudications", [])}
    if any(not r.get("reviewer") or not r.get("evidence_refs") for r in gold.values()):
        raise AVError("Human adjudications require attributed evidence, not a second essay")
    results, prior_supported = [], set()
    expected_ids = None
    for run in runs:
        if run.get("prior_passes_supplied") is not False:
            raise AVError("Ablation passes must declare independence of supplied context")
        rows = run.get("claims", [])
        ids = {c["id"] for c in rows}
        if len(ids) != len(rows) or (expected_ids is not None and ids != expected_ids):
            raise AVError("Ablation passes must answer the same unique claim IDs")
        expected_ids = ids
        supported = {c["id"] for c in rows if c.get("status") == "SUPPORTED" and c.get("evidence_ids")}
        verified = {i for i in supported if gold.get(i, {}).get("status") == "SUPPORTED"}
        false = {i for i in supported if gold.get(i, {}).get("status") in {"OPEN", "CONTRADICTED"}}
        results.append({"pass": run["pass"], "new_supported_claims": sorted(verified-prior_supported),
            "false_claims": sorted(false), "unadjudicated_supported_claims": sorted(supported-gold.keys()),
            "abstentions": sorted(c["id"] for c in rows if c.get("status") == "OPEN"),
            "speaker_errors": run.get("speaker_errors"), "timing_errors": run.get("timing_errors"),
            "wall_seconds": run.get("wall_seconds"), "cost_usd": run.get("cost_usd")})
        prior_supported = verified
    with output_transaction(output, [config]) as stage:
        write_json(stage/"ablation-evaluation.json", {"schema": "ave.ablation-evaluation.v1", "runs": results,
            "quality_scope": "Supported gain is scored only where source-grounded human adjudication exists"})
        return finish_run(stage, "evaluate-ablation", [file_record(config)], metadata={"result_file": "ablation-evaluation.json"})
