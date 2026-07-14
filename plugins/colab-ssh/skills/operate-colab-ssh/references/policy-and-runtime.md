# Colab policy and runtime facts

## Preferred official CLI

Google's `google-colab-cli` is the supported agent/terminal path. It provisions named CPU, T4, L4, G4, H100, A100, v5e1, and v6e1 sessions; executes local scripts and notebooks; transfers artifacts; exports logs; exposes a console; and runs a built-in keep-alive daemon. On Windows, run it through WSL2 because the CLI currently supports Linux and macOS.

The daemon is an official session-lifecycle feature, not a notebook activity spoof. It still cannot override maximum runtime, quota, compute-unit exhaustion, or service reclamation.

- Consumer Colab allocates resources dynamically. A GPU runtime request does not guarantee a particular model, including A100.
- Availability, maximum lifetime, idle timeout, RAM, and accelerator type vary over time and by paid balance/plan.
- Colab may terminate managed runtimes. SSH or remote-control workflows can be restricted, especially on free managed runtimes without a positive compute-unit balance.
- The plugin reports the actual GPU from `nvidia-smi` after connection.
- Do not add fake activity, browser clickers, or no-op loops intended to bypass idle or quota enforcement.
- For guaranteed machine type and lifecycle, use a user-controlled Google Cloud VM or a Colab local runtime instead of a consumer-managed Colab VM.

The SSH fallback uses ngrok TCP because it provides a simple outbound tunnel from the Colab VM. ngrok TCP endpoints require an ngrok account and may require a valid payment method even on a free ngrok account. The token belongs in Colab Secrets as `NGROK_AUTHTOKEN`.
