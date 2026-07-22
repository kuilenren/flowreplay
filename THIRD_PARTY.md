# Third-Party Dependencies

FlowReplay is deliberately dependency-light. The **core** (parsing, distillation,
quality grading, SKILL.md round-trip) depends only on PyYAML. **Recording** and
the forthcoming **replay** engine drive a browser through Playwright.

| Component | Used for | License | Notes |
|-----------|----------|---------|-------|
| [PyYAML](https://pyyaml.org/) | SKILL.md front-matter parse/emit | MIT | Core dependency |
| [Playwright for Python](https://playwright.dev/python/) | Driving a real browser to record (and, in M1, replay) | Apache-2.0 | Optional extra `flowreplay[record]` |
| [pytest](https://pytest.org/) | Test suite | MIT | Dev-only |

## What is intentionally NOT here

FlowReplay records and replays a **human demonstration**. It does **not** ship,
and will not accept contributions of:

- anti-bot-detection / browser-fingerprint spoofing,
- CAPTCHA detection or solving (including third-party solver integrations),
- proxy-egress / geolocation cloaking, or
- any "stealth" evasion of a site's terms of service.

These are explicitly out of scope. See `SPEC.md` §"Non-goals".
