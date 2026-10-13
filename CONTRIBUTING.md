# Contributing

## A rule needs a failure behind it

A proposed rule is judged on where it came from before its wording:

- **What went wrong**, concretely: the command, the output, the moment it was believed.
- **What it cost**: a wrong comment on somebody's request, a missed defect, a wasted run.
- **Why the existing rules did not catch it.** If one did and was skipped, the fix is usually
  to make it enforceable in `vet.py` rather than to add another sentence.

Every rule lives in two places: the instruction in `skills/vet/SKILL.md`, and the failure it
came from in `WHY-IT-EXISTS.md`. A change that adds one without the other is incomplete.

## The script

`vet.py` is standard library only and talks to the forges through `glab` and `gh`.

- **Reads may be retried; writes may not.** A retried write is a second write.
- **A guard refuses before the first write**, and its refusal names every problem at once.
- **Every write is read back.** The forge can accept a request and quietly drop part of it.
- **A new guard comes with a test that fails without it.** Delete the guard, watch the test go
  red, and put the guard back.

```bash
python3 -I tests/test_vet.py
```

## The manifest

`SHA256SUMS.txt` covers the files a user installs. Regenerate it whenever one of them changes:

```bash
shasum -a 256 .claude-plugin/plugin.json commands/*.md skills/vet/SKILL.md skills/vet/workflow.js \
  skills/vet/scripts/vet.py README.md WHY-IT-EXISTS.md CONTRIBUTING.md LICENSE > SHA256SUMS.txt
shasum -a 256 -c SHA256SUMS.txt
```

A plugin's cache is keyed by its version, so a change users should receive also moves
`version` in `.claude-plugin/plugin.json`.

## Style

Plain English, sentences that survive being read once. A number is either measured at the
moment of writing or left out.
