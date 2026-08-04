"""Build Team+Model rate-limit metadata parser.

Port of chenyme/grok2api ``infra/provider/rate_limit.go`` (d00698ac):
extracts RPS/RPM limits, team id, model name and reset window from an
upstream 429 body, and back-fills a ``Retry-After`` header when the body
carries a reset window but the response header does not.

Default cool-downs when no reset window is present: RPS → 2s, RPM → 60s
(RPS floors at 2s even when a shorter window is parsed).
"""

from dataclasses import dataclass
import re

_RATE_LIMIT_USAGE_RE = re.compile(
    r"(?i)\bRequests?\s+per\s+(Second|Minute)\s*\(\s*actual\s*/\s*limit\s*\)\s*:\s*(\d+)\s*/\s*(\d+)"
)
_RATE_LIMIT_TEAM_RE = re.compile(
    r"(?i)\bteam\s+([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\b"
)
_RATE_LIMIT_MODEL_RE = re.compile(r"(?i)\bmodel\s+[\"']?([A-Za-z0-9][A-Za-z0-9._:/-]*)")
_RATE_LIMIT_MODEL_TRIM_CHARS = ".,;"
_RATE_LIMIT_RESET_RE = re.compile(r"(?i)(\d+)\s*([dhms])")

_RPS_DEFAULT_SECONDS = 2.0
_RPM_DEFAULT_SECONDS = 60.0
_SECONDS_PER_UNIT = {"d": 86_400.0, "h": 3_600.0, "m": 60.0, "s": 1.0}


@dataclass
class RateLimitMetadata:
    """Team+Model rate limit parsed from an upstream 429 body.

    ``requests_per_second`` / ``requests_per_minute`` hold the measured
    ("actual") rate from the ``actual/limit`` pair; only the matched scope
    is set. ``resets_in_seconds`` is the cool-down (body reset window when
    present, else the scope default, RPS floored at 2s).
    """

    requests_per_second: float | None
    requests_per_minute: float | None
    team_id: str
    model: str
    resets_in_seconds: float | None


def parse_rate_limit_metadata(body: str) -> RateLimitMetadata | None:
    """Extract RPS/RPM limit metadata from an upstream 429 body, or ``None``.

    Accepts both Console and Build CLI resource-exhausted text shapes.
    """
    match = _RATE_LIMIT_USAGE_RE.search(body)
    if match is None:
        return None
    is_second = match.group(1).lower() == "second"
    actual = float(match.group(2))
    resets = _rate_limit_resets_in_seconds(body)
    if resets is None:
        resets = _RPS_DEFAULT_SECONDS if is_second else _RPM_DEFAULT_SECONDS
    elif is_second and resets < _RPS_DEFAULT_SECONDS:
        resets = _RPS_DEFAULT_SECONDS
    return RateLimitMetadata(
        requests_per_second=actual if is_second else None,
        requests_per_minute=actual if not is_second else None,
        team_id=_rate_limit_team_id(body),
        model=_rate_limit_model(body),
        resets_in_seconds=resets,
    )


def rate_limit_from_response(
    status: int,
    headers: dict[str, str],
    body: str,
) -> RateLimitMetadata | None:
    """Derive rate-limit metadata from a 429 status, headers, and body.

    When ``Retry-After`` is absent but the body carries a reset window, the
    header is back-filled in place.
    """
    if status != 429:
        return None
    metadata = parse_rate_limit_metadata(body)
    if metadata is None:
        return None
    retry_after = _header_retry_after_seconds(headers)
    if retry_after is not None:
        metadata.resets_in_seconds = float(retry_after)
    elif metadata.resets_in_seconds is not None:
        headers["Retry-After"] = str(int(metadata.resets_in_seconds))
    return metadata


def _rate_limit_team_id(text: str) -> str:
    match = _RATE_LIMIT_TEAM_RE.search(text)
    return match.group(1) if match else ""


def _rate_limit_model(text: str) -> str:
    match = _RATE_LIMIT_MODEL_RE.search(text)
    if match is None:
        return ""
    return match.group(1).rstrip(_RATE_LIMIT_MODEL_TRIM_CHARS)


def _rate_limit_resets_in_seconds(text: str) -> float | None:
    index = text.lower().find("resets in:")
    if index < 0:
        return None
    total = 0.0
    for match in _RATE_LIMIT_RESET_RE.finditer(text[index + len("resets in:") :]):
        total += float(match.group(1)) * _SECONDS_PER_UNIT[match.group(2).lower()]
    return total if total > 0 else None


def _header_retry_after_seconds(headers: dict[str, str]) -> float | None:
    from email.utils import parsedate_to_datetime
    from datetime import datetime, timezone

    value = ""
    for key, candidate in headers.items():
        if key.lower() == "retry-after":
            value = str(candidate)
            break
    value = value.strip()
    if not value:
        return None
    try:
        seconds = float(value)
        return seconds if seconds > 0 else None
    except ValueError:
        pass
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        delta = parsed - datetime.now(timezone.utc)
        return delta.total_seconds() if delta.total_seconds() > 0 else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "RateLimitMetadata",
    "parse_rate_limit_metadata",
    "rate_limit_from_response",
]
