import json
import os
import shlex
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from codex_scheduler.backend import FakeBackend
from codex_scheduler.cli import local_time, main
from codex_scheduler.core import Scheduler, worktree_for
from codex_scheduler.protocol import RPC, isolated_env
from codex_scheduler.store import Lock, SchedulerError, Store, clean
from codex_scheduler.usage import capacity, observe


def usage(used=0, reset=1480, weekly=0, weekly_reset=20000, allowed=True):
    return {'accountId': 'stable-workspace', 'ordinaryUsageAllowed': allowed, 'rateLimits': {'limitId':'codex', 'primary': {'usedPercent': used, 'resetsAt': reset, 'windowDurationMins':300}, 'secondary': {'usedPercent': weekly, 'resetsAt': weekly_reset, 'windowDurationMins':10080}}}


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root/'project'
        self.project.mkdir()
        self.fixture = self.root/'fixture.json'
        self.config = {'accounts': {}}
        self.now = 1000
        self.save()
        self.store = Store(self.root/'state', 'fake')
        self.backend = FakeBackend(self.store.home, self.fixture, lambda: self.now)
        self.scheduler = Scheduler(self.store, self.backend, lambda: self.now)

    def tearDown(self):
        self.scheduler.close()
        self.store.close()
        self.temp.cleanup()

    def save(self):
        self.fixture.write_text(json.dumps(self.config))

    def add(self, alias, used=0, reset=1480, weekly=0, **extra):
        self.config['accounts'][alias] = {'usage': usage(used,reset,weekly), **extra}
        self.save()
        self.scheduler.add_profile(alias)
        self.scheduler.refresh(alias)

    def enqueue(self, alias=None, **kw):
        return self.scheduler.enqueue('Review the code', str(self.project), 'model-test', alias, **kw)

    def starts(self):
        return [c for c in self.backend.calls if c[1] == 'turn/start']

    def test_profiles_isolated_and_survive_launcher_restart(self):
        default = self.root/'default'/'auth.json'
        default.parent.mkdir()
        default.write_text('default-credential-sentinel')
        self.add('A', email='a@example.test', workspace='A workspace')
        a_home = self.store.account('A')['home']
        (Path(a_home)/'auth.json').write_text('A-credential-sentinel')
        self.add('B', email='b@example.test', workspace='B workspace')
        self.assertNotEqual(a_home, self.store.account('B')['home'])
        self.store.close()
        self.store = Store(self.root/'state', 'fake')
        self.scheduler = Scheduler(self.store, self.backend, lambda:self.now)
        self.scheduler.refresh('A')
        self.scheduler.refresh('B')
        self.assertEqual(json.loads(self.store.account('A')['identity'])['email'], 'a@example.test')
        self.assertEqual(json.loads(self.store.account('B')['identity'])['workspaceName'], 'B workspace')
        self.assertEqual((Path(a_home)/'auth.json').read_text(), 'A-credential-sentinel')
        self.assertEqual(default.read_text(), 'default-credential-sentinel')
        self.assertEqual(Path(a_home).stat().st_mode & 0o777, 0o700)
        self.assertEqual((Path(a_home)/'config.toml').stat().st_mode & 0o777, 0o600)

    def test_unknown_profile_and_alias_validation(self):
        with self.assertRaises(SchedulerError): self.scheduler.add_profile('../escape')
        with self.assertRaises(SchedulerError): self.enqueue('missing')

    def test_home_cannot_mix_real_and_fake(self):
        with self.assertRaises(SchedulerError): Store(self.store.home, 'real')

    def test_identity_swap_blocks_dispatch(self):
        self.add('A')
        job = self.enqueue('A')
        self.config['accounts']['A']['email'] = 'someone-else@example.test'
        self.save()
        self.scheduler.tick()
        self.assertFalse(self.starts())
        self.assertEqual(self.store.account('A')['verified'], 0)
        self.assertEqual(self.store.job(job['id'])['account'], 'A')

    def test_workspace_id_swap_blocks_dispatch(self):
        self.add('A')
        job = self.enqueue('A')
        self.config['accounts']['A']['usage']['accountId'] = 'different-workspace'
        self.save()
        self.scheduler.tick()
        self.assertFalse(self.starts())
        self.assertFalse(self.store.account('A')['verified'])

    def test_example_selects_A_and_explicit_B_is_binding(self):
        self.add('A', used=80, reset=self.now+480)
        self.add('B', used=25, reset=self.now+10800)
        self.add('C', used=100, reset=self.now+720)
        self.assertEqual(self.enqueue()['account'], 'A')
        manual = self.enqueue('B')
        self.assertEqual(manual['account'], 'B')
        self.assertTrue(manual['explicit'])
        self.assertEqual(self.enqueue('C')['state'], 'waiting_usage')

    def test_large_checkpoint_headroom_can_select_B(self):
        self.add('A', used=80, reset=self.now+480)
        self.add('B', used=25, reset=self.now+10800)
        job = self.enqueue(size='large', headroom=30)
        self.assertEqual(job['account'], 'B')
        self.scheduler.tick()
        prompt = self.starts()[0][2]['input'][0]['text']
        self.assertIn('Prioritize the next useful checkpoint', prompt)

    def test_default_large_estimate_and_all_windows(self):
        self.add('A', used=80, weekly=95)
        self.add('B', used=25, reset=11800)
        self.assertEqual(self.enqueue(size='large')['account'], 'B')

    def test_stale_status_is_refreshed(self):
        self.add('A', used=20)
        self.now += 61
        self.config['accounts']['A']['usage'] = usage(100, reset=3000)
        self.save()
        self.assertEqual(self.enqueue()['state'], 'queued')
        self.assertEqual(self.store.account('A')['usage_checked'], self.now)

    def test_missing_usage_unknown_manual_execution_remains_available(self):
        self.add('A', usageUnknown=True)
        self.assertEqual(self.enqueue()['state'], 'queued')
        job = self.enqueue('A')
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'], 'completed')

    def test_project_and_model_eligibility(self):
        self.add('A')
        self.store.update('accounts', 'alias', 'A', projects=json.dumps([str(self.root/'other')]))
        self.assertIsNone(self.enqueue()['account'])
        with self.assertRaises(SchedulerError): self.enqueue('A')
        self.store.update('accounts', 'alias', 'A', projects='[]', models='["other-model"]')
        self.assertIsNone(self.enqueue()['account'])

    def test_all_blocked_queues_with_next_check_no_model_messages(self):
        self.add('A', used=100, reset=1720)
        self.add('B', used=100, reset=1900)
        job = self.enqueue()
        self.assertEqual(job['state'], 'queued')
        self.assertEqual(job['next_check'], 1720)
        self.scheduler.tick()
        self.assertFalse(self.starts())

    def test_empty_worker_sends_no_model_messages(self):
        self.add('A')
        self.scheduler.start_worker()
        for _ in range(5): self.scheduler.tick()
        self.assertFalse(self.starts())
        self.assertFalse(any(c[1] == 'thread/start' for c in self.backend.calls))

    def test_waits_for_all_blocking_windows_and_rechecks_without_guessing(self):
        self.add('C', used=100, reset=1720, weekly=100)
        job = self.enqueue('C')
        self.assertEqual(job['next_check'], 20000)
        self.now = 20000
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'], 'waiting_usage')
        self.assertEqual(self.store.job(job['id'])['next_check'], 20060)
        self.assertFalse(self.starts())

    def test_C_checked_at_12_minutes_while_B_running(self):
        self.add('C', used=100, reset=1720)
        waiting = self.enqueue('C')
        self.add('B', used=25, reset=11800, turns=['running'])
        busy = self.enqueue('B')
        self.scheduler.tick()
        self.assertEqual(self.store.job(busy['id'])['state'], 'running')
        before = len([c for c in self.backend.calls if c[:2] == ('C','account/rateLimits/read')])
        self.config['accounts']['C']['usage'] = usage(0, reset=3000)
        self.save()
        self.now = 1720
        self.scheduler.tick()
        after = len([c for c in self.backend.calls if c[:2] == ('C','account/rateLimits/read')])
        self.assertGreater(after,before)
        self.assertEqual(self.store.job(waiting['id'])['state'], 'ready')
        self.assertEqual(len(self.starts()), 1)
        self.scheduler.cancel(busy['id'])
        self.scheduler.tick()
        self.assertEqual(self.store.job(waiting['id'])['state'], 'completed')
        self.assertEqual([c[0] for c in self.starts()], ['B','C'])

    def make_limited(self):
        self.add('C', used=80, reset=1720, turns=['limit','complete'])
        job = self.enqueue('C')
        self.scheduler.dispatch()
        self.config['accounts']['C']['usage'] = usage(100, reset=1720)
        self.save()
        self.scheduler.pump()
        return self.store.job(job['id'])

    def test_confirmed_limit_checkpoint_and_one_pinned_continuation_after_restart(self):
        job = self.make_limited()
        self.assertEqual(job['state'], 'waiting_usage')
        self.assertTrue(job['thread_id'])
        self.assertTrue(job['turn_id'])
        self.assertTrue(job['confirmed_limit'])
        self.add('B', used=0, reset=1800)
        self.scheduler.close()
        self.scheduler = Scheduler(self.store, self.backend, lambda:self.now)
        self.scheduler.start_worker()
        self.now = 1720
        self.config['accounts']['C']['usage'] = usage(0, reset=3000)
        self.save()
        for _ in range(3): self.scheduler.tick()
        done = self.store.job(job['id'])
        self.assertEqual(done['state'],'completed')
        self.assertEqual(done['thread_id'],job['thread_id'])
        self.assertEqual(done['account'],'C')
        self.assertEqual(len(self.starts()),2)
        self.assertIn('Continue the saved task',self.starts()[-1][2]['input'][0]['text'])
        self.assertTrue(any(c[1] == 'thread/resume' for c in self.backend.calls))

    def test_weekly_exhaustion_blocks_after_short_window_refresh(self):
        job = self.make_limited()
        self.now = 1720
        self.config['accounts']['C']['usage'] = usage(0,reset=3000,weekly=100,weekly_reset=9000,allowed=False)
        self.save()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'waiting_usage')
        self.assertEqual(self.store.job(job['id'])['next_check'],9000)
        self.assertEqual(len(self.starts()),1)

    def test_null_permission_cannot_recover_confirmed_limit(self):
        job = self.make_limited()
        self.now = 1720
        self.config['accounts']['C']['usage'] = usage(0,reset=3000,allowed=None)
        self.save()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'waiting_usage')
        self.assertEqual(len(self.starts()),1)

    def test_sparse_notifications_merge_without_clearing_weekly_or_denial(self):
        self.add('A', weekly=100)
        observe(self.store,'A',{'ordinaryUsageAllowed':False,'rateLimits':{'limitId':'codex','primary':{'usedPercent':20,'resetsAt':None}}},self.now)
        observe(self.store,'A',{'rateLimits':{'limitId':'codex','primary':{'usedPercent':0,'resetsAt':1900},'secondary':None,'spendControlReached':None}},self.now+5,sparse=True)
        self.assertEqual(self.store.account('A')['ordinary'],0)
        self.assertEqual(capacity(self.store,'A','model-test',5,self.now+5)[0],'blocked')
        primary=self.store.one("SELECT * FROM windows WHERE alias='A' AND kind='primary'")
        self.assertEqual(primary['reset'],1900)
        weekly=self.store.one("SELECT * FROM windows WHERE alias='A' AND kind='secondary'")
        self.assertEqual(weekly['used'],100)

    def test_revised_and_missing_reset_estimates(self):
        self.add('A',used=100,reset=1720)
        observe(self.store,'A',{'rateLimits':{'limitId':'codex','primary':{'usedPercent':100,'resetsAt':2400}}},self.now,sparse=True)
        self.assertEqual(capacity(self.store,'A','model-test',5,self.now)[1],2400)
        observe(self.store,'A',{'rateLimits':{'limitId':'codex','primary':{'usedPercent':100,'resetsAt':None}}},self.now,sparse=True)
        self.assertEqual(capacity(self.store,'A','model-test',5,self.now)[1],1060)

    def test_multiple_buckets_and_model_specific_limits(self):
        self.add('A')
        snap=usage()['rateLimits']
        other={'limitId':'other','normalModelSlug':'different-model','primary':{'usedPercent':100,'resetsAt':5000}}
        observe(self.store,'A',{'rateLimitsByLimitId':{'codex':snap,'other':other},'ordinaryUsageAllowed':True},self.now)
        self.assertEqual(capacity(self.store,'A','model-test',5,self.now)[0],'ready')
        observe(self.store,'A',{'rateLimitsByLimitId':{'unknown-bucket':{'primary':{'usedPercent':100,'resetsAt':6000}}},'ordinaryUsageAllowed':True},self.now)
        self.assertEqual(capacity(self.store,'A','model-test',5,self.now)[0],'blocked')

    def test_cancel_queued_and_waiting_send_nothing(self):
        self.add('A',used=100)
        for job in (self.enqueue(), self.enqueue('A')):
            self.scheduler.cancel(job['id'])
            self.assertEqual(self.store.job(job['id'])['state'],'cancelled')
        self.now=30000
        self.scheduler.tick()
        self.assertFalse(self.starts())

    def test_cancel_running_interrupts_and_does_not_resume(self):
        self.add('A',turns=['running'])
        job=self.enqueue('A')
        self.scheduler.tick()
        self.scheduler.cancel(job['id'])
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'cancelled')
        self.assertEqual(len(self.starts()),1)

    def test_approval_is_durable_and_answered_only_by_current_session(self):
        self.add('A',turns=['approval'])
        job=self.enqueue('A')
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'approval')
        approval=self.store.one('SELECT * FROM approvals')
        self.assertEqual(approval['state'],'pending')
        self.store.update('approvals','id',approval['id'],response='{"decision":"accept"}')
        self.scheduler.tick()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'completed')
        self.assertEqual(self.store.one('SELECT * FROM approvals')['state'],'answered')

    def test_approval_survives_crash_without_replay_or_auto_accept(self):
        self.add('A',turns=['approval'])
        job=self.enqueue('A')
        self.scheduler.tick()
        self.scheduler.close()
        self.scheduler=Scheduler(self.store,self.backend,lambda:self.now)
        self.scheduler.start_worker()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'needs_review')
        self.assertEqual(self.store.one('SELECT * FROM approvals')['state'],'pending')
        self.assertFalse(any(c[1]=='response' for c in self.backend.calls))
        self.assertEqual(len(self.starts()),1)

    def test_crash_after_acceptance_before_ack_reconciles_without_duplicate(self):
        self.add('A')
        job=self.enqueue('A')
        self.scheduler.dispatch()
        self.store.update('jobs','id',job['id'],state='submitting',turn_id=None)
        self.scheduler.close()
        self.scheduler=Scheduler(self.store,self.backend,lambda:self.now)
        self.scheduler.start_worker()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'completed')
        self.assertEqual(len(self.starts()),1)

    def test_crash_before_send_intent_is_ambiguous_and_never_replayed(self):
        self.add('A')
        job=self.enqueue('A')
        rpc=self.scheduler.connect('A')
        thread=rpc.call('thread/start',{'cwd':str(self.project)})['thread']
        self.store.update('jobs','id',job['id'],state='submitting',thread_id=thread['id'],attempt='unsure')
        self.scheduler.start_worker()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'needs_review')
        self.assertFalse(self.starts())

    def test_crash_before_thread_commit_can_safely_dispatch(self):
        self.add('A')
        job=self.enqueue('A')
        self.store.update('jobs','id',job['id'],state='preparing')
        self.scheduler.start_worker()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'completed')
        self.assertEqual(len(self.starts()),1)

    def test_unknown_unfinished_turn_requires_review_and_blocks_same_worktree(self):
        self.add('A',turns=['running'])
        old=self.enqueue('A')
        self.scheduler.tick()
        self.scheduler.close()
        self.scheduler=Scheduler(self.store,self.backend,lambda:self.now)
        self.scheduler.start_worker()
        newer=self.enqueue('A')
        self.scheduler.tick()
        self.assertEqual(self.store.job(old['id'])['state'],'needs_review')
        self.assertEqual(self.store.job(newer['id'])['state'],'ready')
        self.assertEqual(len(self.starts()),1)
        self.scheduler.cancel(old['id'])
        with self.assertRaises(SchedulerError): self.scheduler.resolve(old['id'],'retry')

    def test_worker_and_worktree_locks(self):
        self.scheduler.start_worker()
        with self.assertRaises(SchedulerError):
            Lock(self.store.home/'worker.lock').acquire()
        lock=self.store.home/'locks'/'test.lock'
        with Lock(lock):
            with self.assertRaises(SchedulerError): Lock(lock).acquire()
        with Lock(lock): pass

    def test_real_git_subdirectories_share_writer_key(self):
        subprocess.run(['git','init','-q',str(self.project)],check=True)
        nested=self.project/'src'
        nested.mkdir()
        self.assertEqual(worktree_for(str(nested)),worktree_for(str(self.project)))

    def test_secrets_not_in_scheduler_database_or_events(self):
        self.add('A')
        secret='sk-'+'x'*40
        with self.assertRaises(SchedulerError):
            self.scheduler.enqueue('Use '+secret,str(self.project),'model-test','A')
        self.store.event(None,self.now,'secret-test',{'access_token':'sensitive','message':'Bearer '+secret})
        text='\n'.join(self.store.db.iterdump())
        self.assertNotIn(secret,text)
        self.assertNotIn('sensitive',text)
        self.assertIn('[REDACTED]',text)

    def test_local_time_is_timezone_aware(self):
        self.assertEqual(local_time(None),'unknown')
        self.assertRegex(local_time(1000),r'1970-01-01 .+ [A-Za-z+0-9-]+')

    def test_inherited_auth_environment_is_removed(self):
        with patch.dict(os.environ,{'OPENAI_API_KEY':'secret','CODEX_ACCESS_TOKEN':'secret','CODEX_HOME':'default','CODEX_SQLITE_HOME':'shared-db'}):
            env=isolated_env('/isolated')
            self.assertNotIn('OPENAI_API_KEY',env)
            self.assertNotIn('CODEX_ACCESS_TOKEN',env)
            self.assertNotIn('CODEX_SQLITE_HOME',env)
            self.assertEqual(env['CODEX_HOME'],'/isolated')

    def test_cli_end_to_end_across_processes(self):
        root=Path(__file__).resolve().parents[1]
        args=[sys.executable,str(root/'bin'/'codex-scheduler'),'--backend','fake','--home',str(self.root/'cli-state'),'--now','1000']
        def run(*words):
            result=subprocess.run(args+list(words),capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            return result.stdout
        run('account','add','A')
        run('account','add','B')
        self.assertIn('A@fake.invalid',run('account','verify','A'))
        run('run','--account','B','--project',str(self.project),'Review')
        self.assertIn('completed',run('worker','--once'))
        self.assertEqual(json.loads(run('queue')),[])

    def test_ambiguous_continuation_does_not_mistake_older_turn_for_new_submission(self):
        job=self.make_limited()
        path=self.backend.home/'fake-C.json'
        state=json.loads(path.read_text())
        thread=state['threads'][job['thread_id']]
        thread['turns'].insert(0,{'id':'older-completed-turn','status':'completed','items':[]})
        path.write_text(json.dumps(state))
        self.store.update('jobs','id',job['id'],state='submitting',prior_turn=job['turn_id'],turn_id=None)
        self.scheduler.start_worker()
        self.assertEqual(self.store.job(job['id'])['state'],'needs_review')
        self.assertEqual(len(self.starts()),1)

    def test_final_message_survives_empty_completed_turn_payload(self):
        self.add('A',turns=['running'])
        job=self.enqueue('A')
        self.scheduler.dispatch()
        rpc=self.scheduler.active['rpc']
        job=self.store.job(job['id'])
        rpc.events.extend([
            {'method':'item/completed','params':{'threadId':job['thread_id'],'item':{'type':'agentMessage','text':'Useful final answer'}}},
            {'method':'turn/completed','params':{'threadId':job['thread_id'],'turn':{'id':job['turn_id'],'status':'completed','items':[]}}}])
        self.scheduler.pump()
        self.assertEqual(self.store.job(job['id'])['output'],'Useful final answer')

    def test_notification_is_consumed_and_persisted_during_active_turn(self):
        self.add('A',turns=['running'])
        self.enqueue('A')
        self.scheduler.dispatch()
        self.scheduler.active['rpc'].events.append({'method':'account/rateLimits/updated','params':{'rateLimits':{'limitId':'codex','primary':{'usedPercent':99,'resetsAt':3200}}}})
        self.scheduler.pump()
        self.assertEqual(self.store.one("SELECT used FROM windows WHERE alias='A' AND kind='primary'")['used'],99)

    def test_worktree_lock_is_shared_across_state_directories(self):
        self.add('A',turns=['running'])
        self.enqueue('A')
        self.scheduler.tick()
        other=Store(self.root/'other-state','fake')
        backend=FakeBackend(other.home,self.fixture,lambda:self.now)
        scheduler=Scheduler(other,backend,lambda:self.now)
        try:
            scheduler.add_profile('A')
            scheduler.refresh('A')
            job=scheduler.enqueue('Review',str(self.project),'model-test','A')
            scheduler.tick()
            self.assertEqual(other.job(job['id'])['state'],'ready')
            self.assertFalse(any(c[1]=='turn/start' for c in backend.calls))
        finally:
            scheduler.close()
            other.close()

    def test_missing_blocking_reset_polls_without_erasing_observation(self):
        self.add('A',used=100,reset=None)
        job=self.enqueue('A')
        self.assertEqual(job['next_check'],1060)
        self.now=1060
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'waiting_usage')
        self.assertEqual(self.store.one("SELECT used FROM windows WHERE alias='A' AND kind='primary'")['used'],100)

    def test_non_usage_failure_is_not_automatically_retried(self):
        self.add('A',turns=['running'])
        job=self.enqueue('A')
        self.scheduler.dispatch()
        job=self.store.job(job['id'])
        self.scheduler.active['rpc'].events.append({'method':'turn/completed','params':{'threadId':job['thread_id'],'turn':{'id':job['turn_id'],'status':'failed','items':[],'error':{'codexErrorInfo':'modelNotFound'}}}})
        self.scheduler.pump()
        self.now+=10000
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'failed')
        self.assertEqual(len(self.starts()),1)

    def test_overdue_wake_after_sleep_checks_capacity_instead_of_replaying(self):
        job=self.make_limited()
        self.now+=200000
        self.scheduler.start_worker()
        self.scheduler.tick()
        self.assertEqual(self.store.job(job['id'])['state'],'waiting_usage')
        self.assertEqual(len(self.starts()),1)


    def test_preparing_empty_thread_recovery_creates_new_thread_before_first_turn(self):
        self.add('A')
        job=self.enqueue('A')
        rpc=self.scheduler.connect('A')
        empty=rpc.call('thread/start',{'cwd':str(self.project)})['thread']
        self.store.update('jobs','id',job['id'],state='preparing',thread_id=empty['id'])
        self.scheduler.start_worker()
        self.scheduler.tick()
        done=self.store.job(job['id'])
        self.assertEqual(done['state'],'completed')
        self.assertNotEqual(done['thread_id'],empty['id'])
        self.assertEqual(len(self.starts()),1)

    def test_sparse_primary_does_not_hide_stale_weekly_from_refresh(self):
        self.add('A')
        self.now+=61
        observe(self.store,'A',{'rateLimits':{'limitId':'codex','primary':{'usedPercent':10,'resetsAt':2000}}},self.now,sparse=True)
        before=len([c for c in self.backend.calls if c[1]=='account/rateLimits/read'])
        job=self.enqueue()
        after=len([c for c in self.backend.calls if c[1]=='account/rateLimits/read'])
        self.assertGreater(after,before)
        self.assertEqual(job['account'],'A')

    def test_revised_notification_moves_durable_wake_earlier(self):
        self.add('A',used=100,reset=5000)
        waiting=self.enqueue('A')
        self.add('B',turns=['running'])
        self.enqueue('B')
        self.scheduler.tick()
        # This is also called after a fresh status read in another CLI process.
        observe(self.store,'A',{'rateLimits':{'limitId':'codex','primary':{'usedPercent':100,'resetsAt':2000}}},self.now,sparse=True)
        self.scheduler.reschedule('A')
        self.assertEqual(self.store.job(waiting['id'])['next_check'],2000)
        self.assertEqual(len(self.starts()),1)


    def test_saved_C_thread_waits_for_weekly_while_B_runs_then_resumes_once(self):
        waiting=self.make_limited()
        self.add('B',used=25,reset=11800,turns=['running'])
        busy=self.enqueue('B')
        self.scheduler.tick()
        self.now=1720
        self.config['accounts']['C']['usage']=usage(0,reset=20000,weekly=100,weekly_reset=9000,allowed=False)
        self.save()
        self.scheduler.tick()
        self.assertEqual(self.store.job(waiting['id'])['state'],'waiting_usage')
        self.assertEqual(self.store.job(waiting['id'])['next_check'],9000)
        self.assertEqual(len(self.starts()),2)
        self.now=9000
        self.config['accounts']['C']['usage']=usage(0,reset=20000,weekly=0,weekly_reset=600000,allowed=True)
        self.save()
        self.scheduler.tick()
        self.assertEqual(self.store.job(waiting['id'])['state'],'ready')
        self.assertEqual(len(self.starts()),2)
        self.scheduler.cancel(busy['id'])
        for _ in range(3): self.scheduler.tick()
        done=self.store.job(waiting['id'])
        self.assertEqual(done['state'],'completed')
        self.assertEqual(done['account'],'C')
        self.assertEqual(done['thread_id'],waiting['thread_id'])
        self.assertEqual(len(self.starts()),3)

    def test_cancellation_committed_before_approval_send_prevents_accept(self):
        self.add('A',turns=['approval'])
        job=self.enqueue('A')
        self.scheduler.tick()
        approval=self.store.one('SELECT * FROM approvals')
        self.store.update('approvals','id',approval['id'],response='{"decision":"accept"}')
        original=self.store.update
        def racing_update(table,key,value,**fields):
            original(table,key,value,**fields)
            if table=='approvals' and fields.get('state')=='responding':
                self.scheduler.cancel(job['id'])
        with patch.object(self.store,'update',side_effect=racing_update):
            self.scheduler.pump()
        self.scheduler.tick()
        self.assertFalse(any(c[1]=='response' for c in self.backend.calls))
        self.assertEqual(self.store.job(job['id'])['state'],'cancelled')
        self.assertEqual(len(self.starts()),1)


    def test_expected_email_rejects_wrong_login_even_with_explicit_verification(self):
        self.config['accounts']['A']={'email':'wrong@example.test'}
        self.save()
        self.scheduler.add_profile('A',expected_email='intended@example.test')
        with self.assertRaisesRegex(SchedulerError,'Expected intended@example.test'):
            self.scheduler.refresh('A',verify=True)
        account=self.store.account('A')
        self.assertFalse(account['verified'])
        self.assertEqual(account['expected_email'],'intended@example.test')
        self.assertIn('wrong@example.test',account['problem'])
        with self.assertRaises(SchedulerError): self.enqueue('A')
        self.assertFalse(self.starts())

    def test_expected_email_matching_is_case_insensitive_and_persists_after_restart(self):
        self.config['accounts']['A']={'email':'Intended@Example.Test'}
        self.save()
        self.scheduler.add_profile('A',expected_email=' Intended@example.test ')
        self.scheduler.refresh('A')
        self.store.close()
        self.store=Store(self.root/'state','fake')
        self.scheduler=Scheduler(self.store,self.backend,lambda:self.now)
        self.assertEqual(self.store.account('A')['expected_email'],'intended@example.test')
        self.scheduler.refresh('A')
        self.assertTrue(self.store.account('A')['verified'])

    def test_expected_email_requires_backend_to_report_email(self):
        self.config['accounts']['A']={'email':None}
        self.save()
        self.scheduler.add_profile('A',expected_email='intended@example.test')
        with self.assertRaisesRegex(SchedulerError,'unknown email'):
            self.scheduler.refresh('A')
        self.assertFalse(self.store.account('A')['verified'])

    def test_no_login_registration_does_not_invoke_codex(self):
        root=Path(__file__).resolve().parents[1]
        args=[sys.executable,str(root/'bin'/'codex-scheduler'),'--home',str(self.root/'registered-state'),'--codex','/missing-codex','account','add','A','--email','intended@example.test','--no-login']
        result=subprocess.run(args,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        account=json.loads(result.stdout)[0]
        self.assertFalse(account['verified'])
        self.assertEqual(account['expected_email'],'intended@example.test')
        self.assertFalse((Path(account['home'])/'auth.json').exists())

    def test_expected_email_migration_preserves_existing_account(self):
        import sqlite3
        schema=self.store.one("SELECT sql FROM sqlite_master WHERE name='accounts'")['sql']
        legacy=self.root/'legacy-state'
        legacy.mkdir()
        db=sqlite3.connect(legacy/'scheduler.sqlite3')
        db.execute(schema.replace(', expected_email TEXT',''))
        db.execute('INSERT INTO accounts(alias,home) VALUES(?,?)',('old','/existing/profile/home'))
        db.commit()
        db.close()
        migrated=Store(legacy,'fake')
        try:
            account=migrated.account('old')
            self.assertEqual(account['home'],'/existing/profile/home')
            self.assertIsNone(account['expected_email'])
        finally: migrated.close()



class TransportTests(unittest.TestCase):
    def test_json_rpc_handshake_event_order_and_error(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'codex'
            script=path.with_suffix('.py')
            script.write_text('''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 if m['method']=='initialize':
  print(json.dumps({'id':m['id'],'result':{}}),flush=True)
 elif m['method']=='account/read':
  print(json.dumps({'method':'account/rateLimits/updated','params':{'rateLimits':{'primary':{'usedPercent':1}}}}),flush=True)
  print(json.dumps({'id':m['id'],'result':{'ok':True}}),flush=True)
 else:
  print(json.dumps({'id':m['id'],'error':{'code':-32601,'message':'hidden-secret'}}),flush=True)
''')
            path.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' \"$@\"\n')
            path.chmod(0o755)
            rpc=RPC(str(path),temp,timeout=2)
            try:
                self.assertEqual(rpc.call('account/read'),{'ok':True})
                self.assertEqual(rpc.drain()[0]['method'],'account/rateLimits/updated')
                with self.assertRaises(SchedulerError) as err: rpc.call('missing')
                self.assertNotIn('hidden-secret',str(err.exception))
            finally: rpc.close()


if __name__=='__main__': unittest.main()
