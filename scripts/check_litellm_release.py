"""Keep the LiteLLM chart and pinned application image on one release."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
CHART = ROOT / "infrastructure/litellm/litellm-ocirepository.yaml"
RELEASE = ROOT / "infrastructure/litellm/helmrelease.yaml"


def main() -> None:
    chart = yaml.safe_load(CHART.read_text())
    release = yaml.safe_load(RELEASE.read_text())
    chart_tag = chart["spec"]["ref"]["tag"]
    image_tag = release["spec"]["values"]["image"]["tag"]
    image_version = re.fullmatch(r"v(\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}", image_tag)
    if image_version is None or chart_tag != image_version.group(1):
        raise SystemExit(
            f"LiteLLM chart {chart_tag} and pinned image {image_tag} must use "
            "the same release version"
        )


if __name__ == "__main__":
    main()
