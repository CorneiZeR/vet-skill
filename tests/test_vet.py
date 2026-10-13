"""vet.py against a fake forge: what it stages, and above all what it refuses to write.

Run: python3 -I tests/test_vet.py
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
VET = HERE.parent / "skills" / "vet" / "scripts" / "vet.py"
HEAD = "a" * 40
OLD = "b" * 40
GL = "projects/g%2Fp/merge_requests/7"
GL_URL = "https://gitlab.com/g/p/-/merge_requests/7"
GH = "repos/o/r/pulls/7"
GH_URL = "https://github.com/o/r/pull/7"
# New side: 1 context, 2 and 3 added, 4 context.
PATCH = "@@ -1,2 +1,4 @@\n line1\n+added2\n+added3\n line2\n"


def gitlab_state(**extra):
    state = {
        "me": "me",
        "routes": {
            f"GET {GL}": {
                "web_url": GL_URL,
                "title": "T",
                "state": "opened",
                "author": {"username": "alice"},
                "source_branch": "feat",
                "target_branch": "main",
                "sha": HEAD,
                "source_project_id": 1,
                "target_project_id": 1,
                "diff_refs": {"base_sha": "1" * 40, "start_sha": "2" * 40, "head_sha": HEAD},
            },
            "GET user": {"username": "me"},
            f"GET {GL}/discussions?per_page=100": [],
            f"GET {GL}/diffs?per_page=100": [{"new_path": "app.py", "old_path": "app.py", "diff": PATCH}],
        },
    }
    state.update(extra)
    return state


def github_state(**extra):
    state = {
        "me": "me",
        "routes": {
            f"GET {GH}": {
                "html_url": GH_URL,
                "title": "T",
                "state": "open",
                "user": {"login": "alice"},
                "head": {"ref": "feat", "sha": HEAD, "repo": {"full_name": "o/r"}},
                "base": {"ref": "main", "repo": {"full_name": "o/r"}},
            },
            "GET user": {"login": "me"},
            "GET graphql": {
                "data": {
                    "repository": {
                        "pullRequest": {"reviewThreads": {"pageInfo": {"hasNextPage": False}, "nodes": []}}
                    }
                }
            },
            "GET repos/o/r/issues/7/comments?per_page=100": [],
            f"GET {GH}/files?per_page=100": [{"filename": "app.py", "status": "modified", "patch": PATCH}],
        },
    }
    state.update(extra)
    return state


def review(**extra):
    body = {
        "head_sha": HEAD,
        "summary": "Verdict.",
        "comments": [{"path": "app.py", "line": 2, "body": "@alice **[should-fix] Defect.**"}],
    }
    body.update(extra)
    return body


class ParsePatchTest(unittest.TestCase):
    def test_positions_count_removed_lines_and_later_hunk_headers(self):
        sys.path.insert(0, str(VET.parent))
        import vet

        lines = vet.parse_patch("@@ -1,2 +1,2 @@\n a\n-b\n+c\n@@ -10,1 +10,2 @@\n x\n+y\n", old_path="p")
        self.assertEqual(lines.added, {2, 11})
        self.assertEqual(lines.line_at, {1: 1, 3: 2, 5: 10, 6: 11})
        self.assertNotEqual(lines.hunk_of[2], lines.hunk_of[11])


class QuietTest(unittest.TestCase):
    def test_coderabbit_noise_is_dropped_and_the_review_kept(self):
        sys.path.insert(0, str(VET.parent))
        import vet

        noisy = "\n".join([
            "\u2554\u2550\u2550\u2557",
            "\u2551   New update available! 0.8.2 -> 0.9.0    \u2551",
            "\u2500" * 20,
            "Review complete",
            "app.py:12 Possible None dereference",
            "Writing review comments... 10s elapsed",
            "Error while flushing PostHog PostHogFetchNetworkError: Network error",
            "      at async uZ (/$bunfs/root/coderabbit:137:41558)",
            '  path: "https://us.i.posthog.com/batch/",',
        ])
        self.assertEqual(vet._quiet(noisy), "Review complete\napp.py:12 Possible None dereference\n")


class VetTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        bin_dir = self.tmp / "bin"
        bin_dir.mkdir()
        for name in ("gh", "glab"):
            (bin_dir / name).symlink_to(HERE / "fake_forge.py")
        self.env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", FAKE_STATE=str(self.tmp / "state.json"))

    def run_vet(self, state, *args, review_body=None):
        (self.tmp / "state.json").write_text(json.dumps(state))
        argv = list(args)
        if review_body is not None:
            (self.tmp / "review.json").write_text(json.dumps(review_body))
            argv.insert(2, str(self.tmp / "review.json"))
        done = subprocess.run(
            [sys.executable, "-I", str(VET), *argv], capture_output=True, text=True, env=self.env, check=False
        )
        return done, json.loads((self.tmp / "state.json").read_text())

    def test_gitlab_draft_sends_json_with_nested_position_and_keeps_a_leading_mention(self):
        done, state = self.run_vet(gitlab_state(), "draft", GL_URL, review_body=review())
        self.assertEqual(done.returncode, 0, done.stderr)
        first = state["writes"][0]
        self.assertEqual(first["payload"]["note"], "@alice **[should-fix] Defect.**")
        self.assertEqual(first["payload"]["position"]["new_line"], 2)
        self.assertEqual(first["payload"]["position"]["base_sha"], "1" * 40)
        self.assertEqual(state["writes"][-1]["payload"], {"note": "Verdict."})
        self.assertNotIn("bulk_publish", json.dumps(state["writes"]))

    def test_comment_on_a_context_line_is_refused_before_any_write(self):
        bad = review(comments=[{"path": "app.py", "line": 4, "body": "x"}])
        done, state = self.run_vet(gitlab_state(), "draft", GL_URL, review_body=bad)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not an added line", done.stderr)
        self.assertEqual(state.get("writes", []), [])

    def test_review_made_at_an_older_head_is_refused(self):
        done, state = self.run_vet(gitlab_state(), "draft", GL_URL, review_body=review(head_sha=OLD))
        self.assertEqual(done.returncode, 1)
        self.assertIn("review the new head", done.stderr)
        self.assertEqual(state.get("writes", []), [])

    def test_attribution_line_is_refused(self):
        signed = review(summary="Verdict.\n\nGenerated with [Claude Code](https://claude.com/claude-code)")
        done, state = self.run_vet(gitlab_state(), "check", GL_URL, review_body=signed)
        self.assertEqual(done.returncode, 1)
        self.assertIn("attribution", done.stderr)

    def test_link_pinned_to_another_commit_is_refused(self):
        linked = review(summary=f"See [x](https://github.com/o/r/blob/{OLD}/app.py#L2).")
        done, _ = self.run_vet(github_state(), "check", GH_URL, review_body=linked)
        self.assertEqual(done.returncode, 1)
        self.assertIn("not to the full reviewed tip", done.stderr)

    def test_existing_drafts_block_a_second_set_unless_replacing(self):
        staged = gitlab_state(drafts=[{"id": 5, "note": "old"}])
        done, state = self.run_vet(staged, "draft", GL_URL, review_body=review())
        self.assertEqual(done.returncode, 1)
        self.assertIn("already staged", done.stderr)
        self.assertEqual(state.get("writes", []), [])

        done, state = self.run_vet(staged, "draft", GL_URL, "--replace-drafts", review_body=review())
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(state["writes"][0]["method"], "DELETE")
        self.assertEqual([d["note"] for d in state["drafts"]], ["@alice **[should-fix] Defect.**", "Verdict."])

    def test_failed_write_stops_before_the_summary(self):
        two = review(comments=[{"path": "app.py", "line": 2, "body": "a"}, {"path": "app.py", "line": 3, "body": "b"}])
        done, state = self.run_vet(gitlab_state(fail_write=2), "draft", GL_URL, review_body=two)
        self.assertEqual(done.returncode, 3)
        self.assertIn("discard", done.stderr)
        self.assertEqual(len(state["writes"]), 2)

    def test_dropped_position_is_reported(self):
        done, _ = self.run_vet(gitlab_state(drop_position=True), "draft", GL_URL, review_body=review())
        self.assertEqual(done.returncode, 3)
        self.assertIn("not anchored", done.stderr)

    def test_our_own_open_thread_on_the_line_is_refused(self):
        state = gitlab_state()
        state["routes"][f"GET {GL}/discussions?per_page=100"] = [
            {
                "id": "d1",
                "notes": [
                    {
                        "id": 1,
                        "body": "earlier",
                        "author": {"username": "me"},
                        "resolvable": True,
                        "resolved": False,
                        "position": {"new_path": "app.py", "new_line": 2},
                    }
                ],
            }
        ]
        done, _ = self.run_vet(state, "check", GL_URL, review_body=review())
        self.assertEqual(done.returncode, 1)
        self.assertIn("reply there instead", done.stderr)

    def test_github_draft_is_a_pending_review_without_an_event(self):
        done, state = self.run_vet(github_state(), "draft", GH_URL, review_body=review())
        self.assertEqual(done.returncode, 0, done.stderr)
        payload = state["writes"][0]["payload"]
        self.assertNotIn("event", payload)
        self.assertEqual(payload["commit_id"], HEAD)
        self.assertEqual(payload["comments"][0]["side"], "RIGHT")

    def test_github_refuses_replies_and_ranged_fences(self):
        bad = review(
            replies=[{"thread": "x", "body": "y"}],
            comments=[{"path": "app.py", "line": 3, "body": "```suggestion:-1+0\nx\n```"}],
        )
        done, _ = self.run_vet(github_state(), "check", GH_URL, review_body=bad)
        self.assertEqual(done.returncode, 1)
        self.assertIn("replies cannot be staged", done.stderr)
        self.assertIn("plain ```suggestion fence", done.stderr)

    def test_publish_refuses_a_count_the_user_did_not_see(self):
        staged = gitlab_state(drafts=[{"id": 5, "note": "a"}, {"id": 6, "note": "b"}])
        done, state = self.run_vet(staged, "publish", GL_URL, "--expect", "1")
        self.assertEqual(done.returncode, 1)
        self.assertEqual(state.get("writes", []), [])

        done, state = self.run_vet(staged, "publish", GL_URL, "--expect", "2")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(state["writes"][0]["path"].endswith("/draft_notes/bulk_publish"))

    def test_github_publish_submits_as_comment(self):
        pending = github_state(
            reviews=[{"id": 900, "state": "PENDING", "user": {"login": "me"}, "body": "S", "comments": []}]
        )
        done, state = self.run_vet(pending, "publish", GH_URL, "--expect", "1")
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertEqual(state["writes"][0]["payload"], {"event": "COMMENT", "body": "S"})

    def test_coderabbit_absent_or_skipped_is_one_line_and_exit_zero(self):
        out = self.tmp / "cr.txt"
        env = dict(self.env, PATH=str(self.tmp / "bin"), HOME=str(self.tmp))
        done = subprocess.run(
            [sys.executable, "-I", str(VET), "coderabbit", str(self.tmp), "--base", HEAD, "--out", str(out)],
            capture_output=True, text=True, env=env, check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(out.read_text().startswith("NOT INSTALLED"))

        done = subprocess.run(
            [sys.executable, "-I", str(VET), "coderabbit", str(self.tmp), "--base", HEAD, "--out", str(out)],
            capture_output=True, text=True, env=dict(env, VET_SKIP_CODERABBIT="1"), check=False,
        )
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertTrue(out.read_text().startswith("SKIPPED"))


if __name__ == "__main__":
    unittest.main(verbosity=1)
