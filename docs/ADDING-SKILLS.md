# Add a skill

1. Create `skills/<domain>/<skill-id>/SKILL.md` with a lowercase hyphenated `name`, a narrow `description`, and instructions specific to that workflow. Add helper scripts, references, or templates only when used. Codex UI metadata can live in `agents/openai.yaml`.
2. Add an entry to `skills/catalog.json`:

   ```json
   {
     "review-project": {
       "path": "custom/review-project",
       "provider": "custom",
       "projects": [],
       "files": ["SKILL.md", "agents/*.yaml", "references/**/*.md"]
     }
   }
   ```

   Put that entry inside the existing `skills` object. These are metadata examples, not a shipped custom integration. The target provider label is descriptive and does not enable a backend.
3. For a project setup skill, declare `projects` and an `application` that belongs to that dependency closure. The builder includes transitive dependencies from `workspace.json` and generates `assets/workspace-source.zip` plus checksummed `assets/settings.json`. The helper must understand the target application's actual CLI and authentication flow.
4. Build and inspect it:

   ```sh
   python3 tools/skills/build.py --skill review-project --output dist
   python3 tools/workspace.py test
   ```

   Or build every catalog entry with `--all`. Do not copy private state into a skill's static assets. A skill with no project bundle must not assume generated application settings exist.
5. Install a specific skill:

   ```sh
   python3 tools/skills/install.py review-project
   ```

   It installs into Codex's skills directory by default; `--target /exact/skill/folder` chooses another explicit destination. Other host-specific skill formats require their own verified metadata/installer rather than assuming every CLI discovers Codex skills identically.

The builder and installer resolve skill IDs from the catalog. No packaging function needs a new hardcoded branch for another skill. Tests demonstrate adding and packaging a second independent skill without changing builder code.
