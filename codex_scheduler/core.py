from datetime import datetime
import math
import hashlib
import json
from pathlib import Path
import re
import time
import tempfile
import os
import uuid
from .store import Lock, SchedulerError, clean, encoded
from .usage import capacity, eligible, observe, STALE_SECONDS


LIMIT_ERRORS = {'usageLimitExceeded', 'rateLimitExceeded'}
ACTIVE_STATES = ('preparing', 'submitting', 'running', 'approval', 'cancelling')


def identity(response):
    account = response.get('account') or {}
    if account.get('type') != 'chatgpt':
        raise SchedulerError('A signed-in ChatGPT account is required; run account add in a terminal')
    return {k: account.get(k) for k in ('type', 'email', 'workspaceName', 'planType')} | {'userId': response.get('userId')}


def same_identity(old, new):
    # Plan changes are allowed; a changed identity or workspace requires explicit re-verification.
    return all(not old.get(k) or old.get(k) == new.get(k) for k in ('email', 'workspaceName', 'userId'))


def worktree_for(project):
    """Resolve subdirectories and linked worktrees to one writer lock per actual worktree."""
    import subprocess
    project = str(Path(project).expanduser().resolve())
    if not Path(project).is_dir():
        raise SchedulerError('Project directory does not exist: '+project)
    try:
        out = subprocess.run(['git', '-C', project, 'rev-parse', '--show-toplevel'], capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            return str(Path(out.stdout.strip()).resolve())
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Non-git nested folders conflict by ancestry at dispatch time as well.
    return project


def overlaps(a, b):
    a, b = Path(a), Path(b)
    return a == b or a in b.parents or b in a.parents


class Scheduler:
    def __init__(self, store, backend, clock=time.time):
        self.store, self.backend, self.clock = store, backend, clock
        self.active = None
        self.worker_lock = None
        self.tree_lock = None

    def add_profile(self, alias, projects=(), models=(), expected_email=None):
        if not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,47}', alias):
            raise SchedulerError('Alias must start with a letter and contain at most 48 letters, digits, _ or -')
        if self.store.one('SELECT alias FROM accounts WHERE alias=?', (alias,)):
            raise SchedulerError('Alias already exists; use account verify or account login')
        if expected_email is not None:
            expected_email = expected_email.strip().casefold()
            if not re.fullmatch(r'[^@\s]+@[^@\s]+', expected_email):
                raise SchedulerError('Expected email must be a valid email address')
        path = self.store.home / 'profiles' / str(uuid.uuid4())
        path.mkdir(parents=True, mode=0o700)
        config = path / 'config.toml'
        config.write_text('cli_auth_credentials_store = "file"\n')
        config.chmod(0o600)
        self.store.db.execute('INSERT INTO accounts(alias,home,projects,models,expected_email) VALUES(?,?,?,?,?)', (alias, str(path), encoded([str(Path(p).expanduser().resolve()) for p in projects]), encoded(list(models)), expected_email))
        return self.store.account(alias)

    def connect(self, alias):
        account = self.store.account(alias)
        home = Path(account['home'])
        if home.is_symlink() or home.resolve().parent != (self.store.home/'profiles').resolve():
            raise SchedulerError('Profile home is not isolated')
        return self.backend.connect(account)

    def refresh(self, alias, rpc=None, verify=False):
        owned = rpc is None
        rpc = rpc or self.connect(alias)
        try:
            result = identity(rpc.call('account/read', {'refreshToken': False}))
            account = self.store.account(alias)
            expected = account['expected_email']
            reported = result.get('email')
            if expected and (not reported or reported.strip().casefold() != expected):
                problem = f'Expected {expected}; Codex reported {reported or "unknown email"}. Run account login {alias} with the intended account.'
                self.store.update('accounts', 'alias', alias, verified=0, usage_ok=0, problem=problem)
                raise SchedulerError(problem)
            old = json.loads(account['identity']) if account['identity'] else None
            if old and not same_identity(old, result) and not verify:
                self.store.update('accounts', 'alias', alias, verified=0, problem='identity/workspace changed; account login required')
                raise SchedulerError('Profile identity changed: '+alias)
            self.store.update('accounts', 'alias', alias, identity=encoded(result), verified=1, checked=self.clock())
            try:
                catalog, cursor = [], None
                while True:
                    models = rpc.call('model/list', {'cursor': cursor})
                    catalog.extend(m['model'] for m in sorted(models.get('data', []), key=lambda m: not m.get('isDefault', False)))
                    cursor = models.get('nextCursor')
                    if not cursor:
                        break
                self.store.update('accounts', 'alias', alias, catalog=encoded(catalog))
            except SchedulerError:
                pass
            try:
                data = rpc.call('account/rateLimits/read')
                previous_id = account['account_id']
                if previous_id and data.get('accountId') and previous_id != data['accountId'] and not verify:
                    self.store.update('accounts', 'alias', alias, verified=0, problem='workspace account ID changed; account login required')
                    raise SchedulerError('Workspace account ID changed')
                if verify and previous_id and data.get('accountId') and previous_id != data['accountId']:
                    self.store.db.execute('DELETE FROM windows WHERE alias=?', (alias,))
                    self.store.db.execute('DELETE FROM buckets WHERE alias=?', (alias,))
                observe(self.store, alias, data, self.clock())
                self.reschedule(alias)
            except SchedulerError:
                self.store.update('accounts', 'alias', alias, usage_ok=0, usage_checked=self.clock(), problem='usage unknown')
        except SchedulerError:
            self.store.update('accounts', 'alias', alias, usage_ok=0, usage_checked=self.clock())
            raise
        finally:
            if owned:
                rpc.close()
        return self.store.account(alias)

    def reschedule(self, alias):
        # New observations can revise estimates earlier or later, including from a
        # status command in another process. They schedule a recheck, not a model turn.
        for job in self.store.all("SELECT * FROM jobs WHERE account=? AND state='waiting_usage' AND cancel=0", (alias,)):
            cap, check, _ = capacity(self.store, alias, job['model'], job['headroom'], self.clock(), bool(job['confirmed_limit']))
            self.store.update('jobs', 'id', job['id'], next_check=self.clock() if cap == 'ready' else check)

    def refresh_stale(self, accounts, force=False):
        for account in accounts:
            stale_window = self.store.one('SELECT 1 FROM windows WHERE alias=? AND observed<? LIMIT 1', (account['alias'], self.clock()-STALE_SECONDS))
            if force or stale_window or account['usage_checked'] is None or account['usage_checked'] <= self.clock()-STALE_SECONDS:
                rpc = self.active['rpc'] if self.active and self.active['alias'] == account['alias'] else None
                try:
                    self.refresh(account['alias'], rpc=rpc)
                except SchedulerError:
                    pass

    def choose(self, project, model, headroom):
        accounts = self.store.all('SELECT * FROM accounts ORDER BY alias')
        # Refresh unverified profiles too: a first login can become usable immediately.
        self.refresh_stale(accounts)
        choices, checks = [], []
        for account in self.store.all('SELECT * FROM accounts ORDER BY alias'):
            if not eligible(account, project, model):
                continue
            state, reset, _ = capacity(self.store, account['alias'], model, headroom, self.clock())
            if state == 'ready':
                choices.append((reset, account['alias']))
            else:
                checks.append(reset)
        if choices:
            reset, alias = min(choices)
            refresh = datetime.fromtimestamp(reset).astimezone().strftime('%Y-%m-%d %H:%M:%S %Z') if math.isfinite(reset) else 'unknown'
            return alias, None, f'{alias}: auto; every applicable window has at least {headroom:g}% checkpoint headroom; earliest usable refresh {refresh}'
        return None, min(checks) if checks else self.clock()+60, 'queued: no eligible profile has known checkpoint headroom'

    def enqueue(self, prompt, project, model=None, alias=None, size='small', headroom=None, sandbox='workspace-write'):
        explicit = alias is not None
        project = str(Path(project).expanduser().resolve())
        worktree = worktree_for(project)
        headroom = headroom if headroom is not None else (5 if size == 'small' else 15)
        if not 0 < headroom <= 100:
            raise SchedulerError('Headroom must be between 0 and 100 percent')
        if clean(prompt) != prompt:
            raise SchedulerError('Prompt contains a credential-like value; use a local secret reference instead')
        if not prompt.strip():
            raise SchedulerError('Task prompt must not be empty')
        accounts = [self.store.account(alias)] if alias else self.store.all('SELECT * FROM accounts ORDER BY alias')
        self.refresh_stale(accounts)
        if not model:
            available = [[m for m in json.loads(a['catalog']) if eligible(a, project, m)] for a in self.store.all('SELECT * FROM accounts ORDER BY alias') if (not alias or a['alias'] == alias)]
            model = next((c[0] for c in available if c), None)
            if not model:
                raise SchedulerError('No model catalog available; pass --model explicitly')
        state, check = 'ready', None
        if alias:
            account = self.store.account(alias)
            if not eligible(account, project, model):
                raise SchedulerError('Selected profile is unverified or ineligible for this project/model')
            cap, reset, _ = capacity(self.store, alias, model, headroom, self.clock())
            if cap in ('blocked', 'insufficient'):
                state, check = 'waiting_usage', reset
            reason = f'{alias}: explicit selection is binding' + ('; usage unknown' if cap == 'unknown' else '')
        else:
            alias, check, reason = self.choose(project, model, headroom)
            if alias is None:
                state = 'queued'
        job_id, now = str(uuid.uuid4())[:12], self.clock()
        self.store.db.execute('''INSERT INTO jobs(id,created,updated,account,explicit,project,worktree,model,size,headroom,prompt,state,reason,next_check,sandbox)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (job_id, now, now, alias, int(explicit), project, worktree, model, size, headroom, prompt, state, reason, check, sandbox))
        self.store.event(job_id, now, 'queued', {'account': alias, 'reason': reason})
        return self.store.job(job_id)

    def start_worker(self):
        self.worker_lock = Lock(self.store.home/'worker.lock').acquire()
        self.reconcile()

    def reconcile(self):
        for job in self.store.all("SELECT * FROM jobs WHERE state IN ('preparing','submitting','running','approval','cancelling','needs_review')"):
            rpc = None
            try:
                if job['state'] == 'preparing' or not job['thread_id']:
                    # Preparing precedes the committed submission intent. No turn can be submitted until the thread ID is committed.
                    self.store.update('jobs', 'id', job['id'], state='cancelled' if job['cancel'] else 'ready', reason='recovered before any turn submission', thread_id=None if not job['attempt'] and not job['turn_id'] else job['thread_id'])
                    continue
                rpc = self.connect(job['account'])
                thread = rpc.call('thread/read', {'threadId': job['thread_id'], 'includeTurns': False})['thread']
                try:
                    turns = rpc.call('thread/turns/list', {'threadId': job['thread_id'], 'limit': 100, 'sortDirection': 'desc', 'itemsView': 'full'})['data']
                except SchedulerError:
                    turns = list(reversed(rpc.call('thread/read', {'threadId': job['thread_id'], 'includeTurns': True})['thread'].get('turns', [])))
                latest = turns[0] if turns else None
                turn = next((t for t in turns if t['id'] == job['turn_id']), None) if job['turn_id'] else (latest if latest and latest['id'] != job['prior_turn'] else None)
                pending = self.store.one("SELECT id FROM approvals WHERE job=? AND state IN ('pending','responding')", (job['id'],))
                if pending:
                    self.review(job, 'pending approval survived restart; request transport lost; inspect before recovery')
                elif turn and turn['status'] in ('completed', 'failed'):
                    self.store.update('jobs', 'id', job['id'], turn_id=turn['id'])
                    self.finish(self.store.job(job['id']), turn, rpc)
                elif job['state'] == 'preparing' and not turn:
                    self.store.update('jobs', 'id', job['id'], state='cancelled' if job['cancel'] else 'ready', reason='recovered before turn intent')
                elif job['cancel'] and turn and turn['status'] == 'interrupted':
                    self.store.update('jobs', 'id', job['id'], state='cancelled', reason='cancellation reconciled', next_check=None)
                else:
                    self.review(job, 'uncertain submission or unfinished turn; no automatic replay')
            except (SchedulerError, KeyError):
                self.review(job, 'history unavailable; no automatic replay')
            finally:
                if rpc:
                    rpc.close()

    def review(self, job, reason):
        self.store.update('jobs', 'id', job['id'], state='needs_review', reason=reason, next_check=None, updated=self.clock())
        self.store.event(job['id'], self.clock(), 'needs_review', reason)

    def wake(self):
        # Due checks run even while another turn or approval occupies the worker.
        due = self.store.all("SELECT * FROM jobs WHERE state IN ('queued','waiting_usage') AND next_check<=? AND cancel=0 ORDER BY next_check", (self.clock(),))
        refreshed = set()
        for job in due:
            if job['state'] == 'queued':
                alias, check, reason = self.choose(job['project'], job['model'], job['headroom'])
                self.store.update('jobs', 'id', job['id'], account=alias, state='ready' if alias else 'queued', next_check=check, reason=reason, updated=self.clock())
                continue
            alias = job['account']
            if alias not in refreshed:
                rpc = self.active['rpc'] if self.active and self.active['alias'] == alias else None
                try:
                    self.refresh(alias, rpc=rpc)
                except SchedulerError:
                    pass
                refreshed.add(alias)
            cap, check, reason = capacity(self.store, alias, job['model'], job['headroom'], self.clock(), bool(job['confirmed_limit']))
            if not self.store.account(alias)['verified']:
                cap = 'unknown'
            self.store.update('jobs', 'id', job['id'], state='ready' if cap == 'ready' else 'waiting_usage', next_check=None if cap == 'ready' else check, reason=reason, updated=self.clock())

    def dispatch(self):
        if self.active:
            return
        for job in self.store.all("SELECT * FROM jobs WHERE state='ready' AND cancel=0 ORDER BY created,id"):
            barriers = self.store.all("SELECT worktree FROM jobs WHERE id<>? AND state IN ('running','submitting','preparing','approval','cancelling','needs_review')", (job['id'],))
            if any(overlaps(job['worktree'], b['worktree']) for b in barriers):
                continue
            lock_name = hashlib.sha256(job['worktree'].encode()).hexdigest()+'.lock'
            try:
                # Shared across scheduler homes: separate launchers cannot write the same worktree.
                uid = str(os.getuid()) if hasattr(os, 'getuid') else os.environ.get('USERNAME', 'user')
                lock_root = Path(tempfile.gettempdir())/('codex-scheduler-'+uid)/'worktrees'
                lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
                self.tree_lock = Lock(lock_root/lock_name).acquire()
            except SchedulerError:
                continue
            rpc = None
            try:
                rpc = self.connect(job['account'])
                self.refresh(job['account'], rpc=rpc)
                account = self.store.account(job['account'])
                if not eligible(account, job['project'], job['model']):
                    self.review(job, 'profile no longer eligible; account remains pinned')
                    continue
                cap, check, reason = capacity(self.store, job['account'], job['model'], job['headroom'], self.clock(), bool(job['confirmed_limit']))
                if cap in ('blocked','insufficient') or (cap == 'unknown' and (not job['explicit'] or job['confirmed_limit'])):
                    self.store.update('jobs', 'id', job['id'], state='waiting_usage', next_check=check, reason=reason)
                    continue
                self.store.update('jobs', 'id', job['id'], state='preparing', updated=self.clock())
                params = {'cwd': job['project'], 'model': job['model'], 'sandbox': job['sandbox'], 'approvalPolicy': 'on-request'}
                if job['thread_id']:
                    thread = rpc.call('thread/resume', {'threadId': job['thread_id'], **params})['thread']
                    status = thread.get('status', {}).get('type')
                    if status == 'active':
                        self.review(job, 'thread is still active; refusing to add a continuation')
                        continue
                else:
                    thread = rpc.call('thread/start', {**params, 'ephemeral': False})['thread']
                    self.store.update('jobs', 'id', job['id'], thread_id=thread['id'])
                current = self.store.job(job['id'])
                if current['cancel']:
                    self.store.update('jobs', 'id', job['id'], state='cancelled', next_check=None)
                    continue
                attempt = str(uuid.uuid4())
                with self.store.transaction():
                    self.store.db.execute('INSERT INTO attempts VALUES(?,?,?,?,?,?)', (attempt, job['id'], self.clock(), job['turn_id'], None, 'submitting'))
                    self.store.update('jobs', 'id', job['id'], state='submitting', attempt=attempt, prior_turn=job['turn_id'], turn_id=None, updated=self.clock())
                if job['thread_id']:
                    prompt = 'Continue the saved task from the last completed checkpoint. Inspect existing work first; preserve pending approvals and do not repeat completed actions.'
                else:
                    prompt = job['prompt']
                if job['size'] == 'large':
                    prompt += '\nPrioritize the next useful checkpoint. Keep a concise local progress note before long operations so this thread can resume after a usage limit.'
                # clientUserMessageId is a correlation ID; never assume server-side idempotency.
                turn = rpc.call('turn/start', {'threadId': thread['id'], 'input': [{'type': 'text', 'text': prompt, 'text_elements': []}], 'clientUserMessageId': attempt})['turn']
                with self.store.transaction():
                    self.store.update('attempts', 'id', attempt, turn_id=turn['id'], status='accepted')
                    self.store.update('jobs', 'id', job['id'], state='running', turn_id=turn['id'], reason='turn accepted', updated=self.clock(), next_check=None)
                self.active = {'id': job['id'], 'alias': job['account'], 'rpc': rpc, 'cancel_sent': False}
                self.store.event(job['id'], self.clock(), 'turn_started', {'thread': thread['id'], 'turn': turn['id'], 'attempt': attempt})
                return
            except SchedulerError:
                current = self.store.job(job['id'])
                if current['state'] in ('preparing','submitting'):
                    self.review(current, 'RPC outcome uncertain; reconcile history on restart')
                else:
                    self.store.update('jobs', 'id', job['id'], state='waiting_usage', next_check=self.clock()+60, reason='profile unavailable; retry status only')
            finally:
                if not self.active:
                    if rpc:
                        rpc.close()
                    if self.tree_lock:
                        self.tree_lock.close()
                        self.tree_lock = None

    def finish(self, job, turn, rpc):
        if turn['id'] != job['turn_id']:
            return
        info = (turn.get('error') or {}).get('codexErrorInfo')
        code = info if isinstance(info, str) else None
        now = self.clock()
        if job['cancel']:
            state, check, reason = 'cancelled', None, 'cancelled; terminal turn confirmed'
        elif code in LIMIT_ERRORS:
            try:
                self.refresh(job['account'], rpc=rpc)
            except SchedulerError:
                pass
            cap, check, _ = capacity(self.store, job['account'], job['model'], job['headroom'], now, True)
            if cap == 'ready':
                check = now + 60
            state, reason = 'waiting_usage', 'confirmed usage limit; checkpoint is saved Codex thread and completed tool history'
            self.store.update('jobs', 'id', job['id'], confirmed_limit=1)
        elif turn['status'] == 'completed':
            state, check, reason = 'completed', None, 'completed'
        elif turn['status'] == 'interrupted':
            self.review(job, 'turn interrupted without confirmed cancellation; inspect before retry')
            return
        else:
            state, check, reason = 'failed', None, 'non-usage turn failure; not automatically retried'
        output = '\n'.join(i.get('text','') for i in turn.get('items', []) if i.get('type') == 'agentMessage') or job.get('output') or ''
        with self.store.transaction():
            self.store.update('jobs', 'id', job['id'], state=state, next_check=check, reason=reason, updated=now, output=clean(output))
            if job['attempt']:
                self.store.update('attempts', 'id', job['attempt'], status=state)
            self.store.event(job['id'], now, state, {'turn': turn['id'], 'next_check': check})

    def pump(self):
        if not self.active:
            return
        active = self.active
        job, rpc = self.store.job(active['id']), active['rpc']
        try:
            if job['cancel'] and not active['cancel_sent']:
                rpc.call('turn/interrupt', {'threadId': job['thread_id'], 'turnId': job['turn_id']})
                active['cancel_sent'] = True
                self.store.update('jobs', 'id', job['id'], state='cancelling')
            for event in rpc.drain():
                method, params = event.get('method'), event.get('params', {})
                if method == 'account/rateLimits/updated':
                    observe(self.store, active['alias'], params, self.clock(), sparse=True)
                    self.reschedule(active['alias'])
                elif 'id' in event:
                    # Persist requests without auto-accepting. IDs are scoped to this connection.
                    if params.get('threadId') not in (None, job['thread_id']):
                        continue
                    self.store.db.execute('INSERT OR IGNORE INTO approvals(job,request_id,session,method,details) VALUES(?,?,?,?,?)', (job['id'], json.dumps(event['id']), rpc.session, method, encoded(params)))
                    if not job['cancel']:
                        self.store.update('jobs', 'id', job['id'], state='approval', reason='waiting for user response; see approvals')
                elif method == 'item/completed' and params.get('threadId') == job['thread_id']:
                    item = params.get('item', {})
                    if item.get('type') == 'agentMessage':
                        self.store.update('jobs', 'id', job['id'], output=clean(item.get('text', '')))
                elif method == 'turn/completed' and params.get('threadId') == job['thread_id']:
                    self.finish(self.store.job(job['id']), params['turn'], rpc)
            job = self.store.job(job['id'])
            if job['state'] in ('completed','failed','waiting_usage','cancelled','needs_review'):
                self.release_active()
                return
            for approval in self.store.all("SELECT * FROM approvals WHERE job=? AND session=? AND state='pending' AND response IS NOT NULL", (job['id'], rpc.session)):
                if job['cancel']:
                    continue
                self.store.update('approvals', 'id', approval['id'], state='responding')
                with self.store.transaction():
                    # Serialize a committed cancellation against the response write.
                    if self.store.job(job['id'])['cancel']:
                        continue
                    rpc.respond(json.loads(approval['request_id']), json.loads(approval['response']))
                    self.store.update('approvals', 'id', approval['id'], state='answered')
                    self.store.update('jobs', 'id', job['id'], state='running', reason='approval response sent')
        except SchedulerError:
            self.review(job, 'connection lost; reconcile on restart; no automatic replay')
            self.release_active()

    def tick(self):
        self.pump()
        self.wake()
        self.dispatch()
        self.pump()

    def release_active(self):
        if self.active:
            self.active['rpc'].close()
            self.active = None
        if self.tree_lock:
            self.tree_lock.close()
            self.tree_lock = None

    def close(self):
        # Leave durable running/approval intent for reconciliation; never silently replay.
        self.release_active()
        if self.worker_lock:
            self.worker_lock.close()
            self.worker_lock = None

    def cancel(self, job_id):
        with self.store.transaction():
            job = self.store.job(job_id)
            if job['state'] in ('completed','failed','cancelled'):
                return
            fields = {'cancel': 1, 'updated': self.clock()}
            if job['state'] in ('queued','ready','waiting_usage'):
                fields.update(state='cancelled', next_check=None, reason='cancelled before dispatch')
            self.store.update('jobs', 'id', job_id, **fields)
            self.store.event(job_id, self.clock(), 'cancel_requested', {})

    def resolve(self, job_id, outcome):
        job = self.store.job(job_id)
        if job['state'] != 'needs_review':
            raise SchedulerError('Only needs_review jobs can be manually resolved')
        # Explicit local operator decision; interrupted/ambiguous work is never replayed silently.
        if outcome == 'retry' and job['cancel']:
            raise SchedulerError('Cancellation is binding; submit a new task instead')
        self.store.update('jobs', 'id', job_id, state={'retry':'ready','completed':'completed','cancelled':'cancelled'}[outcome], reason='operator reconciled saved thread', updated=self.clock())
        self.store.event(job_id, self.clock(), 'operator_resolved', {'outcome': outcome})
