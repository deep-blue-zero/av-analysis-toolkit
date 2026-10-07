"""Secret-free hosted errors, bounded HTTP, pricing estimates and local accounting.

The ledger is an append-only local spending guard, not an account balance or an
invoice. Unknown billing blocks further calls in that session/queue.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json
import math
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

from ..common import AVError, utc_now

ENDPOINT = "https://api.openai.com/v1/chat/completions"
MODEL = "gpt-audio-1.5"
PRICING = {
    "snapshot_id": "openai-gpt-audio-1.5-standard-2026-10-06-v1",
    "verified_date": "2026-10-06", "currency": "USD", "model": MODEL,
    "source": "https://developers.openai.com/api/docs/models/gpt-audio-1.5",
    "usd_per_million": {"input_text": 2.5, "input_audio": 32., "output_text": 10., "output_audio": 64.},
    "scope": "Standard synchronous published rates; no Batch discount, tax, credits or account-balance assumption",
}
EXPERIMENTAL_MODELS = ("gpt-audio-2025-08-28",)


def pricing_for_model(model=MODEL):
    """Explicit verified models only; each receipt names its actual tariff."""
    if model != MODEL and model not in EXPERIMENTAL_MODELS:
        raise AVError("Unsupported audio model; require an explicitly verified pricing and transport configuration")
    snapshot = dict(PRICING, usd_per_million=dict(PRICING["usd_per_million"]))
    if model != MODEL:
        snapshot.update(model=model, snapshot_id="openai-"+model+"-standard-2026-10-06-v1",
                        source="https://developers.openai.com/api/docs/models/gpt-audio")
    return snapshot


def model_matches(configured, returned):
    # Only the default alias may resolve to a dated provider revision. The
    # experimental model is pinned, so prefix matches cannot admit another model.
    return isinstance(returned, str) and (returned == configured or
        configured == MODEL and returned.startswith(MODEL+"-"))


class HostedError(AVError):
    def __init__(self, code, message, *, details=None):
        if code not in {"OPENAI_CREDENTIAL_NOT_CONFIGURED", "HOSTED_MEDIA_NOT_AUTHORIZED", "BACKEND_NOT_PROBED",
            "BACKEND_PROBE_FAILED", "BUDGET_GUARD", "PROVIDER_REQUEST_FAILED", "PROVIDER_RATE_LIMITED", "PROVIDER_RESPONSE_INVALID"}:
            code = "PROVIDER_REQUEST_FAILED"
        self.code = code
        self.details = details if isinstance(details, dict) else {}
        super().__init__(code + ": " + message)


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError("Nonfinite JSON")))


def credential(env_name):
    if not isinstance(env_name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", env_name):
        raise HostedError("OPENAI_CREDENTIAL_NOT_CONFIGURED", "Supply an environment-variable name, never a literal key")
    value = os.environ.get(env_name, "")
    if not 8 <= len(value) <= 8192 or value != value.strip() or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise HostedError("OPENAI_CREDENTIAL_NOT_CONFIGURED", "The configured environment credential is absent or invalid")
    return value


def authorize(allow_remote_media, env_name):
    if allow_remote_media != "openai":
        raise HostedError("HOSTED_MEDIA_NOT_AUTHORIZED", "Explicit --allow-remote-media openai is required for this run")
    return credential(env_name)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_transport(payload, key, timeout_s):
    """One attempt, fixed HTTPS endpoint, no redirects/retries or request logging."""
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    req = urllib.request.Request(ENDPOINT, data=data, method="POST",
        headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(_NoRedirect()).open(req, timeout=timeout_s) as response:
            raw = response.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider envelope exceeds the response budget")
        return strict_json(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        code = "PROVIDER_RATE_LIMITED" if exc.code == 429 else "PROVIDER_REQUEST_FAILED"
        # Retain only bounded machine-readable fields. Provider messages can
        # echo credentials or request data and must never enter receipts.
        details = {"http_status": exc.code}
        try:
            error_bytes = exc.read(8193)
            if len(error_bytes) <= 8192:
                envelope = strict_json(error_bytes.decode("utf-8"))
                error = envelope.get("error") if isinstance(envelope, dict) else None
                if isinstance(error, dict):
                    for name in ("code", "type", "param"):
                        value = error.get(name)
                        if (isinstance(value, str) and len(value) <= 128
                            and re.fullmatch(r"[A-Za-z0-9_./\[\]-]+", value)
                            and key not in value and not value.startswith("sk-")):
                            details["provider_error_" + name] = value
        except Exception:
            pass
        finally:
            exc.close()
        raise HostedError(code, "Provider returned HTTP " + str(exc.code), details=details) from None
    except HostedError:
        raise
    except (ValueError, UnicodeError):
        raise HostedError("PROVIDER_RESPONSE_INVALID", "Provider returned an invalid response envelope") from None
    except OSError:
        raise HostedError("PROVIDER_REQUEST_FAILED", "The bounded provider request did not return a usable envelope") from None


def _money(value, name):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise HostedError("BUDGET_GUARD", name + " must be a finite nonnegative USD amount")
    return Decimal(str(value))


def estimate_cost(durations, text, *, max_output_tokens=600, model=MODEL, request=None):
    pricing = pricing_for_model(model)
    from ..auditory_profiles import request_policy, validate_audio_bounds
    policy = request_policy(request)
    validate_audio_bounds(durations, policy)
    if type(max_output_tokens) is not int or not 1 <= max_output_tokens <= 4000 or not isinstance(text, str):
        raise AVError("Invalid bounded text-output estimate")
    # Chat Completions audio tokenization is not promised to equal Realtime's.
    # Use a deliberately conservative, explicit planning assumption, then settle
    # from provider-reported usage. UTF-8 bytes overestimate ordinary text tokens.
    audio_tokens = sum(math.ceil(x * 50) + 64 for x in durations)
    text_tokens = len(text.encode("utf-8")) + 128 + 32 * len(durations)
    rates = pricing["usd_per_million"]
    cost = (audio_tokens * rates["input_audio"] + text_tokens * rates["input_text"]
            + max_output_tokens * rates["output_text"]) / 1000000
    return {"status": "PLANNING_ESTIMATE", "audio_seconds": sum(durations), "clip_seconds": durations,
        "estimated_input_audio_tokens": audio_tokens, "estimated_input_text_tokens": text_tokens,
        "max_output_tokens": max_output_tokens, "estimated_cost_usd": round(cost, 9),
        "pricing_snapshot": pricing, "audio_policy": policy,
        "assumptions": {"audio_tokens_per_second": 50, "audio_overhead_tokens_per_clip": 64,
                        "text_token_estimator": "UTF-8 bytes plus message overhead; not a tokenizer or billing promise"},
        "billing_authority": "Provider-reported tokens; calculated costs remain distinct from an invoice"}


def usage_cost(usage, *, model=MODEL):
    """Only reconcile a complete, internally consistent token breakdown."""
    if not isinstance(usage, dict):
        return None
    try:
        p, c, total = (usage[k] for k in ("prompt_tokens", "completion_tokens", "total_tokens"))
        pa = usage["prompt_tokens_details"]["audio_tokens"]
        ca = usage["completion_tokens_details"]["audio_tokens"]
        if any(type(x) is not int or x < 0 for x in (p, c, total, pa, ca)) or total != p + c or pa > p or ca > c:
            return None
    except (KeyError, TypeError):
        return None
    r = pricing_for_model(model)["usd_per_million"]
    return round((pa*r["input_audio"] + (p-pa)*r["input_text"]
                  + ca*r["output_audio"] + (c-ca)*r["output_text"])/1000000, 9)


def safe_usage(value):
    """Keep only bounded numeric usage fields, not arbitrary provider dictionaries."""
    if not isinstance(value, dict):
        return None
    result = {}
    for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
        x = value.get(key)
        if type(x) is int and 0 <= x <= 10**9:
            result[key] = x
    for key, names in (("prompt_tokens_details", ("audio_tokens", "cached_tokens", "text_tokens")),
                       ("completion_tokens_details", ("audio_tokens", "text_tokens", "reasoning_tokens"))):
        detail = value.get(key)
        if isinstance(detail, dict):
            result[key] = {k: detail[k] for k in names if type(detail.get(k)) is int and 0 <= detail[k] <= 10**9}
    return result or None


@contextmanager
def _ledger_lock(path):
    lock = Path(str(path) + ".lock")
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise HostedError("BUDGET_GUARD", "The usage ledger is locked; concurrent submission is refused") from None
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def _rows(path):
    if not path.exists():
        return []
    if path.is_symlink() or path.stat().st_size > 16 * 1024 * 1024:
        raise HostedError("BUDGET_GUARD", "Usage ledger is unsafe or exceeds the bounded reader")
    try:
        with path.open(encoding="utf-8") as stream:
            return [strict_json(line) for line in stream if line.strip()]
    except (ValueError, OSError):
        raise HostedError("BUDGET_GUARD", "Usage ledger cannot be reconciled; no request sent") from None


def _account(rows, *, queue_limit):
    reserved, settled = {}, {}
    for row in rows:
        if not isinstance(row, dict) or row.get("schema") != "ave.provider-usage.v1" or row.get("queue_limit_usd") != float(queue_limit):
            raise HostedError("BUDGET_GUARD", "Existing ledger schema or queue budget differs; automatic budget changes are refused")
        op = row.get("operation_id")
        if not isinstance(op, str):
            raise HostedError("BUDGET_GUARD", "Invalid usage operation identity")
        if row.get("event") == "RESERVED" and op not in reserved:
            reserved[op] = _money(row.get("estimated_cost_usd"), "Reserved cost")
        elif row.get("event") == "COMPLETED" and op in reserved and op not in settled:
            value = row.get("actual_cost_usd")
            settled[op] = None if value is None else _money(value, "Reported cost")
        else:
            raise HostedError("BUDGET_GUARD", "Ledger events do not form unique reserve/completion pairs")
    unknown = any(op not in settled or settled[op] is None for op in reserved)
    used = sum((settled.get(op) if settled.get(op) is not None else amount for op, amount in reserved.items()), Decimal(0))
    return used, unknown


def _append(path, row):
    encoded = json.dumps(row, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(encoded)
        stream.flush()
        os.fsync(stream.fileno())


class BudgetSession:
    def __init__(self, run_dir, *, budget_usd=.50, request_budget_usd=.10, queue_ledger=None, queue_budget_usd=2., model=MODEL):
        self.model = model
        self.pricing = pricing_for_model(model)
        self.run_dir = Path(run_dir).resolve()
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.local = self.run_dir / "provider-usage.jsonl"
        self.run_limit = _money(budget_usd, "Run budget")
        self.request_limit = _money(request_budget_usd, "Request budget")
        self.queue_limit = _money(queue_budget_usd, "Queue budget")
        self.shared = Path(queue_ledger).resolve() if queue_ledger else None
        if self.shared:
            if self.shared == self.local or self.shared.is_relative_to(self.run_dir) or self.shared.is_symlink():
                raise HostedError("BUDGET_GUARD", "Shared queue ledger must be outside the immutable observation run")
            self.shared.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = uuid.uuid4().hex
        self.tickets = {}

    def reserve(self, estimate, *, request_id, identity, clips, purpose):
        if identity.get("model_revision") != self.model or estimate.get("pricing_snapshot") != self.pricing:
            raise HostedError("BUDGET_GUARD", "Model, estimate and accounting tariff differ; no request sent")
        amount = _money(estimate["estimated_cost_usd"], "Estimated cost")
        if amount > self.request_limit:
            raise HostedError("BUDGET_GUARD", "Estimated request cost exceeds the request budget")
        paths = [self.local] + ([self.shared] if self.shared else [])
        from contextlib import ExitStack
        with ExitStack() as stack:
            for path in sorted(paths, key=str):
                stack.enter_context(_ledger_lock(path))
            for path in paths:
                rows = _rows(path)
                if path == self.local and any(r.get("run_limit_usd") != float(self.run_limit) for r in rows):
                    raise HostedError("BUDGET_GUARD", "Existing run budget differs; automatic budget changes are refused")
                used, unknown = _account(rows, queue_limit=self.queue_limit)
                if unknown:
                    raise HostedError("BUDGET_GUARD", "Unresolved provider usage is retained; reconcile it before further spending")
                limit = self.run_limit if path == self.local else self.queue_limit
                if used + amount > limit:
                    raise HostedError("BUDGET_GUARD", "Estimated next request exceeds the remaining run or queue budget")
                if path == self.local and used + amount > self.queue_limit:
                    raise HostedError("BUDGET_GUARD", "Estimated next request exceeds the queue budget")
            ticket = {"schema": "ave.provider-usage.v1", "event": "RESERVED", "operation_id": uuid.uuid4().hex,
                "run_id": self.run_id, "request_id": request_id, "purpose": purpose, "provider": "openai",
                "model": identity["model_revision"], "backend_identity": identity, "clips": clips,
                "audio_seconds": estimate["audio_seconds"], "estimated_cost_usd": float(amount),
                "estimated_token_counts": {"input_audio_tokens": estimate["estimated_input_audio_tokens"],
                    "input_text_tokens": estimate["estimated_input_text_tokens"], "maximum_output_text_tokens": estimate["max_output_tokens"]},
                "actual_cost_usd": None, "provider_reported_usage": None, "pricing_snapshot": self.pricing,
                "queue_limit_usd": float(self.queue_limit), "run_limit_usd": float(self.run_limit),
                "request_limit_usd": float(self.request_limit), "created_at": utc_now(), "completed_at": None}
            for path in paths:
                _append(path, ticket)
        self.tickets[ticket["operation_id"]] = ticket
        return ticket

    def settle(self, ticket, *, usage, status, returned_model=None):
        if ticket["operation_id"] not in self.tickets:
            raise HostedError("BUDGET_GUARD", "Unknown or already completed usage operation")
        reported = safe_usage(usage)
        cost = usage_cost(reported, model=self.model)
        tokens = None
        if cost is not None:
            audio_in = reported["prompt_tokens_details"]["audio_tokens"]
            audio_out = reported["completion_tokens_details"]["audio_tokens"]
            tokens = {"input_audio_tokens": audio_in, "input_text_tokens": reported["prompt_tokens"]-audio_in,
                      "output_audio_tokens": audio_out, "output_text_tokens": reported["completion_tokens"]-audio_out}
        row = dict(ticket, event="COMPLETED", completed_at=utc_now(), status=status,
                   provider_reported_usage=reported, actual_cost_usd=cost, returned_model=returned_model,
                   reported_token_counts=tokens,
                   billing_status="USAGE_REPORTED_COST_CALCULATED" if cost is not None else "UNKNOWN_RESERVATION_RETAINED",
                   cost_basis="Published uncached standard rates applied to reported tokens; not an invoice")
        for path in [self.local] + ([self.shared] if self.shared else []):
            with _ledger_lock(path):
                _append(path, row)
        self.tickets.pop(ticket["operation_id"])
        return row

    def summary(self):
        rows = _rows(self.local)
        used, unknown = _account(rows, queue_limit=self.queue_limit)
        settled = [row for row in rows if row["event"] == "COMPLETED"]
        return {"schema": "ave.provider-cost-summary.v1", "operations": len([r for r in rows if r["event"] == "RESERVED"]),
            "completed_operations": len(settled), "estimated_cost_usd": sum(r["estimated_cost_usd"] for r in rows if r["event"] == "RESERVED"),
            "actual_cost_usd": None if unknown else round(float(used), 9), "committed_or_reserved_usd": round(float(used), 9),
            "remaining_run_budget_usd": max(0., round(float(self.run_limit-used), 9)), "billing_unresolved": unknown,
            "pricing_snapshot": self.pricing, "scope": "Local guard and calculated cost; no account balance access"}
