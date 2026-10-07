"""Bounded visual sequences and cut candidates; no inferred gaze from stills."""
from pathlib import Path

from .common import AVError, file_record, finish_run, interval, output_transaction, read_json, write_json


def motion_window(input, output, *, start, end, video_stream=None, audio_stream=None,
                  frame_index_run=None, threshold=.35, width=960, make_clip=True):
    from .visual import extract_frames, contact_sheets
    from .clips import clip_av
    a,b = interval(start,end)
    if b-a > 30:
        raise AVError("Targeted motion windows are limited to 30 s")
    with output_transaction(output, [input] + ([frame_index_run] if frame_index_run else [])) as stage:
        key_times = sorted(set([a, a+(b-a)*.05, (a+b)/2, b-(b-a)*.05, max(a,b-.05)]))
        extract_frames(input, stage/"keyframes", mode="exact", timestamps=key_times, width=width,
                       stream_index=video_stream, frame_index_run=frame_index_run)
        contact_sheets(stage/"keyframes", stage/"sequence", columns=5, rows=1, thumb_width=width//3)
        extract_frames(input, stage/"cuts", mode="shots", start=a, end=b, threshold=threshold, width=width,
                       stream_index=video_stream, frame_index_run=frame_index_run)
        if make_clip:
            clip_av(input, stage/"continuous", start=a,end=b,video_stream=video_stream,audio_stream=audio_stream)
        cut_data = read_json(stage/"cuts"/"frames.json")
        boundaries = [a] + [r["source_seconds"] for r in cut_data["rows"] if a < r["source_seconds"] < b] + [b]
        boundaries = sorted(set(boundaries))
        report = {"schema": "ave.visual-temporal.v1", "source_sha256": cut_data["parent_source_sha256"],
            "stream_index": cut_data["parent_stream_index"], "clock": cut_data["clock"], "interval_seconds": [a,b],
            "keyframe_requests": {"pre": key_times[0], "onset_reference": key_times[1], "middle": key_times[2],
                                  "offset_reference": key_times[-2], "post_reference": key_times[-1]},
            "cut_candidates": cut_data["rows"], "estimated_shot_spans": [[x,y] for x,y in zip(boundaries,boundaries[1:])],
            "estimated_shot_durations_s": [y-x for x,y in zip(boundaries,boundaries[1:])],
            "continuous_clip": "continuous/clip.mkv" if make_clip else None,
            "quality_flags": ["MOTION_NOT_VERIFIED", "UNVERIFIED_VISUAL_EVENT"],
            "perceptual_review": "NOT_PERFORMED", "limitations": ["Image change detects cut candidates, including flashes; fades and held animated shots require review",
                "Five points are navigation references, not detected event onsets or proof of motion", "Gaze, posture and reaction-shot identity remain external observations"]}
        write_json(stage/"visual-temporal.json", report)
        return finish_run(stage, "motion-window", [file_record(input)], metadata={"result_file": "visual-temporal.json"})
