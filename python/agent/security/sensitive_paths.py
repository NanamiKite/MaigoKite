"""Known credential paths shared by native policy and ripgrep exclusions.

This is a conservative path guard, not content DLP or a sandbox for arbitrary code.
"""

import os
import sys
from fnmatch import fnmatchcase
from pathlib import Path

SENSITIVE_NAMES = (
    ".env", ".env.*", ".ssh", ".aws", ".gnupg", ".azure", ".kube",
    ".netrc", ".npmrc", ".pypirc", "id_rsa*", "id_ed25519*", "id_ecdsa*",
    "*.key", "*.pem", "*.p12", "*.pfx", "credentials.json", "credentials.yaml",
    "service-account*.json", "browser-profiles",
)


def is_sensitive_path(path: Path) -> bool:
    parts = (part.split(":", 1)[0].rstrip(" .") if os.name == "nt" else part for part in path.parts)
    return any(fnmatchcase(part.casefold(), pattern) for part in parts for pattern in SENSITIVE_NAMES)


def validate_windows_path(path: str | Path) -> None:
    """Reject Win32 aliases before resolve() erases the spelling used for approval."""
    text = str(path)
    if "\x00" in text:
        raise ValueError("file paths must not contain NUL")
    if sys.platform != "win32":
        return
    value = Path(text)
    # C:foo and \foo depend on a drive's current directory; neither identifies
    # the same resource across approval, restart and execution.
    if bool(value.drive) != bool(value.root):
        raise ValueError("use a workspace-relative path or a fully qualified Windows path")
    if text.replace("/", "\\").startswith(("\\\\?\\", "\\\\.\\")) or os.path.isreserved(text):
        raise ValueError("device paths, alternate data streams and reserved Windows names are not supported")


def search_exclusions() -> list[str]:
    # Use ripgrep's existing matcher; do not implement another recursive scanner.
    return [arg for pattern in SENSITIVE_NAMES for glob in (f"!{pattern}", f"!{pattern}/**")
            for arg in ("--iglob", glob)]
