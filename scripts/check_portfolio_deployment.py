"""Validate stable production images and isolated Flux image updates."""

import json
import re
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
PRODUCTION = ROOT / "infrastructure/portfolio-production"
STABLE_VERSION = r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
IMAGE = re.compile(
    rf"ghcr\.io/noxels/portfolio-frontend:{STABLE_VERSION}@sha256:[0-9a-f]{{64}}"
)
POLICY = "flux-system:portfolio-production"


def validate_documents(
    deployment: dict[str, Any],
    configmap: dict[str, Any],
    policy: dict[str, Any],
    automation: dict[str, Any],
) -> None:
    container = next(
        item for item in deployment["spec"]["template"]["spec"]["containers"]
        if item["name"] == "frontend"
    )
    image = IMAGE.fullmatch(container["image"])
    if image is None or tuple(map(int, image.groups())) < (0, 0, 1):
        raise ValueError("production image must use stable SemVer >=0.0.1 and a SHA256 digest")

    if any(
        document["metadata"]["namespace"] != "portfolio-production"
        for document in (deployment, configmap)
    ):
        raise ValueError("production workload and configuration must use their own namespace")
    if (
        configmap["data"]["PUBLIC_DEPLOYMENT_ENV"] != "production"
        or {"configMapRef": {"name": configmap["metadata"]["name"]}}
        not in container["envFrom"]
        or any(
            item["name"] == "PUBLIC_DEPLOYMENT_ENV" and item.get("value") != "production"
            for item in container.get("env", [])
        )
    ):
        raise ValueError("production workload must use production configuration")

    for document in (policy, automation):
        if (
            document["metadata"]["name"] != "portfolio-production"
            or document["metadata"]["namespace"] != "flux-system"
        ):
            raise ValueError("production Flux resources must have distinct production names")
    selection = policy["spec"]
    if (
        selection["imageRepositoryRef"]["name"] != "portfolio-frontend"
        or selection["filterTags"] != {"pattern": f"^{STABLE_VERSION}$"}
        or selection["policy"] != {"semver": {"range": ">=0.0.1"}}
    ):
        raise ValueError("production policy must select only stable portfolio release tags")
    if (
        selection.get("digestReflectionPolicy") != "IfNotPresent"
        or "interval" in selection
    ):
        raise ValueError("production policy must retain the digest of each immutable release")

    update = automation["spec"]
    if (
        update["git"]["checkout"]["ref"] != {"branch": "main"}
        or update["git"]["push"] != {"branch": "flux/portfolio-production-image"}
        or update["update"] != {
            "path": "./infrastructure/portfolio-production", "strategy": "Setters"
        }
    ):
        raise ValueError("production automation must update only its own path and branch")


def validate_image_marker(source: str) -> None:
    image_lines = [line for line in source.splitlines() if re.match(r"\s+image:", line)]
    try:
        marker = json.loads(image_lines[0].partition("#")[2])
    except (IndexError, ValueError) as exc:
        raise ValueError("production image must have a Flux image policy marker") from exc
    if len(image_lines) != 1 or marker != {"$imagepolicy": POLICY}:
        raise ValueError("production image must use the production Flux image policy marker")


def main() -> None:
    try:
        source = (PRODUCTION / "deployment.yaml").read_text()
        documents = {
            document["kind"]: document
            for document in yaml.safe_load_all(
                (PRODUCTION / "image-automation.yaml").read_text()
            )
        }
        validate_documents(
            yaml.safe_load(source),
            yaml.safe_load((PRODUCTION / "configmap.yaml").read_text()),
            documents["ImagePolicy"],
            documents["ImageUpdateAutomation"],
        )
        validate_image_marker(source)
    except (KeyError, TypeError, ValueError, StopIteration) as exc:
        raise SystemExit(f"Portfolio deployment check failed: {exc}") from exc
    print("Portfolio production uses a stable, pinned image and isolated Flux updates")


if __name__ == "__main__":
    main()
