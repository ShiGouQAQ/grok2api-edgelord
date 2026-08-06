"""Audit record domain model (port of Go ``domain/audit.Record``).

Timestamps are epoch ms (codebase convention); the DTO layer converts to ISO.
The Go model carries rich per-attempt diagnostics; the Python hot path records
one gateway-level attempt per request (enrichment is a TODO, see router hook).
"""

from dataclasses import dataclass, field

# 1 USD = 1_000_000 ticks (Go billing tick convention)
USD_TICKS_PER_DOLLAR = 1_000_000


@dataclass
class AuditAttempt:
    number: int = 1
    source: str = "upstream_http"  # upstream_http | gateway_transport | credential
    stage: str = "gateway"
    method: str = ""
    request_path: str = ""
    upstream_url: str = ""
    started_at: int = 0  # epoch ms
    duration_ms: int = 0
    upstream_status_code: int | None = None
    upstream_status: str = ""
    transport_error: str = ""


@dataclass
class AuditRecord:
    id: int = 0
    request_id: str = ""
    client_key_id: int | None = None
    client_key_name: str = ""
    model: str = ""
    provider: str = ""  # grok_web | grok_console | grok_build
    operation: str = ""  # chat | responses | image | video | messages
    status_code: int = 200
    streaming: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    total_tokens: int = 0
    cost_in_usd_ticks: int = 0  # upstream-reported cost (unavailable in port)
    estimated_cost_in_usd_ticks: int = 0  # 0 in port; pricing port is a TODO
    first_token_ms: int | None = None
    duration_ms: int = 0
    error_code: str = ""
    attempt_count: int = 1
    attempts: list[AuditAttempt] = field(default_factory=list)
    created_at: int = 0  # epoch ms


__all__ = ["AuditRecord", "AuditAttempt", "USD_TICKS_PER_DOLLAR"]
