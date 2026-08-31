# Automation V1 Phase B Container

This image is the outer security boundary for one future unattended Codex
development run. It does not choose work, merge branches, touch the live runtime,
or submit Kalshi orders.

## Reproducible image

Build from the repository root with:

```bash
bash automation/container/build.sh
```

The image tag is `kalshi-stats-automation:phase-b-v1`. The Dockerfile pins:

- Python base: `python:3.12.11-slim-bookworm` at its recorded SHA-256 digest
- Node source image: `node:22.18.0-bookworm-slim` at its recorded SHA-256 digest
- Codex CLI: `@openai/codex@0.151.0`

It installs the repository's declared Python package/dependencies, pinned
`pytest==9.1.1`, and the small set of development tools required by this
repository: Bash, Git, curl, wget, jq,
SQLite, compiler/build tools, CA certificates, and bubblewrap. Node/npm and
Python/pip/venv are available. System-package changes belong in the Dockerfile;
task-specific Python dependencies can be installed in a container-local venv
without host sudo or host Python changes.

`.dockerignore` uses a deny-by-default build context. Only `pyproject.toml`,
`README.md`, `src/`, and `automation/container/` enter the build context. Git
metadata, `.env`, databases, reports, run state, and the rest of the worktree are
not sent to the image build and cannot be baked into the image accidentally.

## Runtime boundary

The generated `docker run` command uses the normal Docker bridge network, which
allows outbound internet without host networking. It also uses:

- `--rm` and `--init`;
- the invoking host UID/GID;
- all Linux capabilities dropped;
- `no-new-privileges`;
- PID, memory, and CPU limits;
- a disposable `/tmp` tmpfs;
- no privileged mode and no Docker socket.

Only two host bind mounts are permitted:

1. the exact validated task worktree at `/workspace`, writable;
2. one dedicated automation-only Codex home at `/codex-home`, writable.

The run directory is inside the task worktree at
`automation/runs/<run-id>/`; it does not require a broader parent mount.

Explicitly forbidden mounts include host `/`, host `/home`, the primary
`/home/aceortiz/stats` runtime, `~/.ssh`, `~/.aws`, `~/.config`, the user's
ordinary `~/.codex`, any `.env`, live Kalshi credentials, the Git common
directory, and `/var/run/docker.sock`. The container therefore cannot create
sibling containers. Root inside the container is not granted; normal dependency
installation uses venv/npm user locations, while reproducible OS dependencies
are added by rebuilding the image.

The worktree is a linked Git worktree whose `.git` file normally points into the
primary repository. That target is deliberately not mounted. Immediately before
an actual run, the host runner creates an ignored `.repository.bundle` inside the
run directory. The entrypoint reconstructs the task branch into disposable
`/tmp/automation-task-git` and exports `GIT_DIR`/`GIT_WORK_TREE`. Codex sees the
real reachable branch history and current worktree diff, but it cannot mutate the
host's shared refs, `main`, or `automation-integration`. Container-local commits
are disposable; source-file edits persist in the task worktree for later host
validation/review.

## Codex 0.151.0 noninteractive invocation

Phase B inspected the installed CLI directly. A new run is generated as:

```text
codex
  --ask-for-approval never
  --sandbox danger-full-access
  exec
  --json
  --color never
  --ignore-user-config
  --cd /workspace
  --output-last-message /workspace/automation/runs/<run-id>/final.md
  [--model <model>]
  [--config model_reasoning_effort="<effort>"]
  -
```

The bootstrap/task prompt is supplied on stdin. `--json` writes JSONL events to
stdout, which the host appends to `events.jsonl`; stderr goes to `errors.log`.
The last response goes separately to `final.md`. `--ask-for-approval never`
guarantees no human approval dialog can freeze the run. `danger-full-access`
removes the inner Codex filesystem sandbox only inside the externally constrained
container. It must never be used by this runner directly on the host.

The enclosing Docker invocation must include `--interactive` even though it has
no TTY. That flag keeps stdin attached long enough to deliver the frozen prompt
to Codex. The first Phase C1 canary exposed this requirement when the original
Phase B command omitted it and Codex exited with `No prompt provided via stdin.`
The adapter now includes the flag and has regression coverage; it still exposes
no interactive approval channel because the Codex policy remains `never`.

`--ignore-user-config` prevents an ordinary user config from changing the run;
authentication still comes from `CODEX_HOME`. Optional reasoning effort uses the
0.151.0 accepted strict-config key `model_reasoning_effort`. The CLI exposes
session IDs as UUIDs/thread names and supports:

```text
codex exec resume <SESSION_ID> [PROMPT]
codex exec resume --last [PROMPT]
```

Phase B preserves any thread/session identifier found in JSONL into
`RunRecord.session_thread_id`, but it does not implement a retry or resume
supervisor.

The inspected 0.151.0 capability record is:

| Need | Supported syntax / behavior |
| --- | --- |
| Noninteractive run | `codex [GLOBAL OPTIONS] exec [OPTIONS] [PROMPT]`; use `-` to read stdin |
| Event stream | `--json` prints events to stdout as JSONL |
| Separate final | `-o, --output-last-message <FILE>` |
| Structured final | `--output-schema <FILE>` accepts a JSON Schema |
| Approval policy | global `-a, --ask-for-approval on-request|never` before `exec` |
| Sandbox | global `-s, --sandbox read-only|workspace-write|danger-full-access` before `exec` |
| Working root | `-C, --cd <DIR>` |
| Model | `-m, --model <MODEL>` |
| Reasoning | `-c model_reasoning_effort="<effort>"`; accepted with `--strict-config` |
| New-thread label | `--thread-source <SOURCE>` |
| Session persistence | default is persistent; `--ephemeral` disables persistence |
| Resume | `codex exec resume [SESSION_ID] [PROMPT]` or `--last` |
| Fork | `codex exec fork <SESSION_ID> [PROMPT]` |
| User config | `--ignore-user-config` skips config while auth still uses `CODEX_HOME` |

The resume help defines `SESSION_ID` as a conversation/session UUID or thread
name, with UUID precedence. `resume --all` disables cwd filtering. Phase B starts
new persistent runs only; it records identifiers for Phase C but does not select,
resume, fork, or retry threads.

The global placement is significant in 0.151.0: despite the inherited options
being displayed by `codex exec --help`, the parser rejects
`codex exec --ask-for-approval never`. The verified form is
`codex --ask-for-approval never --sandbox danger-full-access exec ...`.

## Dedicated authentication bootstrap

Never copy or mount the user's entire `~/.codex`, and never paste a token into a
task prompt, source file, shell history, or repository environment file. Create a
dedicated directory outside Git and outside every task worktree, then perform a
one-time device login:

```bash
bash automation/container/bootstrap-auth.sh \
  /home/aceortiz/.local/share/kalshi-stats-automation/codex-home
```

The script creates that directory with mode `0700`, mounts only it at
`/codex-home`, and runs `codex login --device-auth` interactively in the same
restricted image. The browser/device confirmation occurs outside source control;
Codex writes its file-backed authentication state into the dedicated directory.
Future unattended runs mount only that directory. The runner never reads auth
files, injects credential values, forwards API-key variables, or logs environment
values. Rotate or remove this dedicated directory independently of the user's
normal Codex installation.

### Authentication credential boundary and mount mode

The dedicated automation Codex authentication material is a credential required
by the Codex client. It is the **only intentionally mounted credential state**.
All other host credential locations and credential-like environment variables
remain inaccessible. This distinction is unavoidable: an authenticated Codex
call cannot be made without providing the client its own narrowly scoped login
state, but that does not authorize mounting unrelated SSH, cloud, user Codex, or
Kalshi credentials.

Phase C1 audited a read-only `/codex-home` mount with the supported non-secret
`codex login status` command. Authentication status succeeded, but Codex 0.151.0
also emitted a read-only-filesystem warning from an attempted client-state write.
The installed CLI's persistent `codex exec` behavior stores session state, and a
long-lived ChatGPT login may need to persist refreshed authentication state.
Therefore `/codex-home` remains writable for unattended authenticated runs. It is
not broadened: the mount source remains the dedicated mode-0700 automation
directory and no other host credential path is mounted.

Runner command metadata substitutes `<dedicated-automation-auth>` for the host
source path. Streamed stdout/stderr and the final response pass through
deterministic redaction for that host path, private-key blocks, bearer values,
OpenAI-shaped secret values, and credential-like assignments before durable
diagnostics are retained. Environment values are never included in command
metadata.

Docker client configurations can inject proxy variables implicitly. The runner
therefore emits empty values for all standard upper/lowercase proxy variables
unless the caller explicitly requests that allowlisted name; requested values are
inherited by name and remain absent from diagnostics.

Phase B itself did not perform an authenticated unattended Codex call. Phase C1
adds exactly one frozen authenticated canary through the Phase B command and
isolated-container boundary; it does not broaden the runner's authority.

## Smoke test

After building the image, run:

```bash
bash automation/container/run-smoke-test.sh
```

The script creates a disposable `/tmp` host directory and mounts only that
directory. Inside the container it verifies outbound HTTPS to PyPI, creates a
venv, installs the small pinned pure-Python package `idna==3.10`, imports it,
runs Git and commits a disposable file, writes/reads SQLite, and proves the bind
mount is writable. It does not mount any repository worktree, auth state, live
database, home directory, or Docker socket.
