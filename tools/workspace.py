#!/usr/bin/env python3
"""List, install, run, and test the projects declared in workspace.json."""
import argparse
import json
import importlib
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


def within(base,relative):
    relative=PurePosixPath(relative)
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Path must stay inside its declared root')
    path=base/Path(*relative.parts)
    if not path.resolve().is_relative_to(base.resolve()):
        raise ValueError('Path escapes its declared root')
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


def execute(args,cwd,root=ROOT):
    return subprocess.run([str(a) for a in args],cwd=cwd,env=environment(root),check=True)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('list')
    sub.add_parser('providers')
    test=sub.add_parser('test');test.add_argument('project',nargs='?')
    install=sub.add_parser('install');install.add_argument('project')
    run=sub.add_parser('run');run.add_argument('project');run.add_argument('args',nargs=argparse.REMAINDER)
    args=parser.parse_args(argv)
    projects=manifest()['projects']
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
        execute(['sh',within(path,project['installer'])],path)
    else:
        if not project.get('launcher'): raise ValueError('Project has no CLI launcher: '+args.project)
        forwarded=args.args[1:] if args.args[:1]==['--'] else args.args
        execute([sys.executable,within(path,project['launcher']),*forwarded],path)
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (ValueError,OSError,subprocess.CalledProcessError) as error:
        print(str(error),file=sys.stderr);sys.exit(1)
