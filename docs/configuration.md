# Configuration

Use `get_config` to inspect settings and `set_config` to change them. Installer flags provide initial defaults.

| Setting | Default | Notes |
|---|---:|---|
| Accelerator | `cpu` | CPU, T4, L4, G4, H100, A100, TPU v5e-1/v6e-1 |
| Language | `python` | Native Python, R, or Julia kernel |
| Runtime version | `latest` | Recommended; older `YYYY.MM` labels depend on Google |
| High-RAM | off | Availability depends on accelerator/account |
| Maximum lifetime | `0` | Plugin timer disabled; per-session override supported |
| Notifications | on | Popup backend varies by OS; history always recorded |
| Local file roots | none | Upload/download/notebook access is denied until allowlisted |
| SSH | off | Requires explicit sensitive-change confirmation |
| Recovery | off | Opt in per session/job because it may reallocate compute |

Creation requires `acknowledge_cost=true`. This prevents accidental allocations; it is not a price estimate. Use `stop_session_on_finish` for a final job when the runtime is no longer needed. Keep the session alive otherwise so Codex can reuse it.

Allowed roots should be narrow project/output folders rather than a home directory. Configuration and local state must never be committed.
