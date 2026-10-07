"""Bind owner-supplied game event/asset metadata without unnecessary diarization."""
from pathlib import Path

from .analysis_schema import locator
from .common import AVError,file_record,finish_run,output_transaction,read_json,write_json,probe_source,select_stream
from .inventory import identifier,text_value

EVENTS = {"dialogue_UI","speaker_label","voice_asset","sprite_state","choice_state","manual_advance",
          "auto_advance","BGM","SFX","cutscene","3D_animation","pre_rendered_video"}


def bind_game_events(config,output):
    from .evidence_claims import _artifact
    config=Path(config).resolve(); data=read_json(config)
    deps={}; rows=[]; seen=set()
    if not isinstance(data.get("events"),list) or len(data["events"])>10000:
        raise AVError("Require a bounded game event list")
    for row in data["events"]:
        eid=identifier(row.get("event_id"),"game event ID")
        if eid in seen: raise AVError("Duplicate game event ID")
        seen.add(eid); locator(row.get("gameplay_locator"))
        if row.get("type") not in EVENTS: raise AVError("Unknown game event type")
        text_value(row.get("boundary_origin"),"event boundary origin")
        if row.get("voice_asset"):
            asset=_artifact(row["voice_asset"],config.parent,deps)
            if not row.get("canonical_dialogue_id") or not row.get("speaker") or not row.get("asset_to_gameplay_mapping"):
                raise AVError("Voice asset binding requires dialogue ID, speaker and an explicit timeline mapping")
            from .mapping import validated_segments, missing_intervals
            mapping=row["asset_to_gameplay_mapping"]
            if mapping.get("parent_source_sha256") != row["gameplay_locator"]["source_sha256"] or mapping.get("artifact_sha256") != asset["sha256"]:
                raise AVError("Voice asset mapping differs from the gameplay or asset identity")
            spans,_=validated_segments(mapping)
            if missing_intervals([[a,b] for a,b,_,_ in spans],row["gameplay_locator"]["intervals_seconds"]):
                raise AVError("Voice asset mapping lies outside the bound gameplay event")
            src=probe_source(asset["path"]); select_stream(src,"audio")
            if src["duration_seconds"] is not None and any(db>src["duration_seconds"]+1e-6 for _,_,_,db in spans):
                raise AVError("Voice asset mapping exceeds its native presentation endpoint")
        rows.append(dict(row,attribution="OPERATOR_SOURCE_BINDING", automatic_visual_detection="NOT_PERFORMED"))
    with output_transaction(output,[config]) as stage:
        write_json(stage/"game-events.json",{"schema":"ave.game-events.v1","events":rows,
            "scope":"Prefer verified isolated voice assets for acoustics; retain UI/gameplay as separate contextual witnesses"})
        return finish_run(stage,"game-bind",[file_record(config)]+list(deps.values()),metadata={"result_file":"game-events.json"})
