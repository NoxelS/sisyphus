"""Run a finite, serial comparison without claiming production MALG jobs."""

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect denied", headers, fp)


def main() -> None:
    workload = Path("/evaluation/queries.txt").read_bytes()
    queries = [line.strip() for line in workload.decode().splitlines() if line.strip()]
    repeats = int(os.environ["EVALUATION_REPEATS"])
    if not queries or repeats < 1 or len(queries) * repeats * 2 > 200:
        raise ValueError("workload must contain between 1 and 200 total requests")
    endpoints = {
        "tor": os.environ["MALG_SEARCH__URL"],
        "direct": os.environ["DIRECT_SEARCH_URL"],
    }
    for mode, url in endpoints.items():
        host = "searxng-tor" if mode == "tor" else "searxng"
        if url != f"http://{host}.malg.svc.cluster.local.:8080":
            raise ValueError("only the two private search endpoints are permitted")
    print(json.dumps({
        "endpoints": endpoints,
        "workload_sha256": hashlib.sha256(workload).hexdigest(),
        "requests": len(queries) * repeats * 2,
    }), flush=True)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    successes = dict.fromkeys(endpoints, 0)
    for repeat in range(repeats):
        for query_id, query in enumerate(queries):
            # Alternate ordering to reduce systematic timing bias.
            modes = list(endpoints) if repeat % 2 == 0 else list(reversed(endpoints))
            for mode in modes:
                record = {"mode": mode, "query_id": query_id, "repeat": repeat}
                params = urllib.parse.urlencode({"q": query, "format": "json", "safesearch": 2})
                started = time.monotonic()
                try:
                    with opener.open(endpoints[mode] + "/search?" + params, timeout=60) as response:
                        payload = response.read(2 * 1024 * 1024 + 1)
                    if len(payload) > 2 * 1024 * 1024:
                        raise ValueError("response exceeds size limit")
                    data = json.loads(payload)
                    results = data["results"]
                    if not isinstance(results, list):
                        raise ValueError("results must be a list")
                    record.update(
                        results=len(results), engine_errors=data.get("unresponsive_engines", [])
                    )
                    successes[mode] += 1
                except (OSError, ValueError, KeyError, TypeError) as exc:
                    record["error"] = type(exc).__name__
                    if isinstance(exc, urllib.error.HTTPError):
                        record["http_status"] = exc.code
                        exc.close()
                record["latency_seconds"] = round(time.monotonic() - started, 3)
                print(json.dumps(record), flush=True)
                time.sleep(5)
    print(json.dumps({"completed": True, "successful_responses": successes}), flush=True)
    if not all(successes.values()):
        raise SystemExit("at least one endpoint returned no valid responses")


if __name__ == "__main__":
    main()
