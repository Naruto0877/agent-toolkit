import argparse
import contextlib
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from . import __version__
from .providers import FakeBackend, RealBackend, create_backend, registry
from .core import Scheduler
from .providers.codex_protocol import RPC, command, isolated_env
from .store import Lock, SchedulerError, Store, clean, encoded


def default_home():
    if sys.platform == 'darwin':
        return Path.home()/'Library'/'Application Support'/'codex-scheduler'
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home())))/'codex-scheduler'
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home()/'.local'/'state')))/'codex-scheduler'


def local_time(value):
    if value is None:
        return 'unknown'
    from datetime import datetime
    return datetime.fromtimestamp(value).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z')


def emit(value):
    print(json.dumps(clean(value), indent=2, ensure_ascii=False))


def fingerprint(path):
    path = Path(path)
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def show_job(job):
    result = dict(job)
    for key in ('created','updated','next_check'):
        result[key+'_local'] = local_time(result[key])
    return result


def doctor(args):
    codex = shutil.which(args.codex)
    report = {'scheduler': __version__, 'os': platform.platform(), 'architecture': platform.machine(), 'shell': os.environ.get('SHELL', os.environ.get('COMSPEC', 'unknown')), 'python': sys.version.split()[0], 'codex': codex, 'default_login_touched': False}
    if not codex:
        report['next_step'] = 'Install the official CLI: brew install --cask codex (macOS), or npm install -g @openai/codex; then codex-scheduler doctor'
        report['fake_backend'] = 'codex-scheduler --backend fake --home ./fake-state account add A'
        emit(report)
        return 1
    def run(*words):
        return subprocess.run([codex, *words], capture_output=True, text=True, timeout=45)
    report['codex_version'] = run('--version').stdout.strip()
    report['help_checks'] = {}
    for label, words, expected in [('login', ['login','--help'], '--device-auth'), ('resume', ['resume','--help'], 'SESSION_ID'), ('app_server', ['app-server','--help'], 'generate-json-schema')]:
        out = run(*words)
        report['help_checks'][label] = out.returncode == 0 and expected in out.stdout
    with tempfile.TemporaryDirectory(prefix='codex-scheduler-schema-') as temp:
        result = run('app-server','generate-json-schema','--out',temp)
        if result.returncode:
            report['schema'] = 'unavailable; usage unknown, manual selection retained'
        else:
            root = Path(temp)
            client = json.loads((root/'ClientRequest.json').read_text())
            text = json.dumps(client)
            methods = ['account/read','account/rateLimits/read','thread/start','thread/read','thread/resume','thread/turns/list','turn/start','turn/interrupt']
            report['schema_methods'] = {m: '"'+m+'"' in text for m in methods}
            server = (root/'ServerNotification.json').read_text()
            report['rate_limit_notifications'] = 'account/rateLimits/updated' in server
            selected = ['GetAccountResponse', 'GetAccountRateLimitsResponse', 'AccountRateLimitsUpdatedNotification', 'ThreadStartParams', 'ThreadResumeParams', 'ThreadReadParams', 'ThreadTurnsListParams', 'TurnStartParams', 'TurnCompletedNotification']
            report['schema_sha256'] = {name: fingerprint(next(root.rglob(name+'.json'))) for name in selected if list(root.rglob(name+'.json'))}
            report['schema_note'] = 'Validated installed wire contract; no model messages sent'
    # Status command is local and must not dump stderr (some versions print API-key suffixes).
    status = run('login','status')
    report['default_signed_in'] = status.returncode == 0
    if args.probe_default:
        rpc = RPC(codex)
        default = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
        before = fingerprint(default/'auth.json')
        try:
            from .core import identity
            report['default_account'] = identity(rpc.call('account/read', {'refreshToken': False}))
            try:
                usage = rpc.call('account/rateLimits/read')
                # Allowlist metadata; never print arbitrary backend payloads.
                report['default_usage'] = {'ordinaryUsageAllowed': usage.get('ordinaryUsageAllowed'), 'accountId': usage.get('accountId')}
                snapshots = usage.get('rateLimitsByLimitId') or {'codex': usage.get('rateLimits', {})}
                report['default_usage']['windows'] = [{ 'bucket': key, 'window': kind, 'usedPercent': window.get('usedPercent'), 'resetsAt_local': local_time(window.get('resetsAt'))} for key,snapshot in snapshots.items() for kind in ('primary','secondary') if isinstance((window := snapshot.get(kind)),dict)]
            except SchedulerError:
                report['default_usage'] = 'unknown; structured read unavailable'
            report['default_auth_file_unchanged'] = before == fingerprint(default/'auth.json')
        finally:
            rpc.close()
    if args.report:
        Path(args.report).write_text(encoded(report)+'\n')
    emit(report)
    return 0


def account_login(args, scheduler, alias):
    store = scheduler.store
    # No worker can execute while auth files are being modified.
    with Lock(store.home/'worker.lock'):
        account = store.account(alias)
        unfinished = store.one("SELECT id FROM jobs WHERE account=? AND state NOT IN ('completed','failed','cancelled')", (alias,))
        if unfinished:
            raise SchedulerError('Finish/cancel/reconcile jobs before changing this alias login')
        if args.backend == 'fake':
            scheduler.refresh(alias, verify=True)
            return
        if not sys.stdin.isatty():
            raise SchedulerError('Interactive login required. In a local terminal run: '+shlex.join([sys.executable, str(Path(__file__).resolve().parents[1]/'bin'/'codex-scheduler'), '--home', str(store.home), '--codex', args.codex, 'account', 'login', alias]+(['--device-auth'] if args.device_auth else [])))
        codex = shutil.which(args.codex)
        if not codex:
            raise SchedulerError('Official Codex CLI missing. On macOS: brew install --cask codex. Then rerun account login '+alias)
        others = store.all('SELECT * FROM accounts WHERE alias<>?', (alias,))
        snapshots = {a['alias']: fingerprint(Path(a['home'])/'auth.json') for a in others}
        default = Path(os.environ.get('CODEX_HOME', str(Path.home()/'.codex')))
        default_before = fingerprint(default/'auth.json')
        # Official interactive flow inherits terminal I/O; never capture or log login output.
        result = subprocess.run(command(codex, 'login', *(['--device-auth'] if args.device_auth else [])), env=isolated_env(account['home']))
        if result.returncode:
            raise SchedulerError('Official login did not complete; rerun account login '+alias)
        intact = default_before == fingerprint(default/'auth.json') and all(snapshots[a['alias']] == fingerprint(Path(a['home'])/'auth.json') for a in others)
        if not intact:
            raise SchedulerError('Isolation check failed: another credential file changed; execution disabled pending investigation')
        scheduler.refresh(alias, verify=True)
        # Fresh connections prove aliases survive a launcher restart, without reusing process auth.
        verified = []
        for previous in others:
            if previous['verified']:
                scheduler.refresh(previous['alias'])
                if not store.account(previous['alias'])['verified']:
                    raise SchedulerError('Previous profile identity changed: '+previous['alias'])
                verified.append(previous['alias'])
        store.event(None, scheduler.clock(), 'isolation_verified', {'added': alias, 'previous_profiles': verified, 'default_auth_unchanged': True, 'other_auth_files_unchanged': True, 'fresh_app_servers': True})
        print('Isolation verified with fresh app-server processes; default login and other credential files intact.')


def parser():
    p = argparse.ArgumentParser(prog='codex-scheduler', description='Durable, account-pinned local Codex scheduling. One worker; no external service.')
    p.add_argument('--version', action='version', version=__version__)
    p.add_argument('--home', default=os.environ.get('CODEX_SCHEDULER_HOME', str(default_home())), help='Private scheduler state directory')
    p.add_argument('--codex', default='codex', help='Official Codex executable')
    p.add_argument('--backend', choices=['real','fake'], default='real')
    p.add_argument('--fixture', help='Fake backend JSON fixture (never used by real backend)')
    p.add_argument('--now', type=float, help='Frozen Unix time; fake backend only')
    sub = p.add_subparsers(dest='cmd', required=True)
    d = sub.add_parser('doctor', help='Detect local runtime and inspect installed help/schema, without model messages')
    d.add_argument('--probe-default', action='store_true', help='Read default account and structured usage; no login changes')
    d.add_argument('--report', help='Write sanitized JSON verification report')
    a = sub.add_parser('account').add_subparsers(dest='action', required=True)
    add = a.add_parser('add', help='Create isolated profile and sign in through official Codex flow')
    add.add_argument('alias')
    add.add_argument('--project', action='append', default=[], help='Eligible project path (repeatable; default all)')
    add.add_argument('--model', action='append', default=[], help='Eligible model (repeatable; default catalog)')
    add.add_argument('--device-auth', action='store_true')
    add.add_argument('--email', help='Require the official login to report this email')
    add.add_argument('--no-login', action='store_true', help='Register an unverified profile now; complete account login later')
    login = a.add_parser('login', help='Complete/repeat official login for an existing alias')
    login.add_argument('alias')
    login.add_argument('--device-auth', action='store_true')
    verify = a.add_parser('verify', help='Read and explicitly verify account/workspace with fresh app-server processes')
    verify.add_argument('alias', nargs='?')
    a.add_parser('list')
    status = sub.add_parser('status')
    status.add_argument('--refresh', action='store_true')
    run = sub.add_parser('run', help='Queue work; use worker to execute')
    select = run.add_mutually_exclusive_group(required=True)
    select.add_argument('--account')
    select.add_argument('--auto', action='store_true')
    run.add_argument('--project', default=os.getcwd())
    run.add_argument('--model', help='Defaults to first eligible catalog model')
    run.add_argument('--size', choices=['small','large'], default='small')
    run.add_argument('--headroom', type=float, help='Estimated percent needed per window to next checkpoint; defaults small=5, large=15')
    run.add_argument('--sandbox', choices=['workspace-write','read-only'], default='workspace-write')
    run.add_argument('prompt')
    sub.add_parser('providers', help='List implemented live and fixture adapters')
    sub.add_parser('queue')
    inspect = sub.add_parser('inspect')
    inspect.add_argument('job')
    cancel = sub.add_parser('cancel')
    cancel.add_argument('job')
    worker = sub.add_parser('worker')
    worker.add_argument('--once', action='store_true', help='One scheduler tick; for fixtures/status checks, not long real tasks')
    worker.add_argument('--poll', type=float, default=1, help='Maximum wake loop delay, seconds (0.1 to 30)')
    sub.add_parser('approvals')
    approve = sub.add_parser('approve')
    approve.add_argument('id', type=int)
    approve.add_argument('--decision', choices=['accept','decline','cancel'])
    approve.add_argument('--response-file', help='JSON response for other interactive requests')
    resolve = sub.add_parser('resolve', help='After inspecting saved history, reconcile an uncertain job manually')
    resolve.add_argument('job')
    resolve.add_argument('--outcome', required=True, choices=['retry','completed','cancelled'])
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    if args.now is not None and args.backend != 'fake':
        raise SystemExit('--now is allowed only with --backend fake')
    if args.fixture and args.backend != 'fake':
        raise SystemExit('--fixture is allowed only with --backend fake')
    if args.cmd == 'doctor':
        try:
            return doctor(args)
        except (SchedulerError, OSError, subprocess.TimeoutExpired) as err:
            print('doctor: '+str(err), file=sys.stderr)
            return 1
    if args.cmd == 'providers':
        emit(registry.describe())
        return 0
    clock = (lambda: args.now) if args.now is not None else time.time
    store = None
    scheduler = None
    try:
        store = Store(args.home, args.backend)
        backend = create_backend(args.backend,codex=args.codex,home=store.home,fixture=args.fixture,clock=clock)
        scheduler = Scheduler(store, backend, clock)
        if args.cmd == 'account':
            if args.action == 'add':
                scheduler.add_profile(args.alias, args.project, args.model, args.email)
                if not args.no_login:
                    account_login(args, scheduler, args.alias)
            elif args.action == 'login':
                account_login(args, scheduler, args.alias)
            elif args.action == 'verify':
                # Verification uses new processes, so cached in-process credentials cannot mask a swap.
                for a in store.all('SELECT * FROM accounts ORDER BY alias'):
                    if args.alias and a['alias'] != args.alias:
                        continue
                    scheduler.refresh(a['alias'])
                if args.alias:
                    store.account(args.alias)
            emit([account_view(store, a) for a in store.all('SELECT * FROM accounts ORDER BY alias')])
        elif args.cmd == 'status':
            scheduler.refresh_stale(store.all('SELECT * FROM accounts ORDER BY alias'), force=args.refresh)
            emit([account_view(store, a) for a in store.all('SELECT * FROM accounts ORDER BY alias')])
        elif args.cmd == 'run':
            job = scheduler.enqueue(args.prompt, args.project, args.model, args.account, args.size, args.headroom, args.sandbox)
            print(job['reason'])
            print('Job '+job['id']+' '+job['state']+'; next check '+local_time(job['next_check']))
            print('Execute with: codex-scheduler --home '+shlex.quote(str(store.home))+' worker')
        elif args.cmd == 'queue':
            emit([show_job(j) for j in store.all("SELECT * FROM jobs WHERE state NOT IN ('completed','cancelled','failed') ORDER BY created,id")])
        elif args.cmd == 'inspect':
            emit({'job': show_job(store.job(args.job)), 'attempts': store.all('SELECT * FROM attempts WHERE job=? ORDER BY created', (args.job,)), 'events': store.all('SELECT * FROM events WHERE job=? ORDER BY id', (args.job,)), 'approvals': store.all('SELECT * FROM approvals WHERE job=? ORDER BY id', (args.job,))})
        elif args.cmd == 'cancel':
            scheduler.cancel(args.job)
            emit(show_job(store.job(args.job)))
        elif args.cmd == 'worker':
            if not 0.1 <= args.poll <= 30:
                raise SchedulerError('--poll must be between 0.1 and 30 seconds')
            scheduler.start_worker()
            print('Foreground worker ready (one worker, one writer per worktree). Ctrl-C stops; jobs remain durable.', flush=True)
            previous = None
            while True:
                scheduler.tick()
                summary = store.all('SELECT id,account,state,reason,next_check FROM jobs ORDER BY created,id')
                serialized = encoded(summary)
                if serialized != previous:
                    emit(summary)
                    previous = serialized
                if args.once:
                    break
                time.sleep(args.poll)
        elif args.cmd == 'approvals':
            emit(store.all("SELECT * FROM approvals WHERE state IN ('pending','responding') ORDER BY id"))
        elif args.cmd == 'approve':
            approval = store.one('SELECT * FROM approvals WHERE id=?', (args.id,))
            if not approval or approval['state'] != 'pending' or approval['response'] is not None:
                raise SchedulerError('Approval is unavailable or already answered')
            job = store.job(approval['job'])
            if job['cancel'] or job['state'] != 'approval':
                raise SchedulerError('This request is not attached to a live, uncancelled approval; inspect/reconcile the job')
            if bool(args.decision) == bool(args.response_file):
                raise SchedulerError('Supply exactly one of --decision or --response-file')
            if args.decision:
                if approval['method'] not in ('item/commandExecution/requestApproval','item/fileChange/requestApproval'):
                    raise SchedulerError('Use --response-file matching this installed protocol request schema')
                response = {'decision': args.decision}
            else:
                response = json.loads(Path(args.response_file).read_text())
                if clean(response) != response:
                    raise SchedulerError('Do not store credentials in approval responses; provide them outside the scheduler')
            store.update('approvals', 'id', args.id, response=encoded(response))
            print('Response queued for the live worker; no response will be replayed after a connection loss.')
        elif args.cmd == 'resolve':
            with Lock(store.home/'worker.lock'):
                scheduler.resolve(args.job, args.outcome)
            emit(show_job(store.job(args.job)))
        return 0
    except KeyboardInterrupt:
        print('\nWorker stopped. Restart it to reconcile saved turns.', file=sys.stderr)
        return 130
    except (SchedulerError, OSError, ValueError) as err:
        print(clean(str(err)), file=sys.stderr)
        return 1
    finally:
        if scheduler:
            scheduler.close()
        if store:
            store.close()


def account_view(store, account):
    return {'alias': account['alias'], 'expected_email': account['expected_email'], 'verified': bool(account['verified']), 'identity': json.loads(account['identity']) if account['identity'] else 'unknown', 'workspace_account_id': account['account_id'] or 'unknown', 'home': account['home'], 'eligible_projects': json.loads(account['projects']) or ['all'], 'eligible_models': json.loads(account['models']) or json.loads(account['catalog']) or ['unknown'], 'usage': 'reported' if account['usage_ok'] else 'unknown', 'ordinary_usage_allowed': account['ordinary'], 'last_observation_local': local_time(account['usage_checked']), 'problem': account['problem'], 'windows': [dict(w, reset_local=local_time(w['reset']), observed_local=local_time(w['observed'])) for w in store.all('SELECT * FROM windows WHERE alias=? ORDER BY bucket,kind', (account['alias'],))]}


if __name__ == '__main__':
    sys.exit(main())
