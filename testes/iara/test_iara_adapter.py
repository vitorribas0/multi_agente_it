"""Testes das funções puras de tradução do adaptador IARA.

Rodam SEM rede e SEM o SDK iaragenai instalado — cobrem só a camada de
tradução Responses ⇄ Chat Completions e a síntese de eventos SSE.

    python -m testes.iara.test_iara_adapter      # ou: pytest testes/iara
"""

from __future__ import annotations

import sys

from auditor.iara_adapter import (
    ChatResult,
    build_completion_kwargs,
    iter_response_events,
    normalize_chat_response,
    responses_input_to_messages,
    _provider_for,
)


# ── helpers de asserção mínimos (sem depender de pytest) ─────────────────

_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _failures.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


# ── testes ───────────────────────────────────────────────────────────────


def test_input_string_and_instructions():
    print("\n[input str + instructions]")
    msgs, tools, tc = responses_input_to_messages(
        {"instructions": "Você é a Atena.", "input": "Olá"}
    )
    check(msgs[0] == {"role": "system", "content": "Você é a Atena."},
          "instructions vira system message")
    check(msgs[1] == {"role": "user", "content": "Olá"},
          "input string vira user message")
    check(tools is None and tc is None, "sem tools => tools/tool_choice None")


def test_input_list_with_parts():
    print("\n[input list com content parts]")
    body = {
        "input": [
            {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "parte1 "},
                         {"type": "input_text", "text": "parte2"}]},
            {"type": "message", "role": "developer",
             "content": [{"type": "input_text", "text": "regra"}]},
        ]
    }
    msgs, _, _ = responses_input_to_messages(body)
    check(msgs[0] == {"role": "user", "content": "parte1 parte2"},
          "content parts concatenados")
    check(msgs[1] == {"role": "system", "content": "regra"},
          "role developer mapeado para system")


def test_function_call_roundtrip():
    print("\n[function_call + function_call_output]")
    body = {
        "input": [
            {"type": "message", "role": "user", "content": "rode a tool"},
            {"type": "function_call", "call_id": "call_1",
             "name": "listar", "arguments": '{"x":1}'},
            {"type": "function_call_output", "call_id": "call_1",
             "output": {"ok": True}},
        ]
    }
    msgs, _, _ = responses_input_to_messages(body)
    check(msgs[0]["role"] == "user", "user message preservada")
    assistant = msgs[1]
    check(assistant["role"] == "assistant" and "tool_calls" in assistant,
          "function_call vira assistant.tool_calls")
    check(assistant["tool_calls"][0] == {
        "id": "call_1", "type": "function",
        "function": {"name": "listar", "arguments": '{"x":1}'}},
        "tool_call no formato Chat Completions")
    check(msgs[2]["role"] == "tool" and msgs[2]["tool_call_id"] == "call_1",
          "function_call_output vira role=tool com tool_call_id")
    check(msgs[2]["content"] == '{"ok": true}', "output serializado como JSON")


def test_tools_translation():
    print("\n[tradução de tools flat->aninhado]")
    body = {
        "tools": [{
            "type": "function", "name": "buscar",
            "description": "busca X",
            "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
        }],
        "tool_choice": {"type": "function", "name": "buscar"},
    }
    _, tools, tc = responses_input_to_messages(body)
    check(tools == [{"type": "function", "function": {
        "name": "buscar", "description": "busca X",
        "parameters": {"type": "object", "properties": {"q": {"type": "string"}}}}}],
        "FunctionTool flat convertido para formato aninhado")
    check(tc == {"type": "function", "function": {"name": "buscar"}},
          "tool_choice específico convertido")


def test_provider_derivation():
    print("\n[derivação de provider por model id]")
    check(_provider_for("gpt-5.6-terra") == "azure_openai", "gpt* => azure_openai")
    check(_provider_for("anthropic.claude-3") == "bedrock", "claude => bedrock")
    check(_provider_for("gemini-2.0") == "vertex", "gemini => vertex")


def test_build_kwargs_gpt_vs_claude():
    print("\n[build_completion_kwargs GPT vs Claude]")
    k_gpt = build_completion_kwargs("gpt-5.6-terra", [{"role": "user", "content": "hi"}],
                                    None, None, 0.3)
    check(k_gpt.get("temperature") == 0.3 and "thinking" not in k_gpt,
          "GPT usa temperature e não adiciona thinking")
    k_claude = build_completion_kwargs("anthropic.claude-3",
                                       [{"role": "user", "content": "hi"}], None, None, 0.3)
    check(k_claude.get("thinking") == {"type": "adaptive"}
          and k_claude.get("temperature") == 1.0,
          "Claude adiciona thinking adaptive e força temperature=1.0")


def test_normalize_and_events_text():
    print("\n[normalize + eventos SSE de texto]")
    # simula response attr-like
    class _Fn:  # noqa
        pass

    class _Msg:
        content = "resposta final"
        tool_calls = None

    class _Choice:
        message = _Msg()
        finish_reason = "stop"

    class _Resp:
        choices = [_Choice()]

    result = normalize_chat_response(_Resp())
    check(result.text == "resposta final" and not result.tool_calls,
          "normaliza texto sem tool_calls")

    types = [e["type"] for e in iter_response_events(result, "gpt-5.6-terra", {})]
    check(types[0] == "response.created", "primeiro evento é response.created")
    check(types[-1] == "response.completed", "último evento é response.completed")
    check("response.output_text.delta" in types, "emite delta de texto")
    check("response.output_item.done" in types, "emite output_item.done")


def test_events_tool_call():
    print("\n[eventos SSE de function_call]")
    result = ChatResult(text="", tool_calls=[
        {"id": "call_9", "name": "buscar", "arguments": '{"q":"a"}'}],
        finish_reason="tool_calls")
    events = list(iter_response_events(result, "gpt-5.6-terra", {}))
    types = [e["type"] for e in events]
    check("response.function_call_arguments.delta" in types,
          "emite function_call_arguments.delta")
    check("response.function_call_arguments.done" in types,
          "emite function_call_arguments.done")
    done = [e for e in events if e["type"] == "response.output_item.done"][0]
    check(done["item"]["type"] == "function_call"
          and done["item"]["call_id"] == "call_9"
          and done["item"]["arguments"] == '{"q":"a"}',
          "item final é function_call com call_id e arguments corretos")


def main() -> int:
    for fn in [
        test_input_string_and_instructions,
        test_input_list_with_parts,
        test_function_call_roundtrip,
        test_tools_translation,
        test_provider_derivation,
        test_build_kwargs_gpt_vs_claude,
        test_normalize_and_events_text,
        test_events_tool_call,
    ]:
        fn()
    print()
    if _failures:
        print(f"FALHOU: {len(_failures)} verificação(ões)")
        return 1
    print("OK: todas as verificações passaram")
    return 0


if __name__ == "__main__":
    sys.exit(main())
