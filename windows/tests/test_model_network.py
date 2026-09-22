import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

import model_network


class ModelNetworkPolicyTests(unittest.TestCase):
    def test_environment_policy_overrides_inherited_opt_ins_and_endpoint(self):
        environment = {
            "HF_DEBUG": "1",
            "HF_ENDPOINT": "https://attacker.example",
            "HUGGINGFACE_CO_STAGING": "1",
            "HF_HUB_DISABLE_TELEMETRY": "0",
            "HF_XET_TELEMETRY_ENABLED": "1",
            "DISABLE_TELEMETRY": "false",
            "DO_NOT_TRACK": "no",
            "HF_HUB_DISABLE_IMPLICIT_TOKEN": "0",
            "HF_HUB_DISABLE_UPDATE_CHECK": "0",
            "HF_HUB_USER_AGENT_ORIGIN": "private-workstation",
            "HF_TOKEN": "test-token",
            "HUGGING_FACE_HUB_TOKEN": "legacy-test-token",
            "HUGGINGFACEHUB_API_TOKEN": "legacy-api-test-token",
            "HF_HUB_CACHE": r"C:\model-cache",
            "HF_HOME": r"C:\private-cache",
            "HF_HUB_OFFLINE": "1",
        }

        model_network.enforce_environment(environment)

        self.assertEqual(
            environment["HF_ENDPOINT"],
            model_network.HUGGING_FACE_ENDPOINT,
        )
        self.assertEqual(environment["HUGGINGFACE_CO_STAGING"], "0")
        for name in (
                "HF_HUB_DISABLE_TELEMETRY",
                "DISABLE_TELEMETRY",
                "DO_NOT_TRACK",
                "HF_HUB_DISABLE_IMPLICIT_TOKEN",
                "HF_HUB_DISABLE_UPDATE_CHECK"):
            self.assertEqual(environment[name], "1")
        self.assertEqual(environment["HF_XET_TELEMETRY_ENABLED"], "0")
        for name in model_network.REMOVED_ENVIRONMENT:
            self.assertNotIn(name, environment)
        self.assertEqual(environment["HF_HUB_CACHE"], r"C:\model-cache")
        self.assertEqual(environment["HF_HOME"], r"C:\private-cache")
        self.assertEqual(environment["HF_HUB_OFFLINE"], "1")

    def test_import_applies_policy_to_the_real_process(self):
        for name, expected in model_network.FORCED_ENVIRONMENT.items():
            self.assertEqual(os.environ.get(name), expected)
        for name in model_network.REMOVED_ENVIRONMENT:
            self.assertNotIn(name, os.environ)

    def test_loaded_runtime_is_verified_and_session_id_is_fixed(self):
        constants = types.SimpleNamespace(
            ENDPOINT="https://huggingface.co/",
            HF_HUB_DISABLE_TELEMETRY=True,
            HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
            HF_HUB_USER_AGENT_ORIGIN=None,
            HF_DEBUG=False,
        )
        transformers_hub = types.SimpleNamespace(SESSION_ID="random-launch-id")
        transformers_hub.http_user_agent = lambda: (
            "transformers/test; session_id/%s" % transformers_hub.SESSION_ID)

        model_network.harden_loaded_runtime({
            "huggingface_hub.constants": constants,
            "transformers.utils.hub": transformers_hub,
        }, require_loaded=True)

        self.assertEqual(
            transformers_hub.SESSION_ID,
            model_network.TRANSFORMERS_SESSION_ID,
        )

    def test_loaded_runtime_rejects_noncanonical_endpoint(self):
        constants = types.SimpleNamespace(
            ENDPOINT="https://attacker.example",
            HF_HUB_DISABLE_TELEMETRY=True,
            HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
            HF_HUB_USER_AGENT_ORIGIN=None,
            HF_DEBUG=False,
        )
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "endpoint"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.constants": constants,
                "transformers.utils.hub": self._transformers_hub(),
            }, require_loaded=True)

    def test_loaded_runtime_rejects_telemetry_or_implicit_authentication(self):
        for telemetry, implicit, message in (
                (False, True, "telemetry"),
                (True, False, "authentication")):
            with self.subTest(message=message), self.assertRaisesRegex(
                    model_network.ModelNetworkPolicyError, message):
                model_network.harden_loaded_runtime({
                    "huggingface_hub.constants": types.SimpleNamespace(
                        ENDPOINT="https://huggingface.co",
                        HF_HUB_DISABLE_TELEMETRY=telemetry,
                        HF_HUB_DISABLE_IMPLICIT_TOKEN=implicit,
                        HF_HUB_USER_AGENT_ORIGIN=None,
                        HF_DEBUG=False,
                    ),
                    "transformers.utils.hub": self._transformers_hub(),
                }, require_loaded=True)

    def test_required_runtime_cannot_silently_skip_policy_checks(self):
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "could not be verified"):
            model_network.harden_loaded_runtime({}, require_loaded=True)

    def test_runtime_rejects_policy_environment_changed_after_startup(self):
        with mock.patch.dict(
                os.environ, {"HF_XET_TELEMETRY_ENABLED": "1"}), \
                self.assertRaisesRegex(
                    model_network.ModelNetworkPolicyError,
                    "environment changed"):
            model_network.harden_loaded_runtime({})

    def test_runtime_rejects_a_token_added_after_startup(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": "late-token"}), \
                self.assertRaisesRegex(
                    model_network.ModelNetworkPolicyError,
                    "request metadata changed"):
            model_network.harden_loaded_runtime({})

    def test_loaded_runtime_rejects_inherited_user_agent_origin(self):
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "request origin"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.constants": types.SimpleNamespace(
                    ENDPOINT="https://huggingface.co",
                    HF_HUB_DISABLE_TELEMETRY=True,
                    HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
                    HF_HUB_USER_AGENT_ORIGIN="private-workstation",
                    HF_DEBUG=False,
                ),
                "transformers.utils.hub": self._transformers_hub(),
            }, require_loaded=True)

    def test_loaded_runtime_rejects_an_unfixable_request_identifier(self):
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "identifier"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.constants": types.SimpleNamespace(
                    ENDPOINT="https://huggingface.co",
                    HF_HUB_DISABLE_TELEMETRY=True,
                    HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
                    HF_HUB_USER_AGENT_ORIGIN=None,
                    HF_DEBUG=False,
                ),
                "transformers.utils.hub": types.SimpleNamespace(
                    SESSION_ID="random",
                    http_user_agent=lambda: "session_id/still-random",
                ),
            }, require_loaded=True)

    def test_cached_debug_state_fails_even_after_environment_is_cleared(self):
        for cached in (True, None):
            with self.subTest(cached=cached):
                constants = types.SimpleNamespace(
                    ENDPOINT="https://huggingface.co",
                    HF_DEBUG=cached,
                    HF_HUB_DISABLE_TELEMETRY=True,
                    HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
                    HF_HUB_USER_AGENT_ORIGIN=None,
                )
                with self.assertRaisesRegex(
                        model_network.ModelNetworkPolicyError, "debug logging"):
                    model_network.harden_loaded_runtime({
                        "huggingface_hub.constants": constants,
                        "transformers.utils.hub": self._transformers_hub(),
                    }, require_loaded=True)

    def test_cached_debug_setting_is_required(self):
        constants = types.SimpleNamespace(ENDPOINT="https://huggingface.co")
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "debug logging"):
            model_network.harden_loaded_runtime({"huggingface_hub.constants": constants})

    def test_debug_reintroduced_after_startup_is_rejected(self):
        with mock.patch.dict(os.environ, {"HF_DEBUG": "1"}), self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "request metadata changed"):
            model_network.harden_loaded_runtime({})

    def test_installed_hub_reads_policy_before_its_first_import(self):
        if importlib.util.find_spec("huggingface_hub") is None:
            self.skipTest("installed Hub is exercised by Windows CI/package qualification")
        # A fresh interpreter avoids a preceding test masking an import-order
        # error. Only synthetic credentials are supplied; none are transmitted.
        code = """import engine, model_network
from huggingface_hub import constants
assert constants.ENDPOINT == 'https://huggingface.co'
assert constants.HF_DEBUG is False
assert constants.HF_HUB_DISABLE_TELEMETRY is True
assert constants.HF_HUB_DISABLE_IMPLICIT_TOKEN is True
assert constants.HF_HUB_USER_AGENT_ORIGIN is None
model_network.harden_loaded_runtime()
"""
        environment = dict(os.environ, HF_DEBUG="1", HF_ENDPOINT="https://invalid.example",
                           HF_TOKEN="synthetic-fixture", HF_HUB_USER_AGENT_ORIGIN="synthetic-origin",
                           HF_HUB_DISABLE_TELEMETRY="0", HF_HUB_DISABLE_IMPLICIT_TOKEN="0")
        result = subprocess.run([sys.executable, "-c", code],
                                cwd=Path(__file__).resolve().parents[1], env=environment,
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)

    @staticmethod
    def _transformers_hub():
        module = types.SimpleNamespace(SESSION_ID="random")
        module.http_user_agent = lambda: (
            "transformers/test; session_id/%s" % module.SESSION_ID)
        return module


if __name__ == "__main__":
    unittest.main()
