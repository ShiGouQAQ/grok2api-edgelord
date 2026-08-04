# Wave-1-G Report — media audit (c936ab1) + image error handling (8004840)

**Status:** DONE
**Commit:** 762c64f2 (baseline 21a5c08b)
**Tests:** 1402 passed / 1 skipped / 0 failed (full suite); 74 passed across the 3 owned test files

## Ported

### c936ab1 — media audit (greenfield, `app/platform/storage/media_audit.py`)
- `summarize_response_media(body: bytes) -> dict | None` — `b"image"` pre-filter returns `None` BEFORE any JSON decode (hot-path guard, verified by monkeypatched-`json.loads` test). Returns `{input_images, image_bytes, content_arrays, text_bytes}`.
- Faithful walk port: root `input`/`messages` arrays, chat + anthropic blocks, `tool_result` nesting, data-URI/base64 byte estimation (`decoded_base64_bytes` ported with the Go edge-case table).
- `log_response_media_summary` — DEBUG only when `input_images > 0` (never on ContentArray alone); logs counts only, no payload content.
- `is_function_call_output_content_array` — any block type starting `input_` ⇒ strict content-array validation (mixed arrays no longer silently stringified).
- `normalize_function_call_output_input` — content-array vs `encodeToolOutput` JSON-string fallback; `input_text`/`input_image`/`input_file` block validation.
- `normalize_input_image_part` — auto/low/high accepted; `original` → `high` + warning (Build 0.2.103); others rejected; `url`→`image_url` alias; `file_id` preserved.
- Wired at DEBUG into images.py (`generate`/`_generate_lite`/`edit`) and video.py (`create_video`/`completions`).

### 8004840 — image error handling (`generate()` non-streaming, L441+)
- `last_credential_error` recorded on every retryable upstream/credential failure in the attempt loop.
- Final-attempt retryable failure now falls through (non-retryable errors still raise immediately, unchanged) to a post-loop block: logs `image_generation_unavailable status=503 code=upstream_unavailable` + raises `UpstreamError(..., status=503)` wrapping the last credential error (falls back to `RateLimitError` no-account message, matching Go's `ErrNoAvailableAccount`). Loop structure untouched (Wave-2 F refactors it).

## Concerns
- The tool-output/image-part normalizers have **no call site in images.py/video.py** (those paths never see `function_call_output`). They are standalone exports in `media_audit.py` with full tests, ready for Wave-1 B's `responses_input.py`/`responses.py` to consume — do NOT duplicate the logic there.
- Pre-existing LSP noise (dict invariance, `AccountDirectory.release` typing) in untouched images.py lines — not introduced here.
- `summarize_response_media` is case-sensitive like Go (`ImageContent` misses the guard ⇒ `None`) — verified by test.
