"""Generate a fictional scene contract outside the source repository.

No media, provider, heard performance or literary finding is supplied here.
The observer records are explicitly scripted test doubles. An optional external
generated media file lets an offline test exercise the public deep-read command.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

# Run the checked-out example against this checkout, even when a different
# toolkit release is installed in the interpreter's environment.
repository_source = str(Path(__file__).resolve().parent.parent)
if repository_source not in sys.path:
    sys.path.insert(0, repository_source)

from avevidence.common import AVError, sha256, write_json
from avevidence.event_contracts import CLOCK


def build_synthetic_scene(output, *, source=None, audio_stream=0, video_stream=None):
    output = Path(output).resolve()
    repository = Path(__file__).resolve().parent.parent
    if output == repository or output.is_relative_to(repository):
        raise AVError("Generate this example in an external output directory")
    if output.exists():
        raise AVError("Synthetic example output already exists")
    if source is not None and not Path(source).is_file():
        raise AVError("Optional generated source file is absent")
    output.mkdir(parents=True)
    if source is None:
        source = output/"hypothetical-source-script.txt"
        source.write_text("Fictional event contract, not recorded audiovisual media.\n"
                          "A caller says Mira's name beside a closing door.\n", encoding="utf-8")
    source = Path(source).resolve()
    source_hash = sha256(source)
    text = output/"canonical-text.txt"
    text.write_text("Mira, leave the door open.\n", encoding="utf-8")
    directions = output/"fictional-stage-directions.txt"
    directions.write_text("A fictional hand is depicted beside a door latch.\n"
                          "No actual motion, contact, hearing or viewing is asserted.\n", encoding="utf-8")
    audio_hash = hashlib.sha256(b"Scripted imaginary audio bytes; no actual audio witness exists").hexdigest()
    scripted = {"schema":"ave.auditory-observation.v1", "observer_type":"mock",
        "backend_identity":{"type":"mock", "model_revision":"synthetic-scene-only-v1", "adapter_revision":"1"},
        "source_sha256":source_hash, "stream_index":audio_stream, "source_interval_seconds":[1,2],
        "clip_sha256":audio_hash, "task_profile":"SPEECH_PERFORMANCE", "task_capability_status":"UNQUALIFIED",
        "validation_status":"VALID", "stage":1, "supplied_context":{},
        "raw_response":json.dumps({"observations":[{"start_s":0,"end_s":1,"description":"Scripted soft delivery; lexical guess Mila."}],
            "alternatives":[],"interference":[],"abstentions":[],"confidence":"low"}),
        "scope":"A fictional test double; no model or human inspected audio"}
    stage1 = output/"scripted-stage1.json"; write_json(stage1, scripted)
    stage2 = output/"scripted-stage2.json"
    contextual = copy.deepcopy(scripted)
    contextual.update(stage=2, stage1_parent={"observation_sha256":sha256(stage1)},
        supplied_context={"canonical_text":"Mira, leave the door open.", "context_evidence_ids":["TXT1"]},
        raw_response=json.dumps({"observations":[{"start_s":0,"end_s":1,"description":"Scripted contextual wording Mira; delivery unchanged."}],
            "alternatives":[],"interference":[],"abstentions":[],"confidence":"low"}))
    write_json(stage2, contextual)

    def reference(eid, path, authority, *, stream=None, **extra):
        loc = {"source_sha256":source_hash, "clock":CLOCK, "intervals_seconds":[[1,2]]}
        if stream is not None: loc["stream_index"] = stream
        return {"id":eid, "artifact":{"path":path.name,"sha256":sha256(path)},
                "locator":loc, "authority":authority, **extra}
    def observation(oid, proposition, statement, evidence_id, status="REPORTED", **extra):
        return {"id":oid,"proposition":proposition,"statement":statement,"status":status,
                "evidence_ids":[evidence_id],"interval_seconds":[1,2],**extra}
    def claim(cid, proposition, statement, premises, status="PROVISIONAL", **extra):
        return {"id":cid,"proposition":proposition,"statement":statement,"status":status,
                "observation_ids":premises,"interval_seconds":[1,2],"confidence":"LOW",**extra}
    source_record = {"path":str(source),"sha256":source_hash,"clock":CLOCK,"duration_seconds":3,"audio_stream":audio_stream}
    if video_stream is not None: source_record["video_stream"] = video_stream
    before = {"schema":"ave.scene-packet.v1", "scene_id":"fictional-door-event", "source":source_record,
        "interval_seconds":[0,3], "narrative_boundary":{"basis":"fictional_scene_boundary","description":"One invented name-and-door exchange"},
        "question":"How could wording, delivery and a door gesture jointly distinguish reassurance from urgency?",
        "evidence":{
            "TXT":[reference("TXT1",text,"canonical_text")],
            "AO_STAGE1":[reference("AO1",stage1,"observer_report",stream=audio_stream)],
            "AO_STAGE2":[reference("AO2",stage2,"observer_report",stream=audio_stream,stage1_evidence_id="AO1",context_evidence_ids=["TXT1"])],
            "VIS":[reference("VIS1",directions,"generated",stream=video_stream)]},
        "observations":[
            observation("oText","wording","The fictional canonical text addresses Mira.","TXT1",status="CONFIRMED",value="Mira"),
            observation("oGuess","wording","The scripted Stage 1 lexical guess is Mila.","AO1",value="Mila"),
            observation("oContext","wording","The scripted contextual record repeats Mira.","AO2",value="Mira"),
            observation("oDelivery","delivery","The scripted delivery descriptor is soft; it has not been heard.","AO1"),
            observation("oVisual","visual_fact","Fictional stage directions place a hand beside a latch.","VIS1",temporal_status="STATIC_CONFIRMED")],
        "claims":[
            claim("cName","wording","The fictional address is Mira.",["oText"],status="SUPPORTED"),
            claim("cReferent","wording","The caller addresses Mila.",["oGuess"]),
            claim("cUrgency","interpretation","Mila's presumed presence could motivate the warning.",["oGuess"],inference="This hypothesis depends on the mistaken lexical referent."),
            claim("cDelivery","delivery","The soft-delivery hypothesis needs actual listening.",["oDelivery"]),
            claim("cContact","contact","The hand contacts the latch before an alarm.",["oVisual"],status="OPEN")],
        "dependencies":[
            {"from":"oText","to":"cName","relation":"SUPPORTS","reason":"The stronger textual witness supplies this wording premise."},
            {"from":"oGuess","to":"cReferent","relation":"DEPENDS_ON","reason":"The guessed name determines this referent."},
            {"from":"oGuess","to":"cUrgency","relation":"DEPENDS_ON","reason":"The dramatic hypothesis depends on the guessed referent."},
            {"from":"cReferent","to":"cUrgency","relation":"REQUIRES_RECHECK_IF_CHANGED","reason":"A changed referent limits the dramatic hypothesis."},
            {"from":"oDelivery","to":"cDelivery","relation":"DEPENDS_ON","reason":"Only the separate delivery descriptor motivates this listening question."},
            {"from":"oVisual","to":"cContact","relation":"CONSTRAINS","reason":"The stage direction cannot establish actual contact order."}],
        "conflicts":[{"id":"name-conflict","proposition":"wording","observation_ids":["oText","oGuess","oContext"],
            "question":"Which wording witness has authority, and which judgments actually depend on the lexical error?"}],
        "adjudication":{"reviewer":"Fictional contract author","decisions":{
            "oText":{"state":"CONFIRMED","reason":"Only the invented canonical script's wording is declared here."},
            "cName":{"state":"SUPPORTED","reason":"A source-text premise supports this fictional wording claim."}}},
        "open_questions":["No actual delivery was heard.","No actual latch motion or alarm synchrony was reviewed."],
        "holistic_analysis":"A fictional hypothesis combines address, softness and gesture while keeping delivery and motion unverified. This is a contract example, not an interpretation of real media."}
    after = copy.deepcopy(before)
    after["adjudication"]["decisions"]["oGuess"] = {"state":"CONTRADICTED","reason":"The lexical guess conflicts with the explicitly declared fictional canonical script."}
    after["holistic_analysis"] = "The lexical correction affects the referent-dependent hypothesis. It neither disproves nor validates the separate unheard delivery descriptor; contact order remains open."
    before_path, after_path = output/"before-scene.json", output/"after-scene.json"
    write_json(before_path,before); write_json(after_path,after)
    capability_config = output/"task-capability-input.json"
    write_json(capability_config,{"backend_identity":scripted["backend_identity"],
        "historical_probes":[{"status":"PROBE_FAILED","scope":"Synthetic example of a retained historical failure; not a real provider result"}],"scoped_reviews":[]})
    (output/"README.txt").write_text("Generated fictional contract example. No source performance was heard or viewed.\n"
        "The text source, directions and observer records are invented; mock observations cannot become auditory evidence.\n"
        "Use the public scene validate, prepare, reconcile, plan and delta commands on before-scene.json / after-scene.json.\n"
        "The corrected lexical premise rechecks cReferent and cUrgency; cDelivery stays separate and unverified.\n"
        "This directory is external generated output, not part of the software source tree.\n\n"
        "After installing this checkout, run from this generated directory:\n"
        "ave scene validate before-scene.json\n"
        "ave scene prepare before-scene.json prepared\n"
        "ave scene reconcile after-scene.json reconciled\n"
        "ave scene plan after-scene.json planned\n"
        "ave scene delta before-scene.json after-scene.json delta\n"
        "ave observer profiles\n"
        "ave observer capability-profile task-capability-input.json task-capabilities\n",encoding="utf-8")
    return {"output":str(output),"before":str(before_path),"after":str(after_path),"source":str(source),
            "capability_config":str(capability_config),"source_sha256":source_hash}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output"); parser.add_argument("--source",help="Optional external generated media for offline CLI tests")
    parser.add_argument("--audio-stream",type=int,default=0); parser.add_argument("--video-stream",type=int)
    args=parser.parse_args(argv)
    print(json.dumps(build_synthetic_scene(args.output,source=args.source,audio_stream=args.audio_stream,video_stream=args.video_stream),indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
