#!/bin/sh
set -eu
cd "$(dirname "$0")"
python3 -m venv .venv
# No download or packaging dependency: this launcher uses the source checkout.
.venv/bin/python - <<'PY'
from pathlib import Path
import shlex
root = Path.cwd().resolve()
python = root/'.venv'/'bin'/'python'
launcher = root/'.venv'/'bin'/'codex-scheduler'
launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(root/'bin'/'codex-scheduler'))+' "$@"\n')
launcher.chmod(0o755)
print('Installed. For this terminal run:\nexport PATH='+shlex.quote(str(root/'.venv'/'bin'))+':"$PATH"')
PY
