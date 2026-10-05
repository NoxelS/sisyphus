"""Exercise the workflow shell with a fake GitHub API; never call GitHub."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
UPDATED_SHA = "b" * 40
FAKE_GH = r"""import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
with open(os.environ["GH_CALLS"], "a") as stream:
    stream.write(json.dumps(args) + "\n")
scenario = os.environ["SCENARIO"]
if args[:2] == ["workflow", "run"]:
    sys.exit(0)
if args[0] == "api":
    endpoint = args[1] if args[1] != "-X" else args[3]
    if "/git/ref/heads/" in endpoint:
        print("c" * 40 if scenario == "superseded" else "a" * 40)
    elif "/compare/" in endpoint:
        ahead = 0 if scenario == "merged" else 1
        if scenario == "main-refresh":
            print(1 if endpoint.endswith("flux/malg-image") else 0)
        else:
            print(json.dumps({"ahead_by": ahead, "behind_by": 1}))
    elif endpoint.endswith("/files"):
        print("README.md" if scenario == "unexpected-file" else
              "infrastructure/malg/api-deployment.yaml")
    elif endpoint.endswith("/update-branch"):
        Path(os.environ["UPDATED"]).touch()
    else:
        sys.exit("Unexpected API request: " + endpoint)
elif args[:2] == ["pr", "list"]:
    print("123")
elif args[:2] == ["pr", "view"]:
    print("b" * 40 if Path(os.environ["UPDATED"]).exists() else "a" * 40)
elif args[:2] != ["pr", "merge"]:
    sys.exit("Unexpected gh arguments: " + str(args))
"""


class FluxImagePrTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / ".github/workflows/flux-image-pr.yml").read_text())

    def run_workflow(self, scenario, job="pull-request", branch="flux/malg-image"):
        script = self.workflow["jobs"][job]["steps"][-1]["run"]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gh = root / "gh"
            gh.write_text(f"#!{sys.executable}\n" + FAKE_GH)
            gh.chmod(0o700)
            calls = root / "calls"
            env = dict(
                os.environ,
                PATH=f"{root}:{os.environ['PATH']}",
                GH_CALLS=str(calls),
                SCENARIO=scenario,
                UPDATED=str(root / "updated"),
                GITHUB_REPOSITORY="NoxelS/sisyphus",
                GITHUB_SHA=SHA,
                HEAD_SHA=SHA,
                HEAD_BRANCH=branch,
            )
            result = subprocess.run(
                ["bash", "-c", script], env=env, text=True, capture_output=True, timeout=10
            )
            requests = (
                [json.loads(line) for line in calls.read_text().splitlines()]
                if calls.exists()
                else []
            )
            return result, requests

    def test_main_refreshes_only_unmerged_image_branches(self):
        result, calls = self.run_workflow("main-refresh", "dispatch", "main")
        self.assertEqual(result.returncode, 0, result.stderr)
        dispatches = [call for call in calls if call[:2] == ["workflow", "run"]]
        self.assertEqual(len(dispatches), 1)
        self.assertIn("head_branch=flux/malg-image", dispatches[0])
        self.assertIn(f"head_sha={SHA}", dispatches[0])
        self.assertIn("main", dispatches[0])

    def test_flux_push_dispatches_exact_commit(self):
        result, calls = self.run_workflow("push", "dispatch")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)
        self.assertIn(f"head_sha={SHA}", calls[0])

    def test_stale_run_performs_no_mutations(self):
        result, calls = self.run_workflow("superseded")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 1)

    def test_merged_branch_does_not_create_another_pr(self):
        result, calls = self.run_workflow("merged")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(call[0] == "api" for call in calls))

    def test_unexpected_file_blocks_branch_update_and_merge(self):
        result, calls = self.run_workflow("unexpected-file")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Unexpected Flux PR file", result.stderr)
        self.assertFalse(any("PUT" in call or "merge" in call for call in calls))

    def test_branch_update_merges_only_the_new_head(self):
        result, calls = self.run_workflow("behind")
        self.assertEqual(result.returncode, 0, result.stderr)
        updates = [call for call in calls if "PUT" in call]
        self.assertEqual(len(updates), 1)
        self.assertIn(f"expected_head_sha={SHA}", updates[0])
        self.assertEqual(calls[-1][-2:], ["--match-head-commit", UPDATED_SHA])
        self.assertIn("--auto", calls[-1])
        self.assertIn("--merge", calls[-1])

    def test_unknown_branch_is_rejected_before_api_access(self):
        result, calls = self.run_workflow("unknown", branch="feature/untrusted")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, [])
