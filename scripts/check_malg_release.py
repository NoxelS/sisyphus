"""Validate the MALG immutable release tuple against GitOps manifests."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import yaml

_REQUIRED_KEYS = {
    "release_tag",
    "source_sha",
    "backend_image",
    "frontend_image",
    "contract_version",
    "contract_hash",
    "required_database_revision",
}
_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _documents(root: Path) -> list[dict[str, Any]]:
    paths = [
        root / "infrastructure/malg/api-deployment.yaml",
        root / "infrastructure/malg/worker-deployment.yaml",
        root / "infrastructure/malg/crm-schema/job.yaml",
        root / "infrastructure/malg/migration-job.yaml",
        root / "infrastructure/malg/frontend-deployment.yaml",
        root / "infrastructure/malg/network-policy.yaml",
    ]
    paths.extend((root / "clusters").glob("**/*.yaml"))
    docs: list[dict[str, Any]] = []
    for path in paths:
        if not path.is_file():
            raise ValueError(f"missing deployment manifest: {path}")
        for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
            if isinstance(document, dict):
                docs.append(document)
    return docs




def _release_labels(documents: list[dict[str, Any]]) -> list[str]:
    labels: list[str] = []
    for document in documents:
        if document.get("kind") != "Kustomization":
            continue
        metadata = document.get("metadata", {})
        spec = document.get("spec", {})
        for source in (
            metadata.get("labels", {}),
            spec.get("commonMetadata", {}).get("labels", {}),
        ):
            value = source.get("malg-release") if isinstance(source, dict) else None
            if value is not None:
                labels.append(str(value))
    return labels


def check(root: Path, *, allow_reviewed_first_cutover: bool = False) -> None:
    release_path = root / "infrastructure/malg/release.json"
    if not release_path.is_file():
        raise ValueError(f"missing release metadata: {release_path}")
    try:
        release = json.loads(release_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid release metadata: {exc}") from exc
    if not isinstance(release, dict) or set(release) != _REQUIRED_KEYS:
        raise ValueError(f"release metadata keys must be exactly {sorted(_REQUIRED_KEYS)}")
    for key in _REQUIRED_KEYS:
        if key == "contract_version":
            if not isinstance(release[key], int) or isinstance(release[key], bool):
                raise ValueError("contract_version must be an integer")
        elif not isinstance(release[key], str) or not release[key]:
            raise ValueError(f"release metadata field {key!r} is missing")
    for key, repository in (
        ("backend_image", "ghcr.io/noxels/malg"),
        ("frontend_image", "ghcr.io/noxels/malg-frontend"),
    ):
        value = release[key]
        if (
            not isinstance(value, str)
            or not _DIGEST.search(value)
            or not value.startswith(f"{repository}:{release['release_tag']}@")
        ):
            raise ValueError(
                f"{key} must be the release tag with an immutable sha256 digest"
            )
        digest = value.rsplit("@", 1)[1]
        if digest != digest.lower():
            raise ValueError(f"{key} digest must use lowercase hexadecimal")
    if not isinstance(release["contract_version"], int) or release["contract_version"] < 1:
        raise ValueError("contract_version must be a positive integer")
    if (
        not isinstance(release["contract_hash"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", release["contract_hash"])
    ):
        raise ValueError("contract_hash must be a SHA-256 hex digest")
    if not re.fullmatch(r"v[0-9]+\.[0-9]+\.[0-9]+", str(release["release_tag"])):
        raise ValueError("release_tag must be a semantic vX.Y.Z tag")
    if release["required_database_revision"] != "20260924_01":
        raise ValueError("required_database_revision must be 20260924_01")
    if not re.fullmatch(r"[0-9a-f]{40,64}", str(release["source_sha"])):
        raise ValueError("source_sha must be a commit SHA")
    documents = _documents(root)
    backend, frontend = str(release["backend_image"]), str(release["frontend_image"])
    expected_names = {
        "malg-api": backend,
        "malg-worker": backend,
        "malg-crm-schema": backend,
        "malg-migration": backend,
        "frontend": frontend,
    }
    seen_names: set[str] = set()
    for document in documents:
        name = document.get("metadata", {}).get("name")
        if name not in expected_names:
            continue
        template = document.get("spec", {}).get("template")
        if not isinstance(template, dict):
            continue
        seen_names.add(str(name))
        pod_spec = template.get("spec", {})
        containers = list(pod_spec.get("containers", [])) + list(
            pod_spec.get("initContainers", [])
        )
        if not containers or any(
            container.get("image") != expected_names[name] for container in containers
        ):
            raise ValueError(f"{name} image does not match release tuple")
    missing_names = set(expected_names) - seen_names
    if missing_names:
        raise ValueError(
            "missing release-pinned workload manifests: " + ", ".join(sorted(missing_names))
        )

    migration = next(
        (
            d
            for d in documents
            if d.get("kind") == "Job"
            and d.get("metadata", {}).get("name") == "malg-migration"
        ),
        None,
    )
    if migration is None:
        raise ValueError("missing malg-migration Job")
    migration_pod = migration.get("spec", {}).get("template", {}).get("spec", {})
    database_ready = next(
        (
            container
            for container in migration_pod.get("initContainers", [])
            if container.get("name") == "database-ready"
        ),
        None,
    )
    database_ready_command = (
        database_ready.get("command") if isinstance(database_ready, dict) else None
    )
    if (
        not isinstance(database_ready_command, list)
        or database_ready_command[:2] != ["python", "-c"]
        or len(database_ready_command) < 3
        or not isinstance(database_ready_command[2], str)
    ):
        raise ValueError("migration database-ready command must be valid Python")
    try:
        compile(
            database_ready_command[2],
            "<malg-migration database-ready>",
            "exec",
        )
    except SyntaxError as exc:
        raise ValueError("migration database-ready command must be valid Python") from exc
    if migration_pod.get("automountServiceAccountToken") is not False:
        raise ValueError("migration Job must disable the service-account token")
    pod_security = migration_pod.get("securityContext", {})
    if pod_security.get("runAsNonRoot") is not True:
        raise ValueError("migration Job must run as non-root")
    containers = list(migration_pod.get("containers", [])) + list(
        migration_pod.get("initContainers", [])
    )
    if not containers:
        raise ValueError("migration Job has no containers")
    for container in containers:
        security = container.get("securityContext", {})
        if (
            security.get("allowPrivilegeEscalation") is not False
            or security.get("readOnlyRootFilesystem") is not True
            or "ALL" not in security.get("capabilities", {}).get("drop", [])
        ):
            raise ValueError("migration Job containers require restrictive security settings")
        for env in container.get("env", []):
            if env.get("name") in {
                "MALG_CRM_CUTOVER_APPROVED",
                "MALG_LEGACY_CRM_RETIREMENT_APPROVED",
            }:
                if not allow_reviewed_first_cutover:
                    raise ValueError(
                        "migration Job must not contain cutover approval without "
                        "--allow-reviewed-first-cutover"
                    )
                if env.get("value") != "1":
                    raise ValueError("first-cutover approval must be explicitly set to 1")
    migration_command = migration_pod.get("containers", [{}])[0].get("command", [])
    if migration_command != ["alembic", "upgrade", release["required_database_revision"]]:
        raise ValueError("migration Job must target the exact required_database_revision")

    startup_commands = {
        "malg-api": [
            "/bin/sh",
            "-c",
            "python -m malg.database.wait_for_schema --timeout-seconds 300 && "
            "exec uvicorn malg.api.app:app --host 0.0.0.0 --port 8000",
        ],
        "malg-worker": [
            "/bin/sh",
            "-c",
            "python -m malg.database.wait_for_schema --timeout-seconds 300 && "
            "exec python -m malg.worker",
        ],
    }
    for document in documents:
        name = document.get("metadata", {}).get("name")
        expected_command = startup_commands.get(name)
        if expected_command is None:
            continue
        template = document.get("spec", {}).get("template")
        if not isinstance(template, dict):
            continue
        pod_spec = template.get("spec", {})
        command = pod_spec.get("containers", [{}])[0].get("command")
        if command != expected_command:
            raise ValueError(
                f"{name} must wait read-only for database revision before starting"
            )

    labels = _release_labels(documents)
    if len(labels) != 4 or any(label != release["release_tag"] for label in labels):
        raise ValueError("Flux malg-release labels do not match release_tag")

    migration_policy = next(
        (
            d
            for d in documents
            if d.get("kind") == "CiliumNetworkPolicy"
            and d.get("metadata", {}).get("name") == "malg-migration"
        ),
        None,
    )
    if migration_policy is None:
        raise ValueError("missing migration network policy")
    policy_spec = migration_policy.get("spec", {})
    endpoint = policy_spec.get("endpointSelector", {}).get("matchLabels", {})
    if endpoint.get("app.kubernetes.io/name") != "malg-migration":
        raise ValueError("migration network policy selects the wrong workload")
    egress = policy_spec.get("egress", [])
    if not egress or policy_spec.get("ingress"):
        raise ValueError("migration network policy must be egress-only")
    allowed_ports: set[tuple[str, str]] = set()
    for rule in egress:
        for port_rule in rule.get("toPorts", []):
            for port in port_rule.get("ports", []):
                allowed_ports.add((str(port.get("port")), str(port.get("protocol"))))
        for entity in rule.get("toEntities", []):
            raise ValueError("migration network policy grants an unsafe entity")
        for target in rule.get("toEndpoints", []):
            labels = target.get("matchLabels", {})
            namespace = labels.get("k8s:io.kubernetes.pod.namespace")
            name = labels.get("k8s:app.kubernetes.io/name")
            if (namespace, name) not in {("malg", "postgresql"), ("kube-system", None)}:
                raise ValueError("migration network policy grants an unsafe endpoint")
    if ("5432", "TCP") not in allowed_ports or not {
        ("53", "UDP"),
        ("53", "TCP"),
    }.issubset(allowed_ports):
        raise ValueError("migration network policy must allow PostgreSQL and DNS only")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--allow-reviewed-first-cutover",
        action="store_true",
        help="permit an explicit approval env var in a reviewed first-cutover Job",
    )
    args = parser.parse_args()
    try:
        check(
            args.root.resolve(),
            allow_reviewed_first_cutover=args.allow_reviewed_first_cutover,
        )
    except ValueError as exc:
        print(f"MALG release check failed: {exc}")
        return 1
    print("MALG release tuple is consistent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
