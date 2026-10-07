"""Verified, immutable local caches. Cache hits never imply perceptual review."""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
from pathlib import Path

from .common import AVError, output_transaction, read_json, safe_member, sha256, write_json


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode("utf-8")).hexdigest()


def versions(*names):
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def code_identity(*names):
    return {name: sha256(Path(__file__).parent / name) for name in names}


def cache_get(root, category, key_data, build):
    """Publish once; verify every cache artifact before reuse; refuse corruption."""
    key = digest(key_data)
    target = Path(root).resolve() / category / key
    if target.exists():
        receipt = read_json(target / "cache.json")
        if receipt.get("key") != key or receipt.get("inputs") != key_data:
            raise AVError(f"Cache identity mismatch: {target}")
        for artifact in receipt["artifacts"]:
            path = safe_member(target, artifact["path"])
            if not path.is_file() or sha256(path) != artifact["sha256"]:
                raise AVError(f"Cache artifact changed: {path}")
        return target, True, key
    with output_transaction(target) as stage:
        build(stage)
        artifacts = [{"path": p.relative_to(stage).as_posix(), "sha256": sha256(p)}
                     for p in sorted(stage.rglob("*")) if p.is_file()]
        write_json(stage / "cache.json", {"schema": "ave.performance-cache.v1", "key": key,
                                         "inputs": key_data, "artifacts": artifacts})
    return target, False, key


def model_identity(folder):
    """Hash local safe weights and configuration; never silently download a model."""
    root = Path(folder).resolve()
    if not root.is_dir() or not (root / "config.json").is_file():
        raise AVError("--model-dir must be a downloaded local Qwen3 ForcedAligner directory")
    files = [p for p in sorted(root.rglob("*")) if p.is_file()
             and ".cache" not in p.relative_to(root).parts
             and p.suffix in {".json", ".safetensors", ".txt", ".tiktoken", ".model"}]
    if not any(p.suffix == ".safetensors" for p in files):
        raise AVError("Local aligner has no safetensors weights")
    return {"files": {p.relative_to(root).as_posix(): sha256(p) for p in files},
            "runtime": versions("qwen-asr", "transformers", "torch", "nagisa")}
