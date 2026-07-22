"""Static robustness grading for a recorded flow — no execution required.

A step's reliability across sessions is largely decided by its *primary* locator.
A stable CSS selector or a semantic role/text locator survives a re-render; a
framework-volatile id (Element-Plus ``#el-id-…``, React/Ant ``:r0:``, hashed
suffixes, ``nth-of-type`` chains) or a bare viewport coordinate does not. This
module scores that up-front so a fragile recording is flagged before its first
live replay instead of failing mid-run.
"""
from __future__ import annotations

from typing import Any

from .recorder import _css_is_volatile

# Actions that legitimately carry no locator (they act on the page/history/focus,
# not on a specific element) — they are not counted for/against robustness.
_STRUCTURAL_ACTIONS = {
    "navigate", "back", "forward", "reload", "wait",
    "press_keys", "handle_dialog", "scroll",
}


def _step_grade(step: Any) -> str:
    locs = step.get("locators") or step.get("locators_json") or []
    kinds = [l.get("kind") for l in locs if isinstance(l, dict)]
    if not kinds:
        return "structural"
    has_semantic = any(k in ("role", "text") for k in kinds)
    stable_css = any(
        l.get("kind") == "css" and l.get("value") and not _css_is_volatile(l.get("value"))
        for l in locs if isinstance(l, dict)
    )
    if has_semantic or stable_css:
        return "robust"
    if "coordinate" in kinds:
        # Only a viewport coordinate (or only volatile CSS) — replayable, but
        # breaks the moment the layout shifts.
        return "fragile"
    return "moderate"


def assess_flow_quality(steps: list[Any]) -> dict[str, Any]:
    """Grade a flow's locator robustness.

    Returns ``{grade, score, total_steps, graded_steps, robust, moderate,
    fragile, fragile_steps}`` where ``grade`` is ``robust`` (score ≥ 0.8),
    ``moderate`` (≥ 0.5), or ``fragile``. ``score`` is the fraction of
    element-targeting steps that resolve to a durable locator.
    """
    counts = {"robust": 0, "moderate": 0, "fragile": 0, "structural": 0}
    fragile_steps: list[dict[str, Any]] = []

    for i, step in enumerate(steps):
        g = _step_grade(step)
        counts[g] += 1
        if g in ("fragile", "moderate"):
            fragile_steps.append({
                "index": i + 1,
                "action_type": step.get("action_type"),
                "grade": g,
                "description": step.get("description"),
            })

    graded = counts["robust"] + counts["moderate"] + counts["fragile"]
    score = round(counts["robust"] / graded, 4) if graded else 1.0
    grade = "robust" if score >= 0.8 else "moderate" if score >= 0.5 else "fragile"

    return {
        "grade": grade,
        "score": score,
        "total_steps": len(steps),
        "graded_steps": graded,
        "robust": counts["robust"],
        "moderate": counts["moderate"],
        "fragile": counts["fragile"],
        "fragile_steps": fragile_steps,
    }
