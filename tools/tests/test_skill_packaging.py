import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest
import zipfile
from tools.skills import build as builder,install as installer
from tools.workspace import ROOT,project_order


class SkillPackagingTests(unittest.TestCase):
    def test_bundle_includes_app_and_shared_dependency_without_state(self):
        with tempfile.TemporaryDirectory() as temp:
            archive=builder.build(temp)
            with zipfile.ZipFile(archive) as package:
                settings=json.loads(package.read('setup-codex-scheduler/assets/settings.json'))
                self.assertEqual(settings['profiles'],{})
                self.assertEqual(settings['project_path'],'apps/codex-scheduler')
                payload=package.read('setup-codex-scheduler/assets/workspace-source.zip')
                self.assertEqual(hashlib.sha256(payload).hexdigest(),settings['source_sha256'])
                with zipfile.ZipFile(io.BytesIO(payload)) as source:
                    names=source.namelist()
                    self.assertIn('agent-toolkit/apps/codex-scheduler/codex_scheduler/core.py',names)
                    self.assertIn('agent-toolkit/packages/cli-provider-core/src/cli_provider_core/contracts.py',names)
                    for name in names:
                        self.assertFalse({'.state','.venv','.git','__pycache__'}.intersection(Path(name).parts))
                        self.assertFalse(name.endswith(('profiles.local.json','auth.json','.sqlite3','.log')))
                    reduced=json.loads(source.read('agent-toolkit/workspace.json'))
                    self.assertEqual(list(reduced['projects']),['cli-provider-core','codex-scheduler'])

    def test_build_is_reproducible(self):
        with tempfile.TemporaryDirectory() as temp:
            first=builder.build(Path(temp)/'first').read_bytes()
            second=builder.build(Path(temp)/'second').read_bytes()
            self.assertEqual(first,second)

    def test_profiles_are_only_included_explicitly(self):
        with tempfile.TemporaryDirectory() as temp:
            profiles=Path(temp)/'local.json';profiles.write_text(json.dumps({'A':'first@example.test'}))
            archive=builder.build(Path(temp)/'dist',profiles)
            with zipfile.ZipFile(archive) as package:
                settings=json.loads(package.read('setup-codex-scheduler/assets/settings.json'))
                self.assertEqual(settings['profiles'],{'A':'first@example.test'})

    def test_install_refuses_existing_skill(self):
        with tempfile.TemporaryDirectory() as temp:
            target=Path(temp)/'skills/setup-codex-scheduler'
            installer.install(target)
            original=(target/'SKILL.md').read_bytes()
            with self.assertRaises(FileExistsError): installer.install(target)
            self.assertEqual((target/'SKILL.md').read_bytes(),original)

    def test_new_independent_skill_needs_no_packaging_code_change(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)/'workspace';skill=root/'skills/custom/review-example';skill.mkdir(parents=True)
            (root/'workspace.json').write_text(json.dumps({'version':1,'name':'test','projects':{}}))
            catalog={'version':1,'skills':{'review-example':{'path':'custom/review-example','provider':'custom','projects':[],'files':['SKILL.md','references/*.md']}}}
            (root/'skills/catalog.json').write_text(json.dumps(catalog))
            (skill/'SKILL.md').write_text('---\nname: review-example\ndescription: Review custom configuration.\n---\nRead the supplied configuration.\n')
            (skill/'references').mkdir();(skill/'references/format.md').write_text('Custom format instructions.\n')
            archive=builder.build(Path(temp)/'out',skill_name='review-example',root=root)
            with zipfile.ZipFile(archive) as package:
                self.assertEqual(sorted(package.namelist()),['review-example/SKILL.md','review-example/references/format.md'])

    def test_catalog_path_cannot_escape_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'skills').mkdir()
            (root/'skills/catalog.json').write_text(json.dumps({'version':1,'skills':{'bad':{'path':'../../outside','files':['SKILL.md']}}}))
            with self.assertRaises(ValueError): builder.catalog(root)

    def test_setup_accepts_empty_defaults(self):
        path=ROOT/'skills/codex/setup-codex-scheduler/scripts/setup.py'
        spec=importlib.util.spec_from_file_location('scheduler_setup',path)
        setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
        self.assertEqual(setup.profile_map(None,{'profiles':{}}),{})

    def test_declared_dependencies_are_ordered_before_app(self):
        self.assertEqual(project_order(['codex-scheduler']),['cli-provider-core','codex-scheduler'])
