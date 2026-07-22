"""Extraction actions against the local fixture (browser-gated)."""
from __future__ import annotations

import asyncio
import os
import pathlib

import pytest

from flowreplay.player import replay_flow

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "demo.html").as_uri()
CHANNEL = os.environ.get("FLOWREPLAY_TEST_CHANNEL") or None


def _browser_ready() -> bool:
    if CHANNEL:
        return True
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    home = pathlib.Path.home()
    bases = [
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
        pathlib.Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent")),
    ]
    return any(b.exists() and any(b.glob("chromium-*")) for b in bases)


pytestmark = pytest.mark.skipif(not _browser_ready(), reason="Playwright chromium not installed")

VP = {"width": 1024, "height": 768}


def _flow(tail: list[dict]) -> dict:
    return {
        "name": "extract-demo", "description": None, "start_url": FIXTURE, "variables": [],
        "steps": [
            {"action_type": "navigate", "url_template": FIXTURE, "locators": [],
             "options": {"wait_until": "load"}, "viewport": VP},
            *tail,
        ],
    }


def _run(flow):
    return asyncio.run(replay_flow(flow, {}, headless=True, heal=False, channel=CHANNEL))


def test_extract_text_reads_element_text():
    flow = _flow([{"action_type": "extract_text",
                   "locators": [{"kind": "css", "value": "#status"}],
                   "options": {"name": "status"}, "viewport": VP}])
    res = _run(flow)
    assert res["success"], res
    assert res["extractions"]["status"] == "ready"


def test_extract_tables_parses_headers_and_rows():
    flow = _flow([{"action_type": "extract_tables",
                   "locators": [{"kind": "css", "value": "#results"}],
                   "options": {"name": "grid"}, "viewport": VP}])
    res = _run(flow)
    assert res["success"], res
    assert res["extractions"]["grid"] == [
        {"headers": ["Name", "Score"], "rows": [["Alice", "91"], ["Bob", "87"]]}
    ]


def test_extract_structured_maps_fields_to_selectors():
    flow = _flow([{"action_type": "extract_structured", "locators": [],
                   "options": {"name": "fields", "fields": {"state": "#status"}}, "viewport": VP}])
    res = _run(flow)
    assert res["success"], res
    assert res["extractions"]["fields"] == {"state": "ready"}
