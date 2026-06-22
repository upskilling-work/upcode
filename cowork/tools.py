"""Tool system (function calling) for the Upcode agent.

Use the ``@tool`` decorator to turn a plain Python function into a tool the
model can call. The JSON schema is derived automatically from the function's
signature and type annotations.
"""

from __future__ import annotations

import inspect
import json
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, get_args, get_origin


# Mapping of Python types -> JSON Schema types.
_JSON_TYPES: dict[type, str] = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _json_type(annotation: Any) -> dict[str, Any]:
    """Convert a Python type annotation into a JSON Schema fragment."""
    if annotation is inspect.Parameter.empty or annotation is Any:
        return {"type": "string"}

    origin = get_origin(annotation)

    # Optional[X] / X | None -> uses the first non-None type.
    if origin in (typing.Union, getattr(__import__("types"), "UnionType", None)):
        args = [a for a in get_args(annotation) if a is not type(None)]
        return _json_type(args[0]) if args else {"type": "string"}

    if origin in (list, typing.List):
        args = get_args(annotation)
        item = _json_type(args[0]) if args else {"type": "string"}
        return {"type": "array", "items": item}

    if origin in (dict, typing.Dict):
        return {"type": "object"}

    return {"type": _JSON_TYPES.get(annotation, "string")}


def _coerce_args(parameters: dict[str, Any], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Convert arguments to the type declared in the schema.

    Models often send numbers/booleans as strings (e.g. ``"10000"``). Here we
    convert based on the JSON Schema ``type`` so the Python function receives a
    real ``int``/``float``/``bool``. Values that don't convert are left as-is
    (the function decides what to do).
    """
    props = parameters.get("properties", {})
    out: dict[str, Any] = {}
    for key, value in kwargs.items():
        expected = props.get(key, {}).get("type")
        if isinstance(value, str):
            try:
                if expected == "integer":
                    value = int(value)
                elif expected == "number":
                    value = float(value)
                elif expected == "boolean":
                    value = value.strip().lower() in ("true", "1", "yes", "sim")
            except ValueError:
                pass  # leave as string; the tool handles the error
        out[key] = value
    return out


@dataclass
class Tool:
    """An executable tool exposed to the model."""

    func: Callable[..., Any]
    name: str
    description: str
    parameters: dict[str, Any]

    def schema(self) -> dict[str, Any]:
        """Schema in the format expected by the API (``tools`` field)."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }

    def __call__(self, **kwargs: Any) -> Any:
        return self.func(**kwargs)


def tool(func: Callable[..., Any] | None = None, *, name: str | None = None,
         description: str | None = None) -> Any:
    """Decorator that registers a function as a :class:`Tool`.

    The default name is the function name and the description comes from the
    docstring. Each parameter becomes a schema property; parameters without a
    default value are marked as required.
    """

    def wrap(fn: Callable[..., Any]) -> Tool:
        sig = inspect.signature(fn)
        # Resolve real annotations: with `from __future__ import annotations`
        # (PEP 563) they arrive as strings, so we use get_type_hints.
        try:
            hints = typing.get_type_hints(fn)
        except Exception:  # noqa: BLE001 — unresolvable annotations become strings
            hints = {}
        props: dict[str, Any] = {}
        required: list[str] = []

        for pname, param in sig.parameters.items():
            if pname == "self" or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue
            annotation = hints.get(pname, param.annotation)
            props[pname] = _json_type(annotation)
            if param.default is inspect.Parameter.empty:
                required.append(pname)

        params_schema = {
            "type": "object",
            "properties": props,
            "required": required,
        }
        return Tool(
            func=fn,
            name=name or fn.__name__,
            description=(description or inspect.getdoc(fn) or "").strip(),
            parameters=params_schema,
        )

    return wrap if func is None else wrap(func)


@dataclass
class ToolRegistry:
    """Collection of tools indexed by name."""

    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, t: Tool) -> Tool:
        self._tools[t.name] = t
        return t

    def add(self, *tools: Tool) -> None:
        for t in tools:
            self.register(t)

    def schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self._tools.values()]

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def call(self, name: str, arguments: str | dict[str, Any]) -> str:
        """Run the tool ``name`` and return the result as a string.

        ``arguments`` can be the raw JSON coming from the model or a dict. Errors
        are caught and returned as text so the model can react.
        """
        if name not in self._tools:
            return f"Error: unknown tool '{name}'."

        if isinstance(arguments, str):
            try:
                kwargs = json.loads(arguments or "{}")
            except json.JSONDecodeError as exc:
                return f"Error: invalid JSON arguments ({exc})."
        else:
            kwargs = arguments or {}

        kwargs = _coerce_args(self._tools[name].parameters, kwargs)

        try:
            result = self._tools[name](**kwargs)
        except Exception as exc:  # noqa: BLE001 — returns the error to the model
            return f"Error running '{name}': {exc}"

        return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)
