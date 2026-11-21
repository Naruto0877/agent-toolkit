import contextlib
import json
import os
from pathlib import Path
import re
import sqlite3


class SchedulerError(Exception):
    pass


SECRET = re.compile(r'(?i)(?:\b(?:sk|sess|pat)-[A-Za-z0-9_-]{12,}|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+|bearer\s+\S+|(?:access_token|refresh_token|api_key)\s*[=:]\s*[^\s,;]+)')


def clean(value):
    """Never persist transport payloads wholesale; additionally redact common secrets."""
    if isinstance(value, str):
        return SECRET.sub('[REDACTED]', value)
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, dict):
        return {k: '[REDACTED]' if any(s in k.lower() for s in ('token', 'secret', 'password', 'authorization', 'api_key', 'apikey')) else clean(v) for k, v in value.items()}
    return value


def encoded(value):
    return json.dumps(clean(value), sort_keys=True)


class Lock:
    """Kernel-owned lock, released on crash; never decide ownership from a stale PID."""
    def __init__(self, path):
        self.path, self.file = Path(path), None

    def acquire(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.file = self.path.open('a+b')
        os.chmod(self.path, 0o600)
        try:
            if os.name == 'nt':
                import msvcrt
                self.file.seek(0)
                self.file.write(b'0')
                self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            self.file = None
            raise SchedulerError('Another worker or writer holds ' + str(self.path)) from None
        return self

    def close(self):
        if self.file:
            self.file.close()
            self.file = None

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *args):
        self.close()


class Store:
    def __init__(self, home, backend='real'):
        self.home = Path(home).expanduser().resolve()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.home, 0o700)
        self.db = sqlite3.connect(self.home / 'scheduler.sqlite3', timeout=10, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS accounts(
          alias TEXT PRIMARY KEY, home TEXT UNIQUE NOT NULL, identity TEXT, verified INTEGER DEFAULT 0,
          projects TEXT NOT NULL DEFAULT '[]', models TEXT NOT NULL DEFAULT '[]',
          catalog TEXT NOT NULL DEFAULT '[]', checked REAL, usage_checked REAL,
          usage_ok INTEGER DEFAULT 0, ordinary INTEGER, account_id TEXT, problem TEXT, expected_email TEXT);
        CREATE TABLE IF NOT EXISTS windows(
          alias TEXT REFERENCES accounts(alias), bucket TEXT, kind TEXT,
          used REAL, reset REAL, duration REAL, observed REAL NOT NULL,
          PRIMARY KEY(alias,bucket,kind));
        CREATE TABLE IF NOT EXISTS buckets(
          alias TEXT REFERENCES accounts(alias), bucket TEXT, normal_model TEXT,
          reached TEXT, spend INTEGER, observed REAL NOT NULL,
          PRIMARY KEY(alias,bucket));
        CREATE TABLE IF NOT EXISTS jobs(
          id TEXT PRIMARY KEY, created REAL NOT NULL, updated REAL NOT NULL,
          account TEXT REFERENCES accounts(alias), explicit INTEGER NOT NULL, project TEXT NOT NULL,
          worktree TEXT NOT NULL, model TEXT NOT NULL, size TEXT NOT NULL, headroom REAL NOT NULL,
          prompt TEXT NOT NULL, state TEXT NOT NULL, reason TEXT, next_check REAL,
          thread_id TEXT, turn_id TEXT, attempt TEXT, prior_turn TEXT,
          cancel INTEGER NOT NULL DEFAULT 0, confirmed_limit INTEGER NOT NULL DEFAULT 0,
          output TEXT, sandbox TEXT NOT NULL DEFAULT 'workspace-write');
        CREATE TABLE IF NOT EXISTS attempts(
          id TEXT PRIMARY KEY, job TEXT REFERENCES jobs(id), created REAL, prior_turn TEXT,
          turn_id TEXT, status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS approvals(
          id INTEGER PRIMARY KEY, job TEXT REFERENCES jobs(id), request_id TEXT NOT NULL,
          session TEXT NOT NULL, method TEXT NOT NULL, details TEXT NOT NULL,
          response TEXT, state TEXT NOT NULL DEFAULT 'pending', UNIQUE(session,request_id));
        CREATE TABLE IF NOT EXISTS events(
          id INTEGER PRIMARY KEY, job TEXT, at REAL NOT NULL, kind TEXT NOT NULL, details TEXT);
        ''')
        existing = self.one("SELECT value FROM meta WHERE key='backend'")
        if existing and existing['value'] != backend:
            self.db.close()
            raise SchedulerError('Real and fake backends require separate --home directories')
        self.db.execute("INSERT OR IGNORE INTO meta VALUES('backend',?)", (backend,))
        with self.transaction():
            columns = {row['name'] for row in self.all('PRAGMA table_info(accounts)')}
            if 'expected_email' not in columns:
                self.db.execute('ALTER TABLE accounts ADD COLUMN expected_email TEXT')
        os.chmod(self.home / 'scheduler.sqlite3', 0o600)

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            self.db.execute('ROLLBACK')
            raise

    def all(self, sql, args=()):
        return [dict(x) for x in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        row = self.db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def update(self, table, key, value, **fields):
        # Table/column names are internal constants, values always parameters.
        self.db.execute('UPDATE ' + table + ' SET ' + ','.join(k+'=?' for k in fields) + ' WHERE '+key+'=?', tuple(fields.values())+(value,))

    def job(self, job_id):
        job = self.one('SELECT * FROM jobs WHERE id=?', (job_id,))
        if not job:
            raise SchedulerError('Unknown job: ' + job_id)
        return job

    def account(self, alias):
        account = self.one('SELECT * FROM accounts WHERE alias=?', (alias,))
        if not account:
            raise SchedulerError('Unknown account: ' + alias)
        return account

    def event(self, job, now, kind, details):
        self.db.execute('INSERT INTO events(job,at,kind,details) VALUES(?,?,?,?)', (job, now, kind, encoded(details)))

    def close(self):
        self.db.close()
