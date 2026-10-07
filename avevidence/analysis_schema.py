"""Small shared contracts for evidence needs, boundary authority and uncertainty."""
from .common import AVError
from .inventory import digest_value, identifier, intervals, points, text_value

FLAGS = {"BGM_CONTAMINATION", "SPEAKER_OVERLAP", "UNCERTAIN_SPEAKER", "SUBTITLE_BOUNDARY_ONLY",
    "LOW_F0_CONFIDENCE", "SEPARATION_ARTIFACT", "MOTION_NOT_VERIFIED", "AUDITORY_OBSERVER_UNAVAILABLE",
    "POSSIBLE_RECORDING_REUSE", "ALIGNMENT_UNCERTAIN", "SPEAKER_ISOLATION_NOT_ESTABLISHED",
    "F0_BOUNDARY_SATURATION", "F0_DISCONTINUITY", "COVERAGE_GAP", "UNMAPPED_EXTERNAL_SOURCE",
    "UNVALIDATED_AUDITORY_ROUTE", "INSUFFICIENT_INDEPENDENT_PERFORMANCES", "UNVERIFIED_VISUAL_EVENT",
    "UNVERIFIED_SOURCE_SEPARATION", "SOUNDTRACK_COMPONENT_UNIDENTIFIED"}
MODALITIES = {"text", "audio", "visual_stills", "motion"}
LAYERS = {"observed", "textual", "measured", "auditory_observation", "inference", "interpretation"}
KINDS = {"mixed_energy", "pitch", "speech_latency", "delivery", "motion", "speaker_identity",
    "recording_identity", "musical_relation", "text_fact", "visual_fact", "interpretation", "editing", "game_event"}
GATES = {
    "mixed_energy": {"COVERAGE_GAP"},
    "pitch": {"LOW_F0_CONFIDENCE", "BGM_CONTAMINATION", "SPEAKER_OVERLAP", "SEPARATION_ARTIFACT", "SPEAKER_ISOLATION_NOT_ESTABLISHED"},
    "speech_latency": {"SUBTITLE_BOUNDARY_ONLY", "ALIGNMENT_UNCERTAIN", "COVERAGE_GAP"},
    "delivery": {"AUDITORY_OBSERVER_UNAVAILABLE", "UNVALIDATED_AUDITORY_ROUTE", "UNCERTAIN_SPEAKER"},
    "motion": {"MOTION_NOT_VERIFIED", "UNVERIFIED_VISUAL_EVENT"},
    "speaker_identity": {"UNCERTAIN_SPEAKER", "UNVALIDATED_AUDITORY_ROUTE", "AUDITORY_OBSERVER_UNAVAILABLE"},
    "recording_identity": {"POSSIBLE_RECORDING_REUSE"},
    "editing": {"MOTION_NOT_VERIFIED", "UNVERIFIED_VISUAL_EVENT"}}


def flags(value):
    if not isinstance(value, list) or not all(isinstance(x, str) for x in value) or set(value)-FLAGS:
        raise AVError("Unknown or malformed uncertainty flags")
    return sorted(set(value))


def modalities(value):
    if not isinstance(value, list) or not value or not all(isinstance(x, str) for x in value) or set(value)-MODALITIES:
        raise AVError("Require explicit audio, text, visual_stills or motion modalities")
    return sorted(set(value))


def locator(value, *, modality=None):
    if not isinstance(value, dict):
        raise AVError("Source locator must be an object")
    digest_value(value.get("source_sha256"))
    if value.get("clock") != "original_pts_minus_source_origin":
        raise AVError("Evidence locator must declare the source-relative clock")
    if modality in {"audio", "motion"}:
        if type(value.get("stream_index")) is not int or value["stream_index"] < 0:
            raise AVError("Timed media evidence requires its absolute stream index")
    if modality == "visual_stills":
        if "intervals_seconds" in value:
            raise AVError("Stills are point evidence and cannot establish continuous motion")
        points(value.get("points_seconds"))
    else:
        intervals(value.get("intervals_seconds"))
    return value
