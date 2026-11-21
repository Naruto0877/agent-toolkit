import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import build_skill
import install_skill


class SkillPackagingTests(unittest.TestCase):
    def test_default_package_has_no_account_defaults_or_runtime_state(self):
        with tempfile.TemporaryDirectory() as temp:
            archive=build_skill.build(temp)
            with zipfile.ZipFile(archive) as package:
                settings=json.loads(package.read('setup-codex-scheduler/assets/settings.json'))
                self.assertEqual(settings['profiles'],{})
                payload=package.read('setup-codex-scheduler/assets/codex-scheduler-source.zip')
                self.assertEqual(hashlib.sha256(payload).hexdigest(),settings['source_sha256'])
                with zipfile.ZipFile(io.BytesIO(payload)) as source:
                    names=source.namelist()
                    self.assertTrue(any(name.endswith('/codex_scheduler/core.py') for name in names))
                    for name in names:
                        self.assertFalse({'.state','.venv','.git','__pycache__'}.intersection(Path(name).parts))
                        self.assertFalse(name.endswith(('profiles.local.json','auth.json','.sqlite3','.log')))

    def test_build_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            first=build_skill.build(Path(temp)/'first').read_bytes()
            second=build_skill.build(Path(temp)/'second').read_bytes()
            self.assertEqual(first,second)

    def test_profile_settings_are_only_included_when_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            profiles=Path(temp)/'local.json'
            profiles.write_text(json.dumps({'A':'first@example.test'}))
            archive=build_skill.build(Path(temp)/'dist',profiles)
            with zipfile.ZipFile(archive) as package:
                settings=json.loads(package.read('setup-codex-scheduler/assets/settings.json'))
                self.assertEqual(settings['profiles'],{'A':'first@example.test'})

    def test_install_refuses_to_overwrite_existing_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'skills'/'setup-codex-scheduler'
            install_skill.install(target)
            content=(target/'SKILL.md').read_bytes()
            with self.assertRaises(FileExistsError): install_skill.install(target)
            self.assertEqual((target/'SKILL.md').read_bytes(),content)

    def test_setup_accepts_empty_profile_defaults(self):
        spec=importlib.util.spec_from_file_location('scheduler_setup',ROOT/'skills/setup-codex-scheduler/scripts/setup.py')
        setup=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(setup)
        self.assertEqual(setup.profile_map(None,{'profiles':{}}),{})


if __name__=='__main__': unittest.main()
