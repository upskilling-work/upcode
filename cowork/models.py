"""LLM profiles configurable via JSON.

The file (``<UPCODE_HOME_DIR>/conf/models.json``) describes a list of models
that the user can select at runtime with ``/model``.

Format:

    {
      "models": [
        {
          "name": "gpt-4o-mini",          // identifier used in /model
          "label": "OpenAI GPT-4o mini",  // friendly description (optional)
          "model": "gpt-4o-mini",         // model id on the API
          "base_url": "https://api.openai.com/v1",
          "api_key": "sk-...",            // literal key, OR
          "api_key_env": "OPENAI_API_KEY" // name of an environment variable
        }
      ]
    }
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass

from .agent import home_dir


@dataclass
class ModelProfile:
    """A selectable LLM profile."""

    name: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    label: str = ""
    provider: str = ""
    # Name of the environment variable suggested for the key (e.g. "OPENAI_API_KEY").
    api_key_env: str | None = None
    # "chat" (chat/completions) or "responses" (codex/GPT-5 models).
    api: str = "chat"
    # Context window (input tokens) and max output — informational and, in the
    # case of max_output, used as the request's max_tokens.
    context_window: int | None = None
    max_output: int | None = None
    # Sampling temperature. Optional; if absent, keeps the agent's default.
    # For coding models a low value is advisable (e.g. 0.0–0.2).
    temperature: float | None = None
    # Pricing in USD per 1,000,000 tokens (models.dev convention). Drive the
    # cost meter; optional (absent = no cost shown for this model).
    input_cost: float | None = None
    output_cost: float | None = None
    # Extended-thinking budget in tokens (native Anthropic provider). >0 enables
    # it. ``reasoning_effort`` (low/medium/high) is mapped to a budget when set.
    thinking_budget: int | None = None
    reasoning_effort: str | None = None


def _conf_dir() -> str:
    """Agent configuration directory: ``<UPCODE_HOME_DIR>/conf``.

    Absolute/stable path — does NOT depend on the current directory, which
    changes when the agent switches workspace (``apply_workspace``/``/workspace``)."""
    return os.path.join(home_dir(), "conf")


def default_models_path() -> str:
    """Path of ``models.json`` (``<UPCODE_HOME_DIR>/conf/models.json``)."""
    return os.path.join(_conf_dir(), "models.json")


def load_models(path: str | None = None) -> dict[str, ModelProfile]:
    """Load the profiles from JSON. Returns ``{}`` if the file does not exist.

    Raises ``ValueError`` if the JSON is invalid or malformed, so the interface
    can warn the user instead of failing silently.
    """
    path = path or default_models_path()
    if not os.path.isfile(path):
        return {}

    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"could not read '{path}': {exc}") from exc

    out: dict[str, ModelProfile] = {}
    for item in data.get("models", []):
        name = item.get("name")
        if not name:
            continue
        api_key = item.get("api_key")
        if not api_key and item.get("api_key_env"):
            api_key = os.getenv(item["api_key_env"])
        out[name] = ModelProfile(
            name=name,
            model=item.get("model", name),
            base_url=item.get("base_url"),
            api_key=api_key,
            label=item.get("label", ""),
            provider=item.get("provider", ""),
            api_key_env=item.get("api_key_env"),
            api=item.get("api", "chat"),
            context_window=item.get("context_window"),
            max_output=item.get("max_output"),
            temperature=item.get("temperature"),
            input_cost=item.get("input_cost"),
            output_cost=item.get("output_cost"),
            thinking_budget=item.get("thinking_budget"),
            reasoning_effort=item.get("reasoning_effort"),
        )
    return out


# reasoning_effort -> thinking budget (tokens) for the native Anthropic provider.
_EFFORT_BUDGET = {"low": 4000, "medium": 8000, "high": 16000}


def thinking_budget_for(profile: ModelProfile) -> int:
    """Resolve the thinking budget (tokens) for a profile (0 = disabled)."""
    if profile.thinking_budget:
        return int(profile.thinking_budget)
    if profile.reasoning_effort:
        return _EFFORT_BUDGET.get(profile.reasoning_effort.lower(), 0)
    return 0


def last_config_path() -> str:
    """Path of the state file (``<UPCODE_HOME_DIR>/conf/state.json``)."""
    return os.path.join(_conf_dir(), "state.json")


def save_last_config(profile: ModelProfile, path: str | None = None) -> None:
    """Save (best-effort) the last selected model.

    Writes the profile's ``name`` and the resolved ``api_key`` (to also restore
    models whose key was typed on the fly). The file lives in ``conf/`` and is
    ignored by git. Write failures are ignored (they don't break the session).
    """
    path = path or last_config_path()
    try:
        folder = os.path.dirname(path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"model": profile.name, "api_key": profile.api_key},
                      fh, ensure_ascii=False, indent=2)
            fh.write("\n")
    except OSError:
        pass


def load_last_config(path: str | None = None) -> dict | None:
    """Return the saved JSON (``{"model", "api_key"}``) or ``None`` if missing/invalid."""
    path = path or last_config_path()
    if not os.path.isfile(path):
        return None
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("model"):
        return None
    return data


def resolve_last_profile(models: dict[str, ModelProfile],
                         path: str | None = None) -> ModelProfile | None:
    """Rebuild the :class:`ModelProfile` of the last saved model.

    Uses ``models.json`` as the source of truth (base_url, limits, temperature,
    key via environment) and, if the key didn't come from there, falls back to
    the saved ``api_key``. Returns ``None`` if there is no saved config or the
    model no longer exists."""
    data = load_last_config(path)
    if data is None:
        return None
    prof = models.get(data["model"])
    if prof is None:
        return None
    if not prof.api_key and data.get("api_key"):
        prof = dataclasses.replace(prof, api_key=data["api_key"])
    return prof


def is_local(profile: ModelProfile) -> bool:
    """Local model (LM Studio/Ollama/etc.) — does not require an API key."""
    url = (profile.base_url or "").lower()
    return any(h in url for h in ("localhost", "127.0.0.1", "0.0.0.0"))


def needs_api_key(profile: ModelProfile) -> bool:
    """True if the key is missing and the endpoint is not local."""
    return not profile.api_key and not is_local(profile)
