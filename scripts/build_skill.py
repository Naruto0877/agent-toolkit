#!/usr/bin/env python3
"""Build a portable setup skill from allowlisted source, without local state."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import stat
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = 'setup-codex-scheduler'
SKILL = ROOT/'skills'/SKILL_NAME
TEMPLATE_FILES = ['SKILL.md','agents/openai.yaml','scripts/setup.py']
FIXED_DATE = (2026,1,1,0,0,0)


def profiles_from(path=None):
    data = json.loads(Path(path).expanduser().read_text()) if path else {}
    if not isinstance(data,dict):
        raise ValueError('Profiles must be an alias-to-email JSON object')
    for alias,email in data.items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}',alias) or not isinstance(email,str) or not re.fullmatch(r'[^@\s]+@[^@\s]+',email.strip()):
            raise ValueError('Invalid alias or expected email')
    return {key:value.strip().casefold() for key,value in data.items()}


def source_paths(root=ROOT):
    paths = [root/name for name in ['README.md','.gitignore','install.sh','pyproject.toml','bin/codex-scheduler']]
    for folder,pattern in [('codex_scheduler','*.py'),('tests','*.py'),('scripts','*.py'),('docs','*.md'),('examples','*.json')]:
        paths.extend(sorted((root/folder).glob(pattern)))
    paths.extend(root/'skills'/SKILL_NAME/name for name in TEMPLATE_FILES)
    for path in paths:
        if path.is_symlink() or not path.is_file():
            raise ValueError('Expected ordinary source file: '+str(path))
    return sorted(paths)


def add(package,name,data,mode=0o644):
    info = zipfile.ZipInfo(name,FIXED_DATE)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG|mode)<<16
    package.writestr(info,data)


def build(output,profiles_file=None):
    profiles = profiles_from(profiles_file)
    output = Path(output).expanduser().resolve()
    output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='scheduler-skill-build-') as temp:
        source = Path(temp)/'codex-scheduler-source.zip'
        with zipfile.ZipFile(source,'w') as package:
            for path in source_paths():
                add(package,'codex-scheduler/'+path.relative_to(ROOT).as_posix(),path.read_bytes(),stat.S_IMODE(path.stat().st_mode))
        data = source.read_bytes()
        settings = {'version':1,'source_sha256':hashlib.sha256(data).hexdigest(),'profiles':profiles}
        archive = output/(SKILL_NAME+'.zip')
        with zipfile.ZipFile(archive,'w') as package:
            for name in TEMPLATE_FILES:
                add(package,SKILL_NAME+'/'+name,(SKILL/name).read_bytes())
            add(package,SKILL_NAME+'/assets/codex-scheduler-source.zip',data)
            add(package,SKILL_NAME+'/assets/settings.json',(json.dumps(settings,indent=2)+'\n').encode())
    return archive


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'dist'))
    parser.add_argument('--profiles',help='Optional local alias-to-email JSON to include in the package')
    args=parser.parse_args()
    print(build(args.output,args.profiles))


if __name__=='__main__': main()
