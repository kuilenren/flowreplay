"""Flow Skill — distill a recorded web-flow into a reusable skill, and export/
import a portable SKILL.md.

``flow_to_skill_md()`` renders a flow as Markdown: YAML front-matter, a
human-readable numbered step list, and a fenced ``web-flow`` JSON block for a
lossless round-trip (all locators preserved). ``parse_skill_md()`` reads it
back; a *foreign* SKILL.md that lacks the JSON block is best-effort parsed into
navigate/click/fill steps with text locators. ``distill_flow()`` produces a
deterministic capability summary (no LLM required).

Originally developed for the boksclaw platform; released under Apache-2.0.
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.parse import urlparse

import yaml

_FLOW_BLOCK_RE = re.compile(r"```(?:web-flow|boks-flow)\s*\n(.*?)\n```", re.DOTALL)
_FRONTMATTER_RE = re.compile(r"^\s*---\s*\n(.*?)\n---\s*\n?(.*)$", re.DOTALL)

# action_type → coarse capability phase (atomize/classify step).
_PHASE_BY_ACTION = {
    "navigate": "navigate",
    "click": "act",
    "fill": "input",
    "select": "input",
    "press_keys": "input",
    "wait_for_text": "verify",
    "extract_text": "extract",
    "scroll": "act",
    "screenshot": "evidence",
}


def _domain_of(url: str | None) -> str | None:
    if not url:
        return None
    try:
        host = urlparse(url).hostname
        return host or None
    except Exception:
        return None


def _primary_locator_label(step: Any) -> str:
    """Human label for a step's most-meaningful locator (text/role > css)."""
    locs = (getattr(step, "locators_json", None) or _get(step, "locators") or [])
    for kind in ("text", "role", "aria", "css", "xpath"):
        for loc in locs:
            if isinstance(loc, dict) and loc.get("kind") == kind:
                v = loc.get("value") or loc.get("text") or loc.get("selector") or loc.get("name")
                if v:
                    return str(v)[:80]
    return ""


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _step_sentence(step: Any) -> str:
    action = _get(step, "action_type") or "click"
    label = _primary_locator_label(step)
    value = _get(step, "value_template")
    url = _get(step, "url_template")
    desc = _get(step, "description")
    if desc:
        return str(desc)[:160]
    if action == "navigate":
        return f"Open {url or label or 'page'}"
    if action in ("fill", "select"):
        return f"Fill «{label or 'input'}» with {value or ''}".strip()
    if action == "press_keys":
        return f"Press {value or ''} on «{label or 'focus'}»".strip()
    if action == "extract_text":
        return f"Extract «{label or 'content'}»"
    if action == "wait_for_text":
        return f"Wait for «{value or label}»"
    return f"Click «{label or 'element'}»"


def distill_flow(flow: Any, steps: list[Any]) -> dict[str, Any]:
    """Deterministic atomize → classify → summarize. Returns a skill summary."""
    domain = _domain_of(_get(flow, "start_url")) or next(
        (_domain_of(_get(s, "url_template")) for s in steps if _get(s, "action_type") == "navigate"), None
    )
    variables = _get(flow, "variables_json") or _get(flow, "variables") or []
    param_names = [v.get("name") for v in variables if isinstance(v, dict) and v.get("name")]

    # phases: collapse consecutive steps of the same class into a phase
    phases: list[dict[str, Any]] = []
    for s in steps:
        cls = _PHASE_BY_ACTION.get(_get(s, "action_type") or "click", "操作")
        if phases and phases[-1]["phase"] == cls:
            phases[-1]["steps"].append(_step_sentence(s))
        else:
            phases.append({"phase": cls, "steps": [_step_sentence(s)]})

    actions = {_get(s, "action_type") for s in steps}
    if "extract_text" in actions:
        capability = "extract"
    elif {"fill", "select"} & actions and any(_get(s, "action_type") == "click" for s in steps):
        capability = "form-submit"
    elif "navigate" in actions and len(actions) <= 2:
        capability = "navigate"
    else:
        capability = "web-action"

    outputs = [_step_sentence(s) for s in steps if _get(s, "action_type") in ("extract_text", "wait_for_text")]
    strategy = "; ".join(_step_sentence(s) for s in steps)[:1200]

    return {
        "name": _get(flow, "name"),
        "domain": domain,
        "capability": capability,
        "params": param_names,
        "outputs": outputs,
        "step_count": len(steps),
        "phases": phases,
        "strategy": strategy,
    }


def _serialize_steps(steps: list[Any]) -> list[dict[str, Any]]:
    out = []
    for s in steps:
        out.append({
            "action_type": _get(s, "action_type"),
            "description": _get(s, "description"),
            "locators": _get(s, "locators_json") or _get(s, "locators") or [],
            "value_template": _get(s, "value_template"),
            "url_template": _get(s, "url_template"),
            "options": _get(s, "options_json") or _get(s, "options") or {},
            "viewport": _get(s, "viewport_json") or _get(s, "viewport"),
        })
    return out


def flow_to_skill_md(flow: Any, steps: list[Any], distilled: dict[str, Any] | None = None) -> str:
    """Render a portable SKILL.md: frontmatter + human steps + lossless flow block."""
    d = distilled or distill_flow(flow, steps)
    variables = _get(flow, "variables_json") or _get(flow, "variables") or []
    fm = {
        "name": _get(flow, "name"),
        "description": _get(flow, "description") or f"{d['capability']} on {d.get('domain') or 'web'}",
        "format": "web-flow/0.1",
        "domain": d.get("domain"),
        "capability": d.get("capability"),
        "variables": variables,
    }
    front = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()

    lines = [f"---\n{front}\n---", "", f"# {fm['name']}", "", fm["description"], ""]
    if d.get("params"):
        lines += ["## Parameters", ""]
        for v in variables:
            if isinstance(v, dict) and v.get("name"):
                req = "required" if v.get("required") else "optional"
                secret = " (secret)" if v.get("secret") else ""
                lines.append(f"- `{{{{{v['name']}}}}}` — {v.get('label') or v['name']} · {req}{secret}")
        lines.append("")
    lines += ["## Steps", ""]
    for i, s in enumerate(steps, start=1):
        lines.append(f"{i}. {_step_sentence(s)}")
    lines.append("")
    # Lossless structured block for round-trip import (locators preserved).
    flow_payload = {
        "name": _get(flow, "name"),
        "description": _get(flow, "description"),
        "start_url": _get(flow, "start_url"),
        "variables": variables,
        "steps": _serialize_steps(steps),
    }
    lines += ["## flow (machine-readable, for replay import)", "",
              "```web-flow", json.dumps(flow_payload, ensure_ascii=False, indent=2), "```", ""]
    return "\n".join(lines)


# ── Import ────────────────────────────────────────────────────────────────────

_NAV_RE = re.compile(r"(?:打开|访问|导航到?|前往|go to|navigate to|open)\s*[:：]?\s*(\S+)", re.IGNORECASE)
_FILL_RE = re.compile(r"(?:在|into|in)\s*[「\"']?(.+?)[」\"']?\s*(?:填入|输入|type|enter|fill)\s*[:：]?\s*(.+)$", re.IGNORECASE)
_FILL_RE2 = re.compile(r"(?:填入|输入|type|enter|fill)\s+(.+?)\s+(?:到|into|in)\s*[「\"'](.+?)[」\"']", re.IGNORECASE)
_CLICK_RE = re.compile(r"(?:点击|单击|click|press|tap)\s*[「\"']?(.+?)[」\"']?\s*$", re.IGNORECASE)
_STEP_LINE_RE = re.compile(r"^\s*\d+[.)、]\s*(.+)$")


def _text_step_from_sentence(line: str) -> dict[str, Any] | None:
    """Best-effort: a human step sentence → a replayable step with TEXT locators.

    Foreign SKILL.md (e.g. Browser-BC) has no structured locators; we map verbs to
    navigate/fill/click with text/role locators so the SAME replay engine can run
    them (and fall back to VLM grounding when text misses)."""
    m = _NAV_RE.search(line)
    if m and ("http" in m.group(1) or "." in m.group(1)):
        url = m.group(1).strip().strip('。.,;')
        return {"action_type": "navigate", "description": line, "url_template": url, "locators": []}
    m = _FILL_RE2.search(line) or _FILL_RE.search(line)
    if m:
        if _FILL_RE2.search(line):
            value, target = m.group(1), m.group(2)
        else:
            target, value = m.group(1), m.group(2)
        return {"action_type": "fill", "description": line,
                "value_template": value.strip().strip('。.,;'),
                "locators": [{"kind": "text", "value": target.strip()}]}
    m = _CLICK_RE.search(line)
    if m:
        return {"action_type": "click", "description": line,
                "locators": [{"kind": "text", "value": m.group(1).strip()}]}
    return None


def parse_skill_md(markdown: str, *, fallback_name: str | None = None) -> dict[str, Any]:
    """Parse a SKILL.md into a create_flow payload {name, description, start_url,
    variables, steps}. Prefers the lossless ``boks-flow`` block; otherwise best-effort
    parses the human step list into text-locator steps."""
    fm: dict[str, Any] = {}
    body = markdown
    mm = _FRONTMATTER_RE.match(markdown)
    if mm:
        try:
            fm = yaml.safe_load(mm.group(1)) or {}
        except Exception:
            fm = {}
        body = mm.group(2)

    block = _FLOW_BLOCK_RE.search(markdown)
    if block:
        try:
            payload = json.loads(block.group(1))
            if isinstance(payload, dict) and payload.get("steps") is not None:
                payload.setdefault("name", fm.get("name") or fallback_name or "Imported Flow")
                payload.setdefault("description", fm.get("description"))
                payload.setdefault("variables", fm.get("variables") or [])
                return payload
        except Exception:
            pass

    # Foreign skill: derive steps from the human step list.
    steps: list[dict[str, Any]] = []
    for raw in body.splitlines():
        m = _STEP_LINE_RE.match(raw)
        if not m:
            continue
        st = _text_step_from_sentence(m.group(1).strip())
        if st:
            steps.append(st)
    start_url = next((s.get("url_template") for s in steps if s.get("action_type") == "navigate"), None)
    return {
        "name": (fm.get("name") or fallback_name or "Imported Skill"),
        "description": fm.get("description"),
        "start_url": start_url,
        "variables": fm.get("variables") or [],
        "steps": steps,
        "imported_foreign": True,
    }
