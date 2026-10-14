# Why it exists

Every rule in `SKILL.md` and every refusal in `vet.py` is here for one of two reasons. Either
something went wrong without it while this skill was being built and used, or the mechanism it
guards against is plain enough to state on its own. Both kinds are below, each with a pointer to
where the rule lives. A rule that has neither is a candidate for deletion.

## Findings

**A finding is reproduced by a command, or it is a question.** A review agent that reasons its
way to a defect is sometimes wrong. A second agent reasoning about the same claim tends to share
the blind spot. A failing test, a script or a grep that comes back non-empty does not.
→ *The Workflow, step Prove*

**Correctness gates, relevance only removes.** The first template let a finding survive when
either of two verifiers failed to refute it. One verifier checked whether the claim was true; the
other checked whether it was worth a comment and assumed it was true. So a refuted claim survived
whenever its topic was relevant. → *step Verify*

**A verifier that died is not a refutation.** In 0.1.0 a missing correctness verdict marked the
finding as not surviving, with nothing in the log. A real defect could vanish because an agent
did. 0.2.1 overcorrected: such a finding survived even when relevance called it noise, so a
dead correctness verifier let noise through. It now stays as a question only if relevance keeps
it, and the log says which. → *step Verify*

**Dedup before verification.** Without it, two lenses that reported one defect got two
verifications, which came back with opposite verdicts on the same line. → *step Dedup*

**Deleting each added behaviour finds what reading cannot.** A correct implementation and an
untested one look identical on the page, and line coverage counts executed lines, not asserted
ones. Removing one behaviour and re-running the suite is the only cheap way to tell them apart.
A red run also kills any claim that the behaviour lacks tests. → *step Mutation*

**`git diff --stat` before every mutation run.** A mutation that silently failed to apply
reports exactly the same green count as a genuinely untested path. → *step Mutation*

**A mutation is undone with `reset --hard` and `clean`.** In 0.1.0 it was undone with
`checkout -- <file>`, which restored only the edited file and left behind whatever the run
created, so the next mutation ran on a dirty tree. → *step Mutation*

**A baseline separates old defects from new ones.** The first live run met a red lint job. The
file it failed on was not in the change at all; only running the gate against the branch showed
that the defect came from the base. → *step 2*

## Re-reviews

**The second round starts blind.** Reading the previous round first turns a review into a
checklist walk. Known findings get re-confirmed with their old commands, and an old command can
confirm by finding nothing: a grep scoped to a path that has since moved. → *Re-review, 1*

**"Fixed in `<sha>`" is checked against the request's head.** Review threads were resolved with
the fixes pushed to a different branch. Merging at that point would have shipped none of them.
→ *Re-review, 3*

**No output crosses a round.** A pasted output from an earlier tip is indistinguishable from a
fresh one, and stops being evidence the moment the tree moves. → *step 6*

## Git

**Fetch the synthetic head ref, with `--no-prune`.** A fork's branch is not in `origin`, so a
plain fetch of the branch brings nothing. `pull/<n>/head` and `merge-requests/<n>/head` exist for
every request. Where `fetch.prune` is set, an explicit-refspec fetch also prunes the rest of
`refs/remotes/origin/`, the target branch included. A later `--prune` from any session removes
these refs again. → *step 1*

**`git merge-tree` exit 1 is not always a conflict.** The same exit code means "not something we
can merge", so only `CONFLICT` lines count. → *step 2*

**A clean merge is not clean when the trunk generates files.** A textual merge says nothing about
whether a generator still agrees with its output; only the drift gate on the merged tree does.
→ *step 2*

## The forge

**JSON bodies, never `--form`.** `glab api --form` reads a value starting with `@` as a file to
upload, so a comment opening with an @mention breaks. `-f position[...]` returned 201 with the
position silently dropped, which left an "inline" comment floating at the top of the request.
→ *`GitLab._send`*

**Every staged draft is read back.** The dropped position above was only visible by reading the
note back. On GitHub, a pending review's comments come back with `line` null and only their
position in the patch. The first live run of the read-back failed until positions were mapped to
lines. → *`draft`*

**An empty patch is "no patch", not a bad anchor.** GitLab returns an empty `diff` for a
collapsed or too-large file. 0.1.0 reported every comment on such a file as "not an added line",
which sent the reviewer hunting for the wrong problem. A rename or a mode change with no content
change sends an empty `diff` too, and that one is complete, but adds no line to anchor on, so
`check` points at the summary as for a missing patch, saying why. GitLab 18.4 added `collapsed`
and `too_large` flags, which tell the two apart and are trusted when present. An older GitLab
sends neither, and there a collapsed rename with edits looks like a pure one, so an empty `diff`
still means "no patch". → *`check`*

**Drafts, and publishing as a separate act.** The predecessor of this skill posted directly.
Writing to somebody else's request cannot be taken back, and the person running the review
should read it first. Drafts cost nothing to discard. → *The contract*

**`publish --expect` takes a fingerprint, not a count.** Between staging and publishing, drafts
can be edited, deleted or added in the web UI. 0.1.0 compared only the number of drafts, so an
edited draft, or one deleted and another added, passed. → *Publishing*

**Only this repository's links must point at the tip.** 0.1.0 refused every `/blob/<sha>/` link
that was not the reviewed head. That included permalinks into other repositories, which are
exactly the evidence the proof rule asks for. → *step 7*

**A link on a branch is stale too.** 0.2.1 matched only hex refs, so `blob/main/` into this
repository passed, though the line it points at moves with every push. The repository is matched
as a prefix, with scheme, `www.` and case ignored: a `http://www.` spelling is still this
repository, and an owner named `blob` beside a hex-looking repository name does not end the
match early, as a pattern accepting any ref would. A link inside a code block or a code span is a
quote of the file, as in a `suggestion`, and is not checked. → *step 7*

**The summary does not retell the inline comments.** Every inline comment already notifies the
author and sits on its line. A summary that repeats them doubles the reading and buries the few
things that have no line to sit on. → *step 7*

**The commands allow one script, not an interpreter.** 0.1.0 had `Bash(python3:*)` on
`/vet-status`, which allows any `python3` call. `/vet-publish` had no `allowed-tools` at all, and
a pre-run line without permission aborts the whole command. A headless run against a copy with
the rule removed confirmed the abort. → *`commands/`*

## Runs

**Agents that run things take turns.** The first template started the mutation pass alongside
the lenses and every prover in parallel, each in its own worktree. That meant as many concurrent
test suites as there were findings. Reading agents stay parallel; anything that runs a suite or a
build goes through one queue. → *step 4*

**Findings go to disk as they are found.** Whatever exists only in the conversation is lost to
a context compaction. → *Where a run keeps its files*

## Cost

**Model tiers.** The predecessor of this skill, with six lenses and two verifiers per finding, used
74 agents and about 4.7 million subagent tokens on one request. Dedup, one verifier per nit, and
cheaper models on the steps whose output a command checks brought the first live run down to 22
agents and about 1.06 million tokens. The session model stays where a wrong verdict would drop a
real defect. → *step 4*

**CodeRabbit is optional and quiet.** The predecessor made the CodeRabbit pass mandatory, so a
machine without the CLI had no way through it. The CLI's output also carried update banners and
telemetry stack traces into the prompt: 77 lines, of which 29 were the review. The script now
turns every way it cannot run into one line and strips the noise. → *step 1*
