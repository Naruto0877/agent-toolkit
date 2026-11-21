#!/usr/bin/env python3
"""Install the bundled scheduler without carrying credentials or submitting work."""
import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile

SKILL = Path(__file__).resolve().parents[1]
MARKER = '.scheduler-skill-source.json'


class SetupError(Exception):
    pass


def load_bundle():
    settings = json.loads((SKILL/'assets'/'settings.json').read_text())
    bundle = SKILL/'assets'/'codex-scheduler-source.zip'
    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    if digest != settings['source_sha256']:
        raise SetupError('Bundled source checksum mismatch; obtain an intact skill package')
    files = {}
    with zipfile.ZipFile(bundle) as package:
        for item in package.infolist():
            path = PurePosixPath(item.filename)
            if path.is_absolute() or '..' in path.parts or path.parts[:1] != ('codex-scheduler',):
                raise SetupError('Bundled source has an unexpected path')
            if stat.S_ISLNK(item.external_attr >> 16):
                raise SetupError('Bundled source must not contain symlinks')
            if item.is_dir():
                continue
            relative = Path(*path.parts[1:])
            if not relative.parts or any(p in ('.state','.venv','.git','__pycache__') for p in relative.parts) or relative.name == 'auth.json' or relative.suffix in ('.db','.sqlite3','.log'):
                raise SetupError('Local state is not allowed in the bundled source')
            if relative in files:
                raise SetupError('Duplicate source file')
            files[relative] = package.read(item)
    return settings, digest, files


def profile_map(path, settings):
    profiles = json.loads(Path(path).expanduser().read_text()) if path else settings['profiles']
    if not isinstance(profiles, dict):
        raise SetupError('Profiles must be an alias-to-email JSON object')
    for alias, email in profiles.items():
        if not isinstance(alias, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}', alias):
            raise SetupError('Invalid profile alias')
        if not isinstance(email, str) or not re.fullmatch(r'[^@\s]+@[^@\s]+', email.strip()):
            raise SetupError('Invalid expected email')
    return {alias: email.strip().casefold() for alias,email in profiles.items()}


def target_status(target, digest, files):
    if not target.exists() or (target.is_dir() and not any(target.iterdir())):
        return 'new'
    marker = target/MARKER
    if not marker.is_file():
        raise SetupError('Existing target is not managed by this skill; inspect it or choose another --target')
    if json.loads(marker.read_text()).get('source_sha256') != digest:
        raise SetupError('Existing installation uses a different bundle; no upgrade or state migration was attempted')
    for relative, content in files.items():
        file = target/relative
        if file.is_symlink() or not file.is_file() or file.read_bytes() != content:
            raise SetupError('Existing source differs from the bundle; files were not overwritten: '+str(relative))
    return 'matching'


@contextlib.contextmanager
def worker_lock(state):
    import fcntl
    state.mkdir(parents=True,exist_ok=True,mode=0o700)
    with (state/'worker.lock').open('a+b') as handle:
        os.chmod(state/'worker.lock',0o600)
        try:
            fcntl.flock(handle.fileno(),fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise SetupError('Worker/login holds this state directory; stop it before setup') from None
        yield


def run(args, cwd, timeout=120):
    try:
        result = subprocess.run([str(a) for a in args],cwd=cwd,text=True,capture_output=True,timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        raise SetupError('Could not complete: '+shlex.join([str(a) for a in args])) from None
    if result.returncode:
        # Never echo arbitrary process output. These commands do not initiate login.
        raise SetupError('Command failed; inspect locally: '+shlex.join([str(a) for a in args]))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target',default=str(Path.home()/'Tools'/'codex-scheduler'))
    parser.add_argument('--codex',default='codex')
    parser.add_argument('--profiles',help='Optional alias-to-email JSON; never changes an existing binding')
    parser.add_argument('--plan',action='store_true',help='Inspect prerequisites and destination without writes')
    args = parser.parse_args(argv)
    if sys.version_info < (3,10):
        raise SetupError('Python 3.10+ required. macOS with Homebrew: brew install python')
    if os.name == 'nt':
        raise SetupError('Use WSL/Linux for this installer; native Windows setup has not been validated')
    settings,digest,files = load_bundle()
    profiles = profile_map(args.profiles,settings)
    target = Path(args.target).expanduser().resolve()
    status = target_status(target,digest,files)
    codex = shutil.which(args.codex)
    report = {'os':platform.platform(),'shell':os.environ.get('SHELL','unknown'),'python':sys.version.split()[0], 'codex':codex,'target':str(target),'existing_target':status,'source_sha256':digest,'profiles_requested':profiles,'model_messages_sent':0}
    if args.plan:
        report['plan_only'] = True
        print(json.dumps(report,indent=2))
        return 0
    if status == 'new':
        target.mkdir(parents=True,exist_ok=True)
        for relative,content in files.items():
            file = target/relative
            file.parent.mkdir(parents=True,exist_ok=True)
            with file.open('xb') as stream:
                stream.write(content)
        (target/MARKER).write_text(json.dumps({'source_sha256':digest})+'\n')
    state = target/'.state'
    with worker_lock(state):
        cli = target/'.venv'/'bin'/'codex-scheduler'
        if not cli.is_file():
            run(['sh',target/'install.sh'],target)
        run([cli,'--version'],target)
        python = target/'.venv'/'bin'/'python'
        tests = run([python,'-m','unittest','discover','-s','tests','-v'],target)
        report['fixture_tests'] = (tests.stderr+tests.stdout).strip().splitlines()[-4:]
        report['installed'] = True
        base = [cli,'--home',state]
        if codex:
            base += ['--codex',codex]
        # Read every existing alias before any alias insertion: partial remapping is forbidden.
        existing = {a['alias']:a for a in json.loads(run(base+['account','list'],target).stdout)}
        for alias,email in profiles.items():
            if alias in existing and existing[alias].get('expected_email') != email:
                raise SetupError('Existing alias '+alias+' has another email binding; it was not changed')
        for alias,email in profiles.items():
            if alias not in existing:
                run(base+['account','add',alias,'--email',email,'--no-login'],target)
        accounts = json.loads(run(base+['account','list'],target).stdout)
        report['profiles'] = [{'alias':a['alias'],'expected_email':a['expected_email'],'verified':a['verified']} for a in accounts]
        report['state'] = str(state)
        report['launcher'] = str(cli)
        report['doctor'] = 'not_run_codex_missing'
        report['ready_for_login'] = False
        if codex:
            doctor = run(base+['doctor','--report',target/'setup-doctor.json'],target,timeout=180)
            checks = json.loads(doctor.stdout)
            report['codex_version'] = checks.get('codex_version')
            essential = ['account/read','thread/start','thread/read','thread/resume','turn/start','turn/interrupt']
            supported = checks.get('schema_methods',{})
            report['doctor'] = {'essential_methods':{m:supported.get(m,False) for m in essential},'structured_usage':supported.get('account/rateLimits/read',False),'usage_notifications':checks.get('rate_limit_notifications',False)}
            report['ready_for_login'] = all(supported.get(m,False) for m in essential)
        rerun = [sys.executable,str(Path(__file__).resolve()),'--target',str(target),'--codex',args.codex]
        if args.profiles:
            rerun += ['--profiles',str(Path(args.profiles).expanduser().resolve())]
        report['commands'] = {'setup':shlex.join(rerun),'logins':{a:shlex.join([str(x) for x in base+['account','login',a]]) for a in profiles},'verify':shlex.join([str(x) for x in base+['account','verify']]),'status':shlex.join([str(x) for x in base+['status','--refresh']]),'worker':shlex.join([str(x) for x in base+['worker']])}
        if not codex:
            report['dependency_step'] = 'Install official Codex: curl -fsSL https://chatgpt.com/codex/install.sh | sh ; then add its printed bin directory to PATH and run commands.setup (update --codex if its location changed)'
        report['commands']['add_profile_example'] = shlex.join([str(x) for x in base+['account','add','A','--email','you@example.com']])
        report['next_step'] = 'Run account verify/status for existing sign-ins, or the generated login for an unverified alias (A first; B/C later). No worker has been started.' if report['ready_for_login'] else 'Install/repair the official Codex CLI and rerun doctor/setup; fixture checks and profile registration are complete. No worker has been started.'
        if not profiles:
            report['next_step'] += ' No account mappings were supplied; use --profiles on setup or the add_profile_example command with the intended email.'
        (target/'setup-skill-report.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (SetupError,OSError,ValueError,KeyError) as error:
        print(json.dumps({'error':str(error),'model_messages_sent':0}),file=sys.stderr)
        sys.exit(1)
