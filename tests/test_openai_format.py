"""normalize_response_format unit tests — json_schema type normalization.

Ported from Go commit e376c22: when response_format has type="json_schema",
the inner json_schema's own "type" key must be skipped to prevent
overwriting the format type marker.
"""

import pytest
from app.products.openai.chat import normalize_response_format


class TestNormalizeResponseFormatJsonSchema:
    """Tests for normalize_response_format with type="json_schema"."""

    def test_json_schema_type_object_skipped(self):
        """json_schema with type="object" → output type stays "json_schema"."""
        result = normalize_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                },
            }
        )
        assert result["type"] == "json_schema"
        assert "properties" in result
        assert result["properties"]["name"]["type"] == "string"

    def test_json_schema_nested_type_keys_preserved(self):
        """Nested 'type' keys inside properties are preserved."""
        result = normalize_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "age": {"type": "integer"},
                    },
                },
            }
        )
        assert result["type"] == "json_schema"
        assert result["properties"]["name"]["type"] == "string"
        assert result["properties"]["age"]["type"] == "integer"

    def test_json_schema_with_name_and_schema_fields(self):
        """json_schema with name and schema fields → both preserved."""
        result = normalize_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "name": "MySchema",
                    "schema": {
                        "type": "object",
                        "properties": {"x": {"type": "integer"}},
                    },
                },
            }
        )
        assert result["type"] == "json_schema"
        assert result["name"] == "MySchema"
        assert result["schema"]["type"] == "object"
        assert result["schema"]["properties"]["x"]["type"] == "integer"

    def test_json_schema_deeply_nested_all_keys_preserved(self):
        """Deeply nested json_schema with many fields — all non-type keys preserved."""
        result = normalize_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "The name"},
                        "address": {
                            "type": "object",
                            "properties": {
                                "street": {"type": "string"},
                                "zip": {"type": "string"},
                            },
                            "required": ["street"],
                        },
                    },
                    "required": ["name"],
                    "additionalProperties": False,
                },
            }
        )
        assert result["type"] == "json_schema"
        assert result["properties"]["name"]["type"] == "string"
        assert result["properties"]["name"]["description"] == "The name"
        assert result["properties"]["address"]["type"] == "object"
        assert result["properties"]["address"]["required"] == ["street"]
        assert result["required"] == ["name"]
        assert result["additionalProperties"] is False

    def test_json_schema_empty_schema(self):
        """json_schema with empty inner schema → only type preserved."""
        result = normalize_response_format(
            {"type": "json_schema", "json_schema": {"type": "object"}}
        )
        assert result == {"type": "json_schema"}

    def test_json_schema_no_inner_type_key(self):
        """json_schema without inner 'type' key → all keys pass through."""
        result = normalize_response_format(
            {
                "type": "json_schema",
                "json_schema": {
                    "name": "Test",
                    "strict": True,
                    "schema": {"properties": {}},
                },
            }
        )
        assert result["type"] == "json_schema"
        assert result["name"] == "Test"
        assert result["strict"] is True
        assert result["schema"] == {"properties": {}}


class TestNormalizeResponseFormatPassthrough:
    """Inputs that should pass through unchanged."""

    def test_type_text_unchanged(self):
        """type="text" → returned unchanged."""
        inp = {"type": "text"}
        result = normalize_response_format(inp)
        assert result == inp

    def test_type_json_object_unchanged(self):
        """type="json_object" → returned unchanged."""
        inp = {"type": "json_object"}
        result = normalize_response_format(inp)
        assert result == inp

    def test_none_returns_none(self):
        """None → returned unchanged."""
        assert normalize_response_format(None) is None

    def test_empty_dict_unchanged(self):
        """Empty dict → returned unchanged."""
        result = normalize_response_format({})
        assert result == {}

    def test_non_dict_returns_as_is(self):
        """Non-dict (string) → returned unchanged."""
        result = normalize_response_format("not a dict")
        assert result == "not a dict"

    def test_non_dict_integer(self):
        """Non-dict (integer) → returned unchanged."""
        result = normalize_response_format(42)
        assert result == 42

    def test_missing_json_schema_key(self):
        """type="json_schema" but no json_schema key → returned unchanged."""
        inp = {"type": "json_schema"}
        result = normalize_response_format(inp)
        assert result == inp

    def test_json_schema_not_a_dict(self):
        """type="json_schema" but json_schema is a string → returned unchanged."""
        inp = {"type": "json_schema", "json_schema": "not a dict"}
        result = normalize_response_format(inp)
        assert result == inp


class TestNormalizeResponseFormatEdgeCases:
    """Additional edge cases for normalize_response_format."""

    def test_all_fields_except_type_promoted(self):
        """All json_schema fields except 'type' are promoted to top level."""
        rf = {
            "type": "json_schema",
            "json_schema": {
                "name": "my_schema",
                "schema": {"type": "object"},
                "strict": True,
                "description": "A test schema",
                "type": "object",
            },
        }
        result = normalize_response_format(rf)
        assert result["name"] == "my_schema"
        assert result["schema"] == {"type": "object"}
        assert result["strict"] is True
        assert result["description"] == "A test schema"
        assert result.get("type") == "json_schema"

    def test_extra_top_level_keys_lost_when_new_dict_built(self):
        """Extra top-level keys are lost when a new dict is built from json_schema."""
        rf = {
            "type": "json_schema",
            "json_schema": {"name": "resp", "schema": {}},
            "metadata": {"key": "val"},
        }
        result = normalize_response_format(rf)
        assert result["type"] == "json_schema"
        assert result["name"] == "resp"
        assert "metadata" not in result

    def test_json_schema_boolean_true_returns_unchanged(self):
        """type='json_schema' with json_schema=True returns unchanged."""
        rf = {"type": "json_schema", "json_schema": True}
        result = normalize_response_format(rf)
        assert result is rf


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
