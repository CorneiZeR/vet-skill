#!/usr/bin/env python3
"""Read a merge or pull request, and stage a review on it as drafts only the reviewer can see.

    vet.py status  <request URL>
    vet.py check   <request URL> <review.json>
    vet.py draft   <request URL> <review.json> [--replace-drafts]
    vet.py discard <request URL>
    vet.py publish <request URL> --expect <fingerprint>
    vet.py coderabbit <worktree> --base <merge base> --out <file> [--skip]

The forge is picked from the URL, and `glab` or `gh` does the talking with whatever login it
already has. `status` and `check` write nothing. `draft` stages inline comments and one summary
as drafts: draft notes on GitLab, one pending review on GitHub. Only `publish` makes anything
visible to other people, and it refuses unless the staged drafts are exactly the ones the caller
last saw: `status` prints a fingerprint of them (ids, anchors and full bodies), and `publish`
needs it back.

review.json:

    {
      "head_sha": "<full SHA the review was made against>",
      "summary": "<markdown, optional>",
      "comments": [{"path": "a/b.py", "line": 42, "start_line": 40, "body": "..."}],
      "replies": [{"thread": "<GitLab discussion id>", "body": "..."}]
    }

`start_line` is GitHub only; `replies` are GitLab only.
"""

from __future__ import annotations

import abc
import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import Any

_READ_ATTEMPTS = 3
_READ_RETRY_SECONDS = 2.0
_GITHUB_BODY_LIMIT = 65_000
_EXCERPT_WIDTH = 100
_EXIT_REFUSED = 1
_EXIT_FORGE = 3

_GITLAB_URL = re.compile(r"^https://(?P<host>[^/]+)/(?P<project>.+?)/-/merge_requests/(?P<number>\d+)")
_GITHUB_URL = re.compile(r"^https://(?P<host>[^/]+)/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)")
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")
_ATTRIBUTION = re.compile(r"Generated with \[?Claude|Co-Authored-By:\s*Claude|\N{ROBOT FACE}", re.IGNORECASE)
_URL = re.compile(r"https?://[^\s()<>\[\]\"']+", re.IGNORECASE)
# A fence, in a list or on its marker line too, or a code span quotes a file, and its links are the file's.
#  A fence closes on a bare run of its own character at least as long, and only a backtick fence
#  refuses a backtick in its info string, as in CommonMark:
#  https://spec.commonmark.org/0.31.2/#fenced-code-blocks
_CODE = re.compile(
    r"^[ \t]*(?:(?:[-*+]|\d{1,9}[.)])[ \t]+)*"
    r"(?:(?P<ticks>```+)[^`\n]*$.*?^[ \t]*(?P=ticks)`*|(?P<tildes>~~~+)[^\n]*$.*?^[ \t]*(?P=tildes)~*)[ \t\r]*$"
    r"|(?<!\\)`[^`\n]*`",
    re.DOTALL | re.MULTILINE,
)
# Scheme, `www.` and case do not change which repository a link opens.
_REPOSITORY_SPELLING = re.compile(r"^https?://(?:www\.)?", re.IGNORECASE)
_FINGERPRINT = re.compile(r"\d+-[0-9a-f]{12}")
_REQUEST_SUFFIX = re.compile(r"(?:/-/merge_requests|/pull)/\d+.*$")
_SUGGESTION_FENCE = re.compile(r"^\s*```suggestion(?P<range>\S*)\s*$", re.MULTILINE)
_CODERABBIT_NOISE = re.compile(
    r"posthog|^\s+at |^\s*(path|errno|code):|Unable to connect|elapsed$|update available|coderabbit update"
    r"|^[\u2500-\u257f\s]*$|^Notice: Detected|^\s*\(\\\(\\|^\s*\(\u2022",
    re.IGNORECASE,
)


class ForgeError(Exception):
    """A forge call failed; the message carries the command and what it printed."""


class Refused(Exception):
    """A guard said no; nothing was written after it."""


@dataclasses.dataclass(frozen=True)
class Request:
    url: str
    title: str
    is_open: bool
    author: str
    source: str
    target: str
    head_sha: str
    fork: bool


@dataclasses.dataclass(frozen=True)
class Thread:
    id: str
    url: str
    author: str
    resolved: bool | None
    path: str | None
    line: int | None
    excerpt: str


@dataclasses.dataclass(frozen=True)
class Draft:
    id: str
    path: str | None
    line: int | None
    body: str

    @property
    def excerpt(self) -> str:
        return _excerpt(self.body)


def fingerprint(drafts: list[Draft]) -> str:
    """`<count>-<hash>` over every draft's id, anchor and full body: an edit changes it, not only a count."""
    digest = hashlib.sha256()
    for draft in sorted(drafts, key=lambda d: d.id):
        digest.update(f"{draft.id}\0{draft.path}\0{draft.line}\0{draft.body}\0".encode())
    return f"{len(drafts)}-{digest.hexdigest()[:12]}"


@dataclasses.dataclass
class FileLines:
    """Which new-side lines of one file the diff shows, and in which hunk."""

    old_path: str
    added: set[int] = dataclasses.field(default_factory=set)
    hunk_of: dict[int, int] = dataclasses.field(default_factory=dict)
    patch_missing: bool = False
    # GitHub reports a pending comment by its position in the patch, with `line` left null.
    line_at: dict[int, int] = dataclasses.field(default_factory=dict)


def parse_patch(patch: str, *, old_path: str) -> FileLines:
    lines = FileLines(old_path=old_path)
    hunk = -1
    new_line = 0
    position = 0
    for raw in patch.splitlines():
        match = _HUNK.match(raw)
        if hunk >= 0:
            position += 1
        if match:
            hunk += 1
            new_line = int(match["start"])
            continue
        if hunk < 0 or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            lines.added.add(new_line)
        elif raw.startswith("-"):
            continue
        lines.hunk_of[new_line] = hunk
        lines.line_at[position] = new_line
        new_line += 1
    return lines


def _excerpt(text: str) -> str:
    first = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first if len(first) <= _EXCERPT_WIDTH else first[: _EXCERPT_WIDTH - 1] + "\N{HORIZONTAL ELLIPSIS}"


def _run(command: list[str], *, stdin: str | None = None, write: bool = False) -> str:
    """Run a forge CLI. Reads are retried; a write never is, since a retried write is a second write."""
    attempts = 1 if write else _READ_ATTEMPTS
    for attempt in range(1, attempts + 1):
        completed = subprocess.run(command, input=stdin, capture_output=True, text=True, check=False)  # noqa: S603
        if completed.returncode == 0:
            return completed.stdout
        if attempt < attempts:
            time.sleep(_READ_RETRY_SECONDS)
    message = (completed.stderr.strip() or completed.stdout.strip())[:600]
    raise ForgeError(f"{' '.join(command[:4])} ... exited {completed.returncode}: {message}")


def _json(text: str) -> Any:
    return json.loads(text) if text.strip() else None


class Forge(abc.ABC):
    name: str

    @abc.abstractmethod
    def request(self) -> Request: ...

    @abc.abstractmethod
    def username(self) -> str: ...

    @abc.abstractmethod
    def threads(self) -> list[Thread]: ...

    @abc.abstractmethod
    def drafts(self) -> list[Draft]: ...

    @abc.abstractmethod
    def files(self) -> dict[str, FileLines]: ...

    @abc.abstractmethod
    def stage(self, review: dict[str, Any], *, request: Request, files: dict[str, FileLines]) -> None: ...

    @abc.abstractmethod
    def discard(self) -> int: ...

    @abc.abstractmethod
    def publish(self) -> None: ...

    def forge_problems(self, review: dict[str, Any]) -> list[str]:
        return []


class GitLab(Forge):
    name = "GitLab"

    def __init__(self, host: str, project: str, number: int) -> None:
        self._host = host
        self._base = f"projects/{urllib.parse.quote(project, safe='')}/merge_requests/{number}"
        self._mr: dict[str, Any] | None = None

    def _api(self, *arguments: str) -> list[str]:
        return ["glab", "api", "--hostname", self._host, *arguments]

    def _get(self, path: str) -> Any:
        return _json(_run(self._api(path)))

    def _get_all(self, path: str) -> list[dict[str, Any]]:
        # ndjson: one item per line across every page, so pages need no merging.
        text = _run(self._api("--paginate", "--output", "ndjson", path))
        return [json.loads(line) for line in text.splitlines() if line.strip()]

    def _send(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        # JSON body, never --form: a --form value starting with `@` is read as a file to upload,
        # so a comment opening with an @mention would fail or attach the wrong thing.
        command = self._api("--method", method, path)
        if payload is None:
            return _json(_run(command, write=True))
        command += ["--input", "-", "--header", "Content-Type: application/json"]
        return _json(_run(command, stdin=json.dumps(payload), write=True))

    def _merge_request(self) -> dict[str, Any]:
        if self._mr is None:
            self._mr = self._get(self._base)
        return self._mr

    def request(self) -> Request:
        mr = self._merge_request()
        return Request(
            url=mr["web_url"],
            title=mr["title"],
            is_open=mr["state"] == "opened",
            author=mr["author"]["username"],
            source=mr["source_branch"],
            target=mr["target_branch"],
            head_sha=mr["sha"],
            fork=mr.get("source_project_id") != mr.get("target_project_id"),
        )

    def username(self) -> str:
        return self._get("user")["username"]

    def threads(self) -> list[Thread]:
        web_url = self._merge_request()["web_url"]
        threads = []
        for discussion in self._get_all(f"{self._base}/discussions?per_page=100"):
            notes = [note for note in discussion["notes"] if not note.get("system")]
            if not notes:
                continue
            first = notes[0]
            position = first.get("position") or {}
            threads.append(
                Thread(
                    id=discussion["id"],
                    url=f"{web_url}#note_{first['id']}",
                    author=first["author"]["username"],
                    resolved=first.get("resolved") if first.get("resolvable") else None,
                    path=position.get("new_path"),
                    line=position.get("new_line"),
                    excerpt=_excerpt(first["body"]),
                )
            )
        return threads

    def drafts(self) -> list[Draft]:
        # Draft notes are visible to their author only, so every one listed is ours.
        drafts = []
        for note in self._get_all(f"{self._base}/draft_notes?per_page=100"):
            position = note.get("position") or {}
            drafts.append(
                Draft(
                    id=str(note["id"]),
                    path=position.get("new_path"),
                    line=position.get("new_line"),
                    body=note.get("note", ""),
                )
            )
        return drafts

    def files(self) -> dict[str, FileLines]:
        files = {}
        for entry in self._get_all(f"{self._base}/diffs?per_page=100"):
            if entry.get("deleted_file"):
                continue
            diff = entry.get("diff") or ""
            lines = parse_patch(diff, old_path=entry["old_path"])
            lines.patch_missing = _gitlab_patch_withheld(entry, diff=diff)
            files[entry["new_path"]] = lines
        return files

    def forge_problems(self, review: dict[str, Any]) -> list[str]:
        problems = []
        for comment in review.get("comments", []):
            if "start_line" in comment:
                problems.append(
                    f"{comment['path']}:{comment['line']}: GitLab ranges are not staged by this script; "
                    "anchor on one line and widen a suggestion with ```suggestion:-a+b instead"
                )
        known = {thread.id for thread in self.threads()} if review.get("replies") else set()
        for reply in review.get("replies", []):
            if reply.get("thread") not in known:
                problems.append(f"reply to thread {reply.get('thread')!r}: no such discussion on this request")
        return problems

    def stage(self, review: dict[str, Any], *, request: Request, files: dict[str, FileLines]) -> None:
        refs = self._merge_request()["diff_refs"]
        payloads = []
        for comment in review.get("comments", []):
            payloads.append(
                {
                    "note": comment["body"],
                    "position": {
                        "position_type": "text",
                        "base_sha": refs["base_sha"],
                        "start_sha": refs["start_sha"],
                        "head_sha": refs["head_sha"],
                        "old_path": files[comment["path"]].old_path,
                        "new_path": comment["path"],
                        "new_line": comment["line"],
                    },
                }
            )
        payloads += [{"note": r["body"], "in_reply_to_discussion_id": r["thread"]} for r in review.get("replies", [])]
        if review.get("summary"):
            payloads.append({"note": review["summary"]})
        staged = []
        for payload in payloads:
            try:
                staged.append(str(self._send("POST", f"{self._base}/draft_notes", payload)["id"]))
            except ForgeError as error:
                raise ForgeError(
                    f"{error}\nstaged before the failure: {staged or 'nothing'}; `discard` removes them"
                ) from error
        _say(f"staged {len(staged)} draft notes: {', '.join(staged)}")
        anchored = {(d.path, d.line) for d in self.drafts() if d.id in staged}
        lost = [c for c in review.get("comments", []) if (c["path"], c["line"]) not in anchored]
        if lost:
            raise ForgeError(
                "staged but not anchored where asked (the forge dropped the position): "
                + ", ".join(f"{c['path']}:{c['line']}" for c in lost)
            )

    def discard(self) -> int:
        drafts = self.drafts()
        for draft in drafts:
            self._send("DELETE", f"{self._base}/draft_notes/{draft.id}")
        return len(drafts)

    def publish(self) -> None:
        self._send("POST", f"{self._base}/draft_notes/bulk_publish")
        left = self.drafts()
        if left:
            raise ForgeError(f"bulk_publish returned, yet {len(left)} drafts are still staged")


class GitHub(Forge):
    name = "GitHub"

    def __init__(self, host: str, owner: str, repo: str, number: int) -> None:
        self._host = host
        self._owner = owner
        self._repo = repo
        self._number = number
        self._base = f"repos/{owner}/{repo}/pulls/{number}"
        self._pr: dict[str, Any] | None = None
        self._me: str | None = None

    def _api(self, *arguments: str) -> list[str]:
        return ["gh", "api", "--hostname", self._host, *arguments]

    def _get(self, path: str) -> Any:
        return _json(_run(self._api(path)))

    def _get_all(self, path: str) -> list[dict[str, Any]]:
        pages = _json(_run(self._api("--paginate", "--slurp", path))) or []
        return [item for page in pages for item in page]

    def _send(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        command = self._api("--method", method, path)
        if payload is None:
            return _json(_run(command, write=True))
        return _json(_run([*command, "--input", "-"], stdin=json.dumps(payload), write=True))

    def _pull(self) -> dict[str, Any]:
        if self._pr is None:
            self._pr = self._get(self._base)
        return self._pr

    def request(self) -> Request:
        pr = self._pull()
        head_repo = (pr["head"].get("repo") or {}).get("full_name")
        return Request(
            url=pr["html_url"],
            title=pr["title"],
            is_open=pr["state"] == "open",
            author=pr["user"]["login"],
            source=pr["head"]["ref"],
            target=pr["base"]["ref"],
            head_sha=pr["head"]["sha"],
            fork=head_repo != pr["base"]["repo"]["full_name"],
        )

    def username(self) -> str:
        if self._me is None:
            self._me = self._get("user")["login"]
        return self._me

    def threads(self) -> list[Thread]:
        query = (
            "query($owner:String!,$name:String!,$number:Int!){repository(owner:$owner,name:$name){"
            "pullRequest(number:$number){reviewThreads(first:100){pageInfo{hasNextPage} nodes{id isResolved path line "
            "comments(first:1){nodes{author{login} body url}}}}}}}"
        )
        data = _json(
            _run(
                self._api(
                    "graphql",
                    "-f",
                    f"query={query}",
                    "-F",
                    f"owner={self._owner}",
                    "-F",
                    f"name={self._repo}",
                    "-F",
                    f"number={self._number}",
                )
            )
        )
        review_threads = data["data"]["repository"]["pullRequest"]["reviewThreads"]
        if review_threads["pageInfo"]["hasNextPage"]:
            _say("note: more than 100 review threads; only the first 100 are listed")
        threads = []
        for node in review_threads["nodes"]:
            comments = node["comments"]["nodes"]
            if not comments:
                continue
            first = comments[0]
            threads.append(
                Thread(
                    id=node["id"],
                    url=first["url"],
                    author=(first.get("author") or {}).get("login", "ghost"),
                    resolved=node["isResolved"],
                    path=node.get("path"),
                    line=node.get("line"),
                    excerpt=_excerpt(first["body"]),
                )
            )
        for comment in self._get_all(f"repos/{self._owner}/{self._repo}/issues/{self._number}/comments?per_page=100"):
            threads.append(
                Thread(
                    id=str(comment["id"]),
                    url=comment["html_url"],
                    author=comment["user"]["login"],
                    resolved=None,
                    path=None,
                    line=None,
                    excerpt=_excerpt(comment["body"]),
                )
            )
        return threads

    def _pending(self) -> dict[str, Any] | None:
        me = self.username()
        pending = [
            r
            for r in self._get_all(f"{self._base}/reviews?per_page=100")
            if r["state"] == "PENDING" and r["user"]["login"] == me
        ]
        return pending[0] if pending else None

    def drafts(self) -> list[Draft]:
        review = self._pending()
        if review is None:
            return []
        comments = self._get_all(f"{self._base}/reviews/{review['id']}/comments?per_page=100")
        files = self.files() if any(c.get("line") is None for c in comments) else {}
        drafts = [
            Draft(
                id=str(c["id"]),
                path=c["path"],
                line=c.get("line") or files.get(c["path"], FileLines(old_path="")).line_at.get(c.get("position")),
                body=c["body"],
            )
            for c in comments
        ]
        if review.get("body"):
            drafts.append(Draft(id=f"review {review['id']}", path=None, line=None, body=review["body"]))
        return drafts

    def files(self) -> dict[str, FileLines]:
        files = {}
        for entry in self._get_all(f"{self._base}/files?per_page=100"):
            if entry["status"] == "removed":
                continue
            lines = parse_patch(entry.get("patch") or "", old_path=entry.get("previous_filename", entry["filename"]))
            lines.patch_missing = "patch" not in entry
            files[entry["filename"]] = lines
        return files

    def forge_problems(self, review: dict[str, Any]) -> list[str]:
        problems = []
        if review.get("replies"):
            problems.append(
                "GitHub replies cannot be staged in a pending review by this script; "
                "say it in the summary with a link to the thread"
            )
        for comment in review.get("comments", []):
            for match in _SUGGESTION_FENCE.finditer(comment["body"]):
                if match["range"]:
                    problems.append(
                        f"{comment['path']}:{comment['line']}: GitHub suggestions take a plain ```suggestion fence; "
                        "a range is start_line + line on the comment"
                    )
        return problems

    def stage(self, review: dict[str, Any], *, request: Request, files: dict[str, FileLines]) -> None:
        comments = []
        for comment in review.get("comments", []):
            item = {"path": comment["path"], "line": comment["line"], "side": "RIGHT", "body": comment["body"]}
            if "start_line" in comment:
                item |= {"start_line": comment["start_line"], "start_side": "RIGHT"}
            comments.append(item)
        # No `event`: that is what keeps the review PENDING, visible to its author only.
        payload: dict[str, Any] = {"commit_id": request.head_sha, "comments": comments}
        if review.get("summary"):
            payload["body"] = review["summary"]
        created = self._send("POST", f"{self._base}/reviews", payload)
        if created.get("state") != "PENDING":
            raise ForgeError(f"review {created.get('id')} came back {created.get('state')!r}, not PENDING")
        staged = {(d.path, d.line) for d in self.drafts() if d.path is not None}
        _say(f"staged pending review {created['id']} with {len(staged)} inline comments")
        lost = [c for c in comments if (c["path"], c["line"]) not in staged]
        if lost:
            raise ForgeError(
                "staged but not anchored where asked: "
                + ", ".join(f"{c['path']}:{c['line']}" for c in lost)
                + "; `discard` removes the pending review"
            )

    def discard(self) -> int:
        review = self._pending()
        if review is None:
            return 0
        count = len(self.drafts())
        self._send("DELETE", f"{self._base}/reviews/{review['id']}")
        return count

    def publish(self) -> None:
        review = self._pending()
        if review is None:
            raise ForgeError("no pending review to publish")
        payload = {"event": "COMMENT"}
        if review.get("body"):
            payload["body"] = review["body"]
        self._send("POST", f"{self._base}/reviews/{review['id']}/events", payload)
        if self._pending() is not None:
            raise ForgeError("the review was submitted, yet a pending review is still there")


def forge_for(url: str) -> Forge:
    if match := _GITLAB_URL.match(url):
        return GitLab(match["host"], match["project"], int(match["number"]))
    if match := _GITHUB_URL.match(url):
        return GitHub(match["host"], match["owner"], match["repo"], int(match["number"]))
    raise Refused(f"not a merge or pull request URL: {url}")


def _say(text: str) -> None:
    print(text, flush=True)


def problems_with(
    review: dict[str, Any],
    *,
    forge: Forge,
    request: Request,
    files: dict[str, FileLines],
    threads: list[Thread],
    drafts: list[Draft],
    me: str,
    replacing: bool,
) -> tuple[list[str], list[str]]:
    """Everything that stops a review from being staged, and everything merely worth a look."""
    problems: list[str] = []
    warnings: list[str] = []
    repository = _repository_key(_REQUEST_SUFFIX.sub("", request.url))
    if not request.is_open:
        problems.append(f"{request.url} is not open")
    if review.get("head_sha") != request.head_sha:
        problems.append(
            f"the review was made at {review.get('head_sha')}, the request is at {request.head_sha} now; "
            "review the new head instead of anchoring old findings on it"
        )
    comments = review.get("comments", [])
    if not comments and not review.get("summary") and not review.get("replies"):
        problems.append("nothing to stage")
    if drafts and not replacing:
        problems.append(
            f"{len(drafts)} drafts are already staged on this request; --replace-drafts deletes them, "
            "and only the user can say it may"
        )
    bodies = [("summary", review.get("summary") or "")]
    seen: set[tuple[str, int]] = set()
    open_threads = {(t.path, t.line): t for t in threads if t.path and t.resolved is False}
    for comment in comments:
        where = f"{comment.get('path')}:{comment.get('line')}"
        bodies.append((where, comment.get("body") or ""))
        if not (comment.get("body") or "").strip():
            problems.append(f"{where}: empty body")
        lines = files.get(comment.get("path"))
        if lines is None:
            problems.append(f"{where}: the file is not in this request's diff")
            continue
        if lines.patch_missing:
            problems.append(f"{where}: the forge sends no patch for this file, so no line can be anchored; use the summary")
            continue
        if not lines.added:
            problems.append(f"{where}: the change adds no line to this file, so no line can be anchored; use the summary")
            continue
        if comment.get("line") not in lines.added:
            problems.append(f"{where}: not an added line; anchor on the nearest added line and name the real one")
        start = comment.get("start_line")
        if start is not None and not (
            start < comment["line"] and lines.hunk_of.get(start) == lines.hunk_of.get(comment["line"])
        ):
            problems.append(f"{where}: start_line {start} is not above the line inside the same hunk")
        key = (comment.get("path"), comment.get("line"))
        if key in seen:
            problems.append(f"{where}: two comments on one line; merge them")
        seen.add(key)
        thread = open_threads.get(key)
        if thread and thread.author == me:
            problems.append(f"{where}: our own open thread already sits here ({thread.url}); reply there instead")
        elif thread:
            warnings.append(f"{where}: @{thread.author} already has an open thread here ({thread.url}): {thread.excerpt}")
    bodies += [(f"reply to {r.get('thread')}", r.get("body") or "") for r in review.get("replies", [])]
    for where, body in bodies:
        if _ATTRIBUTION.search(body):
            problems.append(f"{where}: carries an attribution line")
        if len(body) > _GITHUB_BODY_LIMIT:
            problems.append(f"{where}: {len(body)} characters; the forge limit is about {_GITHUB_BODY_LIMIT}")
        # Only this repository's links must point at the tip; a permalink elsewhere is evidence.
        for ref in stale_link_refs(body, repository=repository, head_sha=request.head_sha):
            problems.append(f"{where}: a link into this repository pinned to {ref}, not to the full reviewed tip")
    problems += forge.forge_problems(review)
    return problems, warnings


def _gitlab_patch_withheld(entry: dict[str, Any], *, diff: str) -> bool:
    """Whether GitLab sent an empty `diff` for a file whose changes it did not include."""
    # GitLab 18.4 added `collapsed` and `too_large`; before it, a withheld diff is only an empty string.
    # https://gitlab.com/gitlab-org/gitlab/-/blob/df4cf0a439272ac3aeb8228283c82d926584aaa0/doc/api/merge_requests.md#L2340
    if "collapsed" in entry or "too_large" in entry:
        return bool(entry.get("collapsed") or entry.get("too_large"))
    # Without the flags a pure rename and a collapsed rename with edits look the same, and a new
    #  file's `a_mode` is "0", so neither renames nor modes can tell a complete empty diff apart.
    return not diff.strip()


def _repository_key(url: str) -> str:
    return _REPOSITORY_SPELLING.sub("", url).lower()


def stale_link_refs(body: str, *, repository: str, head_sha: str) -> list[str]:
    """The ref of every blob link into `repository` that is not the reviewed tip."""
    # A prefix, not a pattern: a pattern stops at the first `/blob/`, which can be an owner's name.
    prefixes = (f"{repository}/blob/", f"{repository}/-/blob/")
    refs = []
    for url in _URL.findall(_CODE.sub("", body)):
        spelled = _REPOSITORY_SPELLING.sub("", url)
        prefix = next((p for p in prefixes if spelled.lower().startswith(p)), None)
        if prefix is None:
            continue
        ref = spelled[len(prefix) :].split("/")[0]
        if ref.lower() != head_sha.lower():
            refs.append(ref)
    return refs


def _load(path: str) -> dict[str, Any]:
    review = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(review, dict):
        raise Refused(f"{path}: expected a JSON object")
    return review


def _check(forge: Forge, review: dict[str, Any], *, replacing: bool) -> Request:
    request = forge.request()
    files = forge.files()
    problems, warnings = problems_with(
        review,
        forge=forge,
        request=request,
        files=files,
        threads=forge.threads(),
        drafts=forge.drafts(),
        me=forge.username(),
        replacing=replacing,
    )
    for warning in warnings:
        _say(f"warning: {warning}")
    if problems:
        raise Refused("refused, nothing was written:\n" + "\n".join(f"  - {p}" for p in problems))
    return request


def cmd_status(forge: Forge, _args: argparse.Namespace) -> None:
    request = forge.request()
    me = forge.username()
    _say(f"{forge.name} {request.url}")
    _say(f'"{request.title}" by @{request.author}, {"open" if request.is_open else "NOT OPEN"}')
    _say(f"{request.source} -> {request.target}{' (from a fork)' if request.fork else ''}")
    _say(f"head {request.head_sha}")
    _say(f"you are @{me}")
    threads = forge.threads()
    _say(f"\nthreads: {len(threads)}")
    for thread in threads:
        state = {True: "resolved", False: "open", None: "comment"}[thread.resolved]
        where = f" {thread.path}:{thread.line}" if thread.path else ""
        ours = " (ours)" if thread.author == me else ""
        _say(f"  [{state}] @{thread.author}{ours}{where} {thread.excerpt}\n      {thread.url}  id={thread.id}")
    drafts = forge.drafts()
    _say(f"\nstaged drafts (visible to you only): {len(drafts)}")
    for draft in drafts:
        where = f"{draft.path}:{draft.line}" if draft.path else "summary/reply"
        _say(f"  {draft.id} {where} {draft.excerpt}")
    if drafts:
        _say(f"fingerprint: {fingerprint(drafts)}  (publish --expect takes this)")


def coderabbit_pass(worktree: str, *, base: str, out: str, skip: bool) -> str:
    """Run the CodeRabbit CLI on a worktree; every way it cannot run is one line, never a failure.

    The first line of `out` is the verdict the skill reads: `RAN`, `SKIPPED`, `NOT INSTALLED`,
    `SIGNED OUT`, `BUSY`, `LIMITED` or `FAILED`.
    """
    def verdict(line: str, rest: str = "") -> str:
        Path(out).write_text(f"{line}\n{rest}", encoding="utf-8")
        return line

    if skip or os.environ.get("VET_SKIP_CODERABBIT", "") not in ("", "0"):
        return verdict("SKIPPED: asked to run without CodeRabbit")
    binary = shutil.which("coderabbit") or next(
        (str(p) for p in [Path.home() / ".local/bin/coderabbit"] if p.is_file()), None
    )
    if binary is None:
        return verdict(
            "NOT INSTALLED: the review ran without CodeRabbit. To add it: "
            "`brew install coderabbit` (or `curl -fsSL https://cli.coderabbit.ai/install.sh | sh`), "
            "then `coderabbit auth login`"
        )
    # `auth status` exits 0 while signed out, so the text is what counts.
    auth = subprocess.run([binary, "auth", "status"], capture_output=True, text=True, check=False)  # noqa: S603
    if "signed out" in (auth.stdout + auth.stderr).lower():
        return verdict(f"SIGNED OUT: run `{binary} auth login`; the review ran without CodeRabbit")
    # `-x` matches the executable name only; `-f` would match any shell that merely mentions it.
    if subprocess.run(["pgrep", "-x", "coderabbit"], capture_output=True, check=False).returncode == 0:  # noqa: S607
        return verdict("BUSY: another local CodeRabbit review is running; retry once it is gone")
    # Only --base-commit: given --base as well, the CLI silently prefers --base.
    completed = subprocess.run(  # noqa: S603
        [binary, "review", "--committed", "--base-commit", base],
        capture_output=True,
        text=True,
        check=False,
        cwd=worktree,
    )
    output = _quiet(completed.stdout + completed.stderr)
    if "limit reached" in output.lower():
        return verdict("LIMITED: the hourly allowance is spent; the wait is named below", output)
    if completed.returncode != 0:
        return verdict(f"FAILED: exit {completed.returncode}", output)
    return verdict("RAN", output)


def _quiet(output: str) -> str:
    """Drop the CLI's banners, spinner lines and telemetry stack traces; keep the review."""
    kept = [line for line in output.splitlines() if not _CODERABBIT_NOISE.search(line)]
    return "\n".join(kept) + "\n"


def cmd_coderabbit(_forge: Forge | None, args: argparse.Namespace) -> None:
    _say(coderabbit_pass(args.worktree, base=args.base, out=args.out, skip=args.skip))


def cmd_check(forge: Forge, args: argparse.Namespace) -> None:
    _check(forge, _load(args.review), replacing=args.replace_drafts)
    _say("check passed; nothing was written")


def cmd_draft(forge: Forge, args: argparse.Namespace) -> None:
    review = _load(args.review)
    request = _check(forge, review, replacing=args.replace_drafts)
    if args.replace_drafts:
        _say(f"deleted {forge.discard()} earlier drafts")
    forge.stage(review, request=request, files=forge.files())
    _say("drafts are visible to you only; nothing is published")


def cmd_discard(forge: Forge, _args: argparse.Namespace) -> None:
    _say(f"deleted {forge.discard()} drafts")


def cmd_publish(forge: Forge, args: argparse.Namespace) -> None:
    drafts = forge.drafts()
    now = fingerprint(drafts)
    if now != args.expect:
        listing = "\n".join(f"  {d.id} {d.path or 'summary/reply'}:{d.line or ''} {d.excerpt}" for d in drafts)
        raise Refused(
            f"refused: the staged drafts are {now}, the approval was for {args.expect}; something was "
            f"edited, deleted or added since. Show the user what is there now:\n{listing}"
        )
    forge.publish()
    _say(f"published {len(drafts)} drafts on {forge.request().url}")


def _fingerprint_argument(value: str) -> str:
    # A bare count, what an approval looked like before fingerprints, would otherwise be refused as an edit.
    if not _FINGERPRINT.fullmatch(value):
        msg = f"{value!r} is not a fingerprint; `status` prints one, like 3-0123456789ab"
        raise argparse.ArgumentTypeError(msg)
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vet.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status").add_argument("url")
    for name in ("check", "draft"):
        command = sub.add_parser(name)
        command.add_argument("url")
        command.add_argument("review")
        command.add_argument("--replace-drafts", action="store_true")
    sub.add_parser("discard").add_argument("url")
    publish = sub.add_parser("publish")
    publish.add_argument("url")
    publish.add_argument("--expect", required=True, type=_fingerprint_argument, help="the fingerprint `status` printed")
    rabbit = sub.add_parser("coderabbit")
    rabbit.add_argument("worktree")
    rabbit.add_argument("--base", required=True, help="the merge base commit")
    rabbit.add_argument("--out", required=True)
    rabbit.add_argument("--skip", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "coderabbit":
        cmd_coderabbit(None, args)
        return 0
    handlers = {
        "status": cmd_status,
        "check": cmd_check,
        "draft": cmd_draft,
        "discard": cmd_discard,
        "publish": cmd_publish,
    }
    try:
        handlers[args.command](forge_for(args.url), args)
    except Refused as refusal:
        print(refusal, file=sys.stderr)
        return _EXIT_REFUSED
    except ForgeError as error:
        print(f"forge error: {error}", file=sys.stderr)
        return _EXIT_FORGE
    return 0


if __name__ == "__main__":
    sys.exit(main())
