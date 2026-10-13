"""InjectionFilter - Sanitizes inputs to prevent injection attacks."""

from __future__ import annotations

import re
from typing import cast


class InjectionFilter:
    """Filters and sanitizes user inputs to prevent injection attacks."""

    def __init__(self, max_length: int = 40_000) -> None:
        self.max_length = max_length

    def sanitize_input(self, input_text: str) -> str:
        """Validate text without silently changing executable semantics."""
        if not isinstance(input_text, str):
            raise TypeError("input must be a string")
        if len(input_text) > self.max_length:
            raise ValueError(f"input exceeds {self.max_length} characters")
        if "\x00" in input_text:
            raise ValueError("input contains a NUL byte")
        return input_text

    def sanitize_arguments(self, arguments: dict[str, object]) -> dict[str, object]:
        """Sanitize tool arguments."""
        def visit(value: object) -> object:
            if isinstance(value, str):
                return self.sanitize_input(value)
            if isinstance(value, list):
                return [visit(item) for item in value]
            if isinstance(value, dict):
                return {str(key): visit(item) for key, item in value.items()}
            return value
        return cast(dict[str, object], visit(arguments))

    def detect_shell_injection(self, input_text: str) -> bool:
        """Check if input contains potential shell injection patterns."""
        return bool(re.search(r"(?:;|&&|\|\||\||`|\$\(|\r|\n)", input_text, re.IGNORECASE))

    def detect_llm_injection(self, input_text: str) -> bool:
        """Check if input contains potential prompt injection patterns."""
        patterns = (
            r"ignore (?:all |the )?(?:previous|prior) instructions",
            r"reveal (?:the )?(?:system|developer) prompt",
            r"(?:system|developer) message says",
        )
        return any(re.search(pattern, input_text, re.IGNORECASE) for pattern in patterns)
