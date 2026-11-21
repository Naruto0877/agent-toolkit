"""Codex adapter registration. Other real CLIs must implement their own adapter."""
from cli_provider_core import ProviderRegistry
from .codex import RealBackend
from .fake import FakeBackend

registry = ProviderRegistry()
registry.register('codex',RealBackend,RealBackend.capabilities)
registry.register('fake',FakeBackend,FakeBackend.capabilities,kind='fixture')


def create_backend(name, *, codex='codex', home=None, fixture=None, clock=None):
    if name == 'real':
        return registry.create('codex',codex=codex)
    if name == 'fake':
        return registry.create('fake',home=home,fixture=fixture,clock=clock)
    raise ValueError('Unsupported scheduler backend: '+name)
