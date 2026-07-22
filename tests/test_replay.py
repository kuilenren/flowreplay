"""End-to-end replay tests against a local HTML fixture.

These launch a real Chromium via Playwright, so they are skipped automatically
when a browser is not installed (keeps the pure format tests hermetic in CI)."""
from __future__ import annotations

import asyncio
import os
import pathlib

import pytest

from flowreplay import flow_to_skill_md, parse_skill_md
from flowreplay.player import replay_flow

FIXTURE = (pathlib.Path(__file__).parent / "fixtures" / "demo.html").as_uri()
# Set FLOWREPLAY_TEST_CHANNEL=chrome to drive a system-installed browser instead of
# Playwright's bundled Chromium (handy when the bundled build isn't downloaded).
CHANNEL = os.environ.get("FLOWREPLAY_TEST_CHANNEL") or None


def _browser_ready() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    import os
    home = pathlib.Path.home()
    bases = [
        home / "Library" / "Caches" / "ms-playwright",
        home / ".cache" / "ms-playwright",
        pathlib.Path(os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/nonexistent")),
    ]
    return any(b.exists() and any(b.glob("chromium-*")) for b in bases)


pytestmark = pytest.mark.skipif(not _browser_ready(), reason="Playwright chromium not installed")

VP = {"width": 1024, "height": 768}


def _flow(fill_locators: list[dict]) -> dict:
    return {
        "name": "demo", "description": None, "start_url": FIXTURE,
        "variables": [{"name": "query", "required": True}],
        "steps": [
            {"action_type": "navigate", "url_template": FIXTURE, "locators": [],
             "options": {"wait_until": "load"}, "viewport": VP},
            {"action_type": "fill", "value_template": "{{query}}", "locators": fill_locators,
             "options": {}, "viewport": VP},
            {"action_type": "click",
             "locators": [{"kind": "role", "value": "button", "name": "Search"},
                          {"kind": "css", "value": "#go"}],
             "options": {}, "viewport": VP},
        ],
    }


async def _read_out(page):
    return await page.locator("#out").inner_text()


def test_replay_happy_path_fills_and_clicks():
    flow = _flow([{"kind": "css", "value": "#q"}])
    res = asyncio.run(replay_flow(flow, {"query": "hello"}, headless=True, heal=False,
                                  channel=CHANNEL, inspect=_read_out))
    assert res["success"], res
    assert res["inspection"] == "Results for hello"  # the {{query}} really got typed


def test_replay_self_heals_and_writes_back(tmp_path):
    # The fill step's PRIMARY locator is broken; the role fallback must win.
    flow = _flow([
        {"kind": "css", "value": "#does-not-exist"},
        {"kind": "role", "value": "textbox", "name": "Search"},
        {"kind": "css", "value": "#q"},
    ])
    path = tmp_path / "demo.SKILL.md"
    path.write_text(flow_to_skill_md(flow, flow["steps"]), encoding="utf-8")

    reparsed = parse_skill_md(path.read_text(encoding="utf-8"))
    res = asyncio.run(replay_flow(reparsed, {"query": "hi"}, headless=True, heal=True,
                                  source_path=str(path), channel=CHANNEL, inspect=_read_out))
    assert res["success"], res
    assert res["healed"] is True
    assert res["inspection"] == "Results for hi"

    # The winning role locator was promoted to the front and the file rewritten...
    healed = parse_skill_md(path.read_text(encoding="utf-8"))
    fill_locs = healed["steps"][1]["locators"]
    assert fill_locs[0]["kind"] == "role" and fill_locs[0]["name"] == "Search"
    # ...and nothing was deleted (both css locators are retained as fallbacks).
    assert [l["kind"] for l in fill_locs].count("css") == 2


def test_missing_required_variable_fails_before_running():
    from flowreplay.player import ReplayError
    flow = _flow([{"kind": "css", "value": "#q"}])
    with pytest.raises(ReplayError):
        asyncio.run(replay_flow(flow, {}, headless=True))
