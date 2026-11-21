#!/usr/bin/env python3
"""Optional real-account transport smoke test. Never used by the fixture test suite."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from codex_scheduler.protocol import RPC
from codex_scheduler.store import SchedulerError


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--codex',default='codex')
    p.add_argument('--execute',action='store_true',help='Send one small read-only smoke turn, then test resume in a new process')
    p.add_argument('--report',required=True)
    args=p.parse_args()
    binary=shutil.which(args.codex)
    if not binary: raise SystemExit('Codex is not installed')
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    before=digest(home/'auth.json')
    report={'codex':binary,'model_messages':0,'account_read':False,'structured_usage_read':False,'default_auth_file_unchanged':None}
    rpc=None
    try:
        rpc=RPC(binary)
        account=rpc.call('account/read',{'refreshToken':False})
        report['account_read']=account.get('account',{}).get('type')=='chatgpt'
        usage=rpc.call('account/rateLimits/read')
        report['structured_usage_read']=isinstance(usage.get('rateLimits'),dict)
        report['ordinary_usage_allowed']=usage.get('ordinaryUsageAllowed')
        report['usage_windows']={name:{k:window.get(k) for k in ('usedPercent','resetsAt','windowDurationMins')} for name in ('primary','secondary') if isinstance((window:=usage.get('rateLimits',{}).get(name)),dict)}
        if args.execute:
            models=rpc.call('model/list')['data']
            model=next((m['model'] for m in models if m.get('isDefault')),models[0]['model'])
            report['model']=model
            with tempfile.TemporaryDirectory(prefix='codex-scheduler-live-work-') as project:
                params={'cwd':project,'model':model,'sandbox':'read-only','approvalPolicy':'on-request'}
                thread=rpc.call('thread/start',{**params,'ephemeral':False})['thread']
                report['thread_start']=True
                report['model_messages']=1
                turn=rpc.call('turn/start',{'threadId':thread['id'],'input':[{'type':'text','text':'Reply exactly scheduler-smoke-ok. Do not use tools or modify files.','text_elements':[]}]})['turn']
                deadline=time.monotonic()+120
                terminal=None
                while time.monotonic()<deadline and not terminal:
                    for event in rpc.drain():
                        if 'id' in event:
                            raise SchedulerError('Unexpected approval during smoke test; no response sent')
                        if event.get('method')=='turn/completed' and event['params']['turn']['id']==turn['id']:
                            terminal=event['params']['turn']
                    if not terminal: time.sleep(0.1)
                if not terminal: raise SchedulerError('Smoke turn timed out; inspect saved Codex session')
                report['turn_status']=terminal['status']
                if terminal['status']!='completed':
                    report['error_kind']=(terminal.get('error') or {}).get('codexErrorInfo')
                    raise SchedulerError('Smoke turn did not complete')
                rpc.close()
                rpc=RPC(binary)
                resumed=rpc.call('thread/resume',{'threadId':thread['id'],**params})['thread']
                report['resume_after_process_restart']=resumed['id']==thread['id']
                turns=rpc.call('thread/turns/list',{'threadId':thread['id'],'limit':100,'sortDirection':'desc'})['data']
                report['saved_turn_found']=any(t['id']==turn['id'] and t['status']=='completed' for t in turns)
        report['passed']=True
    except SchedulerError as err:
        report['passed']=False
        report['error']=str(err)
    finally:
        if rpc: rpc.close()
        report['default_auth_file_unchanged']=digest(home/'auth.json')==before
        Path(args.report).write_text(json.dumps(report,indent=2)+'\n')
        print(json.dumps(report,indent=2))
    return 0 if report.get('passed') else 1


if __name__=='__main__': sys.exit(main())
