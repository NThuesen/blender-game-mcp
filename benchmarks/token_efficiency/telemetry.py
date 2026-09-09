"""Conservative usage accounting and single-writer, append-only trial journal.

Never infer missing token categories as zero. Reasoning is a subset of output,
not an additional billable category. Journal files are local trusted inputs.
"""
from collections import Counter
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import re
import time

TOKEN_FIELDS = ("uncached_input", "cached_input", "cache_creation", "output", "reasoning")
FAILURE_CATEGORIES = ("planning", "api_misuse", "execution", "timeout", "validation", "visual_mismatch")


def encoded(value):
    """Canonical UTF-8 representation used for hashes and byte accounting."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else encoded(value)).hexdigest()


def redact(value):
    """Defense in depth, not a guarantee for arbitrary model-generated secrets.

    Never pass credentials/headers/environment here. Unknown usage fields are
    discarded separately; persisted transcript text must originate in fixtures
    until a live-run redaction boundary is reviewed.
    """
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if re.search(
            r"authorization|api[_-]?key|password|secret|access_token", key, re.I
        ) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"(?i)(?:bearer\s+\S+|(?<![\w-])sk-[a-z0-9_-]+)", "[REDACTED]", value)
    return value


def sanitized_config(config):
    """Sanitize designated fixture text BEFORE hashing; never rewrite IDs/paths.

    Other config fields are operational, not arbitrary provider payloads. Live
    inputs/credentials remain forbidden until their boundary is reviewed.
    """
    result = copy.deepcopy(config)
    for key in ("canonical_prompt", "policy"):
        if key in result:
            result[key] = redact(result[key])
    contract = result.get("task_contract")
    if isinstance(contract, dict) and "prompt" in contract:
        contract["prompt"] = redact(contract["prompt"])
    return json.loads(encoded(result))


def sanitized_record(record):
    """Prepare explicit payload fields, leaving operational values verbatim.

    Does not recompute identity: callers must sanitize config before hashing.
    Validator diagnostic messages are text; paths, codes and hashes are not.
    """
    result = copy.deepcopy(record)
    if isinstance(result.get("config"), dict):
        result["config"] = sanitized_config(result["config"])
    if "resolved_prompt" in result:
        result["resolved_prompt"] = redact(result["resolved_prompt"])

    def messages(value):
        if isinstance(value, dict):
            return {key: redact(item) if key == "message" else messages(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [messages(item) for item in value]
        return value

    if "validator" in result:
        result["validator"] = messages(result["validator"])
    return json.loads(encoded(result))


def _count(value):
    return value if type(value) is int and value >= 0 else None


def normalize_usage(provider, usage):
    """Provider-reported counts; OpenAI prompt total includes cached tokens."""
    result: dict[str, int | None] = dict.fromkeys(TOKEN_FIELDS)
    usage = usage if isinstance(usage, dict) else {}
    if provider == "openai":
        prompt = _count(usage.get("prompt_tokens"))
        details = usage.get("prompt_tokens_details") or {}
        cached = _count(details.get("cached_tokens")) if isinstance(details, dict) else None
        if cached is not None and prompt is not None and cached <= prompt:
            result["uncached_input"] = prompt - cached
            result["cached_input"] = cached
        result["output"] = _count(usage.get("completion_tokens"))
        details = usage.get("completion_tokens_details") or {}
        if isinstance(details, dict):
            result["reasoning"] = _count(details.get("reasoning_tokens"))
    elif provider == "claude":
        for target, source in (("uncached_input", "input_tokens"),
                               ("cached_input", "cache_read_input_tokens"),
                               ("cache_creation", "cache_creation_input_tokens"),
                               ("output", "output_tokens")):
            result[target] = _count(usage.get(source))
    else:
        raise ValueError("unsupported provider")
    return result


def safe_usage(usage):
    """Preserve known numeric raw usage and nulls, never arbitrary provider text."""
    keys = set(TOKEN_FIELDS) | {
        "prompt_tokens", "completion_tokens", "total_tokens", "input_tokens", "output_tokens",
        "cache_read_input_tokens", "cache_creation_input_tokens", "cached_tokens", "reasoning_tokens",
        "prompt_tokens_details", "completion_tokens_details", "cache_creation",
        "ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens",
    }
    if not isinstance(usage, dict):
        return None
    return {key: safe_usage(value) if isinstance(value, dict) else _count(value)
            for key, value in usage.items() if key in keys}


def estimate_cost(tokens, rates, model):
    """Explicit caller-supplied versioned rates only; never actual billing."""
    if not rates or any(not rates.get(key) for key in ("version", "source", "currency")):
        return None
    if rates.get("model") != model:
        return None
    prices = rates.get("per_million", {})
    categories = TOKEN_FIELDS[:-1]
    if any(tokens.get(key) is None or type(prices.get(key)) not in (int, float)
           or not math.isfinite(prices[key]) or prices[key] < 0 for key in categories):
        return None
    return {"amount": sum(tokens[key] * prices[key] for key in categories) / 1_000_000,
            "currency": rates["currency"], "estimated": True, "billing": False,
            "rates": rates, "method": "provider counts times explicit per-million rates"}


class Telemetry:
    """One fresh instance per trial; tools following errors are observable repairs.

    A repair call is the next invocation of the SAME tool after that tool returned
    an error, regardless of arguments or intervening tools. It is not inferred
    model intent. Durations use a monotonic clock and exclude validator time.
    """
    def __init__(self, provider, model, clock=time.monotonic):
        self.provider, self.model, self.clock = provider, model, clock
        self.started = clock()
        self.responses = []
        self.models = []
        self.counts = Counter()
        self.pending_repairs = set()
        self.tool_errors = self.api_errors = self.repairs = 0
        self.tool_seconds = 0.0
        self.tool_bytes = self.images = self.image_bytes = 0
        self.failure_categories = []

    def api_response(self, response):
        self.responses.append(safe_usage(response.get("usage")))
        self.models.append(response.get("model"))

    def failure(self, category, *, api=False):
        if category not in FAILURE_CATEGORIES:
            raise ValueError("unknown failure category")
        self.failure_categories.append(category)
        self.api_errors += int(api)

    def tool_result(self, name, result, *, duration, error=False, images=()):
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("invalid duration")
        self.counts[name] += 1
        if name in self.pending_repairs:
            self.repairs += 1
            self.pending_repairs.remove(name)
        if error:
            self.tool_errors += 1
            self.pending_repairs.add(name)
        self.tool_seconds += duration
        self.tool_bytes += len(result.encode("utf-8") if isinstance(result, str) else encoded(result))
        for image in images:
            if not isinstance(image, bytes):
                raise TypeError("images must be decoded bytes")
            self.images += 1
            self.image_bytes += len(image)

    def finish(self, transcript, schemas, rates=None):
        usages = [normalize_usage(self.provider, usage) for usage in self.responses]
        tokens = {key: sum(value for usage in usages if (value := usage[key]) is not None)
                  if usages and all(usage[key] is not None for usage in usages) else None
                  for key in TOKEN_FIELDS}
        returned = list(dict.fromkeys(self.models))
        return {
            "requested_model": self.model, "returned_models": returned,
            "provider": self.provider, "tokens": tokens, "raw_usage_redacted": self.responses,
            "turns": len(self.responses), "api_attempts": len(self.responses) + self.api_errors,
            "tool_counts": dict(self.counts), "tool_errors": self.tool_errors,
            "api_errors": self.api_errors, "repair_calls": self.repairs,
            "repair_definition": "next same-tool invocation after that tool errors",
            "failure_categories": self.failure_categories,
            "wall_seconds": self.clock() - self.started, "tool_seconds": self.tool_seconds,
            "subprocess_seconds": None,
            "transcript_utf8_bytes": len(encoded(redact(transcript))),
            "transcript_byte_method": "canonical redacted JSON UTF-8, stored once, not wire bytes",
            "tool_schema_bytes": len(encoded(redact(schemas))),
            "tool_result_bytes": self.tool_bytes,
            "image_count": self.images, "image_bytes": self.image_bytes,
            "image_byte_method": "decoded payload bytes, excludes base64 overhead",
            "resources": {"cpu_seconds": None, "peak_rss": None, "unit": None,
                          "method": "unavailable; no child attribution", "platform": platform.platform()},
            "cost": estimate_cost(tokens, rates, returned[0]) if len(returned) == 1 else None,
        }


def valid_record(record):
    """Shared structural/provenance gate for append, resume, validate and report.

    Hash integrity detects damaged local records, not malicious rewriting of both
    config and hash. Artifact bytes and fresh correctness remain resume/validate
    checks, so historical reporting does not require artifacts to still exist.
    """
    if not isinstance(record, dict) or record.get("status") != "complete":
        return False
    identity = record.get("identity")
    config = record.get("config")
    validator = record.get("validator")
    artifacts = record.get("artifacts")
    telemetry = record.get("telemetry")
    if not (isinstance(identity, dict)
            and all(isinstance(identity.get(key), str) and identity[key]
                    for key in ("task", "condition"))
            and type(identity.get("trial")) is int and identity["trial"] > 0
            and all(isinstance(identity.get(key), str)
                    and re.fullmatch(r"[0-9a-f]{64}", identity[key])
                    for key in ("config_hash", "source_hash"))
            and isinstance(config, dict)
            and type(record.get("synthetic")) is bool
            and type(config.get("synthetic")) is bool
            and record["synthetic"] is config["synthetic"]
            and isinstance(record.get("attempt_root"), str) and record["attempt_root"]
            and isinstance(telemetry, dict)
            and isinstance(telemetry.get("tokens"), dict)
            and all(key in telemetry["tokens"] and (telemetry["tokens"][key] is None
                    or _count(telemetry["tokens"][key]) is not None) for key in TOKEN_FIELDS)
            and type(telemetry.get("wall_seconds")) in (int, float)
            and telemetry["wall_seconds"] >= 0
            and isinstance(validator, dict) and type(validator.get("ok")) is bool
            and isinstance(validator.get("errors"), list) and isinstance(validator.get("details"), dict)
            and isinstance(artifacts, dict) and bool(artifacts)
            and all(isinstance(path, str) and path and isinstance(value, str)
                    and re.fullmatch(r"[0-9a-f]{64}", value)
                    for path, value in artifacts.items())):
        return False
    try:
        # Reject non-JSON/non-finite values even for direct aggregate callers.
        encoded(record)
        return digest(config) == identity["config_hash"]
    except (TypeError, ValueError, RecursionError):
        return False


class Journal:
    """Single-writer JSONL. fsync each record; never truncate a damaged tail.

    An interrupted non-newline tail is fenced off with a newline on next append.
    Corrupt/incomplete lines remain as evidence and are returned as diagnostics.
    Concurrent writers are unsupported. Failed trials are retained but retried
    into new attempt directories; resume requires a fresh passing validator.
    """
    def __init__(self, path):
        self.path = Path(path)

    def append(self, record):
        if not valid_record(record):
            raise ValueError("not a complete trial record")
        payload = encoded(record)
        persisted = json.loads(payload)
        if not valid_record(persisted) or encoded(sanitized_record(persisted)) != payload:
            raise ValueError("record must be sanitized before identity calculation and append")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell():
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    handle.write(b"\n")
            handle.write(payload + b"\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read(self):
        records, errors = [], []
        if not self.path.exists():
            return records, errors
        with self.path.open("rb") as handle:
            for number, line in enumerate(handle, 1):
                try:
                    record = json.loads(line)
                    if not line.endswith(b"\n") or not valid_record(record):
                        raise ValueError("incomplete or invalid record")
                    records.append(record)
                except (ValueError, UnicodeError, RecursionError):
                    errors.append({"line": number, "error": "corrupt/incomplete record"})
        return records, errors

    def resumable(self, identity, validate):
        records, _ = self.read()
        matches = [record for record in records if record["identity"] == identity]
        if not matches:
            return False
        record = matches[-1]
        if not record["validator"]["ok"]:
            return False
        try:
            if any(digest(Path(path).read_bytes()) != expected
                   for path, expected in record["artifacts"].items()):
                return False
            result = validate(record)
            return (result.get("ok") is True and result.get("errors") == []
                    and isinstance(result.get("details"), dict))
        except (OSError, ValueError, KeyError, TypeError):
            return False
