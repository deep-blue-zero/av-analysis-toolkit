"""Deliberately small registry: hosted routes are never chosen automatically."""
from ..common import AVError


def providers():
    from ..auditory_profiles import TASK_PROFILES
    return {"schema": "ave.observer-providers.v1", "default_backend": "mock", "providers": [
        {"backend_id": "mock", "route_type": "mock", "auditory_perception": False},
        {"backend_id": "human", "route_type": "human", "interface": "observer human-import"},
        {"backend_id": "openai-audio", "provider": "openai", "route_type": "hosted",
         "model": "gpt-audio-1.5", "experimental_models": ["gpt-audio-2025-08-28"],
         "model_selection": "Explicit --model only; no automatic fallback; separate matching probe required",
         "api_route": "/v1/chat/completions",
         "task_profiles": {name: "UNQUALIFIED" for name in TASK_PROFILES},
         "requires": ["explicit backend selection", "remote-media authorization", "environment credential",
                      "matching passed semantic probe", "local budget"],
         "live_validation": "OPERATOR_PROBE_REQUIRED", "batch": "DEFERRED", "realtime": "DEFERRED"}]}


def create_backend(backend_id="mock", **configuration):
    if backend_id == "mock":
        if configuration:
            raise AVError("Mock backend does not accept hosted configuration")
        from ..auditory_observer import MockObserver
        return MockObserver()
    if backend_id == "openai-audio":
        from .openai_audio import OpenAIAudioObserver
        return OpenAIAudioObserver(**configuration)
    raise AVError("Unknown executable observer backend; human reviews use human-import")
