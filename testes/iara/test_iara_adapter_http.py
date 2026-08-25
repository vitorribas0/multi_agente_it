"""Teste HTTP ponta-a-ponta do adaptador IARA (sem rede, sem SDK real).

O adaptador opera em modo *passthrough*: repassa o corpo Responses do Codex
para ``client.responses`` do SDK iaragenai e devolve o SSE **verbatim**. Este
teste sobe o servidor numa porta efêmera, injeta um cliente IARA FALSO
(com um recurso ``responses`` falso) e faz requisições HTTP reais a
``/v1/responses`` validando:
  - autenticação por Bearer token,
  - repasse verbatim do SSE de streaming (texto + function_call),
  - modo não-streaming (objeto response devolvido como JSON),
  - que model/input/kwargs chegam corretamente ao ``client.responses``.

    python -m testes.iara.test_iara_adapter_http
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request

import auditor.iara_adapter as A


_failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    if not cond:
        _failures.append(msg)
        print(f"  ✗ {msg}")
    else:
        print(f"  ✓ {msg}")


# ── cliente IARA falso (recurso .responses) ────────────────────────────────

# Registro do último request que chegou ao SDK falso, para asserções.
_LAST_CALL: dict = {}


class _FakeRawResp:
    """Imita httpx.Response no que o adaptador usa: iter_bytes()."""

    def __init__(self, raw: bytes, chunk: int = 17):
        self._raw = raw
        self._chunk = chunk

    def iter_bytes(self):
        for i in range(0, len(self._raw), self._chunk):
            yield self._raw[i:i + self._chunk]


class _FakeSSE:
    """Imita ResponsesSSEStream: context manager expondo ._resp.iter_bytes()."""

    def __init__(self, raw: bytes):
        self._resp = _FakeRawResp(raw)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeResponseObj:
    def __init__(self, payload: dict):
        self._payload = payload

    def model_dump(self, mode=None):
        return self._payload


class _FakeResponses:
    def __init__(self, raw_sse: bytes, blocking_obj: dict):
        self._raw_sse = raw_sse
        self._blocking_obj = blocking_obj

    def stream(self, *, model, input=None, **kwargs):
        _LAST_CALL.clear()
        _LAST_CALL.update(model=model, input=input, kwargs=kwargs, mode="stream")
        return _FakeSSE(self._raw_sse)

    def create(self, *, model, input=None, stream=False, **kwargs):
        _LAST_CALL.clear()
        _LAST_CALL.update(model=model, input=input, kwargs=kwargs, mode="create")
        return _FakeResponseObj(self._blocking_obj)


class _FakeClient:
    def __init__(self, raw_sse: bytes, blocking_obj: dict):
        self.responses = _FakeResponses(raw_sse, blocking_obj)


def _install_fake(raw_sse: bytes = b"", blocking_obj: dict | None = None):
    A._make_iara_client = lambda provider: _FakeClient(  # type: ignore
        raw_sse, blocking_obj or {})


# ── helpers HTTP/SSE ──────────────────────────────────────────────────────


def _post(port, body, token=None):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 **({"Authorization": f"Bearer {token}"} if token else {})},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=10)
    return resp.getcode(), resp.read()


# SSE cru que o IARA (Responses API) emitiria — o adaptador deve repassá-lo
# byte-a-byte, sem tocar. Inclui um evento de function_call para provar que
# eventos que o Chat Completions não representaria passam intactos.
_RAW_SSE = (
    b'event: response.created\n'
    b'data: {"type":"response.created","response":{"id":"resp_1"}}\n\n'
    b'event: response.output_text.delta\n'
    b'data: {"type":"response.output_text.delta","delta":"ola do IARA"}\n\n'
    b'event: response.output_item.done\n'
    b'data: {"type":"response.output_item.done","item":{"type":"function_call",'
    b'"call_id":"call_7","name":"buscar","arguments":"{\\"q\\":\\"x\\"}"}}\n\n'
    b'event: response.completed\n'
    b'data: {"type":"response.completed","response":{"id":"resp_1","status":"completed"}}\n\n'
)


def run():
    import os
    os.environ["IARA_MODEL"] = "gpt-5.6-terra"

    # Objeto Responses completo que o IARA devolveria no modo bloqueante —
    # com um item de texto e um item function_call, para provar que a SÍNTESE
    # do SSE preserva ambos (o function_call não é representável em chat).
    _BLOCKING_OBJ = {
        "id": "resp_9", "object": "response", "status": "completed",
        "output": [
            {"id": "msg_1", "type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "ola do IARA",
                          "annotations": []}]},
            {"id": "fc_1", "type": "function_call", "call_id": "call_7",
             "name": "buscar", "arguments": "{\"q\":\"x\"}"},
        ],
    }

    # Default = bloqueante + síntese (responses.stream quebra no Windows).
    _install_fake(blocking_obj=_BLOCKING_OBJ)
    httpd = A.serve(host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    print("\n[default: bloqueante + síntese do SSE]")
    code, raw = _post(port, {"model": "gpt-5.6-terra", "input": "oi",
                             "stream": True, "tools": [{"type": "custom"}]})
    text = raw.decode("utf-8")
    check(code == 200, "HTTP 200")
    check(_LAST_CALL.get("mode") == "create",
          "usou client.responses.create (bloqueante), não stream")
    check("event: response.created" in text and "event: response.completed" in text,
          "sintetiza response.created … response.completed")
    check("ola do IARA" in text and "response.output_text.delta" in text,
          "texto do item message emitido como delta/done")
    check("function_call" in text and "call_7" in text,
          "item function_call (não representável em chat) preservado na síntese")
    check(_LAST_CALL.get("model") == "gpt-5.6-terra", "model repassado")
    check(_LAST_CALL.get("input") == "oi", "input repassado")
    check("stream" not in _LAST_CALL.get("kwargs", {})
          and "model" not in _LAST_CALL.get("kwargs", {})
          and "input" not in _LAST_CALL.get("kwargs", {}),
          "kwargs não contém meta-chaves (model/input/stream)")
    check(_LAST_CALL.get("kwargs", {}).get("tools") == [{"type": "custom"}],
          "tools (custom) repassadas via kwargs")

    print("\n[passthrough SSE verbatim (flag ATENA_IARA_STREAM_PASSTHROUGH)]")
    os.environ["ATENA_IARA_STREAM_PASSTHROUGH"] = "1"
    _install_fake(raw_sse=_RAW_SSE)
    code, raw = _post(port, {"model": "gpt-5.6-terra", "input": "oi",
                             "stream": True})
    check(code == 200, "HTTP 200")
    check(raw == _RAW_SSE, "SSE repassado byte-a-byte (verbatim)")
    check(_LAST_CALL.get("mode") == "stream", "usou client.responses.stream")
    os.environ.pop("ATENA_IARA_STREAM_PASSTHROUGH", None)

    print("\n[não-streaming]")
    _install_fake(blocking_obj={"object": "response", "status": "completed",
                                "output": []})
    code, raw = _post(port, {"model": "gpt-5.6-terra", "input": "oi",
                             "stream": False})
    obj = json.loads(raw.decode("utf-8"))
    check(code == 200 and obj.get("object") == "response",
          "retorna objeto response no modo não-streaming")
    check(_LAST_CALL.get("mode") == "create", "usou client.responses.create")

    print("\n[auth por token]")
    httpd.shutdown()
    httpd.server_close()
    os.environ["ATENA_IARA_ADAPTER_TOKEN"] = "segredo"
    _install_fake(blocking_obj={"object": "response"})
    httpd2 = A.serve(host="127.0.0.1", port=0)
    port2 = httpd2.server_address[1]
    threading.Thread(target=httpd2.serve_forever, daemon=True).start()
    try:
        _post(port2, {"input": "oi", "stream": False})
        check(False, "requisição sem token deveria falhar com 401")
    except urllib.error.HTTPError as e:
        check(e.code == 401, "sem token => HTTP 401")
    code, _ = _post(port2, {"input": "oi", "stream": False}, token="segredo")
    check(code == 200, "com token correto => HTTP 200")
    httpd2.shutdown()
    httpd2.server_close()
    os.environ.pop("ATENA_IARA_ADAPTER_TOKEN", None)

    print()
    if _failures:
        print(f"FALHOU: {len(_failures)} verificação(ões)")
        return 1
    print("OK: passthrough HTTP/SSE do adaptador validado ponta a ponta")
    return 0


if __name__ == "__main__":
    sys.exit(run())
