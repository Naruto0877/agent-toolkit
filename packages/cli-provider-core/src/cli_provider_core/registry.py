from dataclasses import asdict, dataclass
import re
from typing import Callable
from .contracts import ProviderBackend,ProviderCapabilities


@dataclass(frozen=True)
class Registration:
    factory: Callable[...,ProviderBackend]
    capabilities: ProviderCapabilities
    kind: str


class ProviderRegistry:
    def __init__(self):
        self._providers: dict[str,Registration] = {}

    def register(self, provider_id, factory, capabilities, *, kind='live'):
        if not re.fullmatch(r'[a-z][a-z0-9-]*',provider_id):
            raise ValueError('Invalid provider ID')
        if provider_id in self._providers:
            raise ValueError('Provider already registered: '+provider_id)
        if not callable(factory) or not isinstance(capabilities,ProviderCapabilities):
            raise TypeError('A factory and ProviderCapabilities are required')
        if kind not in ('live','fixture','custom'):
            raise ValueError('Invalid provider kind')
        self._providers[provider_id] = Registration(factory,capabilities,kind)

    def create(self, provider_id, **config):
        if provider_id not in self._providers:
            raise ValueError('Provider is not implemented: '+provider_id)
        entry = self._providers[provider_id]
        provider = entry.factory(**config)
        if not isinstance(provider,ProviderBackend) or provider.provider_id != provider_id or provider.capabilities != entry.capabilities:
            raise TypeError('Provider factory does not match its registered contract')
        return provider

    def describe(self):
        return [{'id':name,'kind':entry.kind,'capabilities':asdict(entry.capabilities)} for name,entry in sorted(self._providers.items())]
