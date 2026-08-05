"""Media audit helpers — port of chenyme/grok2api c936ab1.

Ports ``backend/internal/application/gateway/response_media_audit.go``
(summarize_response_media / log_response_media_summary) plus the Responses
tool-output normalization contracts from ``responses_codex_tools.go`` and
``responses_input.go`` (is_function_call_output_content_array,
normalize_function_call_output_input, normalize_input_image_part).

The summary is a DEBUG-level audit of *input* media in a request body:
counts of image blocks, estimated image bytes, content arrays and text bytes.
It never logs payload contents — only counts — and skips JSON decoding
entirely when the body does not even contain the token ``image``.
"""

import orjson
from collections.abc import Mapping, Sequence
from typing import Protocol, cast

from app.platform.logging.logger import logger


class AuditLogger(Protocol):
    """Minimal logger surface the audit helpers rely on."""

    def debug(self, message: str, /, *args: object, **kwargs: object) -> None: ...


_SUMMARY_KEYS = ("input_images", "image_bytes", "content_arrays", "text_bytes")

# Responses content-block types whose blocks begin with this prefix.
_INPUT_BLOCK_PREFIX = "input_"


def _new_summary() -> dict[str, int]:
    return {key: 0 for key in _SUMMARY_KEYS}


def _add(target: dict[str, int], other: dict[str, int]) -> None:
    for key in _SUMMARY_KEYS:
        target[key] += other[key]


# ---------------------------------------------------------------------------
# Response media summary
# ---------------------------------------------------------------------------


def may_contain_response_media(body: bytes) -> bool:
    """Low-cost pre-filter for the inference hot path.

    Pure-text requests never create a JSON decoder; a hit only means a
    precise scan is worthwhile — the final stats still come from the
    structured parse.
    """
    return b"image" in body


def summarize_response_media(body: bytes) -> dict[str, int] | None:
    """Summarize input media in a request body.

    Returns ``None`` when the body cannot contain media (no ``image`` token)
    or cannot be parsed — the audit is best-effort. Otherwise returns a
    dict with ``input_images``, ``image_bytes``, ``content_arrays`` and
    ``text_bytes``.
    """
    if not may_contain_response_media(body):
        return None
    try:
        value = orjson.loads(body)
    except (ValueError, TypeError):
        return None
    if not isinstance(value, dict):
        return _new_summary()
    summary = _new_summary()
    _add(summary, _stats_for_root_input(value.get("input")))
    _add(summary, _stats_for_messages(value.get("messages")))
    return summary


def log_response_media_summary(
    logger_obj: AuditLogger, request_id: str, summary: dict[str, int] | None
) -> None:
    """Log the media input summary at DEBUG — only when input images exist.

    Logs counts only, never payload contents.
    """
    if summary is None or summary["input_images"] <= 0:
        return
    logger_obj.debug(
        "request_media_input_summary request_id={} media_input_images={} "
        "media_input_image_bytes={} media_content_arrays={} media_text_bytes={}",
        request_id,
        summary["input_images"],
        summary["image_bytes"],
        summary["content_arrays"],
        summary["text_bytes"],
    )


def log_media_input_summary(
    logger_obj: AuditLogger, request_id: str, body: bytes
) -> None:
    """Summarize *body* and log at DEBUG when it contains input images."""
    log_response_media_summary(logger_obj, request_id, summarize_response_media(body))


def _stats_for_root_input(value: object) -> dict[str, int]:
    if value is None:
        return _new_summary()
    if isinstance(value, str):
        return {
            "input_images": 0,
            "image_bytes": 0,
            "content_arrays": 0,
            "text_bytes": len(value),
        }
    if isinstance(value, list):
        summary = _new_summary()
        for item in value:
            _add(summary, _stats_for_input_item(item))
        return summary
    return _stats_for_input_item(value)


def _stats_for_messages(value: object) -> dict[str, int]:
    if value is None:
        return _new_summary()
    if isinstance(value, list):
        summary = _new_summary()
        for item in value:
            _add(summary, _stats_for_message(item))
        return summary
    return _stats_for_message(value)


def _stats_for_input_item(value: object) -> dict[str, int]:
    if value is None:
        return _new_summary()
    if isinstance(value, str):
        return {
            "input_images": 0,
            "image_bytes": 0,
            "content_arrays": 0,
            "text_bytes": len(value),
        }
    if not isinstance(value, dict):
        return _new_summary()
    type_name = value.get("type")
    if type_name == "message":
        return _stats_for_content_field(value.get("content"))
    if type_name == "function_call_output":
        return _stats_for_content_field(value.get("output"))
    if type_name in (
        "input_text",
        "input_image",
        "image_url",
        "image",
        "text",
        "tool_result",
    ):
        return _stats_for_content_block(value)
    if value.get("role"):
        return _stats_for_content_field(value.get("content"))
    return _new_summary()


def _stats_for_message(value: object) -> dict[str, int]:
    if not isinstance(value, dict):
        return _new_summary()
    if not value.get("role") and value.get("type") != "message":
        return _new_summary()
    return _stats_for_content_field(value.get("content"))


def _stats_for_content_field(value: object) -> dict[str, int]:
    if value is None:
        return _new_summary()
    if isinstance(value, str):
        return {
            "input_images": 0,
            "image_bytes": 0,
            "content_arrays": 0,
            "text_bytes": len(value),
        }
    if not isinstance(value, list):
        return _new_summary()
    summary = _new_summary()
    for item in value:
        _add(summary, _stats_for_content_block(item))
    summary["content_arrays"] += 1
    return summary


def _stats_for_content_block(value: object) -> dict[str, int]:
    if value is None:
        return _new_summary()
    if isinstance(value, str):
        return {
            "input_images": 0,
            "image_bytes": 0,
            "content_arrays": 0,
            "text_bytes": len(value),
        }
    if not isinstance(value, dict):
        return _new_summary()
    type_name = value.get("type")
    if type_name in ("input_text", "text", "output_text"):
        text = value.get("text")
        text_bytes = len(text) if isinstance(text, str) else 0
        return {
            "input_images": 0,
            "image_bytes": 0,
            "content_arrays": 0,
            "text_bytes": text_bytes,
        }
    if type_name == "input_image":
        image_url = value.get("image_url")
        return {
            "input_images": 1,
            "image_bytes": _image_ref_bytes(image_url),
            "content_arrays": 0,
            "text_bytes": 0,
        }
    if type_name == "image_url":
        return {
            "input_images": 1,
            "image_bytes": _image_ref_bytes(value.get("image_url"), value.get("url")),
            "content_arrays": 0,
            "text_bytes": 0,
        }
    if type_name == "image":
        source = value.get("source")
        source_type = source.get("type") if isinstance(source, dict) else None
        if source_type == "base64":
            data = source.get("data") if isinstance(source, dict) else None
            image_bytes = decoded_base64_bytes(data) if isinstance(data, str) else 0
        else:
            image_bytes = _image_ref_bytes(value.get("image_url"))
        return {
            "input_images": 1,
            "image_bytes": image_bytes,
            "content_arrays": 0,
            "text_bytes": 0,
        }
    if type_name == "tool_result":
        return _stats_for_content_field(value.get("content"))
    return _new_summary()


def _image_ref_bytes(*values: object) -> int:
    """Return the largest data-URI image byte count among reference values.

    A reference may be a plain URL string or an object like
    ``{"url": "data:image/..."}`` — extract the URL before measuring.
    """
    best = 0
    for value in values:
        if isinstance(value, dict):
            for nested in (value.get("url"), value.get("image_url")):
                if isinstance(nested, str):
                    best = max(best, data_uri_image_bytes(nested))
        elif isinstance(value, str):
            best = max(best, data_uri_image_bytes(value))
    return best


def data_uri_image_bytes(value: str) -> int:
    """Decoded byte count of a ``data:image/...;base64,`` URI, else 0."""
    comma = value.find(",")
    if comma <= 0:
        return 0
    header = value[:comma].lower()
    if not header.startswith("data:image/") or ";base64" not in header:
        return 0
    return decoded_base64_bytes(value[comma + 1 :])


def decoded_base64_bytes(value: str) -> int:
    """Estimated decoded byte count of a base64 string without decoding."""
    symbols = 0
    padding = 0
    seen_padding = False
    for character in value:
        if character in " \n\r\t":
            continue
        if character == "=":
            seen_padding = True
            padding += 1
            if padding > 2:
                return 0
        elif (
            "A" <= character <= "Z"
            or "a" <= character <= "z"
            or "0" <= character <= "9"
            or character in "+/-_"
        ):
            if seen_padding:
                return 0
        else:
            return 0
        symbols += 1
    if symbols == 0:
        return 0
    if padding > 0 and symbols % 4 != 0:
        return 0
    remainder = symbols % 4
    if remainder == 0:
        return symbols // 4 * 3 - padding
    if remainder == 2:
        return symbols // 4 * 3 + 1
    if remainder == 3:
        return symbols // 4 * 3 + 2
    return 0


# ---------------------------------------------------------------------------
# Responses tool-output normalization
# ---------------------------------------------------------------------------


def is_function_call_output_content_array(blocks: Sequence[object]) -> bool:
    """Distinguish Responses content arrays from plain structured JSON arrays.

    If any block type starts with ``input_``, the whole array is validated
    strictly as a content array — so images in mixed arrays are not silently
    stringified. Plain objects/scalars/empty arrays keep the JSON-string
    contract.
    """
    for raw in blocks:
        if not isinstance(raw, dict):
            continue
        block_type = str(raw.get("type") or "").strip()
        if block_type.startswith(_INPUT_BLOCK_PREFIX):
            return True
    return False


def normalize_function_call_output_input(
    item: Mapping[str, object], param: str = "input"
) -> dict[str, object]:
    """Normalize a Responses ``function_call_output`` input item.

    Output is treated as a content array only when
    :func:`is_function_call_output_content_array` matches; otherwise it is
    encoded as a JSON string (``encodeToolOutput``).
    """
    call_id = str(item.get("call_id") or "").strip()
    if not call_id:
        raise ValueError(f"{param}.call_id 不能为空")
    output = item.get("output")
    if isinstance(output, list) and is_function_call_output_content_array(output):
        output = _normalize_function_call_output_blocks(output, f"{param}.output")
    else:
        output = _encode_tool_output(output, f"{param}.output")
    return {"type": "function_call_output", "call_id": call_id, "output": output}


def normalize_input_image_part(
    item: Mapping[str, object], param: str = "input_image"
) -> dict[str, object]:
    """Normalize a Responses ``input_image`` content part.

    Accepts ``auto``/``low``/``high``; maps ``original`` → ``high`` with a
    warning (Grok Build 0.2.103 does not accept ``original``); rejects any
    other value.
    """
    detail = "auto"
    if "detail" in item and item.get("detail") is not None:
        raw = item.get("detail")
        if not isinstance(raw, str):
            raise ValueError(f"{param}.detail 必须是字符串")
        detail = raw.strip()
        if not detail:
            detail = "auto"
    if detail in ("auto", "low", "high"):
        pass
    elif detail == "original":
        detail = "high"
        logger.warning(
            "image_detail_original_downgraded param={} reason=Build 0.2.103 "
            "accepts only auto/low/high",
            param,
        )
    else:
        raise ValueError(f"{param}.detail 只支持 auto、low、high 或 original")

    converted: dict[str, object] = {"type": "input_image", "detail": detail}
    if item.get("image_url") is not None:
        converted["image_url"] = item["image_url"]
    elif item.get("url") is not None:
        converted["image_url"] = item["url"]
    if item.get("file_id") is not None:
        converted["file_id"] = item["file_id"]
    return converted


def _normalize_function_call_output_blocks(
    blocks: Sequence[object], param: str
) -> list[dict[str, object]]:
    normalized: list[dict[str, object]] = []
    for index, raw in enumerate(blocks):
        block_param = f"{param}[{index}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{block_param} 必须是对象")
        block = cast(Mapping[str, object], raw)
        block_type = str(block.get("type") or "").strip()
        if not block_type:
            raise ValueError(f"{block_param}.type 不能为空")
        if block_type == "input_text":
            text = block.get("text")
            if not isinstance(text, str):
                raise ValueError(f"{block_param}.text 必须是字符串")
            normalized.append({"type": "input_text", "text": text})
        elif block_type == "input_image":
            _, has_image_url = _non_empty_content_block_string(
                block, "image_url", block_param
            )
            _, has_file_id = _non_empty_content_block_string(
                block, "file_id", block_param
            )
            if not has_image_url and not has_file_id:
                raise ValueError(f"{block_param}.image_url 或 .file_id 至少需要一个")
            normalized.append(normalize_input_image_part(block, block_param))
        elif block_type == "input_file":
            normalized.append(
                _normalize_function_call_output_file_block(block, block_param)
            )
        else:
            raise ValueError(
                "Grok Build 0.2.103 不支持该 function_call_output.output 类型"
            )
    return normalized


def _normalize_function_call_output_file_block(
    block: Mapping[str, object], param: str
) -> dict[str, object]:
    has_source = False
    for key in ("file_data", "file_id", "file_url", "filename"):
        _, exists = _non_empty_content_block_string(block, key, param)
        if key != "filename" and exists:
            has_source = True
    if not has_source:
        raise ValueError(f"{param} 至少需要 file_data、file_id 或 file_url 之一")
    converted: dict[str, object] = {"type": "input_file"}
    for key in ("file_data", "file_id", "filename", "file_url"):
        if block.get(key) is not None:
            converted[key] = block[key]
    return converted


def _non_empty_content_block_string(
    block: Mapping[str, object], key: str, param: str
) -> tuple[str, bool]:
    raw = block.get(key)
    if raw is None:
        return "", False
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{param}.{key} 必须是非空字符串")
    return raw, True


def _encode_tool_output(value: object, param: str) -> str:
    """encodeToolOutput: strings pass through, everything else is JSON."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return orjson.dumps(value).decode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{param} 无法编码") from exc


__all__ = [
    "may_contain_response_media",
    "summarize_response_media",
    "log_response_media_summary",
    "log_media_input_summary",
    "data_uri_image_bytes",
    "decoded_base64_bytes",
    "is_function_call_output_content_array",
    "normalize_function_call_output_input",
    "normalize_input_image_part",
]
