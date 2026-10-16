# Codex backend

These files connect ARES to the [Codex CLI](https://github.com/openai/codex).
The setup steps are in [docs/providers.md](../docs/providers.md).

| File | Purpose |
| --- | --- |
| `make_codex_env.py` | Creates a clean Codex template home: every tool, skill, and feature is disabled, and the model catalog has no tool instructions. No login data is copied. |
| `codex_call.py` | Runs one `codex exec` call and stops the run when the event stream shows a tool call or an unknown event. |
| `probe_codex.py` | Captures one request through a local server, with no model call, and checks that it declares no tools and no extra context. `box_codex.sh` runs this check before every run. |
| `capture_server.py` | The local HTTP server that `probe_codex.py` uses; it records the request and answers with an error. |

Run `make_codex_env.py` again after every Codex CLI update, because a new CLI
version can add tools or change the request format.
