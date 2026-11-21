from .codex_protocol import RPC
from cli_provider_core import ProviderCapabilities


class RealBackend:
    provider_id = "codex"
    capabilities = ProviderCapabilities(profile_isolation=True, usage_reporting=True, resume=True, approvals=True, cancellation=True, reconciliation=True)

    def __init__(self, codex):
        self.codex = codex

    def connect(self, account):
        return RPC(self.codex, account['home'])
