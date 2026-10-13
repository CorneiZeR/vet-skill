---
argument-hint: <MR/PR URL>
description: Publish the drafts staged on a merge or pull request, exactly as many as are there now
---

## What is staged right now

!`python3 -I "${CLAUDE_PLUGIN_ROOT}/skills/vet/scripts/vet.py" status $ARGUMENTS`

## Your task

The user invoked this command, which is their word to publish **the drafts listed above** on
this one request. It is not a go-ahead for any other request.

1. Show the user the staged drafts above, one line each, and their count.
2. If there are none, say so and stop.
3. Otherwise run, with the count from the listing above:

   ```
   python3 -I "${CLAUDE_PLUGIN_ROOT}/skills/vet/scripts/vet.py" publish $ARGUMENTS --expect <count>
   ```

   A refusal means the drafts changed since the listing (edited, deleted or added in the web
   UI). Show the user the new listing it prints and ask again; never retry with the new count
   on your own.

GitHub publishes the pending review as a plain comment. Approving or requesting changes stays
with the user, in the web UI.
