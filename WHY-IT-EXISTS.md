# Why it exists

Every rule in `SKILL.md` and every refusal in `vet.py` came from a review that went wrong
without it. This file is that list. A rule that cannot point at one of these is a candidate for
deletion, not for keeping.

## Findings

**A finding is reproduced by a command, or it is a question.** Review agents reason well and
are still wrong in a steady fraction of cases. Another agent reasoning about the same claim
shares the blind spot. A failing test, a script or a grep that comes back non-empty does not.
→ *The Workflow, step Prove*

**Correctness gates, relevance only removes.** An earlier version let a finding survive when
either of two verifiers did not refute it. One verifier checked whether the claim was true; the
other checked whether it was worth a comment and assumed it was true. So a refuted claim
survived whenever the topic was relevant. That was found in a design review, not in a published
comment, which is the point of writing it down. → *The Workflow, step Verify*

**Dedup before verification.** Without it, two lenses reporting one defect got two
verifications, and they came back with opposite verdicts on the same line. → *step Dedup*

**Deleting each added behaviour finds what reading cannot.** A branch moving a list to keyset
pagination read as correct line by line, with every gate green and 100% coverage. Deleting each
part of the cursor comparison in turn showed that two of three could be removed with every test
still passing. The same pass killed a third finding, already written down, because removing that
part turned eleven tests red. Coverage said nothing: every deleted line ran, none was asserted on.
→ *step Mutation*

**A mutation that did not apply looks exactly like an untested path.** An edit that silently
produced an empty change reported the same green count, and would have become a false
"untested" finding. Hence `git diff --stat` before every run. → *step Mutation*

**A baseline separates old defects from new ones.** A lint job failing on a file the change never
touched reads as the change's fault until the gate is run on the base. → *step 2*

## Re-reviews

**The second round starts blind.** A second round reused the first round's wording instead of
re-running it, and two findings were wrong. One cited a path that had never existed. Every grep
scoped to that path came back empty, and the emptiness read as confirmation. → *Re-review, 1*

**"Fixed in `<sha>`" is checked against the request's head.** Threads were resolved with fixes
pushed to a different branch. Merging at that point would have shipped none of them.
→ *Re-review, 3*

**No output crosses a round.** An old paste is indistinguishable from a fresh one, and stops
being evidence the moment the tree moves under it. → *step 6*

## Git

**Fetch the synthetic head ref, with `--no-prune`.** A fork's branch is not in `origin`, so a
plain fetch brought nothing, and every later ref failed with `unknown revision`. With
`fetch.prune` set, an explicit-refspec fetch also pruned `origin/<target>`. And a later
`--prune` from another session removed the fetched refs, so a collision check merged nothing and
reported zero conflicts. → *step 1*

**`git merge-tree` exit 1 is not always a conflict.** It also means "not something we can merge".
Phantom conflicts nearly went into a review that way. → *step 2*

**A clean merge is not clean when the trunk generates files.** A branch had hand-edited a
generated catalog; the merge was textually clean, and the drift check failed on the merged tree.
→ *step 2*

**Short SHAs are copied, never typed.** A retyped SHA read right through five files and
resolved to nothing. → *step 6*

## The forge

**JSON bodies, never `--form`.** `glab api --form` reads a value starting with `@` as a file to
upload, so a comment opening with an @mention broke. `-f position[...]` returned 201 with the
position silently dropped, which left an "inline" comment floating at the top of the request.
→ *`GitLab._send`*

**Every staged draft is read back.** The dropped position above was only visible by reading the
note back. On GitHub, a pending review's comments come back with `line` null and only their
position in the patch. The read-back failed against the live API until positions were mapped to
lines. → *`draft`*

**Drafts, and publishing as a separate act.** A first version posted directly. Writing to
somebody else's request is not something to undo, and the person running the review wants to
read it first. Drafts cost nothing to discard. → *The contract*

**`publish --expect`.** Between staging and publishing, the user may edit, delete or add drafts
in the web UI. Publishing "whatever is there" would send something nobody approved.
→ *Publishing*

**One message's worth of noise.** A review posted as a summary that retold every inline comment,
with metadata and folded baselines, came to about 700 words and six notifications per request,
and was read as noise. The summary now holds only what cannot be anchored. → *step 7*

**Findings go to disk as they are found.** A run reviewing several requests kept the findings
in the conversation to write them at the end. A context compaction landed first.
→ *Where a run keeps its files*

## Cost

**Model tiers.** Six lenses with two verifiers per finding came to 74 agents and about 4.7
million subagent tokens for one request. Dedup, a single verifier per nit, and cheaper models on
steps whose output a command checks bring that down without touching the steps where a wrong
verdict would drop a real defect. → *step 4*

**CodeRabbit is optional and quiet.** An earlier version made the CodeRabbit pass mandatory,
so a machine without the CLI had no way through it. Its output carried update banners and telemetry stack traces into the prompt. The script now turns
every way it cannot run into one line and strips the noise. → *step 1*
