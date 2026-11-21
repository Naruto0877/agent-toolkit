# codex-scheduler

Run Codex tasks through isolated ChatGPT account profiles, with durable queues and usage-aware scheduling. Tasks stay on their selected account and saved thread when a usage limit requires waiting.

The CLI uses Python 3.10+ and the official Codex CLI. It has no third-party Python runtime dependencies. One foreground worker executes work; an empty queue sends no model messages.

## Quick start

Authenticate to GitHub with access to this private repository, then clone it:

```sh
git clone https://github.com/Naruto0877/codex-scheduler.git
cd codex-scheduler
sh install.sh
export PATH="$PWD/.venv/bin:$PATH"
export CODEX_SCHEDULER_HOME="$PWD/.state"
codex-scheduler doctor
```

Run the fixture tests without needing Codex login or network access:

```sh
./.venv/bin/python -m unittest discover -s tests -v
```

Create the first isolated profile and complete the official login in your terminal/browser:

```sh
codex-scheduler account add A --email first@example.com
codex-scheduler account verify A
codex-scheduler status --refresh
```

Replace the example email. Add B/C whenever ready with their own expected emails. Existing default Codex credentials are preserved. Every alias has its own `CODEX_HOME`; a mismatched email disables that alias.

Submit a task and run the worker:

```sh
PROJECT=/absolute/path/to/your/project
codex-scheduler run --auto --project "$PROJECT" --sandbox read-only 'Review the code and report issues'
codex-scheduler worker
```

`run` enqueues work. `worker` executes it and stays in the foreground. Use a second terminal with the same `CODEX_SCHEDULER_HOME` to inspect the queue or answer approvals. The scheduler manages CLI-submitted tasks, not this or another pre-existing chat.

## Commands

| Action | Command |
|---|---|
| Check installation/schema | `codex-scheduler doctor` |
| Fresh account/usage status | `codex-scheduler status --refresh` |
| Bind a task to an account | `codex-scheduler run --account B --project /path 'Task'` |
| Automatic selection | `codex-scheduler run --auto --project /path 'Task'` |
| Large-task checkpoint hint | `codex-scheduler run --auto --size large --headroom 25 --project /path 'Task'` |
| Pending jobs | `codex-scheduler queue` |
| Inspect history and attempts | `codex-scheduler inspect JOB_ID` |
| Cancel | `codex-scheduler cancel JOB_ID` |
| Pending approvals | `codex-scheduler approvals` |
| Answer command/file approval | `codex-scheduler approve APPROVAL_ID --decision accept` |

Auto mode refreshes stale observations, checks applicable windows, and prefers sufficient checkpoint headroom with the earliest usable refresh. Explicit account selection is binding. Unknown usage excludes an account from auto selection; manual selection remains available for new work.

A confirmed usage limit persists thread/turn IDs and a durable recheck time. Capacity is reread before one continuation is sent when the worker is free. A short-window reset cannot override an exhausted weekly window. Missing/revised reset estimates, approvals, and cancellations are retained.

A crash during submission may leave an ambiguous outcome: such jobs become `needs_review` instead of being replayed automatically. The scheduler does not promise exactly-once external side effects. See [operations and recovery](docs/OPERATIONS.md).

## Portable setup skill

Install the reusable skill from this clone:

```sh
python3 scripts/install_skill.py
```

For saved expected-email mappings, first create an ignored local configuration:

```sh
cp examples/profiles.example.json profiles.local.json
# Edit profiles.local.json with the intended emails.
python3 scripts/install_skill.py --profiles profiles.local.json
```

Use one of these installation commands, not both: an existing skill is never overwritten. Then start a new Codex session and ask:

```text
Use $setup-codex-scheduler to set up the scheduler on this machine.
```

The installer bundles this checkout's current source into the skill, validates its checksum, and excludes credentials, local state, and local configuration. Without a profiles file, the skill installs/tests the CLI and asks for account mappings before registration. You still complete official sign-ins locally. See [skill setup](docs/SKILL.md).

## Layout

```text
bin/                     Source launcher
codex_scheduler/         CLI, protocol transport, scheduling, usage, SQLite storage
scripts/                 Install support, skill packaging, live probe, macOS service generator
skills/                  Portable setup skill instructions and helper
examples/                Fake-clock scenarios and generic profile configuration
tests/                   Deterministic scheduler and process/transport tests
docs/                    Setup, operations, architecture, and validation notes
.github/workflows/       Offline fixture checks on Linux and macOS
```

[New-machine setup](docs/SETUP.md) · [Operations](docs/OPERATIONS.md) · [Architecture](docs/ARCHITECTURE.md) · [Validation](docs/VALIDATION.md)

## Updates

Commit or stash your source changes before pulling updates:

```sh
git pull --ff-only
sh install.sh
./.venv/bin/python -m unittest discover -s tests -v
```

Stop the worker before updating source. Keep `.state/` local and backed up separately. Account allowances are shared across machines, while queues/worktree locks are local; avoid duplicate jobs on a shared project. Do not synchronize the SQLite state between active installations.
