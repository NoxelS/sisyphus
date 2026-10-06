"""Exercise the built, offline HTTP service; called inside the running container."""

import json
import time
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8000"


def request(path, body=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None if body is None else json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(URL + path, data=data, headers=headers),
                                    timeout=30) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, None


def main():
    for _ in range(120):
        try:
            if request("/health")[0] == 200:
                break
        except (OSError, urllib.error.URLError):
            time.sleep(1)
    else:
        raise SystemExit("Service never became ready")
    body = {"state": "The sky is blue.", "questions": {"color": {
        "type": "choice", "instructions": "Choose the sky's color.",
        "criteria": {"blue": None, "red": None}}}}
    assert request("/v1/systemone", body)[0] == 401
    assert request("/v1/systemone", body, "invalid")[0] == 401
    status, result = request("/v1/systemone", body, "smoke-key")
    assert status == 200, status
    assert result["answers"]["color"]["choice"] == "blue", result
    assert result["routing"]["model"] == "english", result
    for overrides in ({"model": "multilingual"}, {"task": "typed_decisions"},
                      {"lang": "de"}, {"max_len": 513}):
        assert request("/v1/systemone", body | overrides, "smoke-key")[0] == 422
    assert request("/v1/systemone/batch", body, "smoke-key")[0] == 404
    assert request("/v1/systemone", body | {"state": "x" * 8001}, "smoke-key")[0] in (413, 422)
    print("English HTTP, authentication, routing and request-bound checks passed")


if __name__ == "__main__":
    main()
