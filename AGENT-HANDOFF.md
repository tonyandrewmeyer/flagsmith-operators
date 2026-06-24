# Agent handoff: OMP → Claude Code (Opus 4.7)

A note for whoever compares agents on this repo later. The interesting bit
isn't the four-charm scaffold — that was OMP — it's the gap between
"unit tests pass" and "the bundle actually runs".

## What OMP did

Built the monorepo and four k8s charms (`flagsmith-api`,
`flagsmith-task-processor`, `flagsmith-frontend`, `flagsmith-edge-proxy`),
a bundle, observability wiring, and unit tests. Roughly $200 of OpenRouter
spend on `anthropic/claude-opus-4.8`.

Commits up to and including
[`6afb453`](https://github.com/tonyandrewmeyer/flagsmith-operators/commit/6afb453039937627364bf9c6e4ffc8c22bf073d9)
(`final work by omp, roughly $200`).

## Where OMP stopped

Mid-debug of a live deploy in the `opus-flagsmith` multipass VM, with
`flagsmith-frontend/0` stuck in `hook failed: "flagsmith-api-relation-changed"`.
The session's last assistant text was reasoning about whether
`relation.app` could be `None` in a relation-changed hook — the wrong tree.
The actual error in the unit's charm log was

```
ops.pebble.ChangeError: ... cannot start service:
fork/exec /usr/bin/node: no such file or directory
```

The OpenRouter key hit its $200 total limit (HTTP 403) on the very next
turn, so the agent never got to read those logs.

Status at handoff:

| Unit | Status | Real cause |
| --- | --- | --- |
| `flagsmith-api/0` | active | — |
| `flagsmith-frontend/0` | error | Pebble `working-dir: /app` — image's WORKDIR is `/srv/bt` |
| `flagsmith-task-processor/0` | crash-loop | Pebble HTTP probes hit `localhost`, Django returned 400 `DisallowedHost`, workload exited 0 each iteration |
| `flagsmith-edge-proxy/0` | blocked / then error | `ALLOW_ORIGINS=*` raised pydantic-settings `SettingsError` — upstream parses it as a JSON list |

All three were live-deploy bugs the unit tests didn't (and structurally
couldn't) catch — wrong workdir for the upstream image, missing
`DJANGO_ALLOWED_HOSTS` for the probe host, and a workload env value that
wasn't valid JSON.

## What I did

Single commit:
[`9ca3e0a`](https://github.com/tonyandrewmeyer/flagsmith-operators/commit/9ca3e0a)
— `fix(charms): wire up workloads correctly on live deploy`.

- `flagsmith-frontend/src/frontend.py`: `WORKING_DIR = "/srv/bt"` (was
  hard-coded `/app`).
- `flagsmith-task-processor/src/task_processor.py`: added
  `allowed_hosts: str = "*"` and emit `DJANGO_ALLOWED_HOSTS` so Pebble's
  `localhost:8000/health/...` probes pass.
- `flagsmith-edge-proxy/src/edge_proxy.py` and
  `charms/flagsmith-edge-proxy/charmcraft.yaml`: default
  `allow_origins='["*"]'` and document the JSON-list shape.
- Three test assertions added so a future regression on any of these
  fails fast.
- `.gitignore`: stop tracking the `.charm_tracing_buffer.raw` files
  charm-tracing writes at runtime (two were already committed by OMP).

Repacked the three charms and `juju refresh`'d them in the same VM.
After the refresh all four units are `active/idle`. Then pushed the
repo to its remote (none was configured before).

## Cost-of-finishing comparison

Useful to write down because the headline numbers ignore where each agent
spent budget.

- **OMP** put roughly $200 into building everything end-to-end *plus*
  several hours of live debugging that never identified the right root
  causes. The three bugs above are the kind that only surface when you
  actually deploy and read workload logs — which OMP was doing, it just
  never got past the symptom (relation-changed failing) to the cause
  (Pebble `ChangeError` from the workload container) before the key cap
  hit.
- **This finish-off** was ~one Claude Code turn with foreground tool
  use: read OMP's last messages, pulled the unit's charm logs once,
  spotted the three distinct error lines, fixed each in the workload
  helper module, repacked, refreshed, watched units go `active`.

The takeaway isn't "Claude is faster than OMP" — OMP did the bulk of
the work and was on the right track. It's that the last 5% of a charm
project lives in workload-container logs that look nothing like the
charm-side error message, and an agent that exhausts its budget
re-reading the charm code without sampling the workload logs will
plateau there.

## Repro pointers

If you want to reproduce or extend this work:

- The live deploy is `juju model flagsmith` on the `opus-flagsmith`
  multipass VM (`multipass shell opus-flagsmith`).
- OMP's full session transcript is in
  `omp-session-2026-06-23T23-10-40-553Z_019ef6c0-07a9-7000-8847-b4ff54d9d540.html`.
- The decisive log lines all came from
  `kubectl -n flagsmith logs <unit-pod> -c charm` (charm-side
  `ChangeError`) and `-c <workload-container>` (the real cause).
