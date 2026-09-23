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
            "HF_HUB_DISABLE_XET": "0",
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
                "HF_HUB_DISABLE_XET",
                "DISABLE_TELEMETRY",
                "DO_NOT_TRACK",
                "HF_HUB_DISABLE_IMPLICIT_TOKEN",
                "HF_HUB_DISABLE_UPDATE_CHECK"):
            self.assertEqual(environment[name], "1")
        self.assertEqual(environment["HF_XET_TELEMETRY_ENABLED"], "0")
        self.assertEqual(environment["HF_HUB_DISABLE_XET"], "1")
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
            HF_HUB_DISABLE_XET=True,
            HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
            HF_HUB_USER_AGENT_ORIGIN=None,
            HF_DEBUG=False,
        )
        transformers_hub = types.SimpleNamespace(SESSION_ID="random-launch-id")
        transformers_hub.http_user_agent = lambda: (
            "transformers/test; session_id/%s" % transformers_hub.SESSION_ID)
        hub_utils = self._hub_utils()

        model_network.harden_loaded_runtime({
            "huggingface_hub.constants": constants,
            "huggingface_hub.utils": hub_utils,
            "huggingface_hub.file_download": self._hub_download(),
            "transformers.utils.hub": transformers_hub,
        }, require_loaded=True)

        self.assertEqual(
            transformers_hub.SESSION_ID,
            model_network.TRANSFORMERS_SESSION_ID,
        )
        hub_utils.build_hf_headers.assert_called_once_with(token=False)

    def test_loaded_runtime_rejects_xet_still_available(self):
        hub_download = self._hub_download()
        hub_download.is_xet_available.return_value = True
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "Xet transfers"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.constants": self._constants(),
                "huggingface_hub.utils": self._hub_utils(),
                "huggingface_hub.file_download": hub_download,
                "transformers.utils.hub": self._transformers_hub(),
            }, require_loaded=True)

    def test_loaded_runtime_rejects_account_or_inherited_request_metadata(self):
        for headers, message in (
                ({"user-agent": "hf_hub/test", "Authorization": "Bearer fixture"},
                 "authentication"),
                ({"user-agent": "hf_hub/test; agent/codex"}, "identifiers"),
                ({"user-agent": "hf_hub/test; origin/private-host"}, "identifiers"),
                ({"user-agent": "hf_hub/test; session_id/random"}, "identifiers")):
            with self.subTest(headers=headers), self.assertRaisesRegex(
                    model_network.ModelNetworkPolicyError, message):
                model_network.harden_loaded_runtime({
                    "huggingface_hub.utils": self._hub_utils(headers),
                })

    def test_required_runtime_rejects_missing_or_unusable_header_builder(self):
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "headers"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.constants": self._constants(),
                "transformers.utils.hub": self._transformers_hub(),
            }, require_loaded=True)
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "headers"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.utils": types.SimpleNamespace(
                    build_hf_headers=lambda **_kwargs: {"x-test": "missing user agent"}),
            })

    def test_loaded_runtime_rejects_noncanonical_endpoint(self):
        constants = types.SimpleNamespace(
            ENDPOINT="https://attacker.example",
            HF_HUB_DISABLE_TELEMETRY=True,
            HF_HUB_DISABLE_XET=True,
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
                        HF_HUB_DISABLE_XET=True,
                        HF_HUB_DISABLE_IMPLICIT_TOKEN=implicit,
                        HF_HUB_USER_AGENT_ORIGIN=None,
                        HF_DEBUG=False,
                    ),
                    "transformers.utils.hub": self._transformers_hub(),
                }, require_loaded=True)

    def test_loaded_runtime_rejects_enabled_xet_transfers(self):
        constants = self._constants()
        constants.HF_HUB_DISABLE_XET = False
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "Xet transfers"):
            model_network.harden_loaded_runtime({
                "huggingface_hub.constants": constants,
                "transformers.utils.hub": self._transformers_hub(),
            }, require_loaded=True)

    def test_required_runtime_cannot_silently_skip_policy_checks(self):
        with self.assertRaisesRegex(
                model_network.ModelNetworkPolicyError, "could not be verified"):
            model_network.harden_loaded_runtime({}, require_loaded=True)

    def test_runtime_rejects_policy_environment_changed_after_startup(self):
        for name, value in (("HF_HUB_DISABLE_XET", "0"),
                            ("HF_XET_TELEMETRY_ENABLED", "1")):
            with self.subTest(name=name), mock.patch.dict(
                    os.environ, {name: value}), self.assertRaisesRegex(
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
                    HF_HUB_DISABLE_XET=True,
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
                    HF_HUB_DISABLE_XET=True,
                    HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
                    HF_HUB_USER_AGENT_ORIGIN=None,
                    HF_DEBUG=False,
                ),
                "huggingface_hub.utils": self._hub_utils(),
                "huggingface_hub.file_download": self._hub_download(),
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
                    HF_HUB_DISABLE_XET=True,
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
        if (importlib.util.find_spec("huggingface_hub") is None or
                importlib.util.find_spec("transformers") is None):
            self.skipTest(
                "installed Hugging Face stack is exercised by Windows CI/package qualification")
        # A fresh interpreter avoids a preceding test masking an import-order
        # error. Only synthetic credentials are supplied; none are transmitted.
        code = """import engine, model_network
from huggingface_hub import constants, file_download, utils
from huggingface_hub.utils import _detect_agent
from transformers.utils import hub as transformers_hub
registry_fetches = []
_detect_agent._registry = None
_detect_agent._read_cached_registry = lambda *_args, **_kwargs: None
_detect_agent._fetch_registry = lambda: registry_fetches.append(True) or {}
assert constants.ENDPOINT == 'https://huggingface.co'
assert constants.HF_DEBUG is False
assert constants.HF_HUB_DISABLE_TELEMETRY is True
assert constants.HF_HUB_DISABLE_XET is True
assert file_download.is_xet_available() is False
assert constants.HF_HUB_DISABLE_IMPLICIT_TOKEN is True
assert constants.HF_HUB_USER_AGENT_ORIGIN is None
model_network.harden_loaded_runtime()
headers = {str(name).lower(): str(value) for name, value in utils.build_hf_headers(token=False).items()}
assert 'authorization' not in headers
assert all(marker not in headers['user-agent'].lower() for marker in ('agent/', 'origin/', 'session_id/'))
assert not registry_fetches, 'telemetry-disabled header construction attempted an agent-registry request'
assert transformers_hub.SESSION_ID == model_network.TRANSFORMERS_SESSION_ID
transformers_user_agent = transformers_hub.http_user_agent()
assert 'session_id/telemetry-off' in transformers_user_agent
assert 'telemetry/off' in transformers_user_agent
"""
        environment = dict(os.environ, HF_DEBUG="1", HF_ENDPOINT="https://invalid.example",
                           HF_TOKEN="synthetic-fixture", HF_HUB_USER_AGENT_ORIGIN="synthetic-origin",
                           HF_HUB_DISABLE_TELEMETRY="0", HF_HUB_DISABLE_IMPLICIT_TOKEN="0",
                           AI_AGENT="synthetic-agent")
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

    @staticmethod
    def _constants():
        return types.SimpleNamespace(
            ENDPOINT="https://huggingface.co",
            HF_HUB_DISABLE_TELEMETRY=True,
            HF_HUB_DISABLE_XET=True,
            HF_HUB_DISABLE_IMPLICIT_TOKEN=True,
            HF_HUB_USER_AGENT_ORIGIN=None,
            HF_DEBUG=False,
        )

    @staticmethod
    def _hub_utils(headers=None):
        return types.SimpleNamespace(
            build_hf_headers=mock.Mock(return_value=(
                {"user-agent": "unknown/None; hf_hub/test; python/test"}
                if headers is None else headers
            )),
        )

    @staticmethod
    def _hub_download():
        return types.SimpleNamespace(is_xet_available=mock.Mock(return_value=False))


if __name__ == "__main__":
    unittest.main()
