"""Allowlisted format/selected-stream declarations, not catalogue authentication."""
from __future__ import annotations

from .contracts import bounded_text, candidate, mbid, query_receipt
from ..common import AVError

TAGS = {"title", "artist", "album", "album_artist", "track", "disc", "composer", "isrc",
        "musicbrainz_trackid", "musicbrainz_recordingid", "musicbrainz_workid", "musicbrainz_albumid",
        "musicbrainz_releasegroupid", "musicbrainz_artistid", "musicbrainz_albumartistid"}


def embedded_metadata(probe, selected_stream, scope):
    receipt = query_receipt(scope, "embedded-metadata")
    declarations, warnings, candidates = [], [], []
    for container, tags in (("format", probe.get("probe", {}).get("format", {}).get("tags", {})),
                            ("selected_audio_stream", selected_stream.get("tags", {}))):
        if not isinstance(tags, dict):
            warnings.append("Malformed tag container omitted")
            continue
        clean = {}
        for key, value in tags.items():
            if not isinstance(key, str) or key.casefold().replace("-", "_") not in TAGS:
                continue
            key = key.casefold().replace("-", "_")
            try:
                text = bounded_text(value)
            except AVError:
                warnings.append("Malformed allowlisted tag omitted: " + key)
                continue
            if text is not None:
                clean[key] = text
        if not clean:
            continue
        declarations.append({"container": container, "tags": clean, "authority": "METADATA_DECLARED"})
        if clean.get("title") or clean.get("musicbrainz_trackid") or clean.get("musicbrainz_recordingid"):
            candidates.append(candidate(provider="embedded-metadata", title=clean.get("title"), artist=clean.get("artist"),
                album=clean.get("album"), isrc=clean.get("isrc"),
                recording_id=clean.get("musicbrainz_recordingid", clean.get("musicbrainz_trackid")),
                work_ids=[clean.get("musicbrainz_workid")], basis="METADATA_DECLARED", query_id=receipt["query_id"],
                interval=scope["interval_seconds"], relation={"tag_container": container}))
    conflicts = sorted({key for key in TAGS if len({d["tags"][key] for d in declarations if key in d["tags"]}) > 1})
    receipt.update(status="CANDIDATE" if candidates else "UNRESOLVED", candidate_ids=[c["candidate_id"] for c in candidates],
                   normalized_metadata=declarations, reason="Absent, stale or conflicting tags cannot establish identity",
                   missing_fields=["conflicting:" + key for key in conflicts] + warnings)
    return receipt, candidates
