<h1 align="center">vet</h1>

<p align="center"><em>"A finding nobody ran is an opinion."</em></p>

<p align="center">A review pass for GitLab merge requests and GitHub pull requests, as a skill for
Claude Code.<br>Findings are verified and reproduced before anyone sees them, and they reach
the request<br>as private drafts that you publish, edit or throw away.</p>

---

## What it does

`/vet <MR or PR URL>` (`/vet:vet` when another skill is also called `vet`) runs the whole pass
and stops with drafts staged on the request:

1. **Recon.** Reads the description and every thread first, including drafts you already
   staged. Then it pins the head SHA, checks it out into a throwaway worktree, diffs against
   the merge base, and reads CI at that exact commit.
2. **Baseline.** Runs the project's own gates and checks whether the branch merges cleanly into
   its target, so an old defect is not reported as a new one.
3. **Review lenses.** These are written for this diff rather than taken from a fixed list:
   correctness of the core change, every consumer of a changed contract, lifecycle,
   config and docs, tests, scope. A mechanical lens greps for what reading misses.
4. **Verification.** Every finding has to survive an attempt to refute it. Correctness is the
   gate; a "relevant" vote never outvotes a refutation.
5. **Proof.** Every blocker and should-fix is reproduced by a command in its own worktree, with
   the output kept verbatim. Agents that run things take turns, so one review never starts
   several test suites at once. What nothing reproduces is staged as a question, not a claim.
6. **Mutation.** Deletes each behaviour the change added, one at a time, and re-runs the suite.
   A green run is a finding: that behaviour ships untested.
7. **CodeRabbit.** An optional second opinion. Its findings are merged with the rest and
   re-graded on the project's scale.
8. **Drafts.** Inline comments anchored to added lines, with applicable suggestion blocks, plus
   one summary that holds only what cannot be anchored. Draft notes on GitLab, a pending review
   on GitHub; either is visible to you alone.

Then it reports in the chat and asks. Nothing is published until you say so.

Re-reviews run blind first. The current head is reviewed as if there were no earlier round, and
only then are the earlier findings re-run and marked still open, closed or withdrawn.

## What it will not do

The guards live in `skills/vet/scripts/vet.py`, not only in the instructions. Each one refuses
before anything is written:

| Refused | Why |
| --- | --- |
| Publishing unless `--expect` matches the fingerprint of what is staged now | a draft may have been edited, deleted or added in the web UI since you looked; a count alone would miss an edit |
| A review made at an older head | old findings anchored on new code |
| An inline comment on a line the change did not add | the forge rejects it, or anchors it somewhere else |
| A second set of drafts beside an existing one | `--replace-drafts`, and only with your word |
| A comment where our own open thread already sits | reply there instead |
| A link into the reviewed repository pinned to any commit but the tip | the line it points at has moved; permalinks to other repositories are left alone |
| An attribution line (`Generated with Claude`, `Co-Authored-By: Claude`) | the review is yours |

It never approves, requests changes, merges, pushes, or resolves a thread.

## Install

```
/plugin marketplace add CorneiZeR/claude-plugins
/plugin install vet@corneizer
```

`/plugin update` keeps it current. To work on the skill itself, clone it and link the skill
directory:

```bash
git clone https://github.com/CorneiZeR/vet-skill.git
ln -sfn "$PWD/vet-skill/skills/vet" ~/.claude/skills/vet
```

**Prerequisites:**
- `git`;
- `python3` 3.10 or newer (standard library only);
- [`glab`](https://gitlab.com/gitlab-org/cli) for GitLab or [`gh`](https://cli.github.com) for
  GitHub, already authenticated against the repositories you review.

**Optional:** the [`coderabbit` CLI](https://docs.coderabbit.ai/cli). If it is missing or signed
out, the review goes on without it and tells you how to add it.

## Use

```
/vet https://gitlab.com/group/project/-/merge_requests/42
/vet https://github.com/owner/repo/pull/7 the retry path must stay idempotent
/vet https://github.com/owner/repo/pull/7 no-coderabbit
```

| Argument | Effect |
| --- | --- |
| focus notes | extra criteria for the lenses; they never replace a step |
| `no-coderabbit` | skip the CodeRabbit pass (or set `VET_SKIP_CODERABBIT=1`) |
| `max` | run every agent on the session model instead of the tiered set |
| `lang=<language>` | write the staged comments in another language; English otherwise |

| Command | When |
| --- | --- |
| `/vet-status <URL>` | what is on the request now: head, threads, your staged drafts |
| `/vet-publish <URL>` | publish exactly the drafts staged now, after showing them |

Publishing from the forge's web UI works just as well. The drafts are ordinary drafts.

## Cost

The pass runs as a Claude Code Workflow, so it spawns agents. By default each step runs on the
cheapest model that can do it safely. A cheap model is used where a command or plain mechanics
checks its output; the session model is used where a wrong verdict would throw away a real
defect.

| Step | Model |
| --- | --- |
| Core lenses, correctness of blockers and should-fix | session model |
| Other lenses, relevance, proof, mutation, re-review reconciliation | Sonnet |
| Mechanical lens, dedup | Haiku, low effort |

`max` puts everything on the session model, for a request where a missed defect is expensive.

## The script on its own

`skills/vet/scripts/vet.py` works without the skill:

```bash
vet.py status  <URL>                      # head, threads, staged drafts and their fingerprint; writes nothing
vet.py check   <URL> review.json          # every refusal above; writes nothing
vet.py draft   <URL> review.json          # stage, then read back and verify the anchors
vet.py discard <URL>                      # delete your staged drafts
vet.py publish <URL> --expect <fp>        # publish, only if the staged drafts still match that fingerprint
vet.py coderabbit <worktree> --base <sha> --out cr.txt [--skip]
```

The `review.json` format is in the script's docstring.

## Known limits

- **GitHub thread replies.** The script cannot stage replies to existing threads; they go in the
  summary with a link.
- **GitLab multi-line anchors.** Comments anchor on one line; a suggestion widens with
  `suggestion:-a+b`.
- **Very large or collapsed files.** GitHub sends no patch and GitLab an empty diff for them, so
  nothing in them can be anchored; the check says so rather than calling the line wrong.
- **More than 100 GitHub review threads.** Only the first 100 are read, and the script says so.

## Tests

```bash
python3 -I tests/test_vet.py
```

The tests run `vet.py` against a fake `gh` and `glab` that keep draft state and log every write.
Most of them assert on what was **not** written. The draft, read-back and discard paths have also
been exercised against live GitLab and GitHub requests.

Why each rule exists: [`WHY-IT-EXISTS.md`](WHY-IT-EXISTS.md).

## License

MIT
