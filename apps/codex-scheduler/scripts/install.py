#!/usr/bin/env python3
"""Install/repair this app using the selected supported Python, preserving state."""
import sys

if sys.version_info < (3,10):
    raise SystemExit('Python 3.10+ is required; selected Python is '+sys.version.split()[0]+'. Install a supported Python, then use tools/workspace.py install codex-scheduler --python /path/to/python3.12.')

from datetime import datetime
import json
from pathlib import Path
import shlex
import subprocess
import uuid

APP=Path(__file__).resolve().parents[1]


def version_of(python):
    try:
        result=subprocess.run([str(python),'-c','import json,sys; print(json.dumps(list(sys.version_info[:2])))'],capture_output=True,text=True,check=True)
        return tuple(json.loads(result.stdout))
    except (OSError,ValueError,subprocess.CalledProcessError):
        return None


def install(app=APP):
    shared=app.parents[1]/'packages/cli-provider-core/src'
    if not (shared/'cli_provider_core').is_dir():
        raise ValueError('Missing cli-provider-core. Install from the complete agent-toolkit checkout or generated skill bundle.')
    environment=app/'.venv'
    python=environment/'bin'/'python'
    previous=version_of(python) if environment.exists() else None
    backup=None
    if environment.exists() and previous!=sys.version_info[:2]:
        stamp=datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:6]
        backup=app/('.venv-backup-'+stamp)
        environment.rename(backup)
        print('Previous Python environment preserved at '+str(backup),flush=True)
    if not environment.exists():
        # A base executable remains valid even if the caller was itself in a venv.
        base=getattr(sys,'_base_executable',sys.executable)
        subprocess.run([base,'-m','venv',str(environment)],check=True)
    if version_of(python)!=sys.version_info[:2]:
        raise ValueError('Created environment does not match the selected Python; installation was not completed')
    configure='''from pathlib import Path
import site,sys
app=Path(sys.argv[1]); shared=Path(sys.argv[2])
(Path(site.getsitepackages()[0])/'agent-toolkit.pth').write_text(str(app)+'\\n'+str(shared)+'\\n')
'''
    subprocess.run([str(python),'-c',configure,str(app),str(shared)],check=True)
    launcher=environment/'bin'/'codex-scheduler'
    launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(python))+' '+shlex.quote(str(app/'bin/codex-scheduler'))+' "$@"\n')
    launcher.chmod(0o755)
    # Import the actual CLI before announcing success. This sends no model messages.
    subprocess.run([str(python),str(app/'bin/codex-scheduler'),'--version'],check=True,capture_output=True,text=True)
    print('Installed using Python '+sys.version.split()[0]+' ('+str(python)+').')
    print('For this terminal run:\nexport PATH='+shlex.quote(str(environment/'bin'))+':"$PATH"')
    return backup


if __name__=='__main__':
    try: install()
    except (OSError,ValueError,subprocess.CalledProcessError) as error:
        raise SystemExit('Installation failed: '+str(error)+'. Account state was not modified.')
