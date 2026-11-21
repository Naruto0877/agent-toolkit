#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
.venv/bin/python - <<'PY'
from pathlib import Path
import shlex,site
app=Path.cwd().resolve()
shared=app.parents[1]/'packages'/'cli-provider-core'/'src'
if not (shared/'cli_provider_core').is_dir():
    raise SystemExit('Missing cli-provider-core. Install from the complete agent-toolkit checkout or generated skill bundle.')
# Own-workspace dependencies are linked locally; no package download is needed.
(Path(site.getsitepackages()[0])/'agent-toolkit.pth').write_text(str(app)+'\n'+str(shared)+'\n')
python=app/'.venv'/'bin'/'python'
launcher=app/'.venv'/'bin'/'codex-scheduler'
launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(app/'bin/codex-scheduler'))+' "$@"\n')
launcher.chmod(0o755)
print('Installed. For this terminal run:\nexport PATH='+shlex.quote(str(app/'.venv'/'bin'))+':"$PATH"')
PY
