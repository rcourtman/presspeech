"""Process-wide privacy policy for Windows speech-model downloads.

This module must be imported before Transformers, faster-whisper, or
huggingface_hub.  Those libraries snapshot several environment variables at
import time, so setting the policy only when a model starts loading is too
late to uphold Presspeech's fixed-endpoint, unauthenticated, no-telemetry
contract.
"""

import os
import sys


HUGGING_FACE_ENDPOINT = "https://huggingface.co"
TRANSFORMERS_SESSION_ID = "telemetry-off"

# These are product policy, not user preferences.  Presspeech uses public,
# pinned model snapshots and never needs a Hugging Face account.  Set all
# ecosystem-wide spellings because different bundled libraries consult
# different flags, and overwrite false inherited values rather than merely
# supplying defaults.
FORCED_ENVIRONMENT = {
    "HF_ENDPOINT": HUGGING_FACE_ENDPOINT,
    "HUGGINGFACE_CO_STAGING": "0",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    # Forward-compatible opt-out only: pinned hf-xet 1.6.0 has no such
    # telemetry switch. A future runtime must be reviewed independently;
    # setting this variable is not evidence that a binary implements it.
    "HF_XET_TELEMETRY_ENABLED": "0",
    "DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    # The packaged app never invokes the hf CLI, but this also prevents a
    # future library path from adding an undocumented PyPI version check.
    "HF_HUB_DISABLE_UPDATE_CHECK": "1",
}

# huggingface_hub appends the caller-controlled origin to every request's
# User-Agent even when general telemetry is disabled.  Presspeech also has no
# authenticated model path, so do not retain account secrets in a process that
# never needs them (the explicit per-call flags below remain the primary gate).
REMOVED_ENVIRONMENT = (
    "HF_DEBUG",
    "HF_HUB_USER_AGENT_ORIGIN",
    "HF_TOKEN",
    "HUGGING_FACE_HUB_TOKEN",
    "HUGGINGFACEHUB_API_TOKEN",
)


class ModelNetworkPolicyError(RuntimeError):
    """The loaded model client does not match Presspeech's privacy policy."""


def enforce_environment(environ=None):
    """Apply the model-network policy before Hugging Face modules import."""
    target = os.environ if environ is None else environ
    for name in REMOVED_ENVIRONMENT:
        target.pop(name, None)
    target.update(FORCED_ENVIRONMENT)


def harden_loaded_runtime(modules=None, require_loaded=False):
    """Verify import-time settings and request metadata at the runtime boundary.

    Hugging Face's documented telemetry opt-out suppresses telemetry calls and
    extra host metadata, but Transformers 5.16.1 still puts a random process
    session ID in model-download User-Agents.  Replace it with a fixed,
    non-unique token so ordinary model requests cannot be correlated to one
    app launch.
    The release package self-test uses ``require_loaded`` to fail closed if a
    dependency update moves or removes either policy surface.
    """
    loaded = sys.modules if modules is None else modules

    for name, expected in FORCED_ENVIRONMENT.items():
        if os.environ.get(name) != expected:
            raise ModelNetworkPolicyError(
                "speech-model network privacy environment changed after startup")
    if any(name in os.environ for name in REMOVED_ENVIRONMENT):
        raise ModelNetworkPolicyError(
            "speech-model request metadata changed after startup")

    constants = loaded.get("huggingface_hub.constants")
    hub_utils = loaded.get("huggingface_hub.utils")
    transformers_hub = loaded.get("transformers.utils.hub")

    if require_loaded and constants is None:
        raise ModelNetworkPolicyError(
            "Hugging Face privacy policy could not be verified")

    if constants is not None:
        endpoint = str(getattr(constants, "ENDPOINT", "")).rstrip("/")
        if endpoint != HUGGING_FACE_ENDPOINT:
            raise ModelNetworkPolicyError(
                "speech-model endpoint is not the public Hugging Face Hub")
        if getattr(constants, "HF_DEBUG", None) is not False:
            raise ModelNetworkPolicyError(
                "Hugging Face request debug logging is not disabled")
        if not getattr(constants, "HF_HUB_DISABLE_TELEMETRY", False):
            raise ModelNetworkPolicyError(
                "Hugging Face telemetry is not disabled")
        if not getattr(constants, "HF_HUB_DISABLE_IMPLICIT_TOKEN", False):
            raise ModelNetworkPolicyError(
                "implicit Hugging Face authentication is not disabled")
        if getattr(constants, "HF_HUB_USER_AGENT_ORIGIN", None) is not None:
            raise ModelNetworkPolicyError(
                "inherited Hugging Face request origin is not disabled")

    if require_loaded and hub_utils is None:
        raise ModelNetworkPolicyError(
            "Hugging Face request headers could not be verified")
    if hub_utils is not None:
        build_headers = getattr(hub_utils, "build_hf_headers", None)
        if require_loaded and not callable(build_headers):
            raise ModelNetworkPolicyError(
                "Hugging Face request headers could not be verified")
        if callable(build_headers):
            # This is the same explicit account-token boundary used by every
            # snapshot_download call. Inspect the bundled client's rendered
            # result as well as its cached constants so a dependency change
            # cannot silently reintroduce credentials or environment-derived
            # identifiers while leaving the opt-out booleans unchanged.
            try:
                headers = build_headers(token=False)
            except Exception as exc:
                raise ModelNetworkPolicyError(
                    "Hugging Face request headers could not be verified") from exc
            if not isinstance(headers, dict):
                raise ModelNetworkPolicyError(
                    "Hugging Face request headers could not be verified")
            normalized = {str(name).lower(): str(value)
                          for name, value in headers.items()}
            if "authorization" in normalized:
                raise ModelNetworkPolicyError(
                    "Hugging Face account authentication is not disabled")
            user_agent = normalized.get("user-agent", "")
            if not user_agent:
                raise ModelNetworkPolicyError(
                    "Hugging Face request headers could not be verified")
            forbidden_metadata = ("agent/", "origin/", "session_id/")
            if any(marker in user_agent.lower() for marker in forbidden_metadata):
                raise ModelNetworkPolicyError(
                    "inherited Hugging Face request identifiers are not disabled")

    if require_loaded and transformers_hub is None:
        raise ModelNetworkPolicyError(
            "Transformers privacy policy could not be verified")
    if transformers_hub is not None:
        if not hasattr(transformers_hub, "SESSION_ID"):
            raise ModelNetworkPolicyError(
                "Transformers session identifier could not be fixed")
        transformers_hub.SESSION_ID = TRANSFORMERS_SESSION_ID
        user_agent = getattr(transformers_hub, "http_user_agent", None)
        if require_loaded and not callable(user_agent):
            raise ModelNetworkPolicyError(
                "Transformers request identifier could not be verified")
        if callable(user_agent):
            rendered = str(user_agent())
            if "session_id/%s" % TRANSFORMERS_SESSION_ID not in rendered:
                raise ModelNetworkPolicyError(
                    "Transformers request identifier could not be fixed")


# Import-time execution is intentional; see the module docstring.
enforce_environment()
