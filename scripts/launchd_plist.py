#!/usr/bin/env python3
"""Generate a macOS LaunchAgent; registration remains a local operator action."""
import argparse
import os
from pathlib import Path
import plistlib
import shutil
import sys
p=argparse.ArgumentParser()
p.add_argument('--home',required=True)
p.add_argument('--codex',default='codex')
p.add_argument('--out',required=True)
args=p.parse_args()
root=Path(__file__).resolve().parents[1]
executable=root/'.venv'/'bin'/'codex-scheduler'
codex=shutil.which(args.codex)
if not codex or not executable.exists():
    raise SystemExit('Run ./install.sh and install Codex first')
home=Path(args.home).expanduser().resolve()
home.mkdir(parents=True,exist_ok=True,mode=0o700)
if sys.platform!='darwin': raise SystemExit('This generator is for macOS launchd')
value={'Label':'local.codex-scheduler','ProgramArguments':[str(executable),'--home',str(home),'--codex',str(Path(codex).absolute()),'worker'], 'RunAtLoad':True,'KeepAlive':True,'ThrottleInterval':10,'WorkingDirectory':str(root),'StandardOutPath':str(home/'worker.log'),'StandardErrorPath':str(home/'worker-error.log'),'EnvironmentVariables':{'PATH':os.environ.get('PATH',str(Path(codex).parent)+':/usr/bin:/bin:/usr/sbin:/sbin'),'SHELL':os.environ.get('SHELL','/bin/zsh')},'Umask':0o077,'ProcessType':'Background'}
out=Path(args.out).expanduser()
out.write_bytes(plistlib.dumps(value))
out.chmod(0o600)
print(out)
