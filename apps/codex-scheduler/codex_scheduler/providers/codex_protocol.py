"""Newline-delimited app-server JSON RPC. No raw protocol or stderr logging."""
import json
import os
import queue
import subprocess
import threading
import time
import uuid
from ..store import SchedulerError


class RPCError(SchedulerError):
    def __init__(self, code, info=None, detail=None):
        self.code, self.info = code, info or {}
        self.detail = detail
        super().__init__('Codex app-server request failed (code %s)' % code)


def isolated_env(home):
    env = dict(os.environ)
    for key in list(env):
        if key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'CODEX_ACCESS_TOKEN', 'OPENAI_BASE_URL', 'OPENAI_ORG_ID', 'OPENAI_PROJECT_ID', 'CODEX_SQLITE_HOME', 'CODEX_INTERNAL_ORIGINATOR_OVERRIDE'):
            env.pop(key, None)
    env['CODEX_HOME'] = str(home)
    return env


def command(codex, *args):
    # File store is confined to CODEX_HOME; never use a shared OS credential slot.
    return [codex, '-c', 'cli_auth_credentials_store="file"', *args]


class RPC:
    def __init__(self, codex, home=None, timeout=30):
        self.session = str(uuid.uuid4())
        self.timeout = timeout
        self.events, self.inbox, self.responses = [], queue.Queue(), {}
        self.seq = 0
        env = isolated_env(home) if home else dict(os.environ)
        args = command(codex, 'app-server', '--listen', 'stdio://') if home else [codex, 'app-server', '--listen', 'stdio://']
        try:
            self.proc = subprocess.Popen(args, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
        except (OSError, ValueError):
            raise SchedulerError('Codex missing; install the official CLI, then run doctor. Fake backend remains available.') from None
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
        try:
            self.call('initialize', {'clientInfo': {'name': 'codex_scheduler', 'title': 'Codex Scheduler', 'version': '0.1.0'}})
            self.send({'method': 'initialized'})
        except BaseException:
            self.close()
            raise

    def _read(self):
        try:
            for line in self.proc.stdout:
                try:
                    msg = json.loads(line)
                except (ValueError, TypeError):
                    continue
                # Token deltas are not needed for durable scheduling. Final messages arrive separately.
                if msg.get('method', '').endswith('/delta'):
                    continue
                self.inbox.put(msg)
        finally:
            self.inbox.put(None)

    def send(self, msg):
        try:
            self.proc.stdin.write(json.dumps(msg) + '\n')
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError):
            raise SchedulerError('Codex connection closed; reconcile before resubmitting') from None

    def call(self, method, params=None):
        self.seq += 1
        ident = self.seq
        self.send({'id': ident, 'method': method, 'params': params or {}})
        deadline = time.monotonic() + self.timeout
        while ident not in self.responses:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SchedulerError('Codex RPC timeout; outcome must be reconciled')
            try:
                self._accept(self.inbox.get(timeout=remaining))
            except queue.Empty:
                raise SchedulerError('Codex RPC timeout; outcome must be reconciled') from None
        msg = self.responses.pop(ident)
        if 'error' in msg:
            err = msg['error']
            raise RPCError(err.get('code'), err.get('data'), err.get('message'))
        return msg.get('result', {})

    def _accept(self, msg):
        if msg is None:
            raise SchedulerError('Codex app-server exited; reconcile saved turn')
        if 'method' not in msg and 'id' in msg:
            self.responses[msg['id']] = msg
        else:
            self.events.append(msg)

    def drain(self):
        while True:
            try:
                self._accept(self.inbox.get_nowait())
            except queue.Empty:
                break
        events, self.events = self.events, []
        return events

    def respond(self, ident, result):
        self.send({'id': ident, 'result': result})

    def close(self):
        if not hasattr(self, 'proc'):
            return
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=3)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        self.proc.stdout.close()
