"""Run inside a 202 CoPaw container with its WORKING_DIR and SECRET_DIR.

Uses the installed streaming client, persisted model settings and worker's
internal gateway credential. The only tool is synthetic and has no handler
that could perform business actions. Outputs contain no credentials.
"""

import asyncio
import json
import os
import time
import urllib.request

from copaw.providers.provider_manager import ProviderManager


async def main():
    port = int(os.environ.get("COPAW_PROBE_PORT", "8088"))
    if port not in (8088, 18799):
        raise ValueError("Expected the worker or manager API port")
    url = f"http://127.0.0.1:{port}/api/models/active"
    # Fixed loopback HTTP endpoint with an allowlisted numeric port.
    with urllib.request.urlopen(url, timeout=10) as r:  # nosec B310
        active = json.load(r)["active_llm"]
    expected = os.environ.get("REVGUARD_EXPECTED_MODEL", "glm-5.3-flash")
    if active["model"] != expected:
        raise RuntimeError("Active model does not match expected target")
    manager = ProviderManager.get_instance()
    provider = manager.get_provider(active["provider_id"])
    if provider is None:
        raise RuntimeError("No persisted provider")
    effective = provider.get_effective_generate_kwargs(active["model"])
    if expected in {"gpt-5.6-luna", "gpt-5.6-sol"}:
        if effective.get("reasoning_effort") != "none":
            raise RuntimeError("Persisted reasoning_effort is not none")
        if effective.get("max_completion_tokens") != 512:
            raise RuntimeError("Persisted max_completion_tokens is not 512")
        request_limit = {"max_completion_tokens": 128}
        bounded_field = "max_completion_tokens"
    elif expected in {"glm-5.3-flash", "deepseek-flash"}:
        expected_max_tokens = int(os.environ.get("REVGUARD_EXPECTED_MAX_TOKENS", "2048"))
        if effective.get("max_tokens") != expected_max_tokens:
            raise RuntimeError(f"Persisted max_tokens is not {expected_max_tokens}")
        if effective.get("reasoning_effort") != "low":
            raise RuntimeError(f"Persisted {expected} reasoning_effort is not low")
        request_limit = {"max_tokens": 128}
        bounded_field = "max_tokens"
    else:
        expected_max_tokens = int(os.environ.get("REVGUARD_EXPECTED_MAX_TOKENS", "2048"))
        if effective.get("max_tokens") != expected_max_tokens:
            raise RuntimeError(f"Persisted {expected} max_tokens is not {expected_max_tokens}")
        request_limit = {"max_tokens": 128}
        bounded_field = "max_tokens"
    model = provider.get_chat_model_instance(active["model"])
    tools = [
        {
            "type": "function",
            "function": {
                "name": "readiness_probe",
                "description": "Read-only synthetic connectivity probe, no side effects.",
                "parameters": {
                    "type": "object",
                    "properties": {"status": {"type": "string", "enum": ["ok"]}},
                    "required": ["status"],
                    "additionalProperties": False,
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "Call readiness_probe with status=ok."}]
    start = time.monotonic()
    stream = await model(
        messages, tools=tools, tool_choice="readiness_probe", **request_limit
    )
    last = None
    async for chunk in stream:
        last = chunk
    if last is None:
        raise RuntimeError("Empty stream")
    calls = [c for c in last.content if c["type"] == "tool_use"]
    if (
        len(calls) != 1
        or calls[0]["name"] != "readiness_probe"
        or calls[0]["input"] != {"status": "ok"}
    ):
        raise RuntimeError("Unexpected synthetic tool call")
    call = calls[0]
    messages += [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": call["id"],
                    "type": "function",
                    "function": {
                        "name": call["name"],
                        "arguments": json.dumps(call["input"]),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": call["id"], "content": '{"status":"ok"}'},
        {"role": "user", "content": "Reply MODEL_READY only."},
    ]
    stream = await model(messages, tools=tools, tool_choice="none", **request_limit)
    async for chunk in stream:
        last = chunk
    answer = "".join(c["text"] for c in last.content if c["type"] == "text").strip()
    if answer != "MODEL_READY":
        raise RuntimeError("Unexpected synthetic tool continuation")
    print(
        json.dumps(
            {
                "model": active["model"],
                "provider": active["provider_id"],
                "bounded_field": bounded_field,
                "bounded_value": effective.get(bounded_field),
                "reasoning_effort": effective.get("reasoning_effort"),
                "streaming_tool_call": True,
                "tool_result_continuation": True,
                "answer": answer,
                "seconds": round(time.monotonic() - start, 2),
            },
            ensure_ascii=False,
        )
    )


try:
    asyncio.run(main())
except Exception as e:  # noqa: BLE001 - redact all upstream error payloads at the CLI boundary
    # Upstream exception bodies can contain sensitive diagnostic context.
    print(json.dumps({"error_type": type(e).__name__, "passed": False}))
    raise SystemExit(1)
