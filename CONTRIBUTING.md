# Contributing to FlowReplay

Thanks for your interest! FlowReplay is early (alpha). The most useful contributions
right now are **real recorded flows** that break replay, and fixes to the **locator
ladder** so they stop breaking.

## Development setup

```bash
git clone https://github.com/kuilenren/flowreplay && cd flowreplay
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
python -m playwright install chromium
pytest
```

## Ground rules

1. **Scope.** FlowReplay records and replays a *human demonstration*. We will not
   merge anti-bot-detection, fingerprint spoofing, CAPTCHA solving, or proxy/geo
   cloaking. See `SPEC.md` §Non-goals. PRs adding these will be closed.
2. **The format is a contract.** Changes to the `web-flow` block must keep older
   files parseable, or bump the `format:` version in `SPEC.md` and explain migration.
3. **Determinism first.** The core must run without an LLM. Anything LLM-shaped
   (e.g. a visual fallback) ships as an optional, off-by-default plugin.
4. **Tests.** New actions or locator kinds need a round-trip test and, once M1 lands,
   a golden-replay fixture.

## Reporting a broken flow

Open an issue with the `.SKILL.md` file (redact any secrets — they should be
`{{variables}}`, never literals) and the site behavior you saw. That is gold for us.
