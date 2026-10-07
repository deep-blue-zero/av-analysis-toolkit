"""Explicit model download, pinned to the resolved upstream commit. No media uploads."""
import argparse
import hashlib
import json
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination")
    parser.add_argument("--revision", default="main")
    args = parser.parse_args()
    repo = "Qwen/Qwen3-ForcedAligner-0.6B"
    revision = HfApi().model_info(repo, revision=args.revision).sha
    target = Path(args.destination).resolve()
    receipt = target / "MODEL_RECEIPT.json"
    if receipt.exists():
        existing = json.loads(receipt.read_text(encoding="utf-8"))
        if existing["revision"] != revision:
            raise SystemExit("Use a new model directory for a different model revision")
    snapshot_download(repo, revision=revision, local_dir=str(target),
                      allow_patterns=["*.json", "*.safetensors", "*.txt", "*.tiktoken", "*.model", "README.md", "LICENSE*"])
    files = {}
    for path in sorted(target.iterdir()):
        if path.is_file() and path != receipt:
            with path.open("rb") as stream:
                files[path.name] = hashlib.file_digest(stream, "sha256").hexdigest()
    data = {"repository": repo, "revision": revision, "files": files,
            "download_only": True, "media_uploaded": False}
    receipt.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"destination": str(target), "revision": revision, "files": len(files)}))


if __name__ == "__main__":
    main()
