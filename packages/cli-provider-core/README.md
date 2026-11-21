# cli-provider-core

Small contracts for provider backends, session lifetime, explicit capability declarations, and registration. The Codex scheduler consumes these contracts; they are not a universal Codex/Gemini/Claude wire protocol.

`ProviderSession.call` uses opaque adapter-specific operation names. Applications must implement the semantics of identity, usage, approval, persistence, and recovery for their chosen adapter. Never pass Codex methods to another CLI without an explicit, verified translation.

Declare only implemented capabilities. Unknown providers fail explicitly. A fixture provider is labeled separately from a live provider. See [adding providers](../../docs/ADDING-PROVIDERS.md).
