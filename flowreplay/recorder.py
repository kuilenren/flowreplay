"""Browser flow recorder — capture a human's actions once, in the real page.

``RECORDER_JS`` is injected into a live browser session. It attaches capture
listeners (capture phase, passive) for click / input / change / submit / keydown
and records, for EACH interaction, a multi-strategy locator so replay can fall
back from a CSS selector to text/role to an absolute viewport coordinate (visual
operation) when the DOM shifts. Events buffer on ``window.__flowreplayRecorder``
and are flushed by reading ``__flowreplayRecorder.events``.

``compile_events_to_steps`` turns the raw event buffer into ordered, persistable
flow step dicts (see SPEC.md for the format).
"""

from __future__ import annotations

import re
from typing import Any

# CSS selectors whose specificity comes from a value that is regenerated on every
# component mount (or is position-fragile) — matching them on replay is a coin
# flip, so they must not sit AHEAD of a semantic role/text locator.
_VOLATILE_CSS_RE = re.compile(
    r"""
      el-id-\d+-\d+          # Element Plus auto ids: #el-id-3268-14
    | :r[0-9a-z]+:           # React useId / Ant Design: :r0:
    | \bewc-[0-9a-f]{6,}     # web-component hashed ids
    | -[0-9a-f]{8,}\b        # long hashed suffixes (…-a1b2c3d4)
    | \bid_\d{3,}\b          # id_1234 style
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _css_is_volatile(selector: str | None) -> bool:
    """True when a CSS selector is auto-generated/position-fragile and so is
    unreliable as the PRIMARY locator across sessions."""
    s = (selector or "").strip()
    if not s:
        return False
    if _VOLATILE_CSS_RE.search(s):
        return True
    # Structural position chains (…:nth-of-type(2) > div) shift when siblings do.
    if ("nth-of-type" in s or "nth-child" in s) and (">" in s or " " in s):
        return True
    return False


# Injected once per page (and re-injected on navigation). Idempotent: a second
# injection no-ops if the recorder already installed itself.
RECORDER_JS = r"""
(() => {
  if (window.__flowreplayRecorder && window.__flowreplayRecorder.__installed) {
    window.__flowreplayRecorder.recording = true;
    try {
      if (typeof window.__flowreplayRecorder.scanFrames === "function") {
        window.__flowreplayRecorder.scanFrames();
      }
    } catch (e) {}
    return "already";
  }
  const R = { __installed: true, recording: true, events: [], startedAt: Date.now() };
  window.__flowreplayRecorder = R;

  const cssEscape = (s) => (window.CSS && CSS.escape ? CSS.escape(s) : String(s).replace(/[^a-zA-Z0-9_-]/g, "\\$&"));
  const safeArray = (xs) => {
    try { return Array.from(xs || []); } catch (e) { return []; }
  };

  function robustSelector(el) {
    if (!el || el.nodeType !== 1) return null;
    const rootDoc = el.ownerDocument || document;
    if (el.id) return "#" + cssEscape(el.id);
    const attrs = ["data-testid", "data-test", "data-cy", "name", "aria-label", "placeholder", "title"];
    for (const a of attrs) {
      const v = el.getAttribute && el.getAttribute(a);
      if (v) {
        const sel = el.tagName.toLowerCase() + "[" + a + "=" + JSON.stringify(v) + "]";
        try { if (rootDoc.querySelectorAll(sel).length === 1) return sel; } catch (e) {}
      }
    }
    const parts = [];
    let node = el;
    let depth = 0;
    while (node && node.nodeType === 1 && depth < 5) {
      let part = node.tagName.toLowerCase();
      if (node.id) { parts.unshift("#" + cssEscape(node.id)); break; }
      const parent = node.parentElement;
      if (parent) {
        const sames = safeArray(parent.children).filter((c) => c.tagName === node.tagName);
        if (sames.length > 1) part += ":nth-of-type(" + (sames.indexOf(node) + 1) + ")";
      }
      parts.unshift(part);
      node = node.parentElement;
      depth++;
    }
    return parts.join(" > ");
  }

  function roleOf(el) {
    const r = el.getAttribute && el.getAttribute("role");
    if (r) return r;
    const t = (el.tagName || "").toLowerCase();
    if (t === "a") return "link";
    if (t === "button") return "button";
    if (t === "input") {
      const it = (el.getAttribute("type") || "text").toLowerCase();
      if (it === "submit" || it === "button") return "button";
      if (it === "checkbox") return "checkbox";
      return "textbox";
    }
    if (t === "select") return "combobox";
    if (t === "textarea") return "textbox";
    return null;
  }

  function textOf(el) {
    const raw = el && (el.innerText || el.value || el.getAttribute("aria-label") || el.getAttribute("placeholder") || "");
    return String(raw || "").trim().slice(0, 120);
  }

  function closestAction(el) {
    if (!el || !el.closest) return el;
    return el.closest('a,button,input,[role="button"],[role="link"],[role="menuitem"],[onclick],[data-testid],[data-test]') || el;
  }

  function filenameFromHref(href, baseHref) {
    try {
      const u = new URL(href, baseHref || location.href);
      const name = decodeURIComponent((u.pathname.split("/").filter(Boolean).pop() || "").trim());
      return /\.[a-z0-9]{1,10}$/i.test(name) ? name.slice(0, 160) : null;
    } catch (e) {
      return null;
    }
  }

  function downloadHint(el) {
    const actionEl = closestAction(el);
    if (!actionEl || actionEl.nodeType !== 1) return null;
    const link = actionEl.closest && actionEl.closest("a[href]");
    const href = link ? link.href : (actionEl.getAttribute && actionEl.getAttribute("href"));
    const downloadName = link ? link.getAttribute("download") : (actionEl.getAttribute && actionEl.getAttribute("download"));
    const attrs = [
      textOf(actionEl),
      actionEl.getAttribute && actionEl.getAttribute("aria-label"),
      actionEl.getAttribute && actionEl.getAttribute("title"),
      actionEl.getAttribute && actionEl.getAttribute("data-testid"),
      actionEl.getAttribute && actionEl.getAttribute("data-test"),
      actionEl.getAttribute && actionEl.getAttribute("class"),
      actionEl.getAttribute && actionEl.getAttribute("id"),
      actionEl.getAttribute && actionEl.getAttribute("name"),
    ].join(" ").toLowerCase();
    const hrefLooksDownload = href && (/^(blob:|data:)/i.test(href) || /\.(csv|tsv|xlsx?|xlsm|ods|pdf|docx?|pptx?|zip|json|xml|html?)(\?|#|$)/i.test(href));
    const textLooksDownload = /(download|export|csv|excel|xlsx|pdf|report|\u4e0b\u8f7d|\u5bfc\u51fa|\u62a5\u8868|\u5ba2\u6237\u6570\u636e)/i.test(attrs);
    const tag = (actionEl.tagName || "").toLowerCase();
    // Only a GENUINE download trigger \u2014 an <a> that carries a `download` attr or
    // whose href is a file/blob \u2014 is recorded as a download step. Recording every
    // button whose text merely contains \u5bfc\u51fa/export as a download was wrong: on a
    // real export UI (\u66f4\u591a \u2192 \u5bfc\u51fa dropdown \u2192 \u8bbe\u7f6e\u5bfc\u51fa\u5217 panel \u2192 \u786e\u5b9a) the intermediate
    // "\u5bfc\u51fa" controls only OPEN a panel and produce no file, so replay hung waiting
    // for a download that never came. Text-only download buttons are recorded as
    // normal clicks instead; the file a JS click triggers is still captured by the
    // replay engine's passive per-click download capture.
    const genuineAnchorDownload = tag === "a" && textLooksDownload && (hrefLooksDownload || downloadName !== null);
    if (downloadName || hrefLooksDownload || genuineAnchorDownload) {
      const baseHref = actionEl.ownerDocument && actionEl.ownerDocument.defaultView ? actionEl.ownerDocument.defaultView.location.href : location.href;
      const saveAs = (downloadName && downloadName !== "") ? downloadName : filenameFromHref(href || "", baseHref);
      return { el: actionEl, extra: { href: href || null, save_as: saveAs || null, download_hint: true } };
    }
    return null;
  }

  function isHoverCandidate(el) {
    if (!el || el.nodeType !== 1) return false;
    const attrs = [
      el.getAttribute("aria-haspopup"),
      el.getAttribute("aria-expanded"),
      el.getAttribute("role"),
      el.getAttribute("data-testid"),
      el.getAttribute("data-test"),
      el.getAttribute("class"),
      el.getAttribute("id"),
    ].join(" ").toLowerCase();
    return /(menu|dropdown|select|popover|tooltip|nav|submenu|mega|hover)/.test(attrs);
  }

  function isTextEditingTarget(el) {
    if (!el || el.nodeType !== 1) return false;
    const tag = (el.tagName || "").toLowerCase();
    if (tag === "textarea") return true;
    if (el.isContentEditable) return true;
    if (tag !== "input") return false;
    const type = (el.getAttribute("type") || "text").toLowerCase();
    return !/^(button|submit|reset|checkbox|radio|file|range|color|date|time|hidden)$/i.test(type);
  }

  function normalizedKeyName(e) {
    let key = e.key || "";
    const aliases = {
      " ": "Space",
      "Spacebar": "Space",
      "Esc": "Escape",
      "Del": "Delete",
      "Left": "ArrowLeft",
      "Right": "ArrowRight",
      "Up": "ArrowUp",
      "Down": "ArrowDown",
    };
    key = aliases[key] || key;
    if (/^[a-z]$/i.test(key)) return key.toUpperCase();
    return key;
  }

  function keyCombo(e) {
    if (e.repeat) return null;
    const key = normalizedKeyName(e);
    if (!key || /^(Control|Meta|Alt|Shift)$/i.test(key)) return null;
    const hasModifier = !!(e.ctrlKey || e.metaKey || e.altKey);
    const nonTextAction = /^(Enter|Tab|Escape|ArrowUp|ArrowDown|ArrowLeft|ArrowRight|PageUp|PageDown|Home|End|Backspace|Delete|Space|F5|BrowserBack|BrowserForward|BrowserRefresh)$/i.test(key);
    if (!hasModifier && !nonTextAction) return null;
    if (isTextEditingTarget(e.target) && !hasModifier && !/^(Enter|Tab|Escape)$/i.test(key)) return null;
    const keys = [];
    if (e.ctrlKey) keys.push("Control");
    if (e.metaKey) keys.push("Meta");
    if (e.altKey) keys.push("Alt");
    if (e.shiftKey && key !== "Shift") keys.push("Shift");
    keys.push(key);
    return keys;
  }

  function safeFrameUrl(win) {
    try { return win && win.location ? win.location.href : null; } catch (e) { return null; }
  }

  function frameLabel(frameEl) {
    if (!frameEl || frameEl.nodeType !== 1) return null;
    return (
      frameEl.getAttribute("title") ||
      frameEl.getAttribute("name") ||
      frameEl.getAttribute("id") ||
      frameEl.getAttribute("src") ||
      "iframe"
    );
  }

  function framePathForDoc(doc) {
    const path = [];
    try {
      let win = doc && doc.defaultView;
      while (win && win !== window) {
        const frameEl = win.frameElement;
        if (!frameEl) break;
        path.unshift({
          selector: robustSelector(frameEl),
          frame: frameLabel(frameEl),
          src: frameEl.getAttribute("src") || null,
          url: safeFrameUrl(win),
        });
        win = frameEl.ownerDocument && frameEl.ownerDocument.defaultView;
      }
    } catch (e) {}
    return path;
  }

  function frameMetaForDoc(doc) {
    const path = framePathForDoc(doc);
    if (!path.length) return {};
    const leaf = path[path.length - 1] || {};
    return {
      frame_selector: leaf.selector || null,
      frame: leaf.frame || null,
      frame_url: leaf.url || null,
      frame_path: path,
    };
  }

  function viewportRect(el) {
    const rect = el.getBoundingClientRect();
    let x = rect.x;
    let y = rect.y;
    try {
      let win = el.ownerDocument && el.ownerDocument.defaultView;
      while (win && win !== window) {
        const frameEl = win.frameElement;
        if (!frameEl) break;
        const frameRect = frameEl.getBoundingClientRect();
        x += frameRect.x;
        y += frameRect.y;
        win = frameEl.ownerDocument && frameEl.ownerDocument.defaultView;
      }
    } catch (e) {}
    return { x, y, width: rect.width, height: rect.height };
  }

  function eventViewportPoint(e) {
    let x = e.clientX || 0;
    let y = e.clientY || 0;
    try {
      let win = e.target && e.target.ownerDocument && e.target.ownerDocument.defaultView;
      while (win && win !== window) {
        const frameEl = win.frameElement;
        if (!frameEl) break;
        const frameRect = frameEl.getBoundingClientRect();
        x += frameRect.x;
        y += frameRect.y;
        win = frameEl.ownerDocument && frameEl.ownerDocument.defaultView;
      }
    } catch (err) {}
    return { x: Math.round(x), y: Math.round(y) };
  }

  function record(type, el, extra) {
    try {
      if (!R.recording) return;
      if (!el || el.nodeType !== 1) return;
      const doc = el.ownerDocument || document;
      const rect = viewportRect(el);
      R.events.push(Object.assign({
        type,
        t: Date.now() - R.startedAt,
        url: location.href,
        selector: robustSelector(el),
        role: roleOf(el),
        text: textOf(el),
        tag: (el.tagName || "").toLowerCase(),
        input_type: el.getAttribute ? (el.getAttribute("type") || null) : null,
        bbox: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
        cx: Math.round(rect.x + rect.width / 2),
        cy: Math.round(rect.y + rect.height / 2),
        viewport: { width: window.innerWidth, height: window.innerHeight },
      }, frameMetaForDoc(doc), extra || {}));
    } catch (e) {}
  }

  function recordDialog(doc, dialogType, message, result, defaultValue) {
    try {
      if (!R.recording) return;
      const action = dialogType === "alert" ? "accept" : (result === null || result === false ? "dismiss" : "accept");
      R.events.push(Object.assign({
        type: "dialog",
        dialog_type: dialogType,
        message: String(message || "").slice(0, 500),
        default_value: defaultValue == null ? null : String(defaultValue).slice(0, 500),
        prompt_text: dialogType === "prompt" && result !== null ? String(result == null ? "" : result).slice(0, 500) : null,
        result: result === undefined ? null : result,
        action,
        t: Date.now() - R.startedAt,
        url: safeFrameUrl(doc && doc.defaultView) || location.href,
        viewport: { width: window.innerWidth, height: window.innerHeight },
      }, frameMetaForDoc(doc || document)));
    } catch (e) {}
  }

  function installDialogHooks(doc) {
    try {
      const view = doc && doc.defaultView;
      if (!view || view.__flowreplayRecorderDialogHooks) return;
      view.__flowreplayRecorderDialogHooks = true;
      const originalAlert = view.alert && view.alert.bind(view);
      const originalConfirm = view.confirm && view.confirm.bind(view);
      const originalPrompt = view.prompt && view.prompt.bind(view);
      if (originalAlert) {
        view.alert = (message) => {
          const result = originalAlert(message);
          recordDialog(doc, "alert", message, true, null);
          return result;
        };
      }
      if (originalConfirm) {
        view.confirm = (message) => {
          const result = originalConfirm(message);
          recordDialog(doc, "confirm", message, !!result, null);
          return result;
        };
      }
      if (originalPrompt) {
        view.prompt = (message, defaultValue) => {
          const result = originalPrompt(message, defaultValue);
          recordDialog(doc, "prompt", message, result, defaultValue);
          return result;
        };
      }
    } catch (e) {}
  }

  const scrollState = new WeakMap();
  const scrollTimers = new WeakMap();
  const hoverState = new WeakMap();
  const dragState = new WeakMap();

  function scrollTarget(doc, e) {
    const t = e.target;
    if (t && t.nodeType === 1 && t !== doc && t !== doc.body && t !== doc.documentElement) return t;
    return doc.scrollingElement || doc.documentElement || doc.body;
  }

  function installDoc(doc) {
    try {
      if (!doc || doc.__flowreplayRecorderInstalled) return;
      doc.__flowreplayRecorderInstalled = true;
      const view = doc.defaultView || window;
      installDialogHooks(doc);

      doc.addEventListener("click", (e) => {
        const dl = downloadHint(e.target);
        if (dl) record("download", dl.el, dl.extra);
        else record("click", closestAction(e.target) || e.target);
      }, true);

      doc.addEventListener("change", (e) => {
        const el = e.target;
        if (el && el.tagName === "INPUT" && /file/i.test(el.type || "")) {
          const files = safeArray(el.files).map((f) => ({ name: f.name, size: f.size, type: f.type || null }));
          record("upload", el, { files, file_count: files.length, value: "{{upload_file}}" });
        } else if (el && (el.tagName === "SELECT" || (el.tagName === "INPUT" && /checkbox|radio/i.test(el.type || "")))) {
          record("change", el, { value: el.value, checked: !!el.checked });
        }
      }, true);

      doc.addEventListener("input", (e) => {
        const el = e.target;
        if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) el.__flowreplayLastVal = el.value;
      }, true);

      doc.addEventListener("blur", (e) => {
        const el = e.target;
        if (el && el.__flowreplayLastVal != null) {
          record("fill", el, { value: el.__flowreplayLastVal });
          el.__flowreplayLastVal = null;
        }
      }, true);

      doc.addEventListener("keydown", (e) => {
        const keys = keyCombo(e);
        if (keys && keys[keys.length - 1] === "Enter") {
          const el = e.target;
          if (el && el.__flowreplayLastVal != null) {
            record("fill", el, { value: el.__flowreplayLastVal });
            el.__flowreplayLastVal = null;
          }
        }
        if (keys) record("press_keys", e.target || doc.body || document.body, { keys });
      }, true);

      doc.addEventListener("mousedown", (e) => {
        if (e.button !== 0) return;
        const el = closestAction(e.target) || e.target;
        if (!el || el.nodeType !== 1) return;
        const p = eventViewportPoint(e);
        dragState.set(doc, { el, from_x: p.x, from_y: p.y, last_x: p.x, last_y: p.y, moved: false });
      }, true);

      doc.addEventListener("mousemove", (e) => {
        const state = dragState.get(doc);
        if (!state) return;
        const p = eventViewportPoint(e);
        state.last_x = p.x;
        state.last_y = p.y;
        if (Math.abs(p.x - state.from_x) >= 8 || Math.abs(p.y - state.from_y) >= 8) state.moved = true;
      }, true);

      doc.addEventListener("mouseup", (e) => {
        const state = dragState.get(doc);
        if (!state) return;
        dragState.delete(doc);
        const p = eventViewportPoint(e);
        const toX = p.x || state.last_x;
        const toY = p.y || state.last_y;
        const dx = Math.round(toX - state.from_x);
        const dy = Math.round(toY - state.from_y);
        if (!state.moved || (Math.abs(dx) < 20 && Math.abs(dy) < 20)) return;
        record("drag", state.el, {
          from_x: state.from_x,
          from_y: state.from_y,
          to_x: Math.round(toX),
          to_y: Math.round(toY),
          dx,
          dy,
        });
      }, true);

      doc.addEventListener("mouseover", (e) => {
        const el = e.target;
        if (!isHoverCandidate(el)) return;
        const prior = hoverState.get(doc) || {};
        if (prior.timer) view.clearTimeout(prior.timer);
        const timer = view.setTimeout(() => {
          const cur = hoverState.get(doc);
          if (cur && cur.el === el && isHoverCandidate(el)) record("hover", el);
        }, 350);
        hoverState.set(doc, { el, timer });
      }, true);

      doc.addEventListener("scroll", (e) => {
        const el = scrollTarget(doc, e);
        const prev = scrollState.get(el) || { x: 0, y: 0 };
        const cur = { x: el.scrollLeft || view.scrollX || 0, y: el.scrollTop || view.scrollY || 0 };
        const priorTimer = scrollTimers.get(el);
        if (priorTimer) view.clearTimeout(priorTimer);
        const timer = view.setTimeout(() => {
          const dx = Math.round(cur.x - prev.x);
          const dy = Math.round(cur.y - prev.y);
          scrollState.set(el, cur);
          if (Math.abs(dx) < 40 && Math.abs(dy) < 80) return;
          record("scroll", el, { dx, dy });
        }, 180);
        scrollTimers.set(el, timer);
      }, true);
    } catch (e) {}
  }

  function installFrameDocs(rootDoc) {
    try {
      for (const frameEl of safeArray(rootDoc.querySelectorAll("iframe,frame"))) {
        try {
          if (!frameEl.__flowreplayRecorderLoadHook) {
            frameEl.__flowreplayRecorderLoadHook = true;
            frameEl.addEventListener("load", () => window.setTimeout(installAllDocs, 0), true);
          }
          const childDoc = frameEl.contentDocument;
          if (!childDoc) continue;
          installDoc(childDoc);
          installFrameDocs(childDoc);
        } catch (e) {}
      }
    } catch (e) {}
  }

  function installAllDocs() {
    installDoc(document);
    installFrameDocs(document);
  }

  installAllDocs();
  R.scanFrames = installAllDocs;
  R.stop = () => {
    if (R.scanTimer) window.clearInterval(R.scanTimer);
    R.recording = false;
    R.__installed = false;
  };
  R.scanTimer = window.setInterval(installAllDocs, 800);
  return "installed";
})();
"""


_FILLISH = {"fill"}
_CLICKISH = {"click"}


def _browser_navigation_shortcut(keys: Any) -> tuple[str, dict[str, Any]] | None:
    if not isinstance(keys, list):
        return None
    normalized = [str(k).strip() for k in keys if str(k).strip()]
    if not normalized:
        return None

    lowered = {k.lower() for k in normalized}
    key = normalized[-1].lower()
    has_alt = "alt" in lowered or "option" in lowered
    has_control = "control" in lowered or "ctrl" in lowered
    has_meta = bool(lowered & {"meta", "cmd", "command"})

    action: str | None = None
    if key == "browserback" or (has_alt and key in {"arrowleft", "left"}) or (has_meta and key == "["):
        action = "back"
    elif key == "browserforward" or (has_alt and key in {"arrowright", "right"}) or (has_meta and key == "]"):
        action = "forward"
    elif key in {"f5", "browserrefresh"} or ((has_control or has_meta) and key == "r"):
        action = "reload"

    if not action:
        return None
    return action, {"source": "recorded_browser_shortcut", "keys": normalized, "content_ready": True}


def compile_events_to_steps(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Turn a raw recorder event buffer into ordered flow-step dicts.

    Each step carries an ordered ``locators`` list (css → role/text → coordinate)
    plus an ``options`` blob. Consecutive duplicate clicks and empty fills are
    coalesced; navigations are inferred from URL changes between events.
    """
    steps: list[dict[str, Any]] = []
    last_url: str | None = None

    def _frame_meta(ev: dict[str, Any]) -> dict[str, Any]:
        meta: dict[str, Any] = {}
        for key in ("frame_selector", "frame", "frame_url"):
            value = ev.get(key)
            if isinstance(value, str) and value.strip():
                meta[key] = value.strip()
        frame_path = ev.get("frame_path")
        if isinstance(frame_path, list):
            bounded = [item for item in frame_path if isinstance(item, dict)][:5]
            if bounded:
                meta["frame_path"] = bounded
        return meta

    def _locators(ev: dict[str, Any]) -> list[dict[str, Any]]:
        locs: list[dict[str, Any]] = []
        frame_meta = _frame_meta(ev)
        css = ev.get("selector")
        css_loc = {"kind": "css", "value": css, **frame_meta} if css else None
        role_loc = (
            {"kind": "role", "value": ev["role"], "name": ev["text"], **frame_meta}
            if ev.get("role") and ev.get("text") else None
        )
        text_loc = (
            {"kind": "text", "value": ev["text"], **frame_meta}
            if ev.get("text") and ev.get("type") == "click" else None
        )
        # A STABLE css selector stays first (fastest, most precise). But a VOLATILE
        # one — a framework-generated id that changes every mount (Element Plus
        # #el-id-<n>-<n>, React/Ant :r0:, hashed suffixes) or a position-fragile
        # nth-of-type chain — is demoted BELOW the semantic role/text locators, so
        # replay tries the durable locator first instead of burning a full
        # actionability timeout on an id that can never match again.
        if css_loc and not _css_is_volatile(css):
            locs.append(css_loc)
            css_loc = None
        if role_loc:
            locs.append(role_loc)
        if text_loc:
            locs.append(text_loc)
        if css_loc:  # volatile css kept only as a last-resort fallback
            locs.append(css_loc)
        cx, cy = ev.get("cx"), ev.get("cy")
        if isinstance(cx, int) and isinstance(cy, int):
            locs.append({"kind": "coordinate", "x": cx, "y": cy})
        return locs

    def _dialog_step(ev: dict[str, Any]) -> dict[str, Any]:
        dialog_type = str(ev.get("dialog_type") or "dialog")
        action = str(ev.get("action") or "accept")
        options: dict[str, Any] = {
            "action": action,
            "dialog_type": dialog_type,
            "message": str(ev.get("message") or "")[:500],
        }
        if ev.get("default_value") is not None:
            options["default_value"] = str(ev.get("default_value") or "")[:500]
        if dialog_type == "prompt" and action == "accept":
            options["prompt_text"] = str(ev.get("prompt_text") if ev.get("prompt_text") is not None else ev.get("result") or "")
        return {
            "action_type": "handle_dialog",
            "description": f"Handle {dialog_type} dialog",
            "locators": [],
            "options": options,
            "viewport": ev.get("viewport"),
        }

    for ev in events:
        etype = ev.get("type")
        url = ev.get("url")
        # Emit a navigate step when the page URL changes (initial load + redirects).
        if url and url != last_url:
            steps.append({
                "action_type": "navigate",
                "url_template": url,
                "description": f"Open {url}",
                "locators": [],
                "options": {"wait_until": "domcontentloaded"},
                "viewport": ev.get("viewport"),
            })
            last_url = url

        if etype in _CLICKISH:
            # Skip a click that merely produced the navigation we just emitted
            # (e.g. clicking a link) only when it has no locator at all.
            locs = _locators(ev)
            if not locs_meaningful(locs):
                continue
            steps.append({
                "action_type": "click",
                "description": f"Click {ev.get('text') or ev.get('selector') or 'element'}",
                "locators": locs,
                "options": {},
                "viewport": ev.get("viewport"),
            })
        elif etype == "dialog":
            step = _dialog_step(ev)
            if steps and steps[-1].get("action_type") in {"click", "press_keys"}:
                steps.insert(len(steps) - 1, step)
            else:
                steps.append(step)
        elif etype in _FILLISH:
            val = ev.get("value")
            if val is None or val == "":
                continue
            steps.append({
                "action_type": "fill",
                "description": f"Fill {ev.get('text') or ev.get('selector') or 'input'}",
                "locators": _locators(ev),
                "value_template": str(val),
                "options": {"field_type": ev.get("input_type")},
                "viewport": ev.get("viewport"),
            })
        elif etype == "change" and ev.get("tag") == "select":
            steps.append({
                "action_type": "select",
                "description": f"Select {ev.get('text') or ''}",
                "locators": _locators(ev),
                "value_template": str(ev.get("value") or ""),
                "options": {},
                "viewport": ev.get("viewport"),
            })
        elif etype == "hover":
            locs = _locators(ev)
            if not locs_meaningful(locs):
                continue
            steps.append({
                "action_type": "hover",
                "description": f"Hover {ev.get('text') or ev.get('selector') or 'element'}",
                "locators": locs,
                "options": {},
                "viewport": ev.get("viewport"),
            })
        elif etype == "scroll":
            dx = ev.get("dx") if isinstance(ev.get("dx"), int) else 0
            dy = ev.get("dy") if isinstance(ev.get("dy"), int) else 0
            if abs(dx) < 40 and abs(dy) < 80:
                continue
            locs = _locators(ev)
            steps.append({
                "action_type": "scroll",
                "description": f"Scroll {dy}",
                "locators": locs if locs_meaningful(locs) else [],
                "options": {"dx": dx, "dy": dy},
                "viewport": ev.get("viewport"),
            })
        elif etype == "drag":
            from_x = ev.get("from_x") if isinstance(ev.get("from_x"), int) else ev.get("cx")
            from_y = ev.get("from_y") if isinstance(ev.get("from_y"), int) else ev.get("cy")
            to_x = ev.get("to_x")
            to_y = ev.get("to_y")
            if not all(isinstance(v, int) for v in (from_x, from_y, to_x, to_y)):
                continue
            dx = int(to_x) - int(from_x)
            dy = int(to_y) - int(from_y)
            if abs(dx) < 20 and abs(dy) < 20:
                continue
            locs = _locators(ev)
            steps.append({
                "action_type": "drag",
                "description": f"Drag {ev.get('text') or ev.get('selector') or 'element'}",
                "locators": locs if locs_meaningful(locs) else [],
                "options": {
                    "from_x": int(from_x),
                    "from_y": int(from_y),
                    "to_x": int(to_x),
                    "to_y": int(to_y),
                    "dx": dx,
                    "dy": dy,
                },
                "viewport": ev.get("viewport"),
            })
        elif etype == "upload":
            locs = _locators(ev)
            steps.append({
                "action_type": "upload",
                "description": "Upload workspace file",
                "locators": locs,
                "value_template": str(ev.get("value") or "{{upload_file}}"),
                "options": {"file_names": ev.get("files") or [], "file_count": ev.get("file_count") or 0},
                "viewport": ev.get("viewport"),
            })
        elif etype == "download":
            locs = _locators(ev)
            href = str(ev.get("href") or "").strip()
            save_as = str(ev.get("save_as") or "").strip()
            options: dict[str, Any] = {"source": "recorded_download"}
            if href.startswith(("http://", "https://")):
                options["url"] = href
            if save_as:
                options["save_as"] = save_as
            if not locs_meaningful(locs) and not options.get("url"):
                continue
            steps.append({
                "action_type": "download",
                "description": f"Download {ev.get('text') or ev.get('selector') or save_as or 'file'}",
                "locators": locs if locs_meaningful(locs) else [],
                "value_template": save_as or None,
                "options": options,
                "viewport": ev.get("viewport"),
            })
        elif etype == "press_keys":
            keys = ev.get("keys") or ["Enter"]
            navigation_shortcut = _browser_navigation_shortcut(keys)
            if navigation_shortcut:
                action, options = navigation_shortcut
                steps.append({
                    "action_type": action,
                    "description": f"Browser {action} via {'+'.join(options['keys'])}",
                    "locators": [],
                    "options": options,
                    "viewport": ev.get("viewport"),
                })
                continue
            steps.append({
                "action_type": "press_keys",
                "description": f"Press {'+'.join(keys)}",
                "locators": [],
                "options": {"keys": keys},
                "viewport": ev.get("viewport"),
            })
    return steps


def locs_meaningful(locs: list[dict[str, Any]]) -> bool:
    return any(l.get("kind") in ("css", "role", "text") or l.get("kind") == "coordinate" for l in locs)


# ── Interactive recording (drives a real browser via Playwright) ──────────────
#
# Reference recorder for the web-flow format. It injects RECORDER_JS into every
# document (initial load + each navigation) and drains the in-page event buffer
# on a timer, so actions survive page navigations, then compiles on stop. It
# records the primary browsing context; multi-tab/popup capture is a later
# milestone. Requires the optional ``playwright`` dependency.

from urllib.parse import urlparse as _urlparse  # noqa: E402

_DRAIN_JS = """() => {
  const r = window.__flowreplayRecorder;
  if (!r) return [];
  const e = r.events || [];
  r.events = [];
  return e;
}"""


def _host(url: str | None) -> str | None:
    try:
        return _urlparse(url or "").hostname or None
    except Exception:
        return None


async def record_flow(
    url: str,
    *,
    headless: bool = False,
    stop_event: Any = None,
    poll_interval: float = 0.7,
    name: str | None = None,
    browser_channel: str | None = None,
) -> dict[str, Any]:
    """Open ``url`` in a real browser, capture the user's actions, and return a
    flow dict ``{name, description, start_url, variables, steps}``.

    Recording runs until ``stop_event`` (an ``asyncio.Event``) is set, or until
    the user closes the browser window. Install the recorder extra first::

        pip install "flowreplay[record]" && python -m playwright install chromium
    """
    import asyncio

    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "Recording needs Playwright. Install with: "
            "pip install 'flowreplay[record]' && python -m playwright install chromium"
        ) from exc

    stop_event = stop_event or asyncio.Event()
    buffer: list[dict[str, Any]] = []

    async with async_playwright() as p:
        launch_kwargs: dict[str, Any] = {"headless": headless}
        if browser_channel:
            launch_kwargs["channel"] = browser_channel
        browser = await p.chromium.launch(**launch_kwargs)
        context = await browser.new_context()
        await context.add_init_script(RECORDER_JS)  # runs on every fresh document
        context.on("close", lambda: stop_event.set())
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded")
        try:
            await page.evaluate(RECORDER_JS)
        except Exception:
            pass

        async def _drain_once() -> None:
            for pg in list(context.pages):
                try:
                    evts = await pg.evaluate(_DRAIN_JS)
                except Exception:
                    continue
                if evts:
                    buffer.extend(evts)

        while not stop_event.is_set():
            await _drain_once()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
            except asyncio.TimeoutError:
                pass
        await _drain_once()
        try:
            await context.close()
            await browser.close()
        except Exception:
            pass

    steps = compile_events_to_steps(buffer)
    start_url = url
    for s in steps:
        if s.get("action_type") == "navigate" and s.get("url_template"):
            start_url = s["url_template"]
            break
    return {
        "name": name or f"Flow on {_host(start_url) or 'web'}",
        "description": None,
        "start_url": start_url,
        "variables": [],
        "steps": steps,
    }
