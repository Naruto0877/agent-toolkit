"""Allowlisted usage storage, sparse merges, model applicability, conservative recovery."""
import json
import math

STALE_SECONDS = 60
RETRY_SECONDS = 60


def observe(store, alias, data, now, sparse=False):
    # Notifications carry one bucket. Missing/null fields in a notification preserve previous values.
    snapshots = data.get('rateLimitsByLimitId') or {}
    if not snapshots and isinstance(data.get('rateLimits'), dict):
        snap = data['rateLimits']
        snapshots = {snap.get('limitId') or 'codex': snap}
    if not snapshots:
        if not sparse:
            store.update('accounts', 'alias', alias, usage_checked=now, usage_ok=0, problem='usage unknown')
        return
    with store.transaction():
        fields = {'usage_checked': now, 'usage_ok': 1, 'problem': None}
        if not sparse or data.get('ordinaryUsageAllowed') is not None:
            fields['ordinary'] = data.get('ordinaryUsageAllowed')
        if data.get('accountId'):
            fields['account_id'] = str(data['accountId'])
        store.update('accounts', 'alias', alias, **fields)
        for bucket, snap in snapshots.items():
            old = store.one('SELECT * FROM buckets WHERE alias=? AND bucket=?', (alias, bucket)) or {}
            vals = {}
            for column, field in [('normal_model', 'normalModelSlug'), ('reached', 'rateLimitReachedType'), ('spend', 'spendControlReached')]:
                value = snap.get(field)
                vals[column] = old.get(column) if sparse and value is None else value
            store.db.execute('INSERT OR REPLACE INTO buckets VALUES(?,?,?,?,?,?)', (alias, bucket, vals['normal_model'], vals['reached'], vals['spend'], now))
            for kind in ('primary', 'secondary'):
                window = snap.get(kind)
                if window is None:
                    if not sparse:
                        # Preserve last observation for display, but do not treat it as fresh.
                        pass
                    continue
                used = window.get('usedPercent')
                if not isinstance(used, (int, float)) or not math.isfinite(used):
                    continue
                store.db.execute('INSERT OR REPLACE INTO windows VALUES(?,?,?,?,?,?,?)', (alias, bucket, kind, used, window.get('resetsAt'), window.get('windowDurationMins'), now))


def applicable(store, alias, model):
    buckets = store.all('SELECT * FROM buckets WHERE alias=?', (alias,))
    # Global codex quota plus quotas explicitly matching this model. Unmapped buckets
    # are conservatively applicable; never guess that an exhausted bucket is irrelevant.
    buckets = [b for b in buckets if not b['normal_model'] or b['normal_model'] == model or b['bucket'] == 'codex' or b['bucket'] == model]
    names = {b['bucket'] for b in buckets}
    windows = [w for w in store.all('SELECT * FROM windows WHERE alias=?', (alias,)) if w['bucket'] in names]
    return buckets, windows


def capacity(store, alias, model, headroom, now, confirmed=False):
    account = store.account(alias)
    buckets, windows = applicable(store, alias, model)
    blocking = [w for w in windows if w['used'] >= 100]
    blocked = bool(blocking or account['ordinary'] == 0 or any(b['reached'] or b['spend'] for b in buckets))
    known_resets = [w['reset'] for w in blocking if w['reset'] is not None]
    # Wake when ALL known blocking windows might have reset. Unknown estimates poll.
    next_check = max(known_resets) if known_resets else now + RETRY_SECONDS
    if next_check <= now:
        next_check = now + RETRY_SECONDS
    unknown = (not account['usage_ok'] or not windows or any(w['observed'] < now-STALE_SECONDS for w in windows))
    # Recovery requires explicit backend permission if the installed schema supplies it.
    # With older schemas (null flag), fresh percentages suffice only for NEW work.
    if confirmed and account['ordinary'] != 1:
        unknown = True
    insufficient = any(100-w['used'] < headroom for w in windows)
    if blocked:
        return 'blocked', next_check, 'confirmed blocking usage window or backend restriction'
    if unknown:
        return 'unknown', now + RETRY_SECONDS, 'usage unknown; choose explicitly for new work'
    if insufficient:
        future = [w['reset'] for w in windows if 100-w['used'] < headroom and w['reset'] is not None and w['reset'] > now]
        return 'insufficient', max(future) if future else now+RETRY_SECONDS, 'insufficient estimated checkpoint headroom'
    # The earliest refresh among applicable usable windows, without ignoring other windows.
    refresh = min((w['reset'] for w in windows if w['reset'] is not None and w['reset'] > now), default=float('inf'))
    return 'ready', refresh, 'all applicable windows have checkpoint headroom'


def eligible(account, project, model):
    projects, models = json.loads(account['projects']), json.loads(account['models'])
    catalog = json.loads(account['catalog'])
    return bool(account['verified'] and (not projects or project in projects) and (not models or model in models) and (not catalog or model in catalog))
