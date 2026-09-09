"""Minimal injectable provider boundary reusing chat_client protocol helpers.

No default network transport or credential discovery. Live transports and a
bounded shared chat loop are intentionally not enabled in this checkpoint.
"""
import copy
import json
from collections.abc import Callable

ORIGINAL_REVISION = "4309a39646e644261624bfcd2bca669b343b7621"
_BUNDLED = ("search_api_docs", "get_python_api_docs")
CONDITIONS = {
    "A": {"backend": "blender_cli", "docs": "bundled",
          "tools": (*_BUNDLED, "execute_blender_code_for_cli")},
    "B": {"backend": "bpy_cli", "docs": "bundled",
          "tools": (*_BUNDLED, "execute_blender_code_for_cli")},
    "C": {"backend": "bpy_cli", "docs": "bundled+runtime",
          "tools": (*_BUNDLED, "execute_blender_code_for_cli", "get_runtime_python_api_docs_for_cli")},
    "D": {"backend": "live_mcp", "docs": "bundled+runtime",
          "tools": (*_BUNDLED, "execute_blender_code", "get_runtime_python_api_docs")},
}


def condition_config(condition, task, revision):
    """These are enhanced-code backend ablations, NOT the official baseline."""
    result = copy.deepcopy(CONDITIONS[condition])
    if result["backend"] not in task["execution_variants"]:
        raise ValueError("task is not applicable to condition")
    result.update(id=condition, revision=revision, role="enhanced_backend_ablation",
                  original_baseline_revision=ORIGINAL_REVISION,
                  source_mode="saved_scene", fresh_scene_required=True,
                  comparison_warning="A vs C changes backend AND docs; not docs-only causal evidence")
    return result


class ModelAdapter:
    """Transport takes a request dict; response parsing uses existing chat client.

    The existing helpers do not expose temperature/reasoning controls. Non-null
    controls fail closed rather than silently pretending they were sent.
    """
    def __init__(self, provider, model, transport: Callable[[dict], dict] | None,
                 *, temperature=None, reasoning=None):
        if provider not in ("openai", "claude"):
            raise ValueError("unsupported provider")
        if not model:
            raise ValueError("exact model ID required")
        if temperature is not None or reasoning is not None:
            raise ValueError("temperature and reasoning controls unsupported in this checkpoint")
        self.provider, self.model, self.transport = provider, model, transport
        self.controls = {"temperature": None, "reasoning": None,
                         "unsupported_options": ["temperature", "reasoning"],
                         "effective_defaults": "provider defaults, not known"}

    def prepare(self, tools, allowlist):
        from chat_client import chat_client  # existing dependency, no provider initialization
        by_name = {tool["name"]: tool for tool in tools}
        if len(by_name) != len(tools) or set(allowlist) - by_name.keys():
            raise ValueError("duplicate tool names or required tool missing")
        selected = [by_name[name] for name in allowlist]
        converter = (chat_client._mcp_tools_to_openai if self.provider == "openai"
                     else chat_client._mcp_tools_to_claude)
        return converter(selected)

    def call(self, messages, tools, allowlist, *, policy="", max_tokens=4096):
        from chat_client import chat_client
        schemas = self.prepare(tools, allowlist)
        request = {"model": self.model, "messages": copy.deepcopy(messages), "tools": schemas}
        if self.provider == "claude":
            request.update(system=policy, max_tokens=max_tokens)
        elif policy:
            request["messages"].insert(0, {"role": "system", "content": policy})
        if self.transport is None:
            raise RuntimeError("no transport configured; live calls are not enabled")
        response = self.transport(request)
        # Existing chat parser replaces malformed JSON with {}. Benchmarks must
        # instead expose API misuse, and never dispatch non-object arguments.
        if self.provider == "openai":
            calls = response["choices"][0]["message"].get("tool_calls") or []
            for call in calls:
                args = json.loads(call["function"]["arguments"])
                if not isinstance(args, dict):
                    raise ValueError("tool arguments must be an object")
            parsed = chat_client._process_openai_response(response)
        else:
            parsed = chat_client._process_claude_response(response)
        for _, name, arguments in parsed[1]:
            if name not in allowlist or not isinstance(arguments, dict):
                raise ValueError("forbidden tool or invalid arguments")
        return response, parsed, schemas


class FixtureTransport:
    """Explicit synthetic response queue for offline testing, never a live model."""
    synthetic = True

    def __init__(self, responses):
        self.responses = iter(copy.deepcopy(responses))
        self.requests = []

    def __call__(self, request):
        self.requests.append(copy.deepcopy(request))
        return next(self.responses)
