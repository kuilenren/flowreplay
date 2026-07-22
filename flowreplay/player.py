"""Deterministic replay of a recorded flow.

For each step the player walks the locator ladder (css → role → text → xpath →
coordinate) and uses the first locator that resolves against the live page. When a
*fallback* locator wins, the player promotes it to the front of that step and
(optionally) writes the flow back to disk — so the recording heals itself and gets
more robust the more it runs. Replay is deterministic and LLM-free.

This is a slim executor: it re-implements the ~15 action primitives the reference
recorder emits directly on a Playwright ``page``, using Playwright's native locator
engine for the fallback ladder. Reserved actions (extract_*, monitor_*, search,
screenshot) are not part of M1.
"""
from __future__ import annotations

import asyncio
import re
from typing import Any, Awaitable, Callable

from .skillmd import flow_to_skill_md

_VAR_RE = re.compile(r"\{\{\s*([a-zA-Z_][a-zA-Z0-9_]*)\s*\}\}")

# Actions this milestone knows how to execute.
_SUPPORTED = {
    "navigate", "click", "fill", "select", "hover", "drag", "scroll",
    "scroll_to_text", "coordinate_click", "press_keys", "back", "forward",
    "reload", "wait", "handle_dialog", "upload", "download",
    "new_tab", "switch_tab", "close_tab",
    "extract_text", "extract_tables", "extract_structured",
}
# Known but not implemented yet (§SPEC 5.1 "reserved").
_RESERVED = {
    "search", "screenshot", "monitor_start", "monitor_poll", "monitor_stop",
}

# Parse a single <table> element into {headers, rows}.
_TABLE_JS = """el => {
  const tbl = el.tagName === 'TABLE' ? el : el.querySelector('table');
  if (!tbl) return null;
  const grid = Array.from(tbl.rows).map(r => Array.from(r.cells).map(c => (c.innerText || '').trim()));
  let headers = [], body = grid;
  if (tbl.querySelector('thead') && grid.length) { headers = grid[0]; body = grid.slice(1); }
  return { headers, rows: body };
}"""
# Parse every <table> on the page.
_ALL_TABLES_JS = """() => Array.from(document.querySelectorAll('table')).map(tbl => {
  const grid = Array.from(tbl.rows).map(r => Array.from(r.cells).map(c => (c.innerText || '').trim()));
  let headers = [], body = grid;
  if (tbl.querySelector('thead') && grid.length) { headers = grid[0]; body = grid.slice(1); }
  return { headers, rows: body };
})"""


class ReplayError(RuntimeError):
    """A step could not be replayed."""


# ── Variables & templating ────────────────────────────────────────────────────

def substitute(template: str | None, variables: dict[str, Any]) -> str:
    """Replace ``{{name}}`` placeholders. Unknown names are left literal."""
    if not template:
        return ""
    return _VAR_RE.sub(lambda m: str(variables.get(m.group(1), m.group(0))), template)


def resolve_variables(declared: list[Any], supplied: dict[str, Any] | None) -> dict[str, Any]:
    """Merge supplied values over declared defaults; error on a missing required
    variable *before* any step runs (§SPEC 6)."""
    supplied = dict(supplied or {})
    out: dict[str, Any] = {}
    for v in declared or []:
        if not isinstance(v, dict) or not v.get("name"):
            continue
        name = v["name"]
        if name in supplied:
            out[name] = supplied[name]
        elif v.get("default") is not None:
            out[name] = v["default"]
        elif v.get("required"):
            raise ReplayError(f"missing required variable: {name!r}")
    for k, val in supplied.items():
        out.setdefault(k, val)
    return out


# ── Locator ladder resolution ─────────────────────────────────────────────────

def _scope(page: Any, loc: dict[str, Any]) -> Any:
    """Scope to a same-origin iframe when the locator carries frame metadata."""
    fs = loc.get("frame_selector")
    if fs:
        try:
            return page.frame_locator(fs)
        except Exception:
            return page
    return page


def _candidate(page: Any, loc: dict[str, Any]) -> Any | None:
    """A Playwright Locator for an element locator, or None for coordinate/vision."""
    kind = loc.get("kind")
    root = _scope(page, loc)
    value = loc.get("value")
    if kind == "css" and value:
        return root.locator(value)
    if kind == "xpath" and value:
        return root.locator(f"xpath={value}")
    if kind == "role" and value:
        name = loc.get("name")
        return root.get_by_role(value, name=name) if name else root.get_by_role(value)
    if kind == "text" and value:
        return root.get_by_text(value)
    return None


async def _resolve(page: Any, locators: list[dict[str, Any]], timeout_ms: int):
    """Try locators in order; return (index, target, kind). ``target`` is a
    Playwright Locator for element kinds, or the locator dict for ``coordinate``."""
    per = max(600, timeout_ms // max(1, len(locators) or 1))
    for i, loc in enumerate(locators):
        kind = loc.get("kind")
        if kind in ("css", "role", "text", "xpath"):
            cand = _candidate(page, loc)
            if cand is None:
                continue
            try:
                await cand.first.wait_for(state="visible", timeout=per)
                return i, cand.first, kind
            except Exception:
                continue
        elif kind == "coordinate":
            return i, loc, "coordinate"
    return -1, None, None


# ── The runner ────────────────────────────────────────────────────────────────

class _Runner:
    def __init__(self, context: Any, page: Any, timeout_ms: int):
        self.context = context
        self.page = page
        self.timeout = timeout_ms
        self.extractions: dict[str, Any] = {}

    async def execute(self, step: dict[str, Any], variables: dict[str, Any]) -> int | None:
        """Run one step. Returns the winning locator index (for element steps), or
        None for steps that carry no locator ladder. Raises ReplayError on failure."""
        action = (step.get("action_type") or "").lower().strip().replace("-", "_")
        if action in _RESERVED:
            raise ReplayError(f"action {action!r} is reserved (not implemented in M1)")
        if action not in _SUPPORTED:
            raise ReplayError(f"unknown action_type: {action!r}")
        handler = getattr(self, f"_a_{action}")
        return await handler(step, variables)

    # -- navigation / history --
    async def _a_navigate(self, step, variables):
        url = substitute(step.get("url_template"), variables)
        if not url:
            raise ReplayError("navigate step has no url")
        wait = (step.get("options") or {}).get("wait_until") or "domcontentloaded"
        await self.page.goto(url, wait_until=wait, timeout=self.timeout * 2)
        return None

    async def _a_back(self, step, variables):
        await self.page.go_back(); return None

    async def _a_forward(self, step, variables):
        await self.page.go_forward(); return None

    async def _a_reload(self, step, variables):
        await self.page.reload(); return None

    async def _a_wait(self, step, variables):
        ms = int((step.get("options") or {}).get("ms", 800))
        await self.page.wait_for_timeout(ms); return None

    # -- element interactions (walk the ladder) --
    async def _click_target(self, step):
        idx, target, kind = await _resolve(self.page, step.get("locators") or [], self.timeout)
        if idx < 0:
            raise ReplayError("no locator in the ladder resolved")
        return idx, target, kind

    async def _a_click(self, step, variables):
        idx, target, kind = await self._click_target(step)
        if kind == "coordinate":
            await self.page.mouse.click(target["x"], target["y"])
        else:
            await target.click(timeout=self.timeout)
        return idx

    async def _a_fill(self, step, variables):
        value = substitute(step.get("value_template"), variables)
        idx, target, kind = await self._click_target(step)
        if kind == "coordinate":
            await self.page.mouse.click(target["x"], target["y"])
            await self.page.keyboard.type(value)
        else:
            await target.fill(value, timeout=self.timeout)
        return idx

    async def _a_select(self, step, variables):
        value = substitute(step.get("value_template"), variables)
        idx, target, kind = await self._click_target(step)
        if kind == "coordinate":
            raise ReplayError("select needs an element locator, only coordinate resolved")
        await target.select_option(value, timeout=self.timeout)
        return idx

    async def _a_hover(self, step, variables):
        idx, target, kind = await self._click_target(step)
        if kind == "coordinate":
            await self.page.mouse.move(target["x"], target["y"])
        else:
            await target.hover(timeout=self.timeout)
        return idx

    async def _a_upload(self, step, variables):
        path = substitute(step.get("value_template"), variables)
        if not path or path.startswith("{{"):
            raise ReplayError("upload needs a file path (supply the {{variable}})")
        idx, target, kind = await self._click_target(step)
        if kind == "coordinate":
            raise ReplayError("upload needs an element locator")
        await target.set_input_files(path, timeout=self.timeout)
        return idx

    async def _a_download(self, step, variables):
        opts = step.get("options") or {}
        async with self.page.expect_download(timeout=self.timeout * 2) as di:
            if step.get("locators"):
                idx, target, kind = await self._click_target(step)
                if kind == "coordinate":
                    await self.page.mouse.click(target["x"], target["y"])
                else:
                    await target.click(timeout=self.timeout)
            elif opts.get("url"):
                await self.page.goto(opts["url"])
                idx = None
            else:
                raise ReplayError("download step has neither locator nor url")
        download = await di.value
        save_as = substitute(step.get("value_template"), variables) or opts.get("save_as")
        if save_as:
            await download.save_as(save_as)
        return idx

    # -- pointer / keyboard --
    async def _a_scroll(self, step, variables):
        o = step.get("options") or {}
        await self.page.mouse.wheel(int(o.get("dx", 0)), int(o.get("dy", 0)))
        return None

    async def _a_scroll_to_text(self, step, variables):
        text = (step.get("options") or {}).get("text") or ""
        if not text:
            raise ReplayError("scroll_to_text step has no text")
        await self.page.get_by_text(text).first.scroll_into_view_if_needed(timeout=self.timeout)
        return None

    async def _a_drag(self, step, variables):
        o = step.get("options") or {}
        await self.page.mouse.move(int(o["from_x"]), int(o["from_y"]))
        await self.page.mouse.down()
        await self.page.mouse.move(int(o["to_x"]), int(o["to_y"]), steps=12)
        await self.page.mouse.up()
        return None

    async def _a_coordinate_click(self, step, variables):
        o = step.get("options") or {}
        await self.page.mouse.click(int(o["x"]), int(o["y"]))
        return None

    async def _a_press_keys(self, step, variables):
        keys = (step.get("options") or {}).get("keys") or ["Enter"]
        await self.page.keyboard.press("+".join(str(k) for k in keys))
        return None

    async def _a_handle_dialog(self, step, variables):
        # The recorder places a handle_dialog step just BEFORE the click that
        # triggers it, so we arm a one-shot handler here and the next step fires it.
        o = step.get("options") or {}
        action = (o.get("action") or "accept").lower()
        prompt_text = o.get("prompt_text")

        def _on(dialog):
            async def _run():
                try:
                    if action == "accept":
                        await (dialog.accept(prompt_text) if prompt_text is not None else dialog.accept())
                    else:
                        await dialog.dismiss()
                except Exception:
                    pass
            asyncio.ensure_future(_run())

        self.page.once("dialog", _on)
        return None

    # -- tabs --
    async def _a_new_tab(self, step, variables):
        self.page = await self.context.new_page()
        return None

    async def _a_switch_tab(self, step, variables):
        idx = int((step.get("options") or {}).get("index", -1))
        pages = self.context.pages
        if 0 <= idx < len(pages):
            self.page = pages[idx]
            await self.page.bring_to_front()
        return None

    async def _a_close_tab(self, step, variables):
        try:
            await self.page.close()
        except Exception:
            pass
        self.page = self.context.pages[-1] if self.context.pages else await self.context.new_page()
        return None

    # -- extraction (produces data; results land in the run's ``extractions``) --
    def _extract_key(self, step: dict[str, Any], action: str) -> str:
        return (step.get("options") or {}).get("name") or f"{action}_{len(self.extractions) + 1}"

    async def _a_extract_text(self, step, variables):
        idx, target, kind = await self._click_target(step)
        if kind == "coordinate":
            raise ReplayError("extract_text needs an element locator")
        self.extractions[self._extract_key(step, "text")] = (await target.inner_text()).strip()
        return idx

    async def _a_extract_tables(self, step, variables):
        if step.get("locators"):
            idx, target, kind = await self._click_target(step)
            if kind == "coordinate":
                raise ReplayError("extract_tables needs an element locator")
            data = await target.evaluate(_TABLE_JS)
            tables = [data] if data else []
        else:
            idx = None
            tables = await self.page.evaluate(_ALL_TABLES_JS)
        self.extractions[self._extract_key(step, "tables")] = tables
        return idx

    async def _a_extract_structured(self, step, variables):
        # options.fields maps a field name → a CSS selector to read inner_text from.
        fields = (step.get("options") or {}).get("fields") or {}
        out: dict[str, Any] = {}
        for name, selector in fields.items():
            try:
                out[name] = (await self.page.locator(selector).first.inner_text()).strip()
            except Exception:
                out[name] = None
        self.extractions[self._extract_key(step, "structured")] = out
        return None


# ── Self-healing ──────────────────────────────────────────────────────────────

def _promote(step: dict[str, Any], winning_index: int) -> None:
    """Move the locator that actually worked to the front, keeping all others as
    lower-priority fallbacks (never deletes)."""
    locs = step.get("locators") or []
    if 0 < winning_index < len(locs):
        step["locators"] = [locs[winning_index]] + locs[:winning_index] + locs[winning_index + 1:]


def _write_back(flow: dict[str, Any], source_path: str) -> None:
    with open(source_path, "w", encoding="utf-8") as fh:
        fh.write(flow_to_skill_md(flow, flow.get("steps") or []))


# ── Orchestration ─────────────────────────────────────────────────────────────

async def replay_flow(
    flow: dict[str, Any],
    variables: dict[str, Any] | None = None,
    *,
    headless: bool = True,
    heal: bool = True,
    timeout_ms: int = 8000,
    source_path: str | None = None,
    channel: str | None = None,
    on_step: Callable[[dict[str, Any]], Any] | None = None,
    inspect: Callable[[Any], Awaitable[Any]] | None = None,
) -> dict[str, Any]:
    """Replay ``flow`` in a real browser and return a run result::

        {success, steps: [{seq, action, success, locator_index, healed, error?}],
         healed, failed_step, inspection?}

    Variables are resolved up front (§SPEC 6). When ``heal`` and a fallback locator
    wins, the winning locator is promoted; if ``source_path`` is given, the healed
    flow is written back to that file. ``inspect`` (async) is called with the live
    page after the last successful step, and its return value is included as
    ``inspection`` — handy for assertions/tests.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise ReplayError(
            "Replay needs Playwright. Install with: pip install 'flowreplay[record]' "
            "&& python -m playwright install chromium"
        ) from exc

    values = resolve_variables(flow.get("variables") or [], variables)
    steps = flow.get("steps") or []
    viewport = _first_viewport(steps)
    results: list[dict[str, Any]] = []
    healed_any = False
    inspection: Any = None

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if channel:
            launch_kwargs["channel"] = channel
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context(viewport=viewport)
        page = await context.new_page()
        runner = _Runner(context, page, timeout_ms)
        for i, step in enumerate(steps):
            entry: dict[str, Any] = {"seq": i + 1, "action": step.get("action_type"), "success": False}
            try:
                idx = await runner.execute(step, values)
                entry["success"] = True
                entry["locator_index"] = idx
                entry["healed"] = bool(idx is not None and idx > 0)
                if entry["healed"] and heal:
                    _promote(step, idx)
                    healed_any = True
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
                results.append(entry)
                if on_step:
                    on_step(entry)
                break
            results.append(entry)
            if on_step:
                on_step(entry)
        else:
            if inspect is not None:
                try:
                    inspection = await inspect(runner.page)
                except Exception as exc:  # noqa: BLE001
                    inspection = {"inspect_error": str(exc)}
        await context.close()
        await browser.close()

    ok = len(results) == len(steps) and all(r["success"] for r in results)
    if heal and healed_any and source_path:
        _write_back(flow, source_path)
    return {
        "success": ok,
        "steps": results,
        "healed": healed_any,
        "failed_step": next((r["seq"] for r in results if not r["success"]), None),
        "extractions": runner.extractions,
        "inspection": inspection,
    }


def _first_viewport(steps: list[dict[str, Any]]) -> dict[str, int]:
    for s in steps:
        vp = s.get("viewport")
        if isinstance(vp, dict) and vp.get("width") and vp.get("height"):
            return {"width": int(vp["width"]), "height": int(vp["height"])}
    return {"width": 1280, "height": 800}
