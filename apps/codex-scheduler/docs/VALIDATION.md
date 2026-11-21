# Validation

## Offline fixtures and process doubles

```sh
python3 tools/workspace.py test codex-scheduler
```

The original scheduler's 54 tests cover isolated profiles/default-login preservation with process doubles; expected-email binding and migration; model/project eligibility; stale/sparse usage; multiple/weekly windows; explicit and automatic selection; due checks while another turn runs; one pinned continuation; cancellation/approval races; restart reconciliation; and worker/worktree locks.

Workspace tooling tests separately exercise catalog-driven skill packaging and installation; shared-package tests exercise custom registration and capability guards. CI runs offline tests on Linux/macOS with Python 3.10 and 3.12. Fake mode never invokes Codex or sends model messages. No account credentials are required in CI.

## Real verification performed during development

With official Codex 0.171.0 on macOS arm64, installed help/schema inspection passed. A signed-in account returned structured usage; one read-only smoke turn completed, then its thread and saved turn were found after app-server restart/resume. The default credential file was unchanged. Three isolated profiles were subsequently checked against their intended emails and distinct reported account IDs.

A real quota-exhaustion/reset cycle and native Windows execution were not verified. Fixture results are not evidence that another machine/version has passed real authentication or execution checks.

## Optional live probe

```sh
python3 scripts/live_probe.py --execute --report /tmp/codex-scheduler-live.json
```

This explicitly sends one tiny read-only model message using the default Codex login, then checks resume/history in a fresh app-server process. It does not test A/B/C credential isolation. Inspect the installed version's `doctor`, `account verify`, and `status --refresh` on each new machine. Live reports, machine paths, credentials, and runtime state are excluded from Git.
