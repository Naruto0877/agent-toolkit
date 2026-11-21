from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable


class UnsupportedCapability(ValueError):
    pass


@dataclass(frozen=True)
class ProviderCapabilities:
    profile_isolation: bool = False
    usage_reporting: bool = False
    resume: bool = False
    approvals: bool = False
    cancellation: bool = False
    reconciliation: bool = False

    def require(self, *names: str) -> None:
        for name in names:
            if name not in self.__dataclass_fields__:
                raise ValueError('Unknown capability: '+name)
            if not getattr(self,name):
                raise UnsupportedCapability('Provider does not support '+name)


@runtime_checkable
class ProviderSession(Protocol):
    """Opaque operations/events, NOT a shared Codex wire format.

    Each application must understand its adapter's operation names, responses,
    approval semantics, and reconciliation behavior. This interface lets it
    substitute fixtures and own connection lifetime without sharing credentials.
    """
    session: str

    def call(self, operation: str, params: Mapping[str,Any] | None = None) -> dict: ...
    def drain(self) -> list[dict]: ...
    def respond(self, request_id: Any, result: Mapping[str,Any]) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class ProviderBackend(Protocol):
    provider_id: str
    capabilities: ProviderCapabilities

    def connect(self, profile: Mapping[str,Any]) -> ProviderSession: ...
