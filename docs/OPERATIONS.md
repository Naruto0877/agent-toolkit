# Operations and recovery

## Run work

From the checkout, set `CODEX_SCHEDULER_HOME` to its private `.state` directory and add `.venv/bin` to PATH. Set `PROJECT` to the actual project directory. In terminal 1 run `codex-scheduler worker`; in terminal 2:

```sh
codex-scheduler run --auto --project "$PROJECT" --size small 'Review the tests'
codex-scheduler run --account B --project "$PROJECT" 'Implement the requested change'
codex-scheduler run --auto --project "$PROJECT" --size large --headroom 25 'Implement the next milestone'
codex-scheduler queue
codex-scheduler inspect JOB_ID
codex-scheduler cancel JOB_ID
```

Each `run` call submits a distinct job. Replace example descriptions before using them. `--sandbox read-only` is available; workspace-write with on-request approvals is the default. Choose a concrete model with `--model MODEL` from `status`, or use the first eligible catalog default.

Small/large hints estimate 5%/15% allowance needed per window to the next useful checkpoint. `--headroom` overrides this estimate. Percentages do not predict token costs or guarantee completion. Explicit selection never switches accounts; auto selection excludes unknown usage and queues work when no account has sufficient known headroom.

A confirmed usage-limit turn is checkpointed by its saved thread and completed tool history. The durable wake time is the latest reset among known blocking windows, or a 60-second retry when estimates are missing/past. Due usage checks occur while other work is running. The worker rereads capacity before resuming; elapsed time alone never clears a limit.

## Approvals

```sh
codex-scheduler approvals
codex-scheduler approve APPROVAL_ID --decision accept
# Other choices: decline / cancel.
```

Other interactive protocol requests may use `--response-file /absolute/response.json` with the installed request schema. Use the same state directory and keep the worker running while answering. Approvals are never auto-accepted. A lost connection's request IDs or unsent responses are preserved for inspection and never replayed into a new connection.

## Restart and uncertain outcomes

Ctrl-C stops the foreground worker; jobs remain durable. Restart with `codex-scheduler worker`. The worker reconciles unfinished turn/submission history before sending another message. Unknown/in-progress/unavailable outcomes become `needs_review`, blocking further work in that worktree rather than risking automatic replay.

After inspecting the saved thread and stopping any old executing process, stop the worker and make an explicit decision:

```sh
codex-scheduler resolve JOB_ID --outcome completed
# Or: --outcome cancelled / --outcome retry
codex-scheduler worker
```

`retry` authorizes a continuation. Cancellation remains binding. These checks avoid automatic duplicate submission where the outcome is uncertain; exactly-once external tool side effects cannot be guaranteed.

## macOS: after terminal exit

Stop the foreground worker. With `SCHEDULER_ROOT` and `CODEX_SCHEDULER_HOME` set as in [setup](SETUP.md):

```sh
cd "$SCHEDULER_ROOT"
./.venv/bin/python scripts/launchd_plist.py --home "$CODEX_SCHEDULER_HOME" --codex "$(command -v codex)" --out ./local.codex-scheduler.plist
mkdir -p "$HOME/Library/LaunchAgents"
cp ./local.codex-scheduler.plist "$HOME/Library/LaunchAgents/local.codex-scheduler.plist"
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.codex-scheduler.plist"
```

The plist uses this machine's resolved paths and retains the shell PATH for tools. Inspect it with `launchctl print "gui/$(id -u)/local.codex-scheduler"`; logs are `worker.log` and `worker-error.log` under the state directory. Stop it before login changes/manual recovery:

```sh
launchctl bootout "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.codex-scheduler.plist"
```

Rerun `bootstrap` to restart it. Do not run a foreground worker while the LaunchAgent owns the worker lock.

## Linux: simple detached worker

After stopping any foreground worker:

```sh
nohup "$SCHEDULER_ROOT/.venv/bin/codex-scheduler" --home "$CODEX_SCHEDULER_HOME" --codex "$(command -v codex)" worker > "$CODEX_SCHEDULER_HOME/worker.log" 2>&1 < /dev/null &
echo "Worker PID: $!"
```

This survives terminal exit but does not arrange restart after reboot/crash. A user service can be configured separately. Keep one real state directory per installation. Account usage is shared across machines, but local queues and kernel locks do not coordinate different machines.

An empty queue sends zero model messages. Work cannot execute while the machine is asleep/off; overdue wakes are checked when its worker runs again.
