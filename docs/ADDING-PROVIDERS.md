# Add a provider or CLI application

Codex is the only implemented real provider. `fake` is a deterministic fixture, not another vendor. Gemini, Claude, and custom CLIs are future integrations, not fallback aliases for Codex.

## Verify the CLI first

Inspect the installed CLI version/help, supported machine-readable protocol, login/profile storage, usage reporting, session/turn persistence, approvals, interruption, and recovery behavior. Preserve its default login. If a feature is absent, declare the capability false and expose unknown status or a documented limitation; do not infer quota recovery from elapsed time or assume another vendor uses Codex method names.

## Implement the boundary

- A backend implements `ProviderBackend`: a stable `provider_id`, `ProviderCapabilities`, and `connect(profile)` returning a `ProviderSession`.
- A session implements connection lifetime, opaque `call` operations, event draining, approval responses, and close behavior. Operations and event payloads remain provider-specific; this is not a promise of a shared wire protocol.
- Declare only capabilities you actually implement. `ProviderCapabilities.require(...)` rejects unavailable operations. `ProviderRegistry` rejects unknown IDs, duplicate registrations, and factories whose identity/capabilities disagree with their registration.
- Keep provider-specific transport code, normalization, profile/environment isolation, error classification, and resumability together. The current Codex implementation is under `apps/codex-scheduler/codex_scheduler/providers/`.

For another CLI's standalone scheduler, create `apps/<cli>-scheduler/`, own its CLI/state schema, and depend on `cli-provider-core`. Declare its source/test/installer paths in `workspace.json`. If shared scheduling semantics are later proven across real implementations, extract those specific pieces into another package rather than coupling a new provider to Codex's raw RPC methods.

A custom CLI can use a native API, subprocess protocol, or another verified transport behind the session contract. Never construct shell strings from task prompts or expose tokens in scheduler events. Tests must validate isolation, supported status, submission ambiguity, approval/cancellation behavior, and real resume semantics where the provider claims support.

## Register intentionally

Register the factory/capabilities in the owning application's registry, then make that application's configuration select the new adapter explicitly. The current Codex scheduler deliberately accepts `real`/`fake` only; adding a registry class alone does not make its state machine understand a different CLI.

Use `python3 tools/workspace.py providers` to inspect currently implemented Codex/fixture registrations. Add an application's own provider inventory as it becomes operational. Tests prove a separately registered custom fixture can use its own operation vocabulary without silently selecting Codex.

## Add its setup skill

Create a domain folder and catalog entry as described in [adding skills](ADDING-SKILLS.md). A Codex-hosted setup skill may configure another CLI, but that does not mean the target CLI itself loads Codex skills. Keep the setup host, application, and execution provider distinct.
