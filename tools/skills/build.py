#!/usr/bin/env python3
"""Build selected skills and their declared workspace projects deterministically."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import re
import stat
import sys
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tools.workspace import ROOT,manifest,project_order,within

FIXED_DATE=(2026,1,1,0,0,0)
BLOCKED={'.state','.venv','.git','__pycache__','dist','build'}


def catalog(root=ROOT):
    data=json.loads((root/'skills/catalog.json').read_text())
    if data.get('version')!=1 or not isinstance(data.get('skills'),dict):
        raise ValueError('Unsupported skill catalog')
    for name,entry in data['skills'].items():
        if not re.fullmatch(r'[a-z][a-z0-9-]*',name): raise ValueError('Invalid skill ID')
        path=within(root/'skills',entry['path'])
        if not (path/'SKILL.md').is_file(): raise ValueError('Missing SKILL.md for '+name)
        project_order(entry.get('projects',[]),root)
        if entry.get('application') and entry['application'] not in project_order(entry.get('projects',[]),root):
            raise ValueError('Skill application is not in its declared project bundle')
    return data


def profiles_from(path=None):
    data=json.loads(Path(path).expanduser().read_text()) if path else {}
    if not isinstance(data,dict): raise ValueError('Profiles must be an alias-to-email JSON object')
    for alias,email in data.items():
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}',alias) or not isinstance(email,str) or not re.fullmatch(r'[^@\s]+@[^@\s]+',email.strip()):
            raise ValueError('Invalid alias or expected email')
    return {key:value.strip().casefold() for key,value in data.items()}


def allowed(path):
    return not (BLOCKED.intersection(path.parts) or any(part.startswith('.venv-backup-') for part in path.parts) or path.name in ('auth.json','profiles.local.json','settings.json') or path.name.startswith('.env') or path.name.endswith('.local.json') or path.suffix in ('.sqlite3','.db','.log','.pyc'))


def matched(base,patterns):
    result=[]
    for pattern in patterns:
        within(base,pattern)
        for path in base.glob(pattern):
            if path.is_symlink() or not path.resolve().is_relative_to(base.resolve()): raise ValueError('Source symlinks and escaping paths are not packaged')
            if path.is_file() and allowed(path.relative_to(base)): result.append(path)
    return sorted(set(result))


def skill_files(name,root=ROOT):
    entry=catalog(root)['skills'].get(name)
    if entry is None: raise ValueError('Unknown skill: '+name)
    base=within(root/'skills',entry['path'])
    paths=matched(base,entry['files'])
    if base/'SKILL.md' not in paths: raise ValueError('Skill files must include SKILL.md')
    return entry,base,paths


def add(package,name,data,mode=0o644):
    info=zipfile.ZipInfo(name,FIXED_DATE)
    info.compress_type=zipfile.ZIP_DEFLATED
    info.external_attr=(stat.S_IFREG|mode)<<16
    package.writestr(info,data)


def bundle_source(name,root=ROOT):
    entries=catalog(root)['skills'];entry=entries[name]
    workspace=manifest(root)
    selected=project_order(entry.get('projects',[]),root)
    reduced={**workspace,'projects':{key:workspace['projects'][key] for key in selected}}
    files={}
    for project_id in selected:
        project=workspace['projects'][project_id];base=within(root,project['path'])
        for path in matched(base,project['include']): files[path.relative_to(root).as_posix()]=path.read_bytes()
    # Repository tooling and extension instructions travel with the selected projects.
    for pattern in ['README.md','.gitignore','tools/*.py','tools/skills/*.py','tools/tests/*.py','docs/*.md']:
        for path in matched(root,[pattern]): files[path.relative_to(root).as_posix()]=path.read_bytes()
    _,base,paths=skill_files(name,root)
    for path in paths: files[path.relative_to(root).as_posix()]=path.read_bytes()
    files['workspace.json']=(json.dumps(reduced,indent=2)+'\n').encode()
    files['skills/catalog.json']=(json.dumps({'version':1,'skills':{name:entry}},indent=2)+'\n').encode()
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as package:
        for path,data in sorted(files.items()): add(package,'agent-toolkit/'+path,data)
    return output.getvalue(),reduced


def build(output,profiles_file=None,skill_name='setup-codex-scheduler',root=ROOT):
    profiles=profiles_from(profiles_file)
    entry,base,paths=skill_files(skill_name,root)
    output=Path(output).expanduser().resolve();output.mkdir(parents=True,exist_ok=True)
    archive=output/(skill_name+'.zip')
    with zipfile.ZipFile(archive,'w') as package:
        for path in paths: add(package,skill_name+'/'+path.relative_to(base).as_posix(),path.read_bytes())
        if entry.get('projects'):
            source,workspace=bundle_source(skill_name,root)
            project_id=entry.get('application')
            settings={'version':2,'source_root':'agent-toolkit','source_sha256':hashlib.sha256(source).hexdigest(),'project':project_id,'project_path':workspace['projects'][project_id]['path'] if project_id else None,'profiles':profiles}
            add(package,skill_name+'/assets/workspace-source.zip',source)
            add(package,skill_name+'/assets/settings.json',(json.dumps(settings,indent=2)+'\n').encode())
        elif profiles:
            raise ValueError('Account profiles apply only to a project setup skill')
    return archive


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    selection=parser.add_mutually_exclusive_group(required=True)
    selection.add_argument('--skill');selection.add_argument('--all',action='store_true');selection.add_argument('--list',action='store_true')
    parser.add_argument('--output',default=str(ROOT/'dist'))
    parser.add_argument('--profiles')
    args=parser.parse_args()
    entries=catalog()['skills']
    if args.list: print(json.dumps(entries,indent=2));return
    names=list(entries) if args.all else [args.skill]
    for name in names: print(build(args.output,args.profiles,name))


if __name__=='__main__':
    try: main()
    except (ValueError,OSError,KeyError) as error: raise SystemExit(str(error))
