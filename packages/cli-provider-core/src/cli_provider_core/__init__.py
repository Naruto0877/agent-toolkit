"""Small shared boundary for CLI providers; wire formats stay provider-specific."""
from .contracts import ProviderBackend, ProviderCapabilities, ProviderSession, UnsupportedCapability
from .registry import ProviderRegistry

__all__ = ['ProviderBackend','ProviderCapabilities','ProviderSession','ProviderRegistry','UnsupportedCapability']
