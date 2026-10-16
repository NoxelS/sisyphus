import copy
import unittest

import yaml

from scripts import check_litellm_release


class LiteLLMReleaseTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.chart = yaml.safe_load(check_litellm_release.CHART.read_text())
        cls.release = yaml.safe_load(check_litellm_release.RELEASE.read_text())
        cls.kustomization = yaml.safe_load(
            check_litellm_release.KUSTOMIZATION.read_text()
        )

    def validate(self, release: dict, kustomization: dict | None = None) -> None:
        check_litellm_release.validate_documents(
            self.chart,
            release,
            kustomization or self.kustomization,
        )

    def test_repository_release_is_safe(self) -> None:
        self.validate(self.release)

    def test_rejects_upgrade_without_migration_headroom(self) -> None:
        release = copy.deepcopy(self.release)
        release["spec"]["values"]["resources"]["requests"]["memory"] = "4Gi"

        with self.assertRaisesRegex(ValueError, "single-node upgrade budget"):
            self.validate(release)

    def test_rejects_migration_request_that_cannot_bootstrap(self) -> None:
        release = copy.deepcopy(self.release)
        migration = release["spec"]["values"]["migrationJob"]
        migration["resources"]["requests"]["memory"] = "2Gi"

        with self.assertRaisesRegex(ValueError, "bootstrap headroom"):
            self.validate(release)

    def test_rejects_helm_timeout_shorter_than_migration(self) -> None:
        release = copy.deepcopy(self.release)
        release["spec"]["timeout"] = "15m"

        with self.assertRaisesRegex(ValueError, "migration deadline"):
            self.validate(release)

    def test_rejects_kustomization_timeout_without_margin(self) -> None:
        kustomization = copy.deepcopy(self.kustomization)
        kustomization["spec"]["timeout"] = "20m"

        with self.assertRaisesRegex(ValueError, "Kustomization timeout"):
            self.validate(self.release, kustomization)


if __name__ == "__main__":
    unittest.main()
