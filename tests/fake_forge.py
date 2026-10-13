#!/usr/bin/env python3
"""A `gh` or `glab` that answers from a state file and logs every write into it.

Linked into a scratch PATH under both names. FAKE_STATE names the JSON state:

    routes        {"GET <path>": response} for plain reads
    drafts        GitLab draft notes, kept up to date by writes
    reviews       GitHub reviews, each {"id", "state", "user", "body", "comments"}
    fail_write    the 1-based number of the write that should fail, if any
    writes        appended: {"method", "path", "payload"}

The tests assert on `writes`: a guard is only visible as a write that never happened.
"""

import json
import os
import re
import sys
from pathlib import Path


def _position(state: dict, reviews_path: str, path: str, line: int) -> int | None:
    """The patch position GitHub would report for a new-side line, counted after the first hunk header."""
    files_key = f"GET {reviews_path.rsplit('/reviews', 1)[0]}/files?per_page=100"
    entry = next(f for f in state["routes"][files_key] if f["filename"] == path)
    position, new_line, started = 0, 0, False
    for raw in entry["patch"].splitlines():
        if started:
            position += 1
        if match := re.match(r"^@@ -\d+(?:,\d+)? \+(\d+)", raw):
            started, new_line = True, int(match[1])
            continue
        if raw.startswith("-"):
            continue
        if new_line == line:
            return position
        new_line += 1
    return None


def main() -> int:
    state_path = Path(os.environ["FAKE_STATE"])
    state = json.loads(state_path.read_text())
    args = sys.argv[2:] if sys.argv[1:2] == ["api"] else sys.argv[1:]
    method, path, paginate, output, slurp, payload = "GET", None, False, "json", False, None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg in ("--hostname", "--header", "-H", "-f", "-F", "--raw-field", "--field"):
            i += 2
            continue
        if arg == "--method":
            method = args[i + 1]
            i += 2
            continue
        if arg == "--output":
            output = args[i + 1]
            i += 2
            continue
        if arg == "--input":
            payload = json.loads(sys.stdin.read())
            i += 2
            continue
        if arg == "--paginate":
            paginate = True
        elif arg == "--slurp":
            slurp = True
        elif path is None:
            path = arg
        i += 1

    def save() -> None:
        state_path.write_text(json.dumps(state))

    def answer(body: object) -> int:
        if paginate and output == "ndjson":
            print("\n".join(json.dumps(item) for item in body))
        elif paginate and slurp:
            print(json.dumps([body]))
        else:
            print(json.dumps(body))
        return 0

    if method != "GET":
        state.setdefault("writes", []).append({"method": method, "path": path, "payload": payload})
        if state.get("fail_write") == len(state["writes"]):
            save()
            print("HTTP 500: boom", file=sys.stderr)
            return 1

    bare = path.split("?")[0] if path else ""
    try:
        if re.search(r"/draft_notes$", bare):
            if method == "GET":
                return answer(state.get("drafts", []))
            note = {"id": 100 + len(state.get("writes", [])), "note": payload["note"]}
            if "position" in payload and not state.get("drop_position"):
                note["position"] = payload["position"]
            state.setdefault("drafts", []).append(note)
            return answer(note)
        if match := re.search(r"/draft_notes/(\d+)$", bare):
            state["drafts"] = [d for d in state.get("drafts", []) if d["id"] != int(match[1])]
            return answer({})
        if bare.endswith("/draft_notes/bulk_publish"):
            state["drafts"] = []
            return answer({})
        if bare.endswith("/reviews"):
            if method == "GET":
                return answer([{k: v for k, v in r.items() if k != "comments"} for r in state.get("reviews", [])])
            # Like GitHub: a pending comment comes back with `line` null and only its patch position.
            comments = [
                dict(c, id=950 + n, line=None, position=_position(state, bare, c["path"], c["line"]))
                for n, c in enumerate(payload.get("comments", []))
            ]
            review = {
                "id": 900,
                "state": "PENDING" if "event" not in payload else "COMMENTED",
                "user": {"login": state["me"]},
                "body": payload.get("body", ""),
                "comments": comments,
            }
            state.setdefault("reviews", []).append(review)
            return answer({k: v for k, v in review.items() if k != "comments"})
        if match := re.search(r"/reviews/(\d+)/comments$", bare):
            review = next(r for r in state["reviews"] if r["id"] == int(match[1]))
            return answer(review["comments"])
        if match := re.search(r"/reviews/(\d+)/events$", bare):
            for review in state["reviews"]:
                if review["id"] == int(match[1]):
                    review["state"] = "COMMENTED"
            return answer({})
        if match := re.search(r"/reviews/(\d+)$", bare):
            state["reviews"] = [r for r in state["reviews"] if r["id"] != int(match[1])]
            return answer({})
        key = f"{method} {path}"
        if key not in state.get("routes", {}):
            print(f"fake forge: no route for {key}", file=sys.stderr)
            return 1
        return answer(state["routes"][key])
    finally:
        save()


if __name__ == "__main__":
    sys.exit(main())
