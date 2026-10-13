---
allowed-tools: Bash(python3 -I ${CLAUDE_PLUGIN_ROOT}/skills/vet/scripts/vet.py status *)
argument-hint: <MR/PR URL>
description: What is on a merge or pull request now - its head, every thread, and the drafts you have staged
---

## Where the request stands

!`python3 -I ${CLAUDE_PLUGIN_ROOT}/skills/vet/scripts/vet.py status $ARGUMENTS`

## Your task

Summarise the above for the user: the head SHA, the open threads (ours marked), and every
staged draft with its file and line. Drafts are visible to the user alone until published.

Say nothing about publishing unless the user asks. If they want the staged drafts sent,
that is `/vet-publish`.
