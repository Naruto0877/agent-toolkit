#!/usr/bin/env python3
"""Build and install a selected catalog skill, without replacing an existing skill."""
import argparse
import os
from pathlib import Path
import shutil
import sys
import tempfile
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from tools.skills.build import build


def install(target,profiles_file=None,skill_name='setup-codex-scheduler'):
    target=Path(target).expanduser().absolute()
    if target.exists() or target.is_symlink(): raise FileExistsError('Existing skill was not changed: '+str(target))
    with tempfile.TemporaryDirectory(prefix='toolkit-skill-install-') as temp:
        archive=build(Path(temp)/'dist',profiles_file,skill_name)
        unpack=Path(temp)/'unpack'
        with zipfile.ZipFile(archive) as package: package.extractall(unpack)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(unpack/skill_name,target)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('skill')
    parser.add_argument('--target',help='Exact destination skill folder')
    parser.add_argument('--profiles')
    args=parser.parse_args()
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    target=args.target or home/'skills'/args.skill
    print(install(target,args.profiles,args.skill))
    print('Start a new Codex session and invoke $'+args.skill)


if __name__=='__main__':
    try: main()
    except (OSError,ValueError,KeyError) as error: raise SystemExit(str(error))
