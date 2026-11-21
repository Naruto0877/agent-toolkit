# Portable setup skill

The skill is a setup workflow; the installed CLI/worker schedules tasks. Skill instructions and the helper live in `skills/setup-codex-scheduler`. Source ZIPs and settings are generated from the current checkout rather than checked into Git.

## Install into Codex

```sh
python3 scripts/install_skill.py
```

To include your expected-email mappings, create an ignored local file before installation:

```sh
cp examples/profiles.example.json profiles.local.json
# Edit the values, keeping the alias-to-email JSON object.
python3 scripts/install_skill.py --profiles profiles.local.json
```

Choose one installation command. The installer defaults to `${CODEX_HOME:-$HOME/.codex}/skills/setup-codex-scheduler` and refuses to overwrite an existing skill. Use `--target /chosen/skill/folder` for another destination. Review an existing installation before replacing it manually.

Start a new Codex session:

```text
Use $setup-codex-scheduler to set up the scheduler on this machine.
```

Without saved mappings, provide intended account emails when asked or register accounts directly with `account add ALIAS --email EMAIL`. No credentials are stored in the skill. Mappings are ordinary account metadata; include them in a portable package only when you intend its recipients to see those emails.

## Build a distributable ZIP

```sh
python3 scripts/build_skill.py --output dist
# Optional personalized package:
python3 scripts/build_skill.py --output dist --profiles profiles.local.json
```

This produces `dist/setup-codex-scheduler.zip`, containing the skill plus the current allowlisted scheduler source. It excludes `.state/`, `.venv/`, local configuration, credentials, saved sessions, and databases. The helper verifies the source checksum before deployment. Rebuild after changing source; generated output is ignored by Git.

On another machine, copy that ZIP into Downloads and extract it into the skills directory:

```sh
python3 -m zipfile -e "$HOME/Downloads/setup-codex-scheduler.zip" "${CODEX_HOME:-$HOME/.codex}/skills"
```

Inspect an existing skill with this name before extracting over it. Then start a new Codex session and invoke it as above.

The helper installs/tests the CLI, checks the local Codex schema, and registers provided aliases without login. Missing Codex does not block fake tests/source setup. Official interactive sign-in remains local; the helper sends no real model messages, starts no worker, and installs no OS service. It refuses a running worker, modified source, or changed existing alias binding.
