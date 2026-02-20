"""server/server_html_contents.py helpers."""

from __future__ import annotations

from pathlib import Path

# Directory containing the static UI assets (index.html, JS, CSS, etc.).
# Resolved relative to this file so the app works regardless of the current working directory.
_ASSETS_DIR = Path(__file__).resolve().parent / "assets"


def get_index_html(*, history_seconds: int) -> str:
    """
    Load the UI HTML template and inject runtime values.

    Current templating:
    - Replaces the placeholder "{{HISTORY_SECONDS}}" in index.html with an integer value.

    Why this exists:
    - The server delivers a mostly static HTML file, but a small amount of runtime
      configuration is easier to inject server-side than to hardcode in the asset.
    - Avoids introducing a full templating engine for one substitution.

    Args:
        history_seconds: Number of seconds of history the UI should request/display.

    Returns:
        The final HTML string to be served to the browser.

    Raises:
        OSError/IOError: If index.html cannot be read.
    """
    # Read the on-disk template each time. This keeps development iterations simple
    # (no caching) and avoids stale content if the file changes while running.
    html = (_ASSETS_DIR / "index.html").read_text(encoding="utf-8")

    # Ensure the injected value is an integer (stable wire format and avoids surprises).
    return html.replace("{{HISTORY_SECONDS}}", str(int(history_seconds)))
