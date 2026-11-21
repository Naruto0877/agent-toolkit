# Structure and boundaries

## Applications

Each runnable tool lives in `apps/<project>/`, owns its installer, package metadata, tests, CLI entrypoint, docs, and examples, and declares dependencies in `workspace.json`. It should not import another application's internals.

The existing `codex-scheduler` command and its `--account`/`--auto` behavior remain application-specific. Its transport and real/fake adapters now live under `codex_scheduler/providers/`. Its scheduler still understands Codex semantics; it is not advertised as a universal Gemini/Claude scheduler.

## Shared packages

Reusable runtime code lives in `packages/<package>/`. `cli-provider-core` defines backend/session contracts, explicit capabilities, and registry behavior. Codex's backend implements these contracts; its registry distinguishes live and fixture adapters. Native operation names/events remain provider-specific.

The workspace installer passes its selected Python through `AGENT_TOOLKIT_PYTHON`; `install --python /path/to/python` makes that choice explicit. The app validates Python 3.10+, preserves incompatible environments as ignored backups, links its local package through a `.pth` file, and imports the CLI before announcing success. No third-party Python runtime packages are downloaded. Normal distribution metadata also declares the dependency for tooling; the documented workspace installer is the offline source-install path.

## Skills

`skills/catalog.json` maps a stable skill ID to its folder, packaging patterns, target provider, and dependent projects. Skill instructions may be grouped by domain (`codex/`, future `gemini/`, `claude/`, `custom/`, or a shared domain) without changing the installer. Create a group only when adding a real skill; empty implementation folders are unnecessary.

A setup skill may depend on an application and shared packages. A pure instruction skill may declare no projects. Skill assets are generated at build time; nested source ZIPs and personalized settings are not committed. Installing a skill does not start an application or transfer credentials.

## Workspace tooling

`tools/workspace.py` reads the project manifest, tests dependencies before the selected project, and supplies source paths to subprocesses. It runs all project and tooling tests when no project is selected. `tools/skills/build.py` reads the catalog and packages the declared dependency closure with a reduced workspace manifest.

Allowlisted source and catalog paths must remain within their declared roots. Bundles omit `.state`, `.venv`, `.git`, caches, local profiles, credentials, and databases. Source hashes are checked by the setup helper before deployment. Existing installations/skills are not silently overwritten.

## Local data

The default documented scheduler state path is `apps/codex-scheduler/.state/`, overrideable with `CODEX_SCHEDULER_HOME` or `--home`. Existing installations are not migrated by this repository reorganization. Keep each application's state separate; global credential stores or another provider's session directory must not become a shared default.
