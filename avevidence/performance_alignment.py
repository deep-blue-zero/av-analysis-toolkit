"""Optional local Japanese forced alignment. Supplied words are hypotheses."""
from __future__ import annotations

import math
from pathlib import Path
import re

from .common import AVError, read_json, write_json
from .performance_cache import cache_get, code_identity, model_identity, versions


def speech_text(raw):
    """Retain raw text elsewhere; remove explicit SDH speaker labels/furigana only."""
    text = raw.strip()
    # Remove the inner reading before parsing an outer name label: （静江(しずえ)）.
    text = re.sub(r"(?<=[一-龯々])[（(][ぁ-んァ-ヶー]+[）)]", "", text)
    labels = re.findall(r"(?:^|\n)\s*[-－]?\s*[（(]([^）)]+)[）)]", text)
    if re.fullmatch(r"[（(][^）)]+[）)]", text):
        return "", None, "non_speech_caption", []
    text = re.sub(r"(?:^|\n)\s*[-－]?\s*[（(][^）)]+[）)]", " ", text)
    text = re.sub(r"[♪♫]", "", text)
    text = " ".join(text.split())
    if not any(char.isalnum() for char in text):
        return "", None, "non_speech_caption", ["punctuation_or_music_only"]
    speaker = labels[0] if len(set(labels)) == 1 else None
    return text, speaker, "speech_candidate", ["multiple_caption_speakers"] if len(set(labels)) > 1 else []


class QwenAligner:
    def __init__(self, model_dir, device="cuda", identity=None):
        self.model_dir = str(Path(model_dir).resolve())
        self.identity = identity or model_identity(model_dir)
        if not self.identity.get("runtime", {}).get("qwen-asr"):
            raise AVError("Local Japanese alignment requires the toolkit's [alignment] extra")
        self.device = device
        self.model = None
        self.timestamp_resolution_s = read_json(Path(self.model_dir) / "config.json").get("timestamp_segment_time", 0) / 1000 or None

    def __call__(self, audio, rate, text):
        import numpy as np
        import torch
        from scipy.signal import resample_poly
        from qwen_asr import Qwen3ForcedAligner
        if self.model is None:
            if self.device == "cuda" and not torch.cuda.is_available():
                raise AVError("CUDA is unavailable; install a CUDA-enabled runtime or select --device cpu")
            torch.set_num_threads(4)
            self.model = Qwen3ForcedAligner.from_pretrained(self.model_dir,
                dtype=torch.bfloat16 if self.device == "cuda" else torch.float32,
                device_map=self.device, local_files_only=True, trust_remote_code=False,
                use_safetensors=True, attn_implementation="sdpa")
        factor = math.gcd(rate, 16000)
        converted = (resample_poly(np.asarray(audio, dtype=np.float32), 16000 // factor, rate // factor)
                     if rate != 16000 else np.asarray(audio, dtype=np.float32))
        with torch.inference_mode():
            output = self.model.align(audio=(converted, 16000), text=text, language="Japanese")
        return [{"text": x.text, "start_s": float(x.start_time), "end_s": float(x.end_time)}
                for x in output[0]]


def align_cue(cue, samples, metadata, *, aligner, cache_root, audio_key, padding_s=.2, max_window_s=30.):
    if not math.isfinite(padding_s) or padding_s < 0 or not math.isfinite(max_window_s) or not 0 < max_window_s <= 300:
        raise AVError("Alignment padding/window parameters are invalid")
    text = cue["alignment_text"]
    if not text or cue["kind"] == "non_speech_caption":
        return {"status": "SKIPPED_NON_SPEECH", "units": [], "cache_hit": False}
    rate = metadata["identity"]["sample_rate_hz"]
    a, b = cue["start_s"], cue["end_s"]
    eligible = [s for s in metadata["segments"]
                if s["source_start_seconds"] <= a + 1 / rate and s["source_end_seconds"] >= b - 1 / rate]
    if len(eligible) != 1:
        return {"status": "SKIPPED_AUDIO_GAP_OR_PARTIAL_COVERAGE", "units": [], "cache_hit": False}
    segment = eligible[0]
    lo = max(segment["source_start_seconds"], a - padding_s)
    hi = min(segment["source_end_seconds"], b + padding_s)
    first = segment["sample_start"] + math.ceil((lo - segment["source_start_seconds"]) * rate - 1e-6)
    last = min(segment["sample_end"], segment["sample_start"] + math.ceil((hi - segment["source_start_seconds"]) * rate - 1e-6))
    crop_start = segment["source_start_seconds"] + (first - segment["sample_start"]) / rate
    duration = (last - first) / rate
    if duration > max_window_s:
        return {"status": "SKIPPED_WINDOW_TOO_LONG", "units": [], "cache_hit": False}
    key_data = {"schema": "qwen-ja-alignment-v1", "audio_key": audio_key,
                "sample_start": first, "sample_end": last, "text": text,
                "model": aligner.identity, "device": aligner.device,
                "language": "Japanese", "resampler_runtime": versions("scipy", "numpy"),
                "code": code_identity("performance_alignment.py")}

    def build(stage):
        words = aligner(samples[first:last], rate, text)
        rejection = "Aligner returned no units" if not words else None
        previous = 0.
        for word in words:
            start, end = word["start_s"], word["end_s"]
            if (not isinstance(word["text"], str) or not word["text"].strip()
                    or not math.isfinite(start) or not math.isfinite(end)
                    or not 0 <= start <= end <= duration + 1 / rate or start < previous - 1e-6):
                rejection = "Aligner output is nonfinite, nonmonotonic, empty or outside the audio crop"
                break
            previous = end
        if rejection:
            # Deterministic rejected outputs are evidence too, but never usable word timings.
            # Cache them so a warm corpus search does not repeatedly launch model inference.
            write_json(stage / "alignment.json", {"status": "REJECTED_TIMING", "units": [],
                "error": rejection, "input_sample_start": first, "input_sample_end": last,
                "crop_source_start_s": crop_start, "crop_source_end_s": crop_start + duration,
                "raw_rejected_units": [{"text": str(w.get("text", "")),
                    "crop_start_s": w["start_s"] if math.isfinite(w["start_s"]) else None,
                    "crop_end_s": w["end_s"] if math.isfinite(w["end_s"]) else None} for w in words],
                "text_verification": "NOT_ESTABLISHED_BY_ALIGNMENT", "confidence": None})
            return
        zero_count = sum(word["start_s"] == word["end_s"] for word in words)
        write_json(stage / "alignment.json", {"status": "ALIGNED_CANDIDATE",
            "units": [{"text": word["text"], "start_s": crop_start + word["start_s"],
                       "end_s": crop_start + word["end_s"],
                       "crop_start_s": word["start_s"], "crop_end_s": word["end_s"]} for word in words],
            "input_sample_start": first, "input_sample_end": last,
            "crop_source_start_s": crop_start, "crop_source_end_s": crop_start + duration,
            "model_input_rate_hz": 16000, "resampler": "scipy.signal.resample_poly",
            "text_verification": "NOT_ESTABLISHED_BY_ALIGNMENT",
            "confidence": None, "confidence_note": "This backend provides no calibrated alignment confidence",
            "timestamp_resolution_s": getattr(aligner, "timestamp_resolution_s", None),
            "zero_duration_units": zero_count,
            "quality_flags": ["zero_duration_units_require_review"] if zero_count else [],
            "timing_note": "Model-estimated unit boundaries; source sample support is a separate mapping"})
    path, hit, key = cache_get(cache_root, "alignment", key_data, build)
    return {**read_json(path / "alignment.json"), "cache_hit": hit, "cache_key": key}
