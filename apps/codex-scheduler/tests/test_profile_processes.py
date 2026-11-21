"""Real process/transport boundary with a local Codex executable double; no network."""
import argparse
import contextlib
import io
import json
import os
import shlex
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from codex_scheduler.providers import RealBackend
from codex_scheduler.cli import account_login
from codex_scheduler.core import Scheduler
from codex_scheduler.store import Store


class ProfileProcessTests(unittest.TestCase):
    def test_official_flow_adapter_keeps_A_and_default_intact_after_B_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            codex=root/'codex'
            script=codex.with_suffix('.py')
            script.write_text('''import json,os,pathlib,sys
home=pathlib.Path(os.environ['CODEX_HOME'])
assert 'cli_auth_credentials_store="file"' in sys.argv
assert 'OPENAI_API_KEY' not in os.environ
if 'login' in sys.argv:
 (home/'auth.json').write_text(json.dumps({'email':os.environ['TEST_LOGIN_EMAIL'],'credential':'never-persist-this-credential'}))
 sys.exit(0)
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 method=m['method']
 a=json.loads((home/'auth.json').read_text()) if (home/'auth.json').exists() else {}
 if method=='initialize': result={}
 elif method=='account/read': result={'account':{'type':'chatgpt','email':a.get('email'),'workspaceName':'Workspace '+a.get('email','unknown'),'planType':'plus'},'userId':a.get('email')}
 elif method=='model/list': result={'data':[{'model':'test-model','isDefault':True}]}
 elif method=='account/rateLimits/read': result={'accountId':a.get('email'),'ordinaryUsageAllowed':True,'rateLimits':{'limitId':'codex','primary':{'usedPercent':10,'resetsAt':3000}}}
 else: result={}
 print(json.dumps({'id':m['id'],'result':result}),flush=True)
''')
            codex.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(script))+' \"$@\"\n')
            codex.chmod(0o755)
            default=root/'default'
            default.mkdir()
            (default/'auth.json').write_text('default-login-sentinel')
            store=Store(root/'state')
            scheduler=Scheduler(store,RealBackend(str(codex)),lambda:1000)
            args=argparse.Namespace(backend='real',codex=str(codex),device_auth=False)
            try:
                with patch('sys.stdin.isatty',return_value=True), patch.dict(os.environ,{'CODEX_HOME':str(default),'OPENAI_API_KEY':'inherited-secret'}), contextlib.redirect_stdout(io.StringIO()):
                    scheduler.add_profile('A')
                    with patch.dict(os.environ,{'TEST_LOGIN_EMAIL':'a@example.test'}): account_login(args,scheduler,'A')
                    a_path=Path(store.account('A')['home'])/'auth.json'
                    a_before=a_path.read_bytes()
                    scheduler.add_profile('B')
                    with patch.dict(os.environ,{'TEST_LOGIN_EMAIL':'b@example.test'}): account_login(args,scheduler,'B')
                    self.assertEqual(a_path.read_bytes(),a_before)
                    self.assertEqual((default/'auth.json').read_text(),'default-login-sentinel')
                scheduler.close()
                store.close()
                store=Store(root/'state')
                scheduler=Scheduler(store,RealBackend(str(codex)),lambda:1000)
                scheduler.refresh('A')
                scheduler.refresh('B')
                self.assertEqual(json.loads(store.account('A')['identity'])['email'],'a@example.test')
                self.assertEqual(json.loads(store.account('B')['identity'])['workspaceName'],'Workspace b@example.test')
                self.assertNotIn('never-persist-this-credential','\n'.join(store.db.iterdump()))
                event=store.one("SELECT * FROM events WHERE kind='isolation_verified' ORDER BY id DESC")
                self.assertEqual(json.loads(event['details'])['previous_profiles'],['A'])
            finally:
                scheduler.close()
                store.close()
