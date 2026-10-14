---
name: vet
description: The full review pass for a GitLab merge request or a GitHub pull request. Review lenses derived from the diff run as a Workflow; every finding is adversarially verified, the serious ones are reproduced by a command, and a mutation pass finds behaviour no test pins. A CodeRabbit CLI pass adds a second opinion and is skipped cleanly when absent. The result is staged as private drafts (inline comments with applicable suggestions plus one summary) and published only on the user's explicit word. Use when asked to review, re-review or check an MR/PR "properly", "with lenses", "with CodeRabbit", or to stage findings as inline comments. Invoke with an MR/PR URL.
argument-hint: "<MR/PR URL> [focus notes] [no-coderabbit] [max] [lang=<language>]"
---

# vet: review a merge or pull request

`$VET` below is `scripts/vet.py` in this skill's directory, run as `python3 -I $VET`. It does
every forge call, and every guard about what may be written lives in it rather than here.

## The contract

- **Invoking this skill is the opt-in** to run a Workflow and to stage drafts. It is not an
  opt-in to publish.
- **Drafts only.** On GitLab these are draft notes, on GitHub one pending review. Either is
  visible to the user alone until it is submitted. `$VET draft` and `$VET discard` are the
  only writes a run makes.
- **Publishing is a separate act.** It needs the user's word in this conversation, for this
  request, after they have seen what is staged. A re-review does not inherit an earlier
  go-ahead. See `## Publishing`.
- **What a run never does:** approve, request changes, merge, push, resolve a thread, or
  write to the author's branch.
- **No attribution** in anything staged. `$VET` refuses `Generated with Claude`,
  `Co-Authored-By: Claude` and the robot emoji.
- **Nothing is said twice.**
  - The summary does not restate the inline comments.
  - Nothing is staged that an open thread already says.
  - The local record stays in the scratchpad and never goes to the forge.
- **The project defines what a finding is.** The repository's own review skill and its
  `CLAUDE.md` give the categories and the severity scale. This skill defines how the pass
  runs. Load the project's skill first and hand its scale to the lenses.
- **Language.** Everything staged, and the local record, is in English. Only `lang=<language>`
  in the invocation changes that: a repository's own language preference does not apply
  silently.

Arguments after the URL:
- **Focus notes** add criteria and never replace a step.
- **`no-coderabbit`** skips the CodeRabbit pass. `VET_SKIP_CODERABBIT=1` does the same.
- **`max`** runs every Workflow agent on the session model (see step 4).
- **`lang=<language>`** writes the staged comments in that language instead of English.

## Where a run keeps its files

`RUN=<scratchpad>/vet/<host>-<project>-<MR|PR>-<n>`, falling back to
`${TMPDIR:-/tmp}/vet/...` when the session has no scratchpad. The run keeps these files there:

| File | What it holds |
| --- | --- |
| `wt/` | the review worktree |
| `request.diff` | the full diff against the merge base |
| `baseline.txt` | what the gates printed |
| `cr.txt` | the CodeRabbit pass |
| `workflow.json` | the Workflow result |
| `REVIEW.md` | the record |
| `review.json` | what gets staged |

**Write `REVIEW.md` as you go**, not at the end. Findings held only in the conversation are
lost to a compaction. With several requests in one run, finish one on disk before opening
the next.

## 1. Recon: one shell call, then CodeRabbit in the background

```bash
python3 -I $VET status '<URL>'        # head SHA, branches, fork?, every thread (ours marked), our drafts
git fetch --no-prune origin '+pull/<n>/head:refs/remotes/origin/pr-<n>' <target>              # GitHub
git fetch --no-prune origin '+merge-requests/<n>/head:refs/remotes/origin/mr-<n>' <target>    # GitLab
git rev-parse refs/remotes/origin/pr-<n>          # must equal the head `status` printed
git worktree add --detach "$RUN/wt" <head>
BASE=$(git merge-base origin/<target> <head>)
git -C "$RUN/wt" log --oneline $BASE..HEAD
git -C "$RUN/wt" diff --stat $BASE..HEAD
git -C "$RUN/wt" diff $BASE..HEAD > "$RUN/request.diff"
git merge-tree --write-tree origin/<target> <head> | head -20    # step 2 reads this
```

- **Read everything already said before forming an opinion:** the description, every thread,
  and our earlier drafts. These hold three things: what the author already answered or
  disputed, what other reviewers raised, and what we said before. Skipping them makes the
  review repeat settled points or contradict a teammate without knowing it.
- **Always fetch the synthetic `pull/<n>/head` / `merge-requests/<n>/head` ref.** It works
  for forks and same-repository branches alike. A fork's branch is not in `origin`, so a
  plain fetch brings nothing and every later ref fails with `unknown revision`.
- **`--no-prune` is not optional.** Where `fetch.prune` is set, an explicit-refspec fetch
  prunes the rest of `refs/remotes/origin/`, `origin/<target>` included. These refs also
  vanish at the next plain `fetch --prune` from any session, so re-fetch them right before a
  step that reads them.
- **The head must equal the forge's head.** If the forge head and the fetched ref differ,
  the branch moved; stop and start again.
- **Never switch the checkout you started in.** Another session may be working in it.
- **Diff against the merge base**, not against `origin/<target>`. The latter attributes
  every trunk commit since the fork to the author.
- **CI at that SHA, not on the branch.**
  - GitLab: `glab api "projects/:id/pipelines?sha=<head>"`. `glab ci status` takes no
    pipeline id, and `glab ci list -b` misparses branch names.
  - GitHub: `gh api repos/:owner/:repo/commits/<head>/check-runs`.
  - Read the pipeline state, not the first job that finished.
  - Compare the job set with the target's last pipeline. A green run with fewer jobs is the
    one worth catching.

**Start CodeRabbit now, in the background** (`run_in_background: true`):

```bash
python3 -I $VET coderabbit "$RUN/wt" --base $BASE --out "$RUN/cr.txt" [--skip]
```

The first line of `cr.txt` is its verdict: `RAN`, `SKIPPED`, `NOT INSTALLED`, `SIGNED OUT`,
`BUSY`, `LIMITED` or `FAILED`. Anything but `RAN` costs one line in **Not checked** and one
line to the user. For `NOT INSTALLED`, that line is the install command `cr.txt` already
holds. The run goes on either way.

When it is `LIMITED`, the allowance is 3 local reviews per rolling hour and the text names
the wait. Retry in the background once the window opens if the review is still running.
Replaying a finished run is free: `coderabbit review findings`.

## 2. Baseline, and what the trunk did underneath

**Run the project's gates in the worktree** and write what they printed into `baseline.txt`:
test counts, lint state, `terraform fmt -check` / `validate`. Prefer the repository's own
recipes (`just`, `make`, `package.json` scripts). Without a baseline, a pre-existing defect
cannot be told from a new one.

- Record the exact test command. The mutation pass needs it.
- A gate that cannot run here (credentials, services, hardware) is one line in **Not
  checked**, never silently dropped.

**Read the `git merge-tree` output from step 1:**
- First line a tree OID and exit 0: the merge is clean.
- `CONFLICT` lines: a finding, since only a rebase clears them.
- Anything else (`not something we can merge`) is a refusal, not a conflict. Re-fetch and
  re-run.

**A textually clean merge is not clean when the trunk generates files** (catalogs, schemas,
lockfiles). When the trunk moved under the branch, merge it in a throwaway worktree and run
the drift gates there.

## 3. Read the diff yourself, then write the lenses

Read `request.diff` once, end to end, deleted lines included. You anchor the comments and
judge split verdicts, so this cannot be delegated.

Then write 4 to 6 lenses **from this diff, not from a fixed list.** Each lens names the files,
the functions and the scenarios to trace ("A accepts, B answers `{ok:false}` on attempt 2:
what is returned?"). A generic "look for bugs" lens finds nothing the others miss.

The usual set:
- correctness of the core change;
- every consumer of a contract that changed;
- lifecycle and concurrency;
- config, deploy and documentation (the change is everywhere the thing lives);
- tests (does each fail without the change? does its name promise what its body asserts?);
- quality and scope.

The template always adds a **mechanical** lens. It greps rather than reasons: unreachable
code, suppressions with no reason, docs naming what the diff renamed, a number or link
changed in one place only.

## 4. The Workflow: review, dedup, verify, prove, mutate

Load `workflow-authoring`. Copy `workflow.js` from this skill's directory to
`$RUN/workflow.js`, replace only its `LENSES` array, and run it by `scriptPath` rather than
inline. That keeps the template out of the prompt. Pass `args`:

```json
{
  "wt": "...", "diff": "...", "base": "...", "head": "...", "title": "...",
  "scratch": "$RUN", "testCommand": "...", "tier": "balanced|max", "previous": [...]
}
```

What it does, and why:

1. **Review.** The lenses run in parallel, read-only on the shared worktree; they read and
   grep, and never run the suite or a build. The mutation pass starts at the same time in its
   own worktree when there is a test command.

   **Every agent that runs something takes its turn.** Mutation, the provers and the
   reconcile step go through one queue, one at a time. Several test suites at once, each in
   its own worktree, hang a machine. Reading agents stay parallel.
2. **Dedup.** This is the one barrier. Without it, one defect gets two verdicts that can
   disagree. When unsure, it keeps findings apart: a wrong merge loses a finding, a missed
   merge only costs a verification.
3. **Verify.** Correctness is the gate and relevance can only remove. A finding survives
   only if the correctness verifier fails to refute it and the relevance verifier does not
   call it noise. A relevance "yes" never outvotes a refutation; that is how a false claim
   would reach the request. The verifier also corrects the line number.

   A correctness verifier that returns nothing (skipped, or died) is not a refutation. The
   finding stays as a **question** and the run logs it, rather than letting it vanish; the
   relevance verdict still applies to it.
4. **Prove.** Every surviving blocker and should-fix is reproduced by a command in the
   prover's own worktree: a failing test, a script, the build, a grep that comes back
   non-empty. The output is kept verbatim. A finding that nothing reproduces becomes a
   **question**: it is staged as a question to the author, never as a claim.
5. **Mutation.** Delete each behaviour the diff added, one per run, and re-run the suite.
   - **Green** is a finding: the behaviour ships untested. Coverage percentages cannot show
     this; every deleted line was executed, just never asserted on.
   - **Red** kills any lens claim that this behaviour lacks tests.
   - After each run the worktree is reset with `git reset --hard && git clean -fd`, which
     also removes files the run created, and must show a clean `git status` before the next.
   - `git diff --stat` is printed before every run. A mutation that did not apply reports
     the same green as an untested path.
6. **Reconcile.** Re-review only; see `## Re-review`.

**Model tiers** (`tier: 'balanced'`, the default). The rule: a step runs on a cheap model
where its output is checked by running or by mechanics, and on the session model where a
wrong verdict would kill a real defect.

| Step | Model |
| --- | --- |
| Core lenses (correctness, consumers, concurrency) | session model |
| Correctness verification of blockers and should-fix | session model |
| Config/docs, tests and quality lenses | Sonnet |
| Relevance verification | Sonnet |
| Prover, mutation, reconcile (the evidence is the command output) | Sonnet |
| Correctness of nits | Sonnet, low effort |
| Mechanical lens, dedup | Haiku, low effort |

`max` puts every agent on the session model. Use it for a request where a missed defect is
expensive.

**Reading the result.** Dump `.result` to `workflow.json` and print one line per finding
(`index, survives, severity, file:line, title, proof?`) before reading any detail.

## 5. Adjudicate, then merge CodeRabbit

**Adjudicate.**
- Read the detail only for surviving blockers and should-fix, questions, and split verdicts.
- Spot-check each important claim with one grep. A finding that reaches the request under
  the user's name must have been seen by you.
- `checked_ok` can narrow a finding: "the override is bypassed" may become "latent, nothing
  calls it yet". Adjust the wording rather than dropping the finding.
- Merge duplicates. Fold related small items (missing tests, repeated comment-rule
  violations) into one comment that lists their lines.
- Weigh the mutation results:
  - **Green:** becomes a should-fix ("the X branch can be deleted with all N tests passing").
  - **Red:** removes any "untested" claim about that behaviour.

**Merge CodeRabbit.** For each of its findings:
- **Matches a Workflow finding:** merge them and say both found it.
- **No match:** verify it with one grep or run before keeping it, and re-grade it on the
  project's scale. Its severity is not the repository's.

## 6. The record: `$RUN/REVIEW.md`

The record never leaves the machine. It is what a re-review in the same session reconciles
against, and what the summary is condensed from.

- **Reviewed at.** One labelled line each, two trailing spaces each:
  - **Request**: link, title, `@author`
  - **Branch**: source → target
  - **Reviewed tip**: full SHA
  - **Base**: merge base; ahead/behind the target
  - **Pipeline at that tip**: id and state
  - **Round**: number and `YYYY-MM-DD HH:MM ±HH:MM`

  The tip is copied from `git rev-parse`, never retyped: a retyped short SHA reads right
  and resolves to nothing.
- **Verdict.** One paragraph whose first sentence stands alone. It judges the code, never
  the person.
- **Baseline.** What the gates printed.
- **Findings.** Numbered. Each has severity, `file:line`, the claim, the scenario, the fix,
  the reproduction command with its verbatim output, and which lens or CodeRabbit found it.
- **Questions.** Findings nothing reproduced.
- **Checked and fine.**
- **Not checked.** About the branch, with the reason: gates that could not run, CodeRabbit's
  verdict if not `RAN`, files skipped.
- **Dismissed.** What verification refuted, with the reason, so the same claim is not
  rediscovered.
- **Rounds.** One dated block per re-review.

**No output crosses a round.** Every output in the record was produced at the tip it names.

## 7. Stage the drafts

**Re-check every anchor right before staging, in one call:** `grep -n` the exact added text
in `$RUN/wt`.

**Where a comment can anchor:**
- An inline comment anchors only to an **added line**; `$VET check` refuses anything else.
- A defect on an unchanged line goes on the nearest added line and names the real line in
  its text. With no added line near it, it goes in the summary.

**Suggestion blocks.** Only for a short, exact replacement at the same indentation.
- GitLab: ` ```suggestion:-0+N ` replaces the anchor line and the N lines below it.
- GitHub: a plain ` ```suggestion ` fence. A multi-line range is `start_line` + `line` on
  the comment.

**Inline comment shape:**
1. A bold first line that reads alone: `**[severity] One-sentence defect.**` Labels follow
   the project's scale, by default `[blocker]` / `[should-fix]` / `[nit]` / `[question]`.
2. The scenario (inputs → wrong outcome).
3. The fix.
4. The missing test.
5. The reproduction, folded and verbatim:
   `<details><summary>Reproduction</summary>…</details>`

Links into the reviewed repository are pinned to the reviewed tip, three at most; `$VET`
refuses one pinned to another commit or to a branch. A permalink into another repository
(a library, an upstream fix) is evidence and is left alone, and so is a link inside a code
block or a code span, which quotes the file.

**The summary holds only what the inline comments cannot:**
- the verdict;
- findings with no anchor (cross-file, unchanged code, a conflict with the target);
- questions;
- a short "checked and fine" list, which tells the author what not to touch;
- **Not checked**;
- CI at the tip;
- the inline comments by count only ("7 inline comments: 1 blocker, 4 should-fix,
  2 nits"), never retold.

**What others already said:**
- A point already made in an open thread by anyone is not staged again.
- If the run adds evidence to it:
  - on GitLab, stage a reply draft in that thread;
  - on GitHub, one summary line linking the thread.
- `$VET check` warns about another person's open thread on the same line, and refuses on our
  own.

**Write `review.json`, then check and stage:**

```bash
python3 -I $VET check '<URL>' "$RUN/review.json"
python3 -I $VET draft '<URL>' "$RUN/review.json"
```

`check` writes nothing. It refuses, naming each problem, when:
- the head moved;
- an anchor is not an added line;
- two comments sit on one line;
- an attribution line appears;
- a `blob` link into this repository is pinned to another commit or to a branch;
- a GitLab reply names an unknown thread, or a GitHub reply is staged at all;
- drafts are already staged.

`draft` stops at the first failed write and names what was staged. On GitLab it reads the
drafts back and fails if the forge dropped a position.

**Drafts already staged** (the user's, or an earlier run's) need the user's word before
`--replace-drafts` deletes them. Show them what `status` lists, then ask.

## 8. Report, clean up, stop

Tell the user in chat:
- the request link and the verdict;
- the counts: raw → verified → proven → staged, plus questions;
- what CodeRabbit added, or its one-line verdict;
- what mutation found;
- what was dismissed, and why;
- **Not checked**;
- that the drafts are visible to them only.

Then remove the worktrees (`git worktree remove --force "$RUN/wt"`, then `git worktree prune`;
the agents remove their own) and leave `$RUN` in place. Stop, and ask whether to publish.

## Publishing

Only after the user's explicit approval for this request in this conversation.

1. Run `python3 -I $VET status '<URL>'`. The user may have edited, deleted or added drafts
   in the web UI, so what is staged is read from the forge, never from `review.json`.
2. Show the user the drafts it lists. Under them `status` prints a fingerprint: the count
   plus a hash over every draft's id, anchor and full body.
3. Run `python3 -I $VET publish '<URL>' --expect <that fingerprint>`. If anything was edited,
   deleted or added since, the fingerprint differs and the publish is refused with the new
   listing. A count alone would let an edited draft through.

GitHub submits as `COMMENT`. Approving or requesting changes is the user's button in the web
UI. Submitting from the web UI themselves is just as good, and needs nothing from this skill.

## Re-review: a round for this request exists and the branch moved

1. **Blind first.** Run steps 1 to 6 on the current tip as if no earlier round existed. Do
   not open the earlier `REVIEW.md` and do not hand earlier comments to the lenses.
   - Reading them first turns the pass into a checklist walk: it confirms the known
     findings from their old output and never notices the wrong ones.
   - A finding carried over is checked with its old command, and an old command can confirm
     by finding nothing: a grep scoped to a path that has since moved returns empty, which
     looks exactly like "the problem is still there".
2. **Then reconcile.** Pass the earlier findings as `args.previous`; they come from
   `$RUN/REVIEW.md` if this session still has it, otherwise from the threads `status` marks
   `(ours)`. The Reconcile phase re-runs each reproduction at this tip. Each one ends as
   exactly one of:
   - **still open**, with today's output;
   - **closed**, naming the commit and the line;
   - **withdrawn**: we were wrong, and the thread says so by name.

   A finding the blind pass did not rediscover is never carried forward on its old entry.
3. **Check the replies against the tree.**
   - For "fixed in `<sha>`", check that the commit is in the request:
     `git merge-base --is-ancestor <sha> <head>`.
   - A resolved thread whose fix sits on another branch is a **blocker**: merging now ships
     none of it.
   - Read that SHA's diff. A commit message describing the repair proves nothing.
4. **Read the interdiff** (`<old head>..<new head>`) as a diff in its own right. A fix for
   one bug routinely half-fixes it.
   - After a force-push the old head may not resolve; diff merge base to merge base instead.
   - The fix-up history counts too. In a repository that merges without squash, a pile of
     `fix:` commits on the branch's own unmerged work is a finding.
5. **Recompute what the trunk did underneath**, never carry it forward: merge-tree, and
   numbered files both sides added (migrations, decision records).
6. **Staging.** On GitLab, stage a reply draft in each of our earlier threads (closed, still
   open or withdrawn) and new inline drafts for new findings. On GitHub, the summary carries
   one line per earlier thread with its link.

## Gotchas

**Shell**
- zsh breaks on an unquoted `?` in a URL and on `echo ==`. Quote both.
- `grep` may be wrapped to honour ignore files, and git-ignored directories then come back
  empty with exit 1. Use `command grep`.

**GitLab API**
- `--form` reads a value starting with `@` as a file to upload, so a comment opening with an
  @mention breaks. `-f position[...]` returns 201 with the position silently dropped.
  `$VET` sends JSON for both reasons.
- Positions use the forge's `diff_refs`, whose `base_sha` can differ from the local merge
  base. `$VET` reads them itself.

**GitHub API**
- A multi-line comment needs `start_line` and `line` inside one hunk, `side: RIGHT`.
- A 422 means a line outside the diff.
- There is one pending review per user per pull request.
- The forge sends no `patch` for very large files. Nothing in them can be anchored, so they
  go in the summary. GitLab does the same with an empty `diff` for a collapsed or too-large
  file; `$VET` reports both as "no patch", not as a bad anchor. A file the change adds no line
  to goes in the summary the same way, and `check` says so; a pure rename on GitHub has no
  `patch` and is reported as "no patch".

**Git**
- A clean `git merge-tree` exit 1 also means "not something we can merge". Only `CONFLICT`
  lines are conflicts.
- A diff that removes a comment removes the reason with it. Read what left, not only what
  arrived.

## Out of scope

Anything the repository's conventions do not state, naming bikeshedding, type-hint
completeness, coverage percentages and micro-optimisations.
