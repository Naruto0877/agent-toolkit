---
name: setup-codex-scheduler
description: Install or validate the bundled codex-scheduler CLI on a machine, register the user's isolated email-bound profiles, and prepare local login and worker commands. Use for setting up this scheduler elsewhere or repeating its setup; ordinary coding work and an existing chat do not become scheduler jobs.
---

# Set up Codex Scheduler

Use the bundled implementation rather than recreating the scheduler. It supports explicit/automatic account selection, account-pinned threads, usage-window rechecks, SQLite recovery, and a single local worker.

## Setup

1. Detect OS, shell, Python (3.10+), and official Codex CLI. Resolve paths on this machine. Prefer `~/Tools/agent-toolkit` unless the user chose another destination. If that workspace is already cloned, reuse `tools/workspace.py install codex-scheduler` and its app CLI instead of deploying over it. Application code lives at `apps/codex-scheduler`, separate from the skill and shared package. Native Windows setup is unvalidated; use WSL/Linux for the supplied shell installer.
2. Run `scripts/setup.py` with the local Python executable and an explicit target. Paths below are relative to this skill folder, not the current working directory:

   ```sh
   python3 scripts/setup.py --target "$HOME/Tools/agent-toolkit"
   ```

   Use `--plan` first when inspecting an unfamiliar existing setup, and `--codex /absolute/path/to/codex` for a CLI outside PATH. The catalog builder packages the Codex app and its transitive shared-package dependency. The helper verifies that workspace archive, deploys into a new or matching managed directory, installs the app, runs workspace tests and installed CLI/schema checks, and registers supplied aliases without starting login. It emits JSON with resolved paths, results, and next commands. It refuses unrelated/modified installations, changed alias bindings, and a running worker; inspect those conditions rather than overwriting files or state.
3. Use the mappings in `assets/settings.json` as defaults when present. Honor explicit replacements from the user. For changed mappings, pass `--profiles /absolute/profiles.json` containing an alias-to-email JSON object. If mappings are empty, ask for the intended aliases/emails before registering accounts; source installation and fixture tests can continue independently. Do not silently rebind an existing alias. Missing Codex does not prevent installing/testing/registering profiles; give the exact official installation step and continue the fake-backed checks. Do not treat unknown usage as capacity.
4. Complete the official interactive login only when a local terminal/browser is available. Otherwise give the generated `account login A` command, then B/C when ready. Registration is not authentication. Email verification must match the expected email; show workspace/account IDs and mark missing workspace names unknown. A becomes usable after its own verification and capacity check; B/C can be added later.
5. After logins, run `account verify` and `status --refresh` through the installed CLI, including a fresh launcher process. These checks send no model messages. Keep fixture results, actual signed-in verification, and untested real quota-reset behavior separate.
6. If the user asked to run the worker, start one foreground worker and verify its kernel lock/process and queue. Otherwise report the exact worker command. Only CLI-submitted tasks are scheduled; do not adopt this chat or invent a task. If background execution was requested, read the deployed `apps/codex-scheduler/docs/OPERATIONS.md` for macOS launchd/Linux instructions and resolve the executable/state paths locally.

The helper does not download dependencies, log in, submit real model work, start a worker, or install an OS service. Installation requires write access to the chosen destination; request the environment's normal filesystem approval when necessary, after preparing the concrete result.

## Account and recovery invariants

- Keep tokens in Codex-owned credential files under separate profile `CODEX_HOME` directories, never in the skill, scheduler DB, or logs. Preserve the machine's default Codex login. Do not transfer `.state/`, `.venv/`, or credentials from another machine.
- An explicit account selection is binding. A limited task retains its account/thread. Resume only after fresh applicable usage windows and backend permission show capacity, with one continuation when the worker is free.
- Keep an empty queue free of model messages. Retain approvals and cancellations. Uncertain submission history requires review, not automatic replay; do not claim exactly-once external side effects.
- Same-account allowances are shared across machines. Queues and locks are local; do not launch duplicate work on a shared project from separate machines. The skill is a setup workflow, not the persistent worker.

The deployed `apps/codex-scheduler/docs/SETUP.md` and `OPERATIONS.md` contain detailed dependency, login, fake-clock, worker, and recovery commands; read the relevant section when needed. Historical validation notes describe the original machine. Report what passed here and identify the next local login or dependency step without claiming a fresh profile is signed in.
