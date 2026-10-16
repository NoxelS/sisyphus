from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "evaluate", ROOT / "infrastructure/searxng-evaluation/evaluate.py"
)
evaluate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(evaluate)


def document(path: str):
    return yaml.safe_load((ROOT / path).read_text())


class ExperimentManifestTests(unittest.TestCase):
    def test_readiness_is_independent_and_evaluation_is_opt_in(self):
        malg = document("clusters/sisyphus/malg.yaml")
        tor = document("clusters/sisyphus/searxng-tor.yaml")
        evaluation = document("clusters/sisyphus/searxng-evaluation.yaml")
        self.assertEqual(tor["spec"]["dependsOn"], [{"name": "malg"}])
        self.assertTrue(tor["spec"]["wait"])
        self.assertTrue(evaluation["spec"]["suspend"])
        self.assertNotIn({"name": "searxng-tor"}, malg["spec"]["dependsOn"])
        resources = document("infrastructure/malg/kustomization.yaml")["resources"]
        self.assertFalse(any("tor" in name or "evaluation" in name for name in resources))

    def test_tor_reachability_matches_narrow_egress(self):
        policies = list(yaml.safe_load_all(
            (ROOT / "infrastructure/searxng-tor/searxng-tor-network-policy.yaml").read_text()
        ))
        ports = policies[1]["spec"]["egress"][0]["toPorts"][0]["ports"]
        self.assertEqual(ports, [
            {"port": "443", "protocol": "TCP"},
            {"port": "9001", "protocol": "TCP"},
        ])
        torrc = (ROOT / "infrastructure/searxng-tor/torrc").read_text()
        self.assertIn("ReachableAddresses accept *:443, accept *:9001, reject *:*", torrc)
        self.assertNotIn("toEntities", str(policies[0]["spec"]["egress"]))

    def test_evaluation_cannot_claim_production_work(self):
        job = document("infrastructure/searxng-evaluation/job.yaml")
        pod = job["spec"]["template"]["spec"]
        container = pod["containers"][0]
        self.assertEqual(job["spec"]["backoffLimit"], 0)
        self.assertEqual(job["spec"]["activeDeadlineSeconds"], 7200)
        self.assertEqual(pod["restartPolicy"], "Never")
        self.assertFalse(pod["automountServiceAccountToken"])
        self.assertNotIn("envFrom", container)
        self.assertNotIn("secretKeyRef", str(container))
        self.assertEqual(container["command"], ["python3", "-u", "/evaluation/evaluate.py"])


class EvaluationRunnerTests(unittest.TestCase):
    def run_evaluation(self, opener, *, repeats="2", workload=b"query one\n"):
        output = io.StringIO()
        env = {
            "MALG_SEARCH__URL": "http://searxng-tor.malg.svc.cluster.local.:8080",
            "DIRECT_SEARCH_URL": "http://searxng.malg.svc.cluster.local.:8080",
            "EVALUATION_REPEATS": repeats,
        }
        with (
            patch.dict(os.environ, env),
            patch.object(evaluate.Path, "read_bytes", return_value=workload),
            patch.object(evaluate.urllib.request, "build_opener", return_value=opener),
            patch.object(evaluate.time, "sleep"),
            contextlib.redirect_stdout(output),
        ):
            evaluate.main()
        return [json.loads(line) for line in output.getvalue().splitlines()]

    def test_finite_comparison_alternates_order(self):
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = (
            b'{"results": [1], "unresponsive_engines": []}'
        )
        records = self.run_evaluation(opener)
        self.assertEqual(opener.open.call_count, 4)
        self.assertEqual([r["mode"] for r in records[1:-1]], ["tor", "direct", "direct", "tor"])
        self.assertEqual(records[-1]["successful_responses"], {"tor": 2, "direct": 2})

    def test_workload_cap_is_checked_before_requests(self):
        opener = MagicMock()
        with self.assertRaisesRegex(ValueError, "200 total requests"):
            self.run_evaluation(opener, repeats="101")
        opener.open.assert_not_called()

    def test_failed_tor_request_is_not_retried_or_replaced(self):
        opener = MagicMock()
        response = MagicMock()
        response.__enter__.return_value.read.return_value = b'{"results": []}'
        opener.open.side_effect = [urllib.error.URLError("unavailable"), response]
        with self.assertRaises(SystemExit):
            self.run_evaluation(opener, repeats="1")
        urls = [call.args[0] for call in opener.open.call_args_list]
        self.assertEqual(len(urls), 2)
        self.assertIn("searxng-tor.", urls[0])
        self.assertIn("searxng.malg.", urls[1])

    def test_redirects_are_rejected(self):
        request = evaluate.urllib.request.Request("http://searxng-tor:8080/search")
        with self.assertRaises(urllib.error.HTTPError) as raised:
            evaluate.NoRedirect().redirect_request(request, None, 302, "", {}, "https://example.org")
        raised.exception.close()


if __name__ == "__main__":
    unittest.main()
