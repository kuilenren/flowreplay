"""FlowReplay — record a browser flow once; keep it as a portable, self-healing
SKILL.md that agents replay deterministically.

Public API:
    record_flow            # async: drive a real browser and capture a flow
    compile_events_to_steps  # turn a raw recorder event buffer into steps
    flow_to_skill_md       # render a flow as a portable SKILL.md
    parse_skill_md         # read a SKILL.md back into a flow dict
    distill_flow           # deterministic capability summary (LLM-optional)
    assess_flow_quality    # static locator-robustness grade (no execution)
"""
from __future__ import annotations

from .player import ReplayError, replay_flow
from .quality import assess_flow_quality
from .recorder import compile_events_to_steps, record_flow
from .skillmd import distill_flow, flow_to_skill_md, parse_skill_md

__version__ = "0.1.0"

__all__ = [
    "record_flow",
    "compile_events_to_steps",
    "flow_to_skill_md",
    "parse_skill_md",
    "distill_flow",
    "assess_flow_quality",
    "replay_flow",
    "ReplayError",
    "__version__",
]
