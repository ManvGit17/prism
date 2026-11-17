# Learning log

## Phase 0: Scaffolding (2026-11-11)

**What was built.** An empty but working project. `uv` manages Python 3.12 and the dependencies.
Docker Compose runs three services: Postgres with the pgvector extension (for embeddings and
ordinary tables), Neo4j (for the code graph) and Redis (for the job queue later). Ollama runs on
Windows itself, outside Docker, and serves the base reviewer model `qwen2.5-coder:3b`. A smoke
test proves each of the four answers. The Pydantic schemas in `prism/schemas.py` define what a
review looks like before any code produces one.

**The idea worth remembering.** There are two comment schemas. `RawComment` is what the model
writes: a line number and a comment. `ReviewComment` is what gets posted, and only the verifier
can make one, because it adds the severity and category. The model is never asked for fields it
was not trained to produce.

**Quiz**

1. Why does the reviewer model emit only `{line, comment}` and not a severity?
2. `RawComment` rejects unknown fields. What failure does that catch?
3. Why does Ollama run on the host instead of in a Compose container on this machine?
4. `make test` and `make unit` differ by one pytest marker. Which tests does CI skip, and why?
5. Neo4j's heap is fixed at 512 MB. What would you expect to see if that were too small for a
   large repository, and which setting would you change first?

## Phase 1: Code indexer and knowledge graph (2026-11-11)

**What was built.** PRism can now read a Python or Java repository and turn it into a graph.
tree-sitter parses each file into an AST, a tree of the code's structure. From that tree the
indexer pulls out files, classes and functions as nodes, and connects them with edges: `DEFINES`,
`IMPORTS`, `CALLS`, `TESTS`, `INHERITS` and `OVERRIDES`. The graph is stored in Neo4j, and each
function is also embedded into pgvector for similarity search.

**Why tree-sitter and not regex.** A regex sees text, so it cannot tell a call from a mention in
a comment or a string, and it breaks on nesting. A parser knows which function a line sits inside
and what is actually being called.

**Call resolution is a guess with a score.** Working out which function a call refers to is
exact only when you can see the definition: same file, same class, or an import. Otherwise the
indexer falls back to matching by name, which is often wrong for names like `get` or `run`. So
every `CALLS` edge carries a confidence, and edges below 0.5 are left out of queries by default.
On the two libraries checked, 85% of Java call edges and 67% of Python ones were above the line.
Java scores higher because declared types say what a receiver is.

**Base graph plus overlay.** Neo4j holds only the default branch. A pull request never writes to
it. Its changed files are parsed into an in-memory overlay, and queries answer from the base
graph with those files swapped out. Two open PRs therefore cannot disturb each other.

**The ledger.** Neo4j and Postgres cannot commit together, so each indexing run is a row in a
Postgres table: running, then complete. A review waits while a run for its repo is in progress.

**Why a graph and not only vectors.** A vector search finds code that looks similar. It cannot
answer "who calls this function" or "which tests cover it", because those are relationships, not
similarities. A change to a function is risky because of its callers, and those can look nothing
like it.

**Quiz**

1. A call `items.add(x)` appears in a Java class that also defines its own `add` method. Why
   does the indexer not link them, and what would go wrong if it did?
2. Two PRs change the same file in different ways. Explain how each gets correct "callers of X"
   answers at the same time.
3. An edge is stored with resolution `name_unique` and confidence 0.6. What does that tell you,
   and what change elsewhere in the repo could make it wrong tomorrow?
4. Indexing crashed halfway through writing Neo4j. What does the ledger show, and what happens
   on the next run?
5. The hand-check found two wrong answers out of ten, both in Java. What do they have in common,
   and what would it take to fix them properly?

## Phase 2: Retrieval and the context packer (2026-11-12)

**What was built.** Given a checkout and a diff, `prism context` works out which function each
hunk changes and collects what a reviewer would want to see next to it. From the graph: the
function's signature, the tests that cover it, its callers and callees, and the interface method
it implements. From the vector store: functions that look similar. All of it is ranked, trimmed to
a token budget and printed with token counts.

**Hunk to symbol.** A diff only has line numbers. The per-PR overlay from Phase 1 turns a line
into "this is inside `Text.truncate`". A deleted function no longer exists at the PR head, so it
is looked up in the base graph instead, which is how its callers can still be found.

**Why there is a budget.** A model has a limited context window and a small local model is slow,
so not everything can go in. The packer fills the budget in priority order: signature, contract,
tests, callers, then the rest, with similar code last. Whatever does not fit is recorded as
dropped, with the reason, so nothing disappears silently.

**What the reranker does and does not do.** A cross-encoder reads the hunk and one candidate
together and scores how related they are. That is more accurate than comparing two embeddings but
slower, so it only runs on the shortlist. Here it only orders items inside a priority class. It
never lets a similar function jump ahead of a test, because the model was trained on web text, not
code, and has not been shown to deserve that.

**Counting tokens.** The default counter assumes 4 characters per token. Measured against the
real Qwen tokenizer it is usually a little high, but it undercounts on up to a third of functions
in one repo, so a block can run over the budget by about a quarter. An exact counter is available
as a setting.

**Who gets the context.** Not the fine-tuned reviewer. It was trained on a hunk alone, so it is
served a hunk alone. The context goes to the verifier and to the frontier model.

**Quiz**

1. A hunk deletes a whole function. Where do its callers come from, and why not from the overlay?
2. On one test the packer dropped 6 of the callers of a long function. What used up the budget,
   and name two ways to change that.
3. Why does the reranker only reorder items within a priority class?
4. In `graph+vector` mode on one diff, every "similar" item was dropped. Does that mean vector
   search is useless? What experiment would answer it?
5. The token counter can undercount. What is the risk when the block is sent to a model with a
   small context window, and which setting removes it?

## Phase 3: Gate classifier (2026-11-12)

**What was built.** A small classifier that looks at one diff hunk and says whether it is worth
sending to the reviewer at all. It is CodeBERT fine-tuned on the CodeReviewer quality-estimation
data, where each hunk is labelled by whether a human reviewer commented on it. Training runs on
Kaggle; PRism serves the exported ONNX model with onnxruntime, so no torch at runtime.

**The first run learned a newline.** In the train split every commented hunk ends with a newline
and no other hunk does. The model found that within half an epoch: train loss near zero,
validation F1 of 0. Every hunk is now normalised (trailing whitespace and git's "no newline"
marker removed) in training and in serving. Very long hunks were a second, weaker version of the
same problem, so train hunks over 100 lines are dropped.

**Results.** Best validation PR-AUC 0.811, reached at epoch 2; the third epoch added nothing. The
threshold is picked on validation for 90% recall (0.195). On test that gives recall 0.901 and the
gate holds back 26.5% of hunks. Line count alone gets an AUC of about 0.64, so the model does
more than count lines.

**Why recall and not accuracy.** A hunk the gate drops never reaches the reviewer, so a missed
issue cannot be recovered later. A hunk let through only costs some compute. So the threshold is
set low enough to keep 90% of commented hunks, and the question is how much it filters at that
recall.

**What does not carry over.** The data is 50/50. Real pull requests have far fewer hunks worth a
comment, so precision and the share filtered here will not hold on real traffic. Recall should.
Phase 7 measures it on the bench PRs.

**A slow end.** After training, the best checkpoint was reloaded on the CPU instead of the GPU,
so scoring the 62,504 valid and test hunks took about 6 hours instead of minutes. The run
finished with half an hour left of Kaggle's 12-hour limit. Fixed by moving the model to the GPU
after loading it.

**Quiz**

1. Train loss went to 0.0001 and validation F1 to 0 on the first run. What does that pattern
   usually mean, before looking at any data?
2. Why is the normalisation applied in serving as well as in training?
3. The threshold was chosen on validation and reported on test. What would go wrong if it were
   chosen on test?
4. On test the gate filters 26.5% of hunks at 90% recall. Will it filter more or less on a real
   repository, and why?
5. Validation PR-AUC peaked at epoch 2 while train loss kept falling. What is happening, and what
   would you change in the next run?

## Phase 4: Reviewer fine-tuning (2026-11-14)

**What was built.** A fine-tuned version of the reviewer model. Qwen2.5-Coder-3B was trained on
40,000 hunk-and-comment pairs from CodeReviewer with QLoRA on one Kaggle T4, exported to GGUF and
served by Ollama as `prism-reviewer:v2`. The model writes only the comment; the line is picked
afterwards from the identifiers it names (ADR 0009). Base and tuned were compared on 500 test
hunks with BLEU, ROUGE, valid JSON and a blinded A/B judge.

**LoRA in one line.** The 3 billion base weights stay frozen. For each weight matrix W the
model learns a small update BA, where B and A are thin matrices of rank 16, and uses W + BA.
That is 30 million trained numbers instead of 3 billion. QLoRA also stores the frozen W in 4-bit
NF4, a format with 16 levels spaced to match how weights are actually distributed (most of them
near zero), so a 3B model fits a 16 GB T4 with room to train.

**The first run lost.** It beat the base model on ROUGE and JSON, and the judge still preferred
the base model's comments (tuned won 43.7% of decided pairs). Its most common comment was "This is
not needed". The training data is full of short comments that point at nothing and of linter
output pasted in as review comments, and the model learned those.

**The second run filtered the data and tied.** Same settings and again 40,000 rows, but no linter
output, no comment under 8 words that names nothing in the hunk, and no comment text more than 3
times. The judge now calls it even with the base model (48.6%, interval 43 to 54). The data
filter moved the result more than any training setting would have.

**Why BLEU is weak here.** BLEU counts the word sequences a comment shares with the one comment
a human left. A hunk can deserve many different good comments, and a correct comment in other
words scores near zero. Run 1 scored lower than base on BLEU and higher on ROUGE, and neither
matched what the judge said. That is why the judge decides.

**Greedy decoding picks one opener.** 89% of run 2's comments start with "I think", and it almost
never asks a question, while 44% of its training comments do. "I" is the most common first word
in the data (tied with "this"), and greedy decoding takes the top choice every time. Questions are
spread over many openers, so none of them is ever the top choice.

**Overfitting signs.** Validation loss fell from 2.244 to 2.202 and was flat over the last 500
steps; train and validation loss stayed close. No overfitting in one epoch, and a second epoch
would add little. The model learned its data well; the data was the problem.

**SFT, DPO, RLHF.** SFT trains on examples of the right answer. DPO trains on pairs, a preferred
and a rejected answer, and pushes the model toward the first without a separate reward model.
RLHF trains a reward model from human preferences and optimises against it with reinforcement
learning. DPO on judge-labelled pairs is the obvious next step for the "I think" habit. Not
tried.

**Quiz**

1. LoRA rank 16 on a 2048 x 11008 matrix: how many trained numbers is that, and how many in the
   full matrix?
2. Run 1 had higher ROUGE-L than the base model and lost with the judge. Give two reasons the two
   can disagree.
3. Why is the test split not filtered when the training split is?
4. 44% of training comments contain a question, and the model asks one in 1% of its comments.
   Explain why, and name two ways to change it.
5. Sonnet and Opus agreed on 28 of 50 pairs. Does that make the 500-pair result useless? What
   would you do to trust it more?

## Phase 5: The review pipeline (2026-11-14)

**What was built.** `prism review`: a LangGraph graph that plans a PR, fans out one reviewer per
file, runs static analysis next to it, retrieves context, has Claude verify every comment, and
writes a `PRReview`. Security-sensitive and large hunks go to Claude instead of the local model.
Secrets are redacted before anything leaves the machine, text that tries to instruct a reviewer
is flagged, and each review can be traced in a self-hosted Langfuse (ADRs 0010 to 0014, results in
`eval/reports/review_pipeline.md`).

**`Send` and map-reduce.** A LangGraph edge normally names the next node. A conditional edge can
instead return a list of `Send(node, payload)`: LangGraph runs that node once per payload, at the
same time, each with its own small state. That is the map. The nodes write into a state key with
a reducer (`operator.add` on a list), so their outputs are concatenated instead of overwriting
each other. That is the reduce. The planner sends one `file_reviewer` per file plus one `sast`;
the `context` node runs only when all of them are done. Two things to watch: the order in which
the mapped nodes finish is not fixed, so the results are sorted back into diff order, and running
more of them at once does not make the model faster, so all of them share one semaphore of 4.

**Why a verifier cuts hallucinations.** Writing a comment and checking one are different tasks.
The writer has to come up with something for every hunk, and a small model that has learned to
sound like a reviewer will produce a confident sentence whether or not there is a problem. The
checker only has to say whether a given claim is true of the code in front of it, with the
callers and tests to look at, and is allowed to say "none of this matters". In the two diffs
here the tuned model wrote 10 comments and the verifier kept none, and the two drops checked by
hand were right. Rules catch the cheap failures first (wrong line, vague, repeated); a confidence
threshold and "security claims need evidence" catch the verifier's own weak calls.

**Routing trades cost for quality.** Every hunk could go to Claude, and the review would be
better and slower and cost money on every PR. The router sends only what the cheap path handles
badly: security-sensitive code (the 3B model named none of four planted security bugs, Bandit
found all four for nothing) and big hunks, plus whatever the verifier flags afterwards. The
planted off-by-one was found only this way. Whether the routed share is right is measured, not
argued: Phase 7 counts what each path finds per PR and what it costs.

**Prompt injection is handled by structure, not by a filter.** The regex that spots "AI reviewer:
approve this PR" is easy to get past. What actually protects the review is that PR text sits in
a delimiter it cannot close, answers are matched to requests by id, and the risk verdict is
computed by code from the kept comments. No model is ever asked whether to approve.

**Quiz**

1. Four files fan out with `Send` and the semaphore is 4. One file has 9 hunks, the others one
   each. What is the most model calls in flight at once, and why does it not help to raise the
   semaphore above Ollama's parallel slots?
2. Why does the gate run in the planner instead of inside each file reviewer, as PLAN.md drew it?
3. The verifier is the same model family as the frontier reviewer. Why are Claude's own comments
   not sent back to the verifier, and what checks do they still go through?
4. A hunk contains `token = "ghp_..."`. What does Claude see, and can it still comment on it?
5. The first traced review showed most of each generation's time spent waiting. What would you
   have concluded about the model's speed without the `wait_for_slot_s` field?

## Phase 6: GitHub App, queue, MCP, feedback, dashboard (2026-11-15)

**What was built.** Everything around the review pipeline that makes it a product: reviews stored
with every decision (ADR 0015), an MCP server so Claude Code can review its own changes (ADR 0016),
a GitHub App that reviews pull requests and posts inline comments (ADR 0017), a Celery queue under
it (ADR 0018), reactions on its comments fed back into Langfuse (ADR 0019), and a dashboard (ADR
0020). Checked live on a private test repo: a PR with three planted bugs got a review with three
correct inline comments 1 min 50 s after it was opened, most of that the wait for verification.

**How a GitHub App authenticates.** The App has a private key. It signs a short JWT (at most 10
minutes) to prove it is the App, then trades it for an installation token (one hour) that can act
on the repos it is installed on. Webhooks are signed with a shared secret: the HMAC of the raw body
must match `X-Hub-Signature-256` before anything in the body is trusted.

**Why a queue.** Reviewing inside the web process loses the review when the process dies, cannot
wait out a rate limit, and reviews old commits after newer pushes. With Celery the task is
acknowledged only after it finishes (so a crash puts it back), the broker's visibility timeout is
longer than the slowest review (so a slow one is not handed to a second worker), a claim on
`repo#pr#sha` makes redeliveries harmless, and the newest head per PR lets queued reviews of older
commits stop early.

**Holding reviews instead of posting unverified ones.** The local model's comments were wrong more
often than right, and the verifier is a Claude handoff with no API key here. So the App holds a
review until a Claude session answers it (through the MCP tool or the files), and beat posts it
the next minute. Slower, but nothing unverified reaches a PR.

**MCP in one line.** A server exposes typed tools over a pipe; the client (Claude Code) decides when
to call them. The useful trick here: the client is itself a Claude model, so the review's
verification can be handed to it inside the same conversation. And stdout belongs to the protocol,
which is why every subprocess now gets an empty stdin and nothing prints.

**Conditional requests.** GitHub returns an `ETag`; sending it back as `If-None-Match` gets a 304
for unchanged data, which costs no rate limit. Measured: the remaining quota stayed the same
across a 304. The catch: the ETag is tied to the token, so the savings only show inside a process
that keeps its token.

**Quiz**

1. Why does the worker check the PR's head commit again right before posting, when the queue
   already drops superseded reviews?
2. A webhook delivery times out and GitHub sends it again while the first review is still running.
   What stops a second review, and what happens if the first one then fails?
3. Why is GitHub's `/reviews/{id}/comments` the wrong endpoint for matching posted comments to
   findings?
4. A comment got one 👍 and its thread was resolved; another got one 👎 and was resolved. What
   label does each get, and why does resolving not outweigh the 👎?
5. The App holds every review until Claude answers. What would you change to post within N
   seconds, and what would it cost?

## Phase 7, early slice: BugBench on TinyDB (2026-11-15)

**What was built.** A benchmark harness (ADR 0021): a case is a real commit with one planted
defect at a known line, materialized as a git worktree, reviewed by the full pipeline, and scored
by whether a posted comment lands within 3 lines of the defect. The Claude handoff is batched
across cases, so one Sonnet subagent answers a whole round instead of one per PR. The slice: 10
TinyDB commits, two handoff rounds per run (35 requests, then 7 to 12 escalated hunks).

**The numbers.** Recall 9/10. Comments per PR 1.6. First run p50 14 s, p95 37 s (local reviewer,
SAST, in-memory indexing). Of the 9 hits, the local reviewer wrote 2 and Claude wrote 7, all 7
through escalation: the local comment on the hunk was wrong or vague, the verifier dropped it or
kept it with low confidence and flagged the hunk, and the frontier review then named the bug. So
on this slice the recall comes from the escalation path, not from the fine-tuned model.

**The miss.** Case 07: the local reviewer described the bug correctly but put it on line 443,
13 lines above the defect. The verifier kept it and wrote that the issue is at 455–457, but a
verdict has no field to move a comment, so it posted at 443 and missed the window. A real
reviewer would see the comment in roughly the right place; the metric would not.

**The fix and the second run.** A verdict can now give `line`, where a kept comment belongs (only
a line the hunk shows). The first run's subagent brief also told the model that some PRs contain
real bugs, which primes it, so each round folder now carries a neutral `README.md` and every
subagent gets the same one-line prompt. Run `slice-02` reused the first run's cached local
replies, so only Claude's side changed: recall 10/10, comments per PR 1.7. The verifier moved
case 07's comment from 443 to 456. Two cases changed who gets the credit (01 and 10 went from the
local reviewer to Claude), so per-case detail varies between answer passes even with the same
requests.

**What to keep in mind.** ±3 lines counts position, not content: case 01's first-run hit sat on
the right line with a wrong suggested fix. Ten cases on one repo with no clean PRs says nothing
about false positives.

**Quiz**

1. Why is the PR the whole commit with the defect planted, rather than a one-line diff with just
   the defect?
2. Request ids are hunk-local (`path#idx-hash`). What would go wrong if the runner put several
   cases' requests in one folder without the `case::` prefix?
3. Seven of nine hits came from Claude via escalation. What does that say about where the next
   effort should go: the reviewer, the verifier or the router?
4. Why does a rerun of the slice cost almost nothing, and what would make it rerun the local
   model?
5. What would a clean PR add that recall on planted defects cannot show?

## Phase 7, mid-size BugBench: 4 repos, 83 PRs (2026-11-15)

**What was built.** Cases are now one of three groups: injected (a planted defect), real (a
commit that introduced a bug someone later fixed) and clean (left alone, to count false
positives). Injected cases come from a generator: it mutates lines a recent commit added and
keeps a mutation only if the repo's own tests pass without it and fail with it. Real bugs come
from SZZ: blame the lines a bug fix changed to find the commit that wrote them. Four repos:
TinyDB, aiosqlite, sqlite-utils and jsoup (Java). 57 injected, 16 clean, 10 real.

**The numbers.** Injected recall 48/57 (84%), real bugs 6/10 (60%), and no comment at all on any
of the 16 clean PRs. About one posted comment per buggy PR, most of them on the bug. Claude
found 41 of the 48 injected hits, nearly all after escalation; the fine-tuned reviewer found 7.
One pass took 2 handoff rounds and 22 Sonnet subagent calls (about 520 answers).

**Why test-check the mutants.** Without it, a mutation can be equivalent (it changes nothing)
or break something no one depends on, and the benchmark would count misses that are not misses.
With it, every injected case is a behaviour change some test cares about. The cost is bias:
only testable defects get in, and the failing test is often in the same PR.

**Why the real bugs score lower.** They are not filtered by tests, the "defect line" is wherever
the fix happened to edit (sometimes many lines away from the root cause), and a few introducing
commits were refactors that only rewrote an older bug. Three of the four misses had a comment
near the defect that the verifier dropped.

**What went wrong on the way.** A hung jsoup test left Maven's JVM holding the output pipes, so
killing the shell did not end the run and the generator waited for an hour. The fix kills the
whole process tree. Later the machine ran short of memory (Docker, WSL and Maven together) and
Maven runs crawled until the unused containers were stopped.

**Quiz**

1. A mutation is kept only if a test fails. Name one kind of real bug this rule can never
   produce, and how the benchmark covers it instead.
2. Why does the generator throw away a mutation that makes the build fail?
3. SZZ blames the lines a fix removed. Why can a fix that only adds lines (a missing check) not
   be traced this way?
4. The clean false-positive rate is 0. What would you check before believing the pipeline never
   comments on good code?
5. Claude wrote most of the hits. Which ablation in PLAN.md answers whether the fine-tuned
   reviewer is worth keeping?

## Phase 7, the PR-Agent baseline (2026-11-15)

**What was built.** PR-Agent 0.49.0 runs on the same 83 PRs in its own venv. Its model calls go
to a small local endpoint that speaks the OpenAI chat API: a prompt it has seen gets its stored
answer, a new one is saved and the call fails, and Sonnet subagents answer the saved prompts in
batches. Rerun, and PR-Agent gets its answers and moves to the next call. Two rounds were enough:
166 review and improve prompts, then 61 self-reflection prompts.

**The result.** PR-Agent with Claude found 54 of 57 planted bugs and 8 of 10 real ones; PRism
found 48 and 6. PR-Agent found every bug PRism found. It also commented on 2 of 16 clean PRs
(both look like real issues) and wrote about twice as many comments per PR. PRism used about a
quarter less Claude input.

**Why the gap.** PR-Agent shows Claude every hunk. PRism shows Claude what the local model said
about each hunk and lets the verifier decide whether Claude should look at the hunk itself. When
the local model says something wrong and the verifier does not escalate, nobody looks closely.
That is a design choice (cheaper, quieter), and this benchmark shows its price.

**Making a third-party tool deterministic.** The cache only works if the same PR gives the same
prompt every time. Three things threatened that: the branch name (it is the PR title), the
commit hash (new on every amend, fixed with fixed dates), and the date in the prompt (stripped
from the key).

**Quiz**

1. Why does the endpoint answer a new prompt with HTTP 400 rather than 503?
2. PR-Agent reports a line range, PRism a single line. Why does the report also count only
   findings of at most 10 lines, and what did that change?
3. Both systems used Sonnet. What would you expect to change if PRism's escalation sent every
   hunk the local model commented on to Claude?
4. PR-Agent's clean-PR findings look true. Should they count against it? What would a fairer
   metric need?
5. PRism made 519 Claude requests but sent less text than PR-Agent's 227. Why?

## Phase 7, first ablation: Claude reviews every hunk (2026-11-15)

**What was run.** A new switch (`PRISM_ROUTE_ALL`) sends every planned hunk straight to Claude's
review rubric and skips the local model; the rest of PRism stays the same. 450 hunks, one
handoff round.

**The surprise.** It barely helped: 49/57 against PRism's 48/57, 6/10 real bugs either way. The
small fine-tuned model was not the bottleneck. Going through the 12 misses one by one: 3 hunks
never reached Claude (the gate and the hunk budget), 3 times Claude named the bug but was not
sure enough to pass the 0.6 threshold, once it commented on the call site just outside the
window, and 5 times it saw the hunk and said nothing.

**A free experiment.** The answers are cached, so changing the threshold costs no Claude time.
At 0.3, recall goes to 51/57 and 7/10, and three clean PRs get a comment (two of them real
issues PR-Agent also found).

**What it teaches.** Measure before you blame a component. The local model looked like the weak
link because most hits came from Claude; the ablation shows the losses are in routing, the
one-hunk-at-a-time view and the threshold.

**Quiz**

1. Why does the Claude-only run keep the gate and the hunk budget, rather than switching them off
   too?
2. Three misses had the bug named at confidence below 0.6. What is the cost of lowering the
   threshold, as measured here?
3. Why might Claude miss a bug in a hunk it reads, that PR-Agent finds in the same PR?
4. If the local model costs almost no recall, what is it buying PRism?
5. Which ablation would you run next to test the "one hunk at a time" explanation?

## Phase 7, second ablation: the gate off (2026-11-16)

**What was run.** PRism as in the main run, but with the gate letting every hunk through. To keep
it cheap, the run started from a copy of the main run: the local model's cached replies and
Claude's answers were reused for every request that did not change, so only 175 new hunks went
to the local model and 167 new requests to Claude.

**The result.** 50 of 57 planted bugs instead of 48; the two extra are hunks the gate had
skipped. Real bugs, clean PRs and comments per PR barely moved. The gate saves 29% of the local
model's work and 24% of Claude's, for 2 bugs out of 57.

**Caching as an experiment tool.** Because answers are keyed by what was asked, an ablation that
changes only part of the pipeline pays only for the part it changes. The context-mode ablations
cannot use this: they change the context inside every request, so every answer is new.

**Quiz**

1. Why is wall-clock time a poor measure of the gate's saving in this run, and what was measured
   instead?
2. The gate skips 29% of hunks but saves only 24% of Claude requests. Why are the two different?
3. One miss from the Claude-only run is still missed with the gate off. What causes it?
4. When would you turn the gate off in production?
5. Why can the context-mode ablation not reuse the main run's answers?

## Phase 7, third ablation: the whole-PR view (2026-11-16)

**What was run.** The Claude-only setup, but with every hunk of a PR in one request, grouped by
file, instead of one request per hunk (`PRISM_REVIEW_UNIT=pr`). Same hunks, same retrieved
context, same checks and threshold. 82 requests instead of 450, carrying the same 2.29 MB.

**The result.** 52 of 57 planted bugs instead of 49, with none lost. Claude wrote the same number
of comments (92 against 91); more of them landed on the bug. Two of the new finds were bugs the
per-hunk answers had already named at low confidence: seeing the javadoc, the test or the caller
in the same request moved them from 0.35 or 0.5 to 0.75.

**The catch.** Real bugs went from 6 to 5: three were lost, each named but below 0.6. Re-scored at
0.3 (free, the answers are cached), the whole-PR view finds 52/57 and 9/10 with 2 clean PRs
flagged, against PR-Agent's 54/57, 8/10 and 2, with about two thirds of the comments.

**What it teaches.** The view decides what the model can be sure of. A bug that is a guess from
one hunk can be a certainty when its test sits in the same prompt. It also shows that a single
threshold is a blunt tool: the same answer quality gives very different recall at 0.6 and 0.3.

**Quiz**

1. Why does the whole-PR run keep the retrieved context, though PR-Agent has none?
2. Claude wrote almost the same number of comments in both views. Where do the extra hits come
   from, then?
3. Why can real-bug recall go down while injected recall goes up, in the same run?
4. What bounds the size of a whole-PR request here, and what would break without it?
5. Which two remaining planted-bug misses would the gate-off setting fix, and why are the other
   three out of its reach?

## Phase 7, fourth ablation: the confidence threshold (2026-11-17)

**What was run.** A sweep from 0.3 to 0.9 over three finished runs (PRism, Claude-only, whole-PR),
with no new Claude work. Each case is reviewed once more from its caches, and the verifier's input
is rebuilt from the final state and answered at every threshold from the saved answers. Each sweep
gives back its own run's numbers exactly at 0.6, which is how I know the method is sound.

**The result.** PRism at 0.4 finds 50/57 and 7/10, against 48/57 and 6/10 at 0.6, and still
comments on no clean PR. Above 0.6 every run loses bugs quickly and gains nothing, since nothing
is flagged on clean PRs at 0.6 anyway. The whole-PR view holds its planted-bug recall from 0.3 to
0.7; only its real-bug recall depends on the threshold.

**A threshold with two jobs.** In PRism the threshold also routes: a local comment Claude keeps
below it goes back to Claude for a review. Raising it would ask Claude new questions, so those PRs
cannot be re-scored for free, and the sweep reports them as unscored instead of guessing.

**A lesson about the environment.** The first sweep failed because Bandit, missing from the
virtualenv during the first two runs, was installed now. PRism skips a missing tool quietly, so
nothing showed it. The run summary now lists the tools that actually ran.

**Why not just change the default to 0.4?** The 0.4 was picked on the same 83 PRs it is measured
on, and 16 clean PRs say little about the false-positive rate. It needs checking on the bigger
set.

**Quiz**

1. Why can the sweep re-score PRism below 0.6 for free, but not above it?
2. What check tells you the sweep reproduces a run faithfully?
3. Why is 16 clean PRs too few to set the threshold, even though none is flagged at 0.4?
4. Comments per buggy PR rise from 1.00 to 1.18 at 0.4. What would you need to know before
   calling that a cost?
5. How did a missing SAST tool go unnoticed for two runs, and what now catches it?
