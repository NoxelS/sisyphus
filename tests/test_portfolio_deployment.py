import copy
import unittest

import yaml

from scripts import check_portfolio_deployment as portfolio


class PortfolioDeploymentTest(unittest.TestCase):
    def setUp(self):
        self.source = (portfolio.PRODUCTION / "deployment.yaml").read_text()
        self.deployment = yaml.safe_load(self.source)
        self.configmap = yaml.safe_load((portfolio.PRODUCTION / "configmap.yaml").read_text())
        documents = {
            document["kind"]: document
            for document in yaml.safe_load_all(
                (portfolio.PRODUCTION / "image-automation.yaml").read_text()
            )
        }
        self.policy = documents["ImagePolicy"]
        self.automation = documents["ImageUpdateAutomation"]
        self.container = self.deployment["spec"]["template"]["spec"]["containers"][0]

    def validate(self):
        portfolio.validate_documents(
            self.deployment, self.configmap, self.policy, self.automation
        )

    def test_repository_production_is_valid(self):
        self.validate()
        portfolio.validate_image_marker(self.source)

    def test_new_stable_releases_are_accepted(self):
        for version in ("0.0.2", "1.0.0", "12.34.56"):
            with self.subTest(version=version):
                self.container["image"] = (
                    f"ghcr.io/noxels/portfolio-frontend:{version}@sha256:" + "a" * 64
                )
                self.validate()

    def test_rejects_staging_mutable_and_unpinned_images(self):
        for image in (
            "0.0.2-staging.1@sha256:" + "a" * 64,
            "0.0.2-rc.1@sha256:" + "a" * 64,
            "v0.0.2@sha256:" + "a" * 64,
            "latest@sha256:" + "a" * 64,
            "sha-abc123@sha256:" + "a" * 64,
            "0.0.2",
            "0.0.2@sha256:invalid",
            "0.0.0@sha256:" + "a" * 64,
        ):
            with self.subTest(image=image):
                self.container["image"] = "ghcr.io/noxels/portfolio-frontend:" + image
                with self.assertRaisesRegex(ValueError, "stable SemVer"):
                    self.validate()

    def test_rejects_staging_configuration_and_environment_override(self):
        self.configmap["data"]["PUBLIC_DEPLOYMENT_ENV"] = "staging"
        with self.assertRaisesRegex(ValueError, "production configuration"):
            self.validate()
        self.configmap["data"]["PUBLIC_DEPLOYMENT_ENV"] = "production"
        self.container["env"] = [{"name": "PUBLIC_DEPLOYMENT_ENV", "value": "staging"}]
        with self.assertRaisesRegex(ValueError, "production configuration"):
            self.validate()

    def test_rejects_staging_namespace(self):
        self.deployment["metadata"]["namespace"] = "portfolio-staging"
        with self.assertRaisesRegex(ValueError, "own namespace"):
            self.validate()

    def test_rejects_policy_that_accepts_staging_tags(self):
        self.policy["spec"]["filterTags"]["pattern"] = ".*"
        with self.assertRaisesRegex(ValueError, "only stable"):
            self.validate()

    def test_rejects_policy_that_can_change_a_released_digest(self):
        self.policy["spec"]["digestReflectionPolicy"] = "Always"
        with self.assertRaisesRegex(ValueError, "immutable release"):
            self.validate()

    def test_rejects_automation_writing_outside_production(self):
        original = copy.deepcopy(self.automation)
        for section, key, value in (
            ("push", "branch", "main"),
            ("push", "branch", "flux/portfolio-staging-image"),
            ("checkout", "ref", {"branch": "production"}),
        ):
            with self.subTest(section=section, value=value):
                self.automation = copy.deepcopy(original)
                self.automation["spec"]["git"][section][key] = value
                with self.assertRaisesRegex(ValueError, "own path and branch"):
                    self.validate()
        self.automation = original
        self.automation["spec"]["update"]["path"] = "./infrastructure/portfolio-staging"
        with self.assertRaisesRegex(ValueError, "own path and branch"):
            self.validate()

    def test_rollback_can_suspend_image_updates(self):
        self.automation["spec"]["suspend"] = True
        self.validate()

    def test_rejects_missing_or_staging_image_marker(self):
        for source in (
            self.source.replace(
                '# {"$imagepolicy": "flux-system:portfolio-production"}', ""
            ),
            self.source.replace(
                '"flux-system:portfolio-production"', '"flux-system:portfolio-staging"'
            ),
        ):
            with self.subTest(source=source):
                with self.assertRaisesRegex(ValueError, "policy marker"):
                    portfolio.validate_image_marker(source)
