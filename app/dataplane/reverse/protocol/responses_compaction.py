"""Context compaction for Build API.

Port of Go cli/responses_compaction.go and responses_compaction_forward.go.
Handles conversation history compaction with encrypted summary blobs.
"""

import base64
import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Compaction version prefix
_COMPACT_PREFIX = "g2a_compact_v1."

# Strip <analysis> and <summary> tags from compaction text
_ANALYSIS_PATTERN = re.compile(r"<analysis>.*?</analysis>", re.DOTALL)
_SUMMARY_PATTERN = re.compile(r"<summary>.*?</summary>", re.DOTALL)

# Compaction prompt template
_COMPACTION_SYSTEM_PROMPT = (
    "You are a conversation state encoder. Your task is to produce a compact "
    "representation of the conversation so far that preserves all information "
    "needed to continue naturally. Focus on: user goals, decisions made, "
    "code/files discussed, pending actions, and the current state."
)


def encode_compaction(items: list[dict[str, Any]], key: str) -> str:
    """encode_compaction encrypts and encodes compaction items.

    Returns a compact string with prefix for identification.
    """
    # Derive encryption key from the provided key
    derived = hashlib.sha256(key.encode()).hexdigest()[:32]

    # Serialize items
    payload = json.dumps({"items": items, "v": 1})

    # Simple XOR "encryption" with derived key (port of Go's approach)
    encoded = _xor_cipher(payload.encode(), derived.encode())

    # Base64 encode with prefix
    result = _COMPACT_PREFIX + base64.urlsafe_b64encode(encoded).decode()
    return result


def decode_compaction(data: str, key: str) -> list[dict[str, Any]] | None:
    """decode_compaction decodes and decrypts a compaction string.

    Returns None if decoding fails.
    """
    if not data.startswith(_COMPACT_PREFIX):
        return None

    try:
        encoded_data = data[len(_COMPACT_PREFIX) :]
        decoded = base64.urlsafe_b64decode(encoded_data)

        derived = hashlib.sha256(key.encode()).hexdigest()[:32]
        decrypted = _xor_cipher(decoded, derived.encode())

        payload = json.loads(decrypted.decode())
        return payload.get("items", [])
    except Exception as e:
        logger.warning("Failed to decode compaction: %s", e)
        return None


def _xor_cipher(data: bytes, key: bytes) -> bytes:
    """_xor_cipher applies XOR cipher with the given key."""
    return bytes(data[i] ^ key[i % len(key)] for i in range(len(data)))


def prepare_gateway_compaction_sample(
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """prepare_gateway_compaction_sample extracts first/last/reasoning items for compaction sample."""
    sample: dict[str, Any] = {
        "system_prompt": _COMPACTION_SYSTEM_PROMPT,
        "truncated": False,
    }

    if not items:
        return sample

    # Include first and last few items
    max_items = 20
    if len(items) > max_items:
        sample["history"] = (
            items[:5] + [{"type": "truncation_marker"}] + items[-(max_items - 6) :]
        )
        sample["truncated"] = True
    else:
        sample["history"] = list(items)

    return sample


def clean_gateway_compaction_summary(text: str) -> str:
    """clean_gateway_compaction_summary strips <analysis>/<summary> tags from compaction output."""
    text = _ANALYSIS_PATTERN.sub("", text)
    text = _SUMMARY_PATTERN.sub("", text)
    return text.strip()


def build_gateway_compaction_response(
    body: dict[str, Any], operation: str
) -> dict[str, Any] | None:
    """build_gateway_compaction_response processes a gateway compaction response.

    Returns the input-rewritten body for a compaction response, or None if
    this response is not a compaction result.
    """
    # Check if this is a compaction response
    output = body.get("output", [])
    for item in output:
        if item.get("type") == "compaction_trigger":
            # Extract the compacted input from the response
            compacted = item.get("compacted_input", "")
            if compacted:
                return {
                    "input": [{"type": "message", "role": "user", "content": compacted}]
                }
    return None
