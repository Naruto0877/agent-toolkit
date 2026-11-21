#!/usr/bin/env python3
"""List, install, run, and test the projects declared in workspace.json."""
import argparse
import json
import importlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]


def within(base,relative):
    relative=PurePosixPath(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Path must stay inside its declared root')
    path=base/Path(*relative.parts)
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise ValueError('Path escapes its declared root') from None
    return path


def manifest(root=ROOT):
    data=json.loads((root/'workspace.json').read_text())
    if data.get('version')!=1 or not isinstance(data.get('projects'),dict):
        raise ValueError('Unsupported workspace manifest')
    return data


def project_order(names,root=ROOT):
    projects=manifest(root)['projects']
    ordered,visiting=[],set()
    def visit(name):
        if name not in projects: raise ValueError('Unknown project: '+name)
        if name in visiting: raise ValueError('Cyclic project dependency: '+name)
        if name in ordered: return
        visiting.add(name)
        for dependency in projects[name].get('dependencies',[]): visit(dependency)
        visiting.remove(name)
        ordered.append(name)
    for name in names: visit(name)
    return ordered


def environment(root=ROOT):
    paths=[str(root)]
    for project in manifest(root)['projects'].values():
        paths.append(str(within(root,project['path'])/project.get('python_source','.')))
    env=dict(os.environ)
    if env.get('PYTHONPATH'): paths.append(env['PYTHONPATH'])
    env['PYTHONPATH']=os.pathsep.join(paths)
    return env


def select_python(requested=None):
    candidates=[requested] if requested else [sys.executable,*['python3.'+str(minor) for minor in range(15,9,-1)]]
    for candidate in candidates:
        executable=shutil.which(candidate)
        if not executable: continue
        try:
            result=subprocess.run([executable,'-c','import json,sys; print(json.dumps(list(sys.version_info[:2])))'],capture_output=True,text=True,check=True)
            if tuple(json.loads(result.stdout)) >= (3,10): return executable
        except (OSError,ValueError,subprocess.CalledProcessError):
            continue
    raise ValueError('Python 3.10+ is required. On macOS: brew install python@3.12; then run tools/workspace.py install codex-scheduler --python /path/to/python3.12.')


def execute(args,cwd,root=ROOT,python=None):
    env=environment(root)
    if python: env['AGENT_TOOLKIT_PYTHON']=str(python)
    return subprocess.run([str(a) for a in args],cwd=cwd,env=env,check=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    sub.add_parser('providers')
    test=sub.add_parser('test');test.add_argument('project',nargs='?')
    install=sub.add_parser('install');install.add_argument('project')
    install.add_argument('--python',help='Python 3.10+ executable used to create/repair the app environment')
    run=sub.add_parser('run');run.add_argument('project');run.add_argument('args',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    projects=manifest()['projects']
    if args.command in ('providers','test') and sys.version_info < (3,10):
        selected=select_python()
        return subprocess.run([selected,str(Path(__file__).resolve()),*(argv if argv is not None else sys.argv[1:])]).returncode
    if args.command=='list':
        print(json.dumps(projects,indent=2));return 0
    if args.command=='providers':
        for path in environment()['PYTHONPATH'].split(os.pathsep): sys.path.insert(0,path)
        inventory=[]
        for name,project in projects.items():
            if not project.get('provider_registry'): continue
            module,symbol=project['provider_registry'].split(':',1)
            registry=getattr(importlib.import_module(module),symbol)
            inventory.extend({'application':name,**entry} for entry in registry.describe())
        print(json.dumps(inventory,indent=2));return 0
    if args.command=='test':
        selected=project_order([args.project] if args.project else list(projects))
        for name in selected:
            project=projects[name];path=within(ROOT,project['path'])
            print('Testing '+name,flush=True)
            execute([sys.executable,'-m','unittest','discover','-s',project['tests'],'-v'],path)
        if not args.project:
            execute([sys.executable,'-m','unittest','discover','-s','tools/tests','-v'],ROOT)
        return 0
    if args.project not in projects: raise ValueError('Unknown project: '+args.project)
    project=projects[args.project];path=within(ROOT,project['path'])
    if args.command=='install':
        if not project.get('installer'): raise ValueError('Project has no source installer: '+args.project)
        selected=select_python(args.python or os.environ.get('AGENT_TOOLKIT_PYTHON'))
        execute(['sh',within(path,project['installer'])],path,python=selected)
    else:
        if not project.get('launcher'): raise ValueError('Project has no CLI launcher: '+args.project)
        forwarded=args.args[1:] if args.args[:1]==['--'] else args.args
        installed=path/'.venv/bin/python'
        selected=select_python(str(installed) if installed.exists() else None)
        execute([selected,within(path,project['launcher']),*forwarded],path)
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr);sys.exit(1)
