#!/usr/bin/env python3
"""Build and install the skill; existing skill folders are never overwritten."""
import argparse
import os
from pathlib import Path
import shutil
import tempfile
import zipfile
from build_skill import build,SKILL_NAME


def install(target,profiles_file=None):
    target=Path(target).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise FileExistsError('Existing skill was not changed: '+str(target))
    with tempfile.TemporaryDirectory(prefix='scheduler-skill-install-') as temp:
        archive=build(Path(temp)/'dist',profiles_file)
        unpack=Path(temp)/'unpack'
        with zipfile.ZipFile(archive) as package:
            package.extractall(unpack)
        target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copytree(unpack/SKILL_NAME,target)
    return target


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    parser.add_argument('--target',default=str(home/'skills'/SKILL_NAME))
    parser.add_argument('--profiles',help='Optional local alias-to-email JSON to include')
    args=parser.parse_args()
    try:
        print(install(args.target,args.profiles))
    except (OSError,ValueError) as error:
        parser.exit(1,str(error)+'\n')
    print('Start a new Codex session and invoke $'+SKILL_NAME)


if __name__=='__main__': main()
