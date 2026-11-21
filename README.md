# agent-toolkit

A reusable workspace for CLI applications, shared provider contracts, and an expanding skill catalog. Application code, provider implementations, and skill instructions have separate homes.

The first working application is **codex-scheduler**: isolated account profiles, usage-aware selection, durable queues, and account-pinned resume. Gemini, Claude, and custom CLIs have an extension path; their real integrations are not implemented yet.

## Layout

```text
apps/
  codex-scheduler/          Runnable scheduler, CLI, tests, docs, and examples
packages/
  cli-provider-core/        Shared provider/session contracts and capability registry
skills/
  catalog.json              Skill IDs, locations, and project dependencies
  codex/
    setup-codex-scheduler/   Setup workflow and helper; generated assets stay out of Git
tools/
  workspace.py              List, install, run, and test workspace projects
  skills/                   Catalog-driven skill builder and installer
  tests/                    Packaging and extension checks
docs/                       Workspace structure and contribution/extension guides
workspace.json              Project paths, source allowlists, and dependency graph
```

New skills are catalog entries, not changes to a Codex-specific packaging script. New CLI integrations own their protocol, login, usage, and resume behavior behind a provider boundary. The skill host and the CLI being configured are separate concepts.

## Clone and check

Use Python 3.10+ on macOS/Linux, or WSL/Linux on Windows. Authenticate to GitHub with access to this private repository:

```sh
git clone https://github.com/Naruto0877/agent-toolkit.git
cd agent-toolkit
python3 tools/workspace.py list
python3 tools/workspace.py test
python3 tools/workspace.py providers
```

Tests use fixtures and local process doubles; no Codex login, network, or model messages are required. Provider listing distinguishes the implemented Codex backend from its fake fixture. It does not claim Gemini/Claude support.

## Use the Codex scheduler

```sh
python3 tools/workspace.py install codex-scheduler
export PATH="$PWD/apps/codex-scheduler/.venv/bin:$PATH"
export CODEX_SCHEDULER_HOME="$PWD/apps/codex-scheduler/.state"
codex-scheduler doctor
codex-scheduler account add A --email first@example.com
codex-scheduler status --refresh
```

Replace the example email and complete the official browser flow. A becomes usable after its own verification/capacity check; other profiles can be added later. Default Codex credentials are preserved, and an unexpected authenticated email disables the alias.

```sh
PROJECT=/absolute/path/to/your/project
codex-scheduler run --auto --project "$PROJECT" --sandbox read-only 'Review the code and report issues'
codex-scheduler worker
```

`run` enqueues and `worker` executes. Keep the foreground worker open; use another terminal with the same state directory for queue inspection and approvals. It does not adopt an existing chat or generate model messages for an empty queue.

You can also run a project's source launcher through the workspace tool without editing PATH:

```sh
python3 tools/workspace.py run codex-scheduler -- --help
python3 tools/workspace.py test codex-scheduler
```

See the [scheduler application guide](apps/codex-scheduler/README.md), [new-machine setup](apps/codex-scheduler/docs/SETUP.md), and [operations/recovery](apps/codex-scheduler/docs/OPERATIONS.md).

## Build or install a skill

```sh
python3 tools/skills/build.py --list
python3 tools/skills/build.py --skill setup-codex-scheduler --output dist
python3 tools/skills/install.py setup-codex-scheduler
```

The installer targets the current Codex skills directory by default and refuses to overwrite an existing skill. For saved account mappings, edit the ignored local file before installation:

```sh
cp apps/codex-scheduler/examples/profiles.example.json profiles.local.json
# Replace the example emails.
python3 tools/skills/install.py setup-codex-scheduler --profiles profiles.local.json
```

Choose one installation command; do not install twice over an existing skill. Start a new Codex session and ask:

```text
Use $setup-codex-scheduler to set up the scheduler on this machine.
```

The self-contained package includes only its declared projects plus transitive dependencies. Credentials, sessions, local configuration, virtual environments, and databases are excluded. Email mappings are included only with an explicit `--profiles` input. Official sign-in remains a local interactive step.

## Extend the workspace

- [Structure and boundaries](docs/STRUCTURE.md)
- [Add a skill](docs/ADDING-SKILLS.md)
- [Add a provider or CLI application](docs/ADDING-PROVIDERS.md)
- [Portable skill setup](docs/SKILLS.md)

Keep private state and local profiles out of Git. Queues/locks are local to each machine; same-account usage remains shared. Stop a worker before updating its source and rerun the project installer/tests after pulling changes.
