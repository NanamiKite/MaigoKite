"""Task output contracts, bounded JSON validation and model schema projection."""
from __future__ import annotations

import json
from copy import deepcopy
from typing import TYPE_CHECKING, Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from referencing import Registry
from referencing.jsonschema import DRAFT202012

from agent.data_contracts import JSON_OBJECT, JSONObject
from agent.errors import UserFacingError

if TYPE_CHECKING:
    from agent.runtime.run_context import RunContext
    from agent.runtime.subagents import ChildContext

OUTPUT_TOOL_NAME = "maigokite_structured_result"
SCHEMA_MAX_BYTES = 16 * 1024
RESULT_MAX_BYTES = 256 * 1024


class StructuredResultError(UserFacingError):
    """A final result failed its task contract; no consumable result is published."""


def _bounded_json(value: object, limit: int, depth: int) -> str:
    pending: list[tuple[object, int]] = [(value, 0)]
    while pending:
        item, level = pending.pop()
        if level > depth:
            raise ValueError("JSON nesting exceeds the supported depth")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise ValueError("JSON object keys must be strings")
            pending.extend((part, level + 1) for part in item.values())
        elif isinstance(item, list):
            pending.extend((part, level + 1) for part in item)
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    if len(encoded.encode("utf-8")) > limit:
        raise ValueError("JSON exceeds the supported size")
    return encoded


def validate_output_schema(schema: object) -> JSONObject | None:
    """Validate before admission; references never retrieve network or filesystem data."""
    if schema is None:
        return None
    try:
        encoded = _bounded_json(schema, SCHEMA_MAX_BYTES, 32)
        if not isinstance(schema, dict) or schema.get("type") != "object":
            raise ValueError("an object schema is required")
        if "oneOf" in schema:
            # ToolStrategy interprets a top-level union as several result tools.
            # A task has one result contract; nested unions remain available.
            raise ValueError("top-level oneOf is not supported")
        Draft202012Validator.check_schema(schema)
        pending = [DRAFT202012.create_resource(schema)]
        while pending:
            resource = pending.pop()
            item = resource.contents
            if isinstance(item, dict):
                if (
                    item.get("$schema", Draft202012Validator.META_SCHEMA["$id"])
                    != Draft202012Validator.META_SCHEMA["$id"]
                ):
                    raise ValueError("only JSON Schema 2020-12 is supported")
                for key in ("$ref", "$dynamicRef"):
                    if key in item and (not isinstance(item[key], str) or not item[key].startswith("#")):
                        raise ValueError("only local references are supported")
                if "$id" in item:
                    raise ValueError("schema resource identifiers are not supported")
            # The standard resource traversal distinguishes actual subschemas
            # from user property names, enum values and examples.
            pending.extend(resource.subresources())
    except (ValueError, TypeError, RecursionError, SchemaError) as exc:
        raise UserFacingError(
            "输出 Schema 无效：须为 JSON Schema 2020-12 的 object，最多 16 KiB、32 层；"
            "不支持外部引用、$id 或顶层 oneOf。"
        ) from exc
    return JSON_OBJECT.validate_json(encoded, strict=True)


def response_schema(schema: JSONObject) -> JSONObject:
    """Give LangChain's result tool a reserved identity, independent of user titles."""
    result = deepcopy(schema)
    result["title"] = OUTPUT_TOOL_NAME
    result["description"] = (
        "Submit the final task result after all necessary work and tool results are complete. "
        "Do not combine this submission with other tool calls. " + str(schema.get("description", ""))
    )
    return result


def output_budget_tools(tools: list[Any], context: RunContext | ChildContext | None) -> list[Any]:
    """Count the schema that LangChain adds after product model middleware."""
    from langchain_core.utils.function_calling import convert_to_openai_tool

    schema = context.request.output_schema if context is not None else None
    return [*tools, convert_to_openai_tool(response_schema(schema))] if schema is not None else tools


def validated_result_json(schema: JSONObject, value: object) -> str:
    try:
        encoded = _bounded_json(value, RESULT_MAX_BYTES, 64)
        validator = Draft202012Validator(schema, registry=Registry())
        if not isinstance(value, dict) or not validator.is_valid(value):
            raise ValueError("result does not match the task schema")
    except Exception as exc:
        # Validation exceptions can embed the entire result. They are not safe
        # diagnostics for UI events, audit logs or model feedback.
        raise StructuredResultError(
            "最终结果未通过输出 Schema 校验，或超过 256 KiB / 64 层限制；未保存可供后续步骤读取的结果。"
        ) from exc
    return encoded
