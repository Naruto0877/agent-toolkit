import unittest
from cli_provider_core import ProviderCapabilities,ProviderRegistry,ProviderSession,UnsupportedCapability


class CustomSession:
    session='custom-fixture'
    def __init__(self): self.closed=False
    def call(self, operation, params=None):
        if operation!='example/echo': raise ValueError('Unknown custom operation')
        return {'echo':params}
    def drain(self): return []
    def respond(self, request_id, result): pass
    def close(self): self.closed=True


class CustomBackend:
    provider_id='custom-example'
    capabilities=ProviderCapabilities(profile_isolation=True)
    def __init__(self, root): self.root=root
    def connect(self, profile): return CustomSession()


class RegistryTests(unittest.TestCase):
    def test_custom_provider_registration_and_configuration(self):
        registry=ProviderRegistry()
        registry.register('custom-example',CustomBackend,CustomBackend.capabilities,kind='custom')
        provider=registry.create('custom-example',root='/isolated')
        self.assertEqual(provider.root,'/isolated')
        session=provider.connect({'home':'/isolated'})
        self.assertIsInstance(session,ProviderSession)
        self.assertEqual(session.call('example/echo',{'value':7}),{'echo':{'value':7}})
        session.close()
        self.assertTrue(session.closed)
        self.assertEqual(registry.describe()[0]['kind'],'custom')
        self.assertFalse(registry.describe()[0]['capabilities']['usage_reporting'])

    def test_missing_provider_does_not_silently_use_codex(self):
        with self.assertRaisesRegex(ValueError,'not implemented'):
            ProviderRegistry().create('gemini')

    def test_missing_capability_is_explicit(self):
        with self.assertRaises(UnsupportedCapability):
            CustomBackend.capabilities.require('resume')
        with self.assertRaisesRegex(ValueError,'Unknown capability'):
            CustomBackend.capabilities.require('invented')

    def test_duplicate_registration_is_rejected(self):
        registry=ProviderRegistry()
        registry.register('custom-example',CustomBackend,CustomBackend.capabilities)
        with self.assertRaises(ValueError): registry.register('custom-example',CustomBackend,CustomBackend.capabilities)

    def test_mismatched_factory_is_rejected(self):
        registry=ProviderRegistry()
        registry.register('wrong-id',CustomBackend,CustomBackend.capabilities)
        with self.assertRaises(TypeError): registry.create('wrong-id',root='/tmp')
