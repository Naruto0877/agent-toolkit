import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from tools.skills import build as builder
from tools import workspace


def legacy_python():
    candidates=[os.environ.get('TOOLKIT_TEST_LEGACY_PYTHON'),shutil.which('python3.9'),'/usr/bin/python3']
    for executable in candidates:
        if not executable or not Path(executable).exists(): continue
        result=subprocess.run([executable,'-c','import json,sys; print(json.dumps(list(sys.version_info[:2])))'],capture_output=True,text=True)
        if result.returncode==0 and (3,8)<=tuple(json.loads(result.stdout))<(3,10): return executable
    return None


class RuntimeInstallationTests(unittest.TestCase):
    def test_explicit_unsupported_python_is_not_silently_replaced(self):
        result=subprocess.CompletedProcess([],0,stdout='[3,9]',stderr='')
        with patch('tools.workspace.shutil.which',return_value='/legacy/python'), patch('tools.workspace.subprocess.run',return_value=result):
            with self.assertRaisesRegex(ValueError,'Python 3.10'):
                workspace.select_python('/legacy/python')

    def test_selected_interpreter_is_passed_to_the_app_installer(self):
        with patch('tools.workspace.subprocess.run') as call:
            workspace.execute(['sh','install.sh'],Path.cwd(),python='/supported/python')
        self.assertEqual(call.call_args.kwargs['env']['AGENT_TOOLKIT_PYTHON'],'/supported/python')

    def test_source_launcher_rejects_legacy_runtime_without_traceback(self):
        legacy=legacy_python()
        if not legacy: self.skipTest('Python below 3.10 not available; covered by runtime-repair CI')
        result=subprocess.run([legacy,str(workspace.ROOT/'apps/codex-scheduler/bin/codex-scheduler'),'--help'],capture_output=True,text=True)
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Python 3.10+',result.stderr)
        self.assertNotIn('Traceback',result.stderr)
        self.assertNotIn('unsupported operand',result.stderr)

    def test_direct_installer_rejects_legacy_before_creating_environment(self):
        legacy=legacy_python()
        if not legacy: self.skipTest('Python below 3.10 not available; covered by runtime-repair CI')
        with tempfile.TemporaryDirectory() as temp:
            app=Path(temp)/'app';(app/'scripts').mkdir(parents=True)
            shutil.copy2(workspace.ROOT/'apps/codex-scheduler/install.sh',app/'install.sh')
            shutil.copy2(workspace.ROOT/'apps/codex-scheduler/scripts/install.py',app/'scripts/install.py')
            env=dict(os.environ,AGENT_TOOLKIT_PYTHON=legacy)
            result=subprocess.run(['sh',str(app/'install.sh')],env=env,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('Python 3.10+',result.stderr)
            self.assertFalse((app/'.venv').exists())
            self.assertNotIn('Installed',result.stdout)

    def test_existing_legacy_environment_is_repaired_and_state_preserved(self):
        legacy=legacy_python()
        if not legacy: self.skipTest('Python below 3.10 not available; covered by runtime-repair CI')
        with tempfile.TemporaryDirectory(prefix='runtime repair ') as temp:
            with zipfile.ZipFile(io.BytesIO(builder.bundle_source('setup-codex-scheduler')[0])) as package:
                package.extractall(temp)
            root=Path(temp)/'agent-toolkit';app=root/'apps/codex-scheduler'
            subprocess.run([legacy,'-m','venv','--without-pip',str(app/'.venv')],check=True,capture_output=True,text=True)
            state=app/'.state';state.mkdir();sentinel=state/'scheduler.sqlite3';sentinel.write_bytes(b'fixture state: preserve exactly')
            credential=state/'profiles/A/auth.json';credential.parent.mkdir(parents=True);credential.write_text('fixture-only credential')
            # Simulate an old default python3 despite explicitly selecting a supported runtime.
            shim=root/'legacy-bin';shim.mkdir();(shim/'python3').symlink_to(legacy)
            env=dict(os.environ,PATH=str(shim)+os.pathsep+os.environ.get('PATH',''))
            result=subprocess.run([legacy,str(root/'tools/workspace.py'),'install','codex-scheduler','--python',sys.executable],env=env,capture_output=True,text=True,timeout=90)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('Installed using Python',result.stdout)
            backups=list(app.glob('.venv-backup-*'))
            self.assertEqual(len(backups),1)
            self.assertTrue((backups[0]/'bin/python').exists())
            current=subprocess.run([str(app/'.venv/bin/python'),'-c','import json,sys; print(json.dumps(list(sys.version_info[:2])))'],capture_output=True,text=True,check=True)
            self.assertEqual(tuple(json.loads(current.stdout)),sys.version_info[:2])
            help_result=subprocess.run([str(app/'.venv/bin/codex-scheduler'),'--help'],capture_output=True,text=True)
            self.assertEqual(help_result.returncode,0,help_result.stderr)
            self.assertIn('account',help_result.stdout)
            self.assertEqual(sentinel.read_bytes(),b'fixture state: preserve exactly')
            self.assertEqual(credential.read_text(),'fixture-only credential')
