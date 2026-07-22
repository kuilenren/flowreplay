"""Format-contract tests: SKILL.md round-trip, quality grading, recorder compile,
foreign import, and the boks-flow compatibility alias. No browser required."""
from __future__ import annotations

import pathlib

from flowreplay import (
    assess_flow_quality,
    compile_events_to_steps,
    distill_flow,
    flow_to_skill_md,
    parse_skill_md,
)
from flowreplay.skillmd import _serialize_steps

EXAMPLE = pathlib.Path(__file__).parent.parent / "examples" / "example.SKILL.md"


def _sample_flow() -> dict:
    steps = [
        {"action_type": "navigate", "url_template": "https://example.com",
         "description": "Open https://example.com", "locators": [],
         "options": {"wait_until": "domcontentloaded"}, "viewport": {"width": 1280, "height": 800}},
        {"action_type": "fill", "value_template": "{{query}}", "description": "Fill search",
         "locators": [{"kind": "css", "value": "#q"},
                      {"kind": "role", "value": "textbox", "name": "Search"},
                      {"kind": "coordinate", "x": 100, "y": 60}],
         "options": {}, "viewport": {"width": 1280, "height": 800}},
        {"action_type": "click", "description": "Click Search",
         "locators": [{"kind": "text", "value": "Search"}, {"kind": "coordinate", "x": 200, "y": 60}],
         "options": {}, "viewport": {"width": 1280, "height": 800}},
    ]
    return {"name": "Search example", "description": None, "start_url": "https://example.com",
            "variables": [{"name": "query", "label": "Search term", "required": True}], "steps": steps}


def test_block_roundtrip_is_lossless_and_idempotent():
    flow = _sample_flow()
    steps = flow["steps"]
    md = flow_to_skill_md(flow, steps)
    back = parse_skill_md(md)
    # The machine-readable block preserves every step field exactly.
    assert back["steps"] == _serialize_steps(steps)
    assert back["name"] == "Search example"
    assert back["variables"][0]["name"] == "query"
    # Re-emitting the parsed flow reproduces byte-identical Markdown.
    assert flow_to_skill_md(back, back["steps"]) == md


def test_quality_flags_visual_only_step_as_fragile():
    steps = [
        {"action_type": "click", "description": "robust",
         "locators": [{"kind": "role", "value": "button", "name": "OK"}], "options": {}},
        {"action_type": "click", "description": "visual only",
         "locators": [{"kind": "css", "value": "div:nth-of-type(3) > a"},
                      {"kind": "coordinate", "x": 5, "y": 5}], "options": {}},
    ]
    q = assess_flow_quality(steps)
    assert q["fragile"] == 1
    assert q["fragile_steps"][0]["index"] == 2
    assert q["grade"] in {"moderate", "fragile"}


def test_recorder_demotes_volatile_css_below_semantic_locators():
    # A click on an element whose CSS id is framework-volatile (Element-Plus
    # #el-id-<n>-<n>) but which has a stable role + text.
    events = [{
        "type": "click", "url": "https://x.example/",
        "selector": "#el-id-3268-14 > span", "role": "button", "text": "Submit",
        "tag": "button", "cx": 10, "cy": 20, "viewport": {"width": 1280, "height": 800},
    }]
    steps = compile_events_to_steps(events)
    assert [s["action_type"] for s in steps] == ["navigate", "click"]
    kinds = [loc["kind"] for loc in steps[1]["locators"]]
    # Semantic locators come first; the volatile CSS is demoted below them.
    assert kinds[0] in ("role", "text")
    assert "css" in kinds and kinds.index("css") > kinds.index("role")


def test_foreign_skill_md_without_block_is_best_effort_imported():
    foreign = "---\nname: Foreign\n---\n# Foreign\n## Steps\n1. Open https://foo.example\n2. Click «Login»\n"
    flow = parse_skill_md(foreign)
    assert flow.get("imported_foreign") is True
    assert [s["action_type"] for s in flow["steps"]] == ["navigate", "click"]


def test_boks_flow_fence_is_accepted_as_compatibility_alias():
    md = "---\nname: Compat\n---\n# Compat\n## flow\n```boks-flow\n" \
         '{"name": "Compat", "steps": [{"action_type": "navigate", "url_template": "https://a.example"}]}\n```\n'
    flow = parse_skill_md(md)
    assert flow["steps"][0]["action_type"] == "navigate"
    assert flow["steps"][0]["url_template"] == "https://a.example"


def test_shipped_example_parses_and_distills():
    flow = parse_skill_md(EXAMPLE.read_text(encoding="utf-8"))
    assert flow["steps"], "example should carry steps"
    d = distill_flow(flow, flow["steps"])
    assert d["domain"] == "news.ycombinator.com"
    assert "query" in d["params"]
