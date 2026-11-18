# Running BugBench

This page shows how to run the whole BugBench evaluation, what the configuration file controls, how the handoff rounds work, and how to grow the benchmark or add a configuration. It is for someone who wants to reproduce the results or measure a change. What BugBench is and what it found is on [BugBench](bugbench.md).

## What you need

* The project set up as in the README: Python 3.12, `uv`, Docker is not required for the benchmark itself.
* Ollama on the host with the reviewer model (`PRISM_REVIEWER_MODEL`) pulled or created. The configurations use the ONNX gate (`gate = "onnx"`), so the gate model must be in `PRISM_GATE_MODEL_DIR` (`models/gate` by default).
* The `sast` group (`make eval-bugbench` runs with `--group sast`). PMD for the Java cases needs `PRISM_PMD_HOME`. A SAST tool that is not installed is skipped, and the run summary records which tools ran in `sast_ran` (see [Troubleshooting](../operations/troubleshooting.md)).
* A working folder outside the repository for clones, worktrees, test environments and runs, set with `PRISM_BENCH_ROOT`.
* For planted and real cases, a test environment per repository under `<root>/envs/<repo>` (the `{python}` placeholder in `bugbench/repos.toml`). For jsoup, Maven: `{mvn}` is `PRISM_BENCH_MVN` (default `mvn`) and `{m2}` is `PRISM_BENCH_M2` (default `~/.m2/repository`). Reviewing existing cases does not run the tests; generating new planted cases does.

Repositories are cloned into `<root>/<repo>` the first time a case needs them.

## Run it

```bash
export PRISM_BENCH_ROOT=/path/outside/the/repo
make eval-bugbench
```

`make eval-bugbench` runs `uv run --group sast prism bench all bugbench/configs.toml`. For each configuration it reviews every case (from cached answers where they exist), then judges every posted comment, sweeps the confidence threshold where asked, and writes the report to `eval/reports/bugbench.md`.

To run only some configurations, name their run folders:

```bash
uv run --group sast prism bench all bugbench/configs.toml --only bb-prism
```

## The handoff rounds

Claude's work in the benchmark goes through the same handoff as a normal review ([ADR 0011](../adr/0011-claude-verifier-handoff.md)), batched across all cases.

1. The command reviews every case with the local model, static analysis and retrieval. Where Claude's answer is missing it stops with **exit code 2** and lists the round folders to answer, for example `<root>/runs/bb-prism/handoff/round_01`.
2. A round folder holds request files, the rubrics and a `README.md` that says nothing about whether any PR has a bug. A Claude session (or several, one per file) writes the answer file next to each request file, following the README.
3. Run the same command again. It hands each answer back to its case, reviews what is still waiting, and may open a further round, because the verifier can escalate a hunk to a review of its own. Repeat until the command stops returning exit code 2.
4. Once every run is answered, the same stop-and-answer cycle happens for the **judge**: the comments to judge go to `<root>/judge/round_NN`. Answer them and run again.
5. With everything answered, the command writes the report.

Whole-PR requests carry every hunk of a PR, so their files are cut by size as well as by count.

## Caching and seeding

Local model replies and Claude's answers are cached per case in `<root>/runs/<run>/<case id>/`. A pass after the first only reviews the cases still waiting, and a finished case keeps its `result.json`.

A configuration can be **seeded** from earlier runs: case folders the run does not have yet are copied from the first seed that has them, so their cached answers are reused (the earlier run's `result.json`, `results.jsonl`, `summary.json`, `sweep.json` and `handoff` folder are not copied). `seed_replies` takes only the local model's cached replies, which is right when the model and prompt are the same but the Claude requests differ.

!!! warning
    Seed only from a run that asked the same questions: the same context mode, model and routing. Request ids do not cover the retrieved context, so a wrong seed silently reuses answers to different questions.

## `bugbench/configs.toml`

The file has a few top-level keys, a `[settings]` table that applies to every run, and one `[[run]]` table per configuration. The fields are read by `prism/bench/suite.py`.

### Top level

| Key | Meaning |
|---|---|
| `cases` | Glob patterns of case files, relative to the repository root (`["bugbench/cases/*.toml"]`) |
| `exclude` | Case files left out of the glob (`tinydb-slice.toml`, the early slice, is excluded) |
| `report` | Where the Markdown report is written (`eval/reports/bugbench.md`) |
| `thresholds` | Confidence thresholds for the sweep (`[0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9]`) |

### `[settings]`

Any `PRISM_*` setting by its lower-case field name, applied to every run. The file sets `gate = "onnx"`. The runner always sets `review_store = "off"` and `tracing = "off"` for benchmark runs.

### `[[run]]`

| Field | Default | Meaning |
|---|---|---|
| `name` | required | The column name in the report |
| `run` | required | The run folder under `<root>/runs`. Each configuration needs its own; duplicates are refused |
| `kind` | `prism` | `prism`, or `pr-agent` for the baseline ([ADR 0022](../adr/0022-pr-agent-baseline.md)) |
| `mode` | `graph` | Context mode for the verifier |
| `model` | the reviewer setting | A different local reviewer model |
| `verifier` | `handoff` | The verifier backend |
| `settings` | `{}` | Settings for this run only, on top of `[settings]` |
| `seed` | `[]` | Runs to copy cached case folders from |
| `seed_replies` | `[]` | Runs to copy only the local model's replies from |
| `worktrees` | `worktrees` | Folder under the root for this run's checkouts; give runs that go on at the same time their own |
| `sweep` | `false` | Run the confidence threshold sweep for this run |
| `judge` | `true` | Judge this run's posted comments |

The two runs in the file today:

```toml
[settings]  # for every run
gate = "onnx"

[[run]]
name = "PRism"
run = "bb-prism"
seed = ["mid-01"]
sweep = true

[[run]]
name = "Whole PR to Claude"
run = "bb-whole-pr"
settings = { route_all = true, review_unit = "pr" }
seed = ["whole-pr-01"]
sweep = true
worktrees = "worktrees-whole-pr"
judge = false
```

## The sweep

A sweep re-scores a finished run at other confidence thresholds from its saved answers, with no new Claude work ([ADR 0026](../adr/0026-threshold-sweep.md)). In the suite it runs when a run has `sweep = true`, and is redone if the saved sweep was made with other thresholds or cases. To run one by hand:

```bash
uv run prism bench sweep bugbench/cases/*.toml --run bb-prism
uv run prism bench sweep-table "PRism=bb-prism" "Whole PR=bb-whole-pr" --report sweep.md
```

Pass the same case files the run used; the suite leaves out `bugbench/cases/tinydb-slice.toml`, so list the files rather than a `*.toml` glob if you want the same 198 cases. The sweep must run with the run's own settings (pass `--mode`, `--model` and set the same `PRISM_*` variables). At a threshold where PRism would ask Claude something the run never asked, because a local comment kept below the threshold sends its hunk back to Claude, that PR is reported as unscored and the counts cover the scored PRs only.

## The judge

`prism bench judge` shows every comment a run posted to a judge (Claude, through a handoff) and records whether it is a true, useful comment and whether it names the defect. Comments are keyed by what they say and where, so the same comment posted by several runs is judged once.

```bash
uv run prism bench judge bugbench/cases/*.toml --runs bb-prism
```

It exits with code 2 while verdicts are missing and tells you which `judge/round_NN` folder to answer. `make eval-bugbench` does this for you for every run with `judge = true`.

## Growing the benchmark

### More planted cases

```bash
uv run prism bench generate tinydb --out bugbench/cases/tinydb-3.toml \
  --extend bugbench/cases/tinydb.toml --extend bugbench/cases/tinydb-2.toml
```

`generate` mutates recent commits of the repository, keeps a mutation only if the repository's tests pass at the commit and a named test fails with the mutation, and writes a case file. `--extend` names the repository's existing case files, so their commits are skipped and new ids continue after theirs (`-i` for injected, `-c` for clean). Other options: `--injected` (default 15), `--clean` (default 4), `--window` (recent commits to draw from, default 150) and `--seed` (default 7). It needs the repository's test environment, because it runs the tests.

### More real bugs

```bash
uv run prism bench real tinydb --out candidates.jsonl --extend bugbench/cases/tinydb-real.toml
```

This lists SZZ candidates, one JSON object per line, skipping commits that already have a case. The candidates are not cases. Read each one and keep it only if the fix changes behaviour and the blamed lines are the faulty code the commit added. Then write the case by hand in a case file with `group = "real"`, the commit, the `path`, the `lines` of the defect at that commit, the `fix` commit and a `note`.

### A new repository

Add a table to `bugbench/repos.toml` with the URL, licence, language, the `source` paths whose added lines may be mutated, the `exclude` paths, the `test` command and a `timeout`. Create its test environment under `<root>/envs/<name>`, then run `prism bench generate`.

## Adding a configuration

Add another `[[run]]` to `bugbench/configs.toml` with its own run folder:

```toml
[[run]]
name = "PRism, gate off"
run = "bb-gate-off"
settings = { gate = "pass-all" }
seed = ["bb-prism"]
```

Run `make eval-bugbench` (or `prism bench all ... --only bb-gate-off`), answer the handoff rounds, and the report gets a new column. Seed it from a run that asked the same questions, as above; with the gate off, only the hunks the gate used to skip are reviewed again.

The PR-Agent baseline is a run with `kind = "pr-agent"`. It needs PR-Agent installed in its own virtual environment; the default location is `<root>/envs/pr-agent` (`Scripts/pr-agent.exe` on Windows, `bin/pr-agent` elsewhere), or pass `--pr-agent`.

## Other commands

| Command | Use |
|---|---|
| `prism bench run CASES --run NAME` | One configuration without the config file. Options include `--mode`, `--verifier`, `--model`, `--no-sast`, `--only` and `--report` |
| `prism bench baseline CASES` | PR-Agent on the cases, with Claude answering through a handoff |
| `prism bench compare PRISM_RUN OTHER_RUN --report FILE` | A PRism run against a baseline run on the same cases |
| `prism bench table NAME=RUN ... --report FILE` | Finished runs side by side |
| `prism bench experiments CASES --runs NAME=RUN,...` | Publish finished runs to Langfuse as experiments over a `bugbench` dataset (needs the `observability` group and the `langfuse` Compose profile) |

Every command takes `--root` (or `PRISM_BENCH_ROOT`). The full option lists are in the [CLI reference](../reference/cli.md).
