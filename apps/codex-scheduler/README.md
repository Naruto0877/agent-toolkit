# codex-scheduler

The first runnable application in [agent-toolkit](../../README.md). It schedules official Codex work using isolated ChatGPT account profiles, structured usage observations, durable SQLite state, and a single worker. It does not execute Gemini/Claude tasks; see [provider extensions](../../docs/ADDING-PROVIDERS.md).

## Install from the workspace root

```sh
python3 tools/workspace.py install codex-scheduler
export PATH="$PWD/apps/codex-scheduler/.venv/bin:$PATH"
export CODEX_SCHEDULER_HOME="$PWD/apps/codex-scheduler/.state"
codex-scheduler doctor
```

The installer links the local `cli-provider-core` package and app source into this app's virtual environment without downloading third-party Python packages. Keep the full checkout or generated skill workspace together. Standard package metadata declares the shared dependency; this source installer is the documented offline installation path.

## Accounts and jobs

```sh
codex-scheduler account add A --email first@example.com
codex-scheduler account verify A
codex-scheduler status --refresh
PROJECT=/absolute/path/to/your/project
codex-scheduler run --auto --project "$PROJECT" --sandbox read-only 'Review the code and report issues'
codex-scheduler worker
```

Replace the example email/project/task. Add B/C later with their intended emails. Account selection with `--account B` is binding. Auto mode compares fresh applicable windows and estimated headroom, preferring the earliest usable refresh. Unknown usage excludes an account from auto selection, while explicit selection remains available for new work.

A confirmed limit keeps the selected account/thread and schedules a durable recheck for all known blocking windows. Fresh capacity is required before one continuation when a worker is free. Missing reset estimates, weekly exhaustion, pending approvals, and cancellations are preserved. Empty queues send zero model messages.

| Action | Command |
|---|---|
| Installation/schema checks | `codex-scheduler doctor` |
| Profile/usage refresh | `codex-scheduler status --refresh` |
| Implemented adapters | `codex-scheduler providers` |
| Queue | `codex-scheduler queue` |
| Inspect | `codex-scheduler inspect JOB_ID` |
| Cancel | `codex-scheduler cancel JOB_ID` |
| Approvals | `codex-scheduler approvals` |
| Answer command/file approval | `codex-scheduler approve APPROVAL_ID --decision accept` |

For a large task's next checkpoint, use `--size large` and optionally `--headroom 25`. The default small/large estimates are 5%/15% per window; they are not token predictions or completion guarantees. Workspace-write with on-request approvals is the default sandbox.

## Tests and details

```sh
python3 tools/workspace.py test codex-scheduler
```

Run this from the workspace root; it tests the shared dependency before the scheduler. A source-only standalone copy missing the shared package is incomplete. Current runtime code lives in `codex_scheduler/`, with Codex/fake adapters and transport under `codex_scheduler/providers/`.

[Setup](docs/SETUP.md) · [Operations and recovery](docs/OPERATIONS.md) · [Architecture](docs/ARCHITECTURE.md) · [Validation](docs/VALIDATION.md) · [Skill catalog](../../docs/SKILLS.md)
