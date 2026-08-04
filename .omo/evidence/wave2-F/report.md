# Wave2-F Report — Routing Attempt Policy Port (15146556 + 72340380)

**Status:** ✅ 完成
**Branch:** `main`
**Python commit:** `626595ed` — `feat(routing): port 15146556+72340380 routing attempt policy`

## What was ported

Go `routingAttemptPolicy` / `newRoutingAttemptPolicy` (backend/internal/application/gateway/service.go,
commits 15146556 + 72340380) plus the `routing.maxAttempts` validation (config.go:
allows -1, rejects 0 and >200).

- **`app/products/_routing_policy.py`** (new, 105 lines):
  - `RoutingAttemptPolicy` (frozen dataclass): `allows(attempt)` / `has_next(attempt)`
    + `total_attempts` / `retry_budget` log-denominator properties.
  - `new_routing_attempt_policy(configured)`: -1 → unlimited, ≤0 → 3, else limit
    (faithful Go port).
  - `routing_attempt_policy(legacy_retries)`: reads `routing.max_routing_attempts`;
    validation ValueError on < -1 / 0 / >200 (Go `Config.Validate` parity).
- **10 product files** converted (`for attempt in range(max_retries + 1)` →
  `for attempt in count(): if not policy.allows(attempt): break`):
  openai/chat.py, build_chat.py, console_chat.py, responses.py, build_responses.py,
  console_responses.py, images.py; anthropic/messages.py, build_messages.py,
  console_messages.py. `attempt < max_retries` → `policy.has_next(attempt)`
  (semantically identical: limit == old max_retries+1); log denominators →
  `total_attempts` / `retry_budget`. `_should_retry_upstream` untouched.
- **Stored Responses**: `responses.create()` gains `previous_response_id` param;
  when set → `new_routing_attempt_policy(1)` (Go `ownership != nil`), single attempt.
- **config.defaults.toml**: new `[routing]` section (`max_routing_attempts = 200`,
  `unlimited_routing_attempts = -1`).
- **tests/test_routing_policy.py** (new, 16 tests): unlimited sentinel, ≤0→3 fallback,
  cap bound, config resolution (200 explicit, -1, invalid 0/201/-2 raise), unset-config
  legacy preservation (random 5 retries → 6 attempts, quota 1 retry → 2 attempts),
  shipped-default sentinel, stored-responses single attempt, non-stored legacy budget.

## Key design decisions

1. **Shipped-default sentinel**: defaults.toml always merges `max_routing_attempts = 200`
   into the config snapshot, so `routing_attempt_policy()` treats the shipped value
   (200) as "not overridden" and falls back to the legacy strategy-aware budget
   (`selection_max_retries()` + 1). This is required by "default config must preserve
   existing behavior (5 random / 1 quota)" — without it, pre-existing
   `test_image_edit.py::test_credential_failures_log_503_and_wrap_last_error`
   (monkeypatches `selection_max_retries` → 1, expects 2 attempts) regresses as soon
   as the config singleton is loaded by earlier tests (e.g. test_config.py). Only
   explicit non-default values (1..199, 201 invalid, -1) activate config routing.
2. **Product loops pass their module-level `selection_max_retries()`** into
   `routing_attempt_policy()` so per-module monkeypatching (test_image_edit pattern)
   keeps working.
3. **video.py has no retry loop** — the plan's "video.py main retry loop" does not
   exist (single reservation in `_run_video_with_account`); nothing to convert.
   `app/control/proxy/__init__.py:493` is a mihomo clearance-refresh retry (hardcoded
   3, proxy domain) — out of scope, left untouched.
4. **build_responses.py** (2 loops) was converted too — grep found it beyond the
   plan's listed sites ("find every match and convert each").

## Test results

```
$ uv run pytest tests/test_regression.py tests/test_routing_policy.py -q --timeout=30
21 passed, 1 warning in 1.48s

$ uv run pytest tests/ -q --timeout=30
1467 passed, 1 skipped, 1 warning, 6 subtests passed in 17.47s
```

Full suite green (baseline 1402 passed 1 skipped + Wave-1 additions + my 16).
`tests/test_build_detect.py` / `test_build_refresh_routing.py` failed intermittently
during the session — they are **untracked Wave-1 in-flight work** (committed later by
the Wave-1 agent as part of their build-detection commit) exercising
`app/control/account/build_detect.py` (real SSO→Build mint proxy semantics, the known
flake documented in CLAUDE.md); they import none of my files. Their failures were
absent in the final run.

## History note (concurrency)

While this task ran, the Wave-1 build agent committed a blanket `git add -A` commit
that swept up my uncommitted routing changes. Since nothing was pushed (branch 8
ahead of origin, all local), the commit was split non-destructively:

- `626595ed` feat(routing): port 15146556+72340380 routing attempt policy (this task)
- `4116c3c4` feat(build): port bcc6435f/ef10c4cb/34811392/b4c7baab detection+retry
  (recreated with the original message; content identical minus this task's files)

## Concerns

- Setting `max_routing_attempts` to exactly the shipped default (200) still means
  "legacy budget" (5/1) — the merged-config snapshot cannot distinguish "shipped
  default" from "user explicitly set 200". Documented in `_routing_policy.py`
  (`_SHIPPED_DEFAULT_ATTEMPTS`). Values 1..199 / -1 behave as configured.
- `routing.unlimited_routing_attempts` in defaults.toml is documentation-only
  (Go keeps it a const, not config); the code uses the `-1` constant.
