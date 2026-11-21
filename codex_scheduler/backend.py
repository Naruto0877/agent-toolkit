import json
from pathlib import Path
import uuid
from .protocol import RPC, RPCError
from .store import SchedulerError


class RealBackend:
    def __init__(self, codex):
        self.codex = codex

    def connect(self, account):
        return RPC(self.codex, account['home'])


class FakeBackend:
    """Durable, deterministic protocol double; never invokes Codex or makes network calls."""
    def __init__(self, home, fixture, clock):
        self.home, self.fixture, self.clock = Path(home), Path(fixture) if fixture else None, clock
        self.calls = []

    def config(self, alias):
        data = json.loads(self.fixture.read_text()) if self.fixture else {}
        return data.get('accounts', {}).get(alias, {})

    def connect(self, account):
        return FakeRPC(self, account['alias'])


class FakeRPC:
    def __init__(self, backend, alias):
        self.backend, self.alias = backend, alias
        self.session, self.events = str(uuid.uuid4()), []
        self.path = backend.home / ('fake-' + alias + '.json')
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'threads': {}, 'count': 0}

    def save(self):
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.state))
        tmp.chmod(0o600)
        tmp.replace(self.path)

    def call(self, method, params=None):
        params = params or {}
        self.backend.calls.append((self.alias, method, params))
        config = self.backend.config(self.alias)
        if method == 'account/read':
            return {'account': {'type': 'chatgpt', 'email': config.get('email', self.alias+'@fake.invalid'), 'workspaceName': config.get('workspace', 'Fake workspace'), 'planType': 'plus'}, 'userId': self.alias}
        if method == 'account/rateLimits/read':
            if config.get('usageUnknown'):
                raise SchedulerError('Fake usage unavailable')
            frames = config.get('frames', [])
            result = config.get('usage', {'ordinaryUsageAllowed': True, 'accountId': self.alias, 'rateLimits': {'limitId': 'codex', 'primary': {'usedPercent': 0, 'resetsAt': int(self.backend.clock()+3600)}}})
            for frame in sorted(frames, key=lambda x: x['at']):
                if frame['at'] <= self.backend.clock():
                    result = frame['usage']
            return result
        if method == 'model/list':
            return {'data': [{'id': m, 'model': m, 'isDefault': i == 0} for i, m in enumerate(config.get('models', ['model-test']))]}
        if method == 'thread/start':
            ident = str(uuid.uuid4())
            thread = {'id': ident, 'cwd': params['cwd'], 'turns': [], 'status': {'type': 'idle'}}
            self.state['threads'][ident] = thread
            self.save()
            return {'thread': thread}
        if method in ('thread/read', 'thread/resume', 'thread/turns/list'):
            # Reload: recovery may use a fresh process, just like the official backend.
            self.state = json.loads(self.path.read_text())
            thread = self.state['threads'][params['threadId']]
            if method == 'thread/resume' and not thread['turns']:
                raise RPCError(-32600)  # Actual Codex has no rollout until its first turn.
            return {'data': list(reversed(thread['turns'])), 'nextCursor': None} if method.endswith('/list') else {'thread': thread}
        if method == 'turn/start':
            thread = self.state['threads'][params['threadId']]
            seq = self.state['count']
            outcomes = config.get('turns', ['complete'])
            outcome = outcomes[min(seq, len(outcomes)-1)]
            self.state['count'] += 1
            turn = {'id': str(uuid.uuid4()), 'status': 'inProgress', 'items': []}
            if outcome == 'limit':
                turn.update(status='failed', error={'codexErrorInfo': 'usageLimitExceeded'})
            elif outcome == 'complete':
                turn.update(status='completed', items=[{'type': 'agentMessage', 'text': 'Fake task completed'}])
            thread['turns'].append(turn)
            thread['status'] = {'type': 'active' if turn['status'] == 'inProgress' else 'idle'}
            self.save()
            if outcome == 'approval':
                self.events.append({'id': 900, 'method': 'item/commandExecution/requestApproval', 'params': {'threadId': thread['id'], 'turnId': turn['id'], 'itemId': 'fake-item', 'command': 'echo fake', 'reason': 'Fake approval'}})
            elif outcome != 'running':
                self.events.append({'method': 'turn/completed', 'params': {'threadId': thread['id'], 'turn': turn}})
            return {'turn': turn}
        if method == 'turn/interrupt':
            thread = self.state['threads'][params['threadId']]
            turn = thread['turns'][-1]
            turn['status'] = 'interrupted'
            thread['status'] = {'type': 'idle'}
            self.save()
            self.events.append({'method': 'turn/completed', 'params': {'threadId': thread['id'], 'turn': turn}})
            return {}
        raise SchedulerError('Unsupported fake RPC: '+method)

    def drain(self):
        events, self.events = self.events, []
        return events

    def respond(self, ident, result):
        self.backend.calls.append((self.alias, 'response', result))
        if ident == 900:
            thread = next(t for t in reversed(list(self.state['threads'].values())) if t['status']['type'] == 'active')
            turn = thread['turns'][-1]
            turn['status'] = 'completed' if result.get('decision') == 'accept' else 'interrupted'
            thread['status'] = {'type': 'idle'}
            self.save()
            self.events.append({'method': 'turn/completed', 'params': {'threadId': thread['id'], 'turn': turn}})

    def close(self):
        pass
