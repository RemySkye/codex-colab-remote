"""Strict JSONC parsing and canonical commented configuration rendering."""

from __future__ import annotations

import json
from typing import Any


def _without_comments(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    line_comment = False
    block_comment = False
    while index < len(text):
        character = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if line_comment:
            if character in "\r\n":
                line_comment = False
                output.append(character)
            else:
                output.append(" ")
        elif block_comment:
            if character == "*" and following == "/":
                output.extend((" ", " "))
                index += 1
                block_comment = False
            else:
                output.append(character if character in "\r\n" else " ")
        elif in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
            output.append(character)
        elif character == "/" and following == "/":
            output.extend((" ", " "))
            index += 1
            line_comment = True
        elif character == "/" and following == "*":
            output.extend((" ", " "))
            index += 1
            block_comment = True
        else:
            output.append(character)
        index += 1
    if block_comment:
        raise ValueError("Unterminated block comment in config.jsonc")
    return "".join(output)


def _without_trailing_commas(text: str) -> str:
    output: list[str] = []
    index = 0
    in_string = False
    escaped = False
    while index < len(text):
        character = text[index]
        if in_string:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
        elif character == '"':
            in_string = True
            output.append(character)
        elif character == ",":
            lookahead = index + 1
            while lookahead < len(text) and text[lookahead].isspace():
                lookahead += 1
            if lookahead >= len(text) or text[lookahead] not in "}]":
                output.append(character)
        else:
            output.append(character)
        index += 1
    return "".join(output)


def loads(text: str) -> dict[str, Any]:
    """Parse JSONC comments and trailing commas without changing string contents."""
    value = json.loads(_without_trailing_commas(_without_comments(text)))
    if not isinstance(value, dict):
        raise ValueError("Colab Remote config must be a JSON object")
    return value


def _comment(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(json.dumps(item, ensure_ascii=False) for item in value)
    if isinstance(value, (str, bool)) or value is None:
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render(config: dict[str, Any], documentation: dict[str, Any]) -> str:
    """Render canonical JSONC with documentation immediately above every setting."""
    settings = documentation["settings"]
    ordered_names = list(settings)
    ordered_names.extend(
        sorted(
            name
            for name in config
            if name not in settings and name != "_documentation"
        )
    )
    lines = [
        "{",
        "  // Colab Remote configuration. Comments and trailing commas are supported.",
        "  // Never store passwords, OAuth codes, tokens, or other secrets here.",
    ]
    for position, name in enumerate(ordered_names):
        details = settings.get(name)
        if details:
            lines.append(f"  // {details['description']}")
            lines.append(
                f"  // Type: {details['type']}. Default: {_comment(details['default'])}."
            )
            lines.append(f"  // Allowed: {_comment(details['allowed'])}.")
        else:
            lines.append("  // Preserved additional setting not known by this version.")
        encoded = json.dumps(config[name], indent=2, ensure_ascii=False).splitlines()
        suffix = "," if position + 1 < len(ordered_names) else ""
        lines.append(f"  {json.dumps(name)}: {encoded[0]}")
        lines.extend(f"  {line}" for line in encoded[1:-1])
        if len(encoded) > 1:
            lines.append(f"  {encoded[-1]}{suffix}")
        else:
            lines[-1] += suffix
        if position + 1 < len(ordered_names):
            lines.append("")
    lines.append("}")
    return "\n".join(lines) + "\n"
