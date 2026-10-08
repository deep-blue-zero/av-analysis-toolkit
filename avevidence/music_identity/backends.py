"""Explicit backend/privacy interface; no commercial or hosted-audio fallback."""
from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from ..common import AVError, utc_now, write_json
from ..event_contracts import canonical_digest, read_event_json
from .contracts import candidate, mbid, query_receipt

MAX_RESPONSE = 2 * 1024 * 1024
MAX_JSON_DEPTH = 64


class MusicIdentityBackend(ABC):
    name: str
    privacy_class: str

    @abstractmethod
    def query(self, context):
        """Return a lookup receipt and attributed candidates for an admitted scope."""


class EmbeddedMetadataBackend(MusicIdentityBackend):
    name, privacy_class = "embedded-metadata", "LOCAL_ONLY"

    def query(self, context):
        from .metadata import embedded_metadata
        return embedded_metadata(context["probe"], context["stream"], context["source"])


class LocalReuseBackend(MusicIdentityBackend):
    name, privacy_class = "local-reuse", "LOCAL_ONLY"

    def query(self, context):
        from .local import search_corpus
        return search_corpus(context)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_transport(method, url, headers, body):
    """One fixed HTTPS request, no redirects/retries, bounded response, safe errors."""
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            raw = response.read(MAX_RESPONSE + 1)
    except (OSError, urllib.error.URLError):
        raise AVError("Music catalogue request failed; response and credential details are not logged") from None
    if len(raw) > MAX_RESPONSE:
        raise AVError("Music catalogue response exceeds the bounded response budget")
    return raw


def strict_response(raw, secret=None):
    if not isinstance(raw, bytes) or len(raw) > MAX_RESPONSE:
        raise AVError("Music catalogue transport must return bounded response bytes")
    if secret and secret.encode("utf-8") in raw:
        raise AVError("Provider echoed a credential; response rejected without persistence")
    try:
        def pairs(items):
            row = {}
            for k, v in items:
                if k in row:
                    raise ValueError()
                row[k] = v
            return row
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise AVError("Music catalogue returned malformed JSON") from None
    if not isinstance(value, dict):
        raise AVError("Music catalogue response must be an object")
    # Bound decoded nesting independently of the host's Python recursion setting.
    # JSON escapes can hide a credential echo in raw bytes.
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_JSON_DEPTH:
            raise AVError("Music catalogue JSON exceeds its bounded nesting depth")
        if secret and isinstance(item, str) and secret in item:
            raise AVError("Provider echoed a credential; response rejected without persistence")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.keys())
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value, canonical_digest(value)


def authorize(provider, allowed):
    if provider not in allowed:
        raise AVError("External lookup requires --allow-external-lookup " + provider + "; remote-media permission is separate")


def credential(env_name):
    if not isinstance(env_name, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,100}", env_name):
        raise AVError("Use an environment-variable name for the AcoustID client key")
    key = os.environ.get(env_name)
    if not key or not 8 <= len(key) <= 512 or any(ord(c) < 33 or ord(c) > 126 for c in key):
        raise AVError("AcoustID client key is unavailable or malformed in the named environment variable")
    return key


def _artist(value):
    if not isinstance(value, list) or len(value) > 100:
        return None
    names = [row.get("name") or row.get("artist", {}).get("name") for row in value if isinstance(row, dict)]
    return "; ".join(name for name in names if isinstance(name, str)) or None


class AcoustIDBackend(MusicIdentityBackend):
    name, privacy_class = "acoustid", "DERIVED_FINGERPRINT_LOOKUP"

    def __init__(self, allowed=(), credential_env="ACOUSTID_API_KEY", transport=None):
        self.allowed, self.credential_env, self.transport = allowed, credential_env, transport or http_transport

    def query(self, context):
        authorize(self.name, self.allowed)
        fp = context["fingerprint"]
        secret = credential(self.credential_env)
        receipt = query_receipt(context["source"], self.name, self.privacy_class,
            fingerprint_sha256=fp["fingerprint_sha256"], fingerprint_sent=True, duration_sent=True,
            authorization="EXPLICIT_EXTERNAL_LOOKUP", queried_at=utc_now())
        # Only this derived fingerprint/duration/client identifier leave the host.
        body = urllib.parse.urlencode({"client": secret, "duration": round(fp["duration_seconds"]),
            "fingerprint": fp["fingerprint"], "meta": "recordings releases"}).encode("ascii")
        receipt["external_request_performed"] = self.transport is http_transport
        receipt["test_double"] = self.transport is not http_transport
        try:
            raw = self.transport("POST", "https://api.acoustid.org/v2/lookup",
                                 {"Content-Type": "application/x-www-form-urlencoded"}, body)
            response, digest = strict_response(raw, secret)
        except AVError:
            receipt.update(status="UNRESOLVED", reason="Provider transport/JSON validation failed; no identity conclusion or response details persisted")
            return receipt, []
        receipt["response_sha256"] = digest
        if response.get("status") != "ok":
            receipt.update(status="UNRESOLVED", reason="Provider returned a non-success result; no identity conclusion")
            return receipt, []
        try:
            candidates = self._candidates(response, context, receipt, fp)
        except (AVError, TypeError, ValueError, KeyError, AttributeError):
            receipt.update(status="UNRESOLVED", reason="Malformed or excessive provider candidates rejected; response hash retained without private details")
            return receipt, []
        receipt.update(status="CANDIDATE" if candidates else "UNRESOLVED",
                       candidate_ids=[c["candidate_id"] for c in candidates],
                       reason="Provider-native scores are not probabilities; no result is not evidence of originality")
        return receipt, candidates

    def _candidates(self, response, context, receipt, fp):
        results = response.get("results", [])
        if not isinstance(results, list) or len(results) > 100:
            raise AVError("Malformed or excessive AcoustID result list")
        candidates = []
        for result in results:
            if not isinstance(result, dict):
                raise AVError("Malformed AcoustID result")
            recordings = result.get("recordings", [])
            if not isinstance(recordings, list) or len(recordings) > 100:
                raise AVError("Malformed or excessive AcoustID recordings")
            for recording in recordings:
                if not isinstance(recording, dict):
                    raise AVError("Malformed AcoustID recording")
                candidates.append(candidate(provider=self.name, title=recording.get("title"),
                    artist=_artist(recording.get("artists")), recording_id=recording.get("id"),
                    provider_id=result.get("id"), score=result.get("score"), basis="DERIVED_FINGERPRINT_CANDIDATE",
                    query_id=receipt["query_id"], interval=context["source"]["interval_seconds"],
                    fingerprint_sha256=fp["fingerprint_sha256"]))
        unique = {c["candidate_id"]: c for c in candidates}
        candidates = list(unique.values())
        if len(candidates) > 1000:
            raise AVError("AcoustID candidate budget exceeded")
        return candidates


def normalize_musicbrainz(response, expected_id):
    if response.get("id") != expected_id or not mbid(expected_id):
        raise AVError("MusicBrainz recording identity differs from the requested ID")
    relations = response.get("relations", [])
    if not isinstance(relations, list) or len(relations) > 200:
        raise AVError("Malformed MusicBrainz relations")
    works = []
    for relation in relations:
        if not isinstance(relation, dict):
            raise AVError("Malformed MusicBrainz relation")
        work = relation.get("work")
        if relation.get("target-type") == "work" and isinstance(work, dict) and mbid(work.get("id")):
            # Preserve the provider's relation type and attributes, including cover/medley.
            works.append({"id": work["id"], "title": work.get("title"), "relation_type": relation.get("type"),
                          "attributes": relation.get("attributes", [])})
    releases = response.get("releases", [])
    if not isinstance(releases, list) or len(releases) > 100:
        raise AVError("Malformed MusicBrainz releases")
    normalized = {"recording_id": expected_id, "title": response.get("title"),
        "artist": _artist(response.get("artist-credit")), "isrcs": response.get("isrcs", []),
        "works": works, "releases": [{"id": r.get("id"), "title": r.get("title"),
            "release_group_id": r.get("release-group", {}).get("id")} for r in releases if isinstance(r, dict)]}
    # Validate metadata through the bounded public candidate contract, retain nulls.
    if not isinstance(normalized["isrcs"], list) or len(normalized["isrcs"]) > 100:
        raise AVError("Malformed MusicBrainz ISRC list")
    from .contracts import bounded_text
    for item in (normalized["title"], normalized["artist"], *normalized["isrcs"]):
        bounded_text(item)
    for work in works:
        bounded_text(work["title"])
        bounded_text(work["relation_type"])
        if not isinstance(work["attributes"], list) or len(work["attributes"]) > 20:
            raise AVError("Malformed MusicBrainz work attributes")
        for attr in work["attributes"]:
            bounded_text(attr)
    for release in normalized["releases"]:
        bounded_text(release["title"])
        if not mbid(release["id"]) or release["release_group_id"] is not None and not mbid(release["release_group_id"]):
            raise AVError("Malformed MusicBrainz release identifier")
    return normalized


class MusicBrainzNormalizer:
    """Explicitly authorized, cached, rate-limited metadata-only normalization."""
    def __init__(self, allowed=(), cache=None, transport=None, user_agent="AVEvidenceToolkit/1.6 (https://github.com/deep-blue-zero/av-analysis-toolkit)"):
        self.allowed, self.cache, self.transport = allowed, Path(cache) if cache else None, transport or http_transport
        if not isinstance(user_agent, str) or not 10 <= len(user_agent) <= 256 or any(ord(c) < 32 for c in user_agent):
            raise AVError("MusicBrainz requires a bounded identifying User-Agent")
        self.user_agent, self.last_request = user_agent, 0.

    def query(self, recording_id, source):
        authorize("musicbrainz", self.allowed)
        if not mbid(recording_id):
            raise AVError("MusicBrainz normalization requires a recording UUID")
        receipt = query_receipt(source, "musicbrainz", "DERIVED_FINGERPRINT_LOOKUP",
            authorization="EXPLICIT_EXTERNAL_LOOKUP", queried_at=utc_now(), requested_recording_id=recording_id)
        cache_path = self.cache / (recording_id + ".json") if self.cache else None
        entry = read_event_json(cache_path, MAX_RESPONSE) if cache_path and cache_path.is_file() else None
        if entry is not None:
            if not isinstance(entry, dict) or entry.get("schema") != "ave.musicbrainz.cache.v1" or entry.get("recording_id") != recording_id:
                raise AVError("Malformed MusicBrainz cache binding")
            response, retrieved_at, response_hash = entry["response"], entry["retrieved_at"], entry["response_sha256"]
            if canonical_digest(response) != response_hash:
                raise AVError("MusicBrainz cached response hash differs")
            receipt["external_request_performed"] = False
        else:
            if self.transport is http_transport:
                time.sleep(max(0., 1.1 - (time.monotonic() - self.last_request)))
            url = "https://musicbrainz.org/ws/2/recording/" + recording_id + "?inc=artists+releases+release-groups+isrcs+work-rels&fmt=json"
            receipt["external_request_performed"] = self.transport is http_transport
            receipt["test_double"] = self.transport is not http_transport
            # Rate-limit attempted requests, including transport/JSON failures.
            self.last_request = time.monotonic()
            try:
                raw = self.transport("GET", url, {"User-Agent": self.user_agent, "Accept": "application/json"}, None)
                response, response_hash = strict_response(raw)
            except AVError:
                receipt.update(status="UNRESOLVED", reason="Provider transport/JSON validation failed; no identity conclusion", requested_recording_id=recording_id)
                return receipt, []
            retrieved_at = utc_now()
        try:
            normalized = normalize_musicbrainz(response, recording_id)
        except (AVError, TypeError, ValueError, KeyError, AttributeError):
            receipt.update(status="UNRESOLVED", reason="Malformed catalogue metadata rejected; identity remains unresolved",
                           response_sha256=response_hash, metadata_retrieved_at=retrieved_at)
            return receipt, []
        # Persist allowlisted normalized metadata, never arbitrary provider fields.
        if cache_path and entry is None:
            from ..event_contracts import canonical_digest as digest
            cached_response = {"id": recording_id, "title": normalized["title"], "artist-credit": [{"name": normalized["artist"]}] if normalized["artist"] else [],
                "isrcs": normalized["isrcs"], "relations": [{"target-type": "work", "type": w["relation_type"], "attributes": w["attributes"], "work": {"id": w["id"], "title": w["title"]}} for w in normalized["works"]],
                "releases": [{"id": r["id"], "title": r["title"], "release-group": {"id": r["release_group_id"]}} for r in normalized["releases"]]}
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            if not cache_path.exists():
                write_json(cache_path, {"schema": "ave.musicbrainz.cache.v1", "recording_id": recording_id,
                    "retrieved_at": retrieved_at, "response": cached_response, "response_sha256": digest(cached_response)})
        c = candidate(provider="musicbrainz", title=normalized["title"], artist=normalized["artist"], recording_id=recording_id,
            work_ids=[w["id"] for w in normalized["works"] if w["relation_type"] == "performance"],
            isrc=normalized["isrcs"][0] if normalized["isrcs"] else None, basis="CATALOGUE_RELATION",
            query_id=receipt["query_id"], interval=source["interval_seconds"], relation=normalized)
        receipt.update(status="CANDIDATE", candidate_ids=[c["candidate_id"]], normalized_metadata=[normalized],
            missing_fields=[k for k in ("title", "artist", "isrcs", "works", "releases") if not normalized[k]],
            response_sha256=response_hash, metadata_retrieved_at=retrieved_at,
            reason="Recording, release and work are distinct catalogue entities; normalization does not authenticate this source")
        return receipt, [c]


BACKENDS = {b.name: b for b in (EmbeddedMetadataBackend, LocalReuseBackend, AcoustIDBackend)}
