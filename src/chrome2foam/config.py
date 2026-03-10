"""Configuration management."""

from __future__ import annotations

import os
import platform
from pathlib import Path


def default_chrome_bookmarks_path() -> Path:
    """Return the platform-dependent default path for Chrome's Bookmarks file.

    Supports native Windows/macOS/Linux and WSL2 environments.
    """
    system = platform.system()

    if system == "Windows":
        local_app = os.environ.get("LOCALAPPDATA", "")
        return Path(local_app) / "Google" / "Chrome" / "User Data" / "Default" / "Bookmarks"

    if system == "Darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "Default"
            / "Bookmarks"
        )

    # Linux - check for WSL2 first via WSL_DISTRO_NAME (set by WSL2 kernel)
    if os.environ.get("WSL_DISTRO_NAME"):
        wsl_path = _find_wsl_chrome_bookmarks()
        if wsl_path is not None:
            return wsl_path

    return Path.home() / ".config" / "google-chrome" / "Default" / "Bookmarks"


_WSL_CHROME_REL = Path("AppData/Local/Google/Chrome/User Data/Default/Bookmarks")
_WSL_EXCLUDED_USERS = {"Public", "Default", "Default User", "All Users"}


def _find_wsl_chrome_bookmarks() -> Path | None:
    """Search /mnt/c/Users/* for the Chrome Bookmarks file and return the first match."""
    users_dir = Path("/mnt/c/Users")
    if not users_dir.is_dir():
        return None
    try:
        user_dirs = list(users_dir.iterdir())
    except OSError:
        return None
    for user_dir in sorted(user_dirs):
        if user_dir.name in _WSL_EXCLUDED_USERS:
            continue
        try:
            if not user_dir.is_dir():
                continue
            candidate = user_dir / _WSL_CHROME_REL
            if candidate.exists():
                return candidate
        except PermissionError:
            continue
    return None
