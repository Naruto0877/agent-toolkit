# Portable skills

List/build/install from the repository root:

```sh
python3 tools/skills/build.py --list
python3 tools/skills/build.py --skill setup-codex-scheduler --output dist
python3 tools/skills/build.py --all --output dist
python3 tools/skills/install.py setup-codex-scheduler
```

Each catalog entry has its own independent ZIP. The installer builds current source, defaults to `${CODEX_HOME:-$HOME/.codex}/skills/<skill-id>`, and refuses to overwrite a skill already there. Use `--target` for another exact folder.

To save expected account emails, copy `apps/codex-scheduler/examples/profiles.example.json` to ignored `profiles.local.json`, edit it, and pass `--profiles profiles.local.json` to build or install. These email mappings are account metadata, not login credentials; only include them when you intend the package's recipient to see them. Without mappings, the setup helper can install/test source before asking for intended accounts.

Copy a generated ZIP to another machine and install it:

```sh
python3 -m zipfile -e "$HOME/Downloads/setup-codex-scheduler.zip" "${CODEX_HOME:-$HOME/.codex}/skills"
```

Inspect an existing skill with the same name before extracting over it. Start a new Codex session and ask:

```text
Use $setup-codex-scheduler to set up the scheduler on this machine.
```

The current setup skill verifies its embedded workspace hash, deploys the declared app plus its shared package, runs workspace tests, checks the installed Codex schema, and registers supplied aliases without signing in. Official local browser/device sign-ins remain necessary. The helper does not submit real model work, start a worker, or install an OS service.

If this workspace is already cloned at the chosen target, use its `tools/workspace.py install codex-scheduler` command directly instead of trying to overwrite it with a generated source bundle. The helper refuses unrelated/modified installations and existing email-binding changes. It emits exact local login/status/worker commands and a setup report.

After deployment, the scheduler's docs live under `apps/codex-scheduler/docs/`; its state defaults to `apps/codex-scheduler/.state`. No existing chat, active job, credential file, or first-machine worker is migrated.
