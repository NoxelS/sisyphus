"""Check the production pass-through configuration against a real Laya server."""

import json
import sys
import time
import urllib.error
import urllib.request

import yaml


def main(mode):
    if mode == "config":
        with open("/tmp/helmrelease.yaml") as source:
            endpoints = yaml.safe_load(source)["spec"]["values"]["proxy_config"][
                "general_settings"]["pass_through_endpoints"]
        with open("/tmp/proxy.yaml", "w") as target:
            yaml.safe_dump({"model_list": [], "general_settings": {
                "master_key": "sk-smoke-gateway", "pass_through_endpoints": endpoints}}, target)
        return
    body = json.dumps({"state": "The sky is blue.", "questions": {"color": {
        "type": "choice", "instructions": "Choose the sky's color.",
        "criteria": {"blue": None, "red": None}}}}).encode()
    for _ in range(60):
        try:
            urllib.request.urlopen("http://127.0.0.1:4000/health/liveliness", timeout=2).close()
            break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        raise SystemExit("Proxy never became ready")
    for token in (None, "invalid", "sk-smoke-gateway"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        request = urllib.request.Request("http://127.0.0.1:4000/laya/v1/systemone",
                                         data=body, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                assert token == "sk-smoke-gateway"
                result = json.load(response)
                assert result["answers"]["color"]["choice"] == "blue", result
        except urllib.error.HTTPError as error:
            assert token != "sk-smoke-gateway"
            # Without a DB the OSS proxy refuses unknown keys with 400; production has a DB.
            assert error.code in (400, 401, 403), error.code
    print("Pinned LiteLLM authentication, production route and upstream header replacement passed")


if __name__ == "__main__":
    main(sys.argv[1])
