# New-machine setup

Use macOS or Linux with Python 3.10+. For Windows, use WSL/Linux; native Windows installation/service operation has not been validated for this build.

## Prerequisites

```sh
uname -s
python3 --version
command -v codex
codex --version
```

If Python is missing on a Mac with Homebrew: `brew install python`. On Debian/Ubuntu, install `python3 python3-venv curl git` and verify that the Python version is at least 3.10. The scheduler installer uses `venv` and downloads no Python packages.

For Codex, follow the [official installation guide](https://learn.chatgpt.com/docs/codex/cli). Its standalone macOS/Linux command is:

```sh
curl -fsSL https://chatgpt.com/codex/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
```

Use the bin directory printed by the installer if it differs. The scheduler was developed against Codex 0.171.0; run `doctor` against the version on this machine rather than assuming protocol compatibility.

## Clone and install

```sh
mkdir -p "$HOME/Tools"
git clone https://github.com/Naruto0877/codex-scheduler.git "$HOME/Tools/codex-scheduler"
cd "$HOME/Tools/codex-scheduler"
sh install.sh
```

In every terminal used for this installation:

```sh
export SCHEDULER_ROOT="$HOME/Tools/codex-scheduler"
export PATH="$SCHEDULER_ROOT/.venv/bin:$HOME/.local/bin:$PATH"
export CODEX_SCHEDULER_HOME="$SCHEDULER_ROOT/.state"
```

Keep the checkout at this path; rerun the installer after moving it. Save those exports in your shell startup file if desired. All global options (`--home`, `--codex`, `--backend`, `--fixture`, `--now`) precede the subcommand.

```sh
codex-scheduler doctor
cd "$SCHEDULER_ROOT"
"$SCHEDULER_ROOT/.venv/bin/python" -m unittest discover -s "$SCHEDULER_ROOT/tests" -v
```

Run the test command from the repository root so the source package is importable.

## Accounts

Replace each email below with its intended account:

```sh
codex-scheduler account add A --email first@example.com --no-login
codex-scheduler account add B --email second@example.com --no-login
codex-scheduler account add C --email third@example.com --no-login
codex-scheduler account login A
codex-scheduler account verify A
codex-scheduler status --refresh
```

A is usable after its own login/verification and capacity check; B/C can be signed in later. Use `account login B` and `account login C` without repeating registration. `--device-auth` is available for the official device flow when supported by Codex.

Choose the expected email and workspace in each browser flow. An email mismatch disables the alias. Workspace display names may be unavailable; status also shows the reported workspace/account ID. `account verify` uses fresh app-server processes so identities are checked independently of an earlier launcher process.

Profiles are isolated, and login checks that the default and other credential files remain intact. Tokens stay in Codex-owned credential files. Stop the worker before changing logins; unfinished jobs for an alias must be finished, cancelled, or reconciled first.

## Fake mode

No login or model messages are needed:

```sh
cd "$SCHEDULER_ROOT"
FAKE_HOME="$(mktemp -d)"
for ACCOUNT in A B C; do
  codex-scheduler --backend fake --home "$FAKE_HOME" --fixture examples/three-accounts.json --now 1000 account add "$ACCOUNT"
done
codex-scheduler --backend fake --home "$FAKE_HOME" --fixture examples/three-accounts.json --now 1000 run --auto --project "$PWD" 'Small review'
codex-scheduler --backend fake --home "$FAKE_HOME" --fixture examples/three-accounts.json --now 1000 worker --once
```

The example chooses A (20% left, refresh in eight minutes) over B (75% left, refresh in three hours). C starts exhausted. Fixture tests cover C being rechecked at twelve minutes while B runs and a saved C task resuming only after relevant capacity returns. Fake and real state directories cannot be mixed.

Fresh-machine installation does not migrate active jobs, approvals, saved sessions, or this conversation. Sign in again locally instead of copying `.state/`, `.venv/`, or `~/.codex/` from the other machine.
