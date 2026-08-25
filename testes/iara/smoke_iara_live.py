"""Smoke test AO VIVO do adaptador IARA (usa credenciais e SDK reais).

Diferente dos testes em test_iara_adapter*.py (que são offline e usam um cliente
falso), este script:
  1. sobe o adaptador de verdade,
  2. faz uma requisição HTTP /v1/responses igual à que o Codex faz,
  3. chama o IARA GenAI DE VERDADE via SDK iaragenai,
  4. imprime os eventos SSE recebidos.

Pré-requisitos (no ambiente do usuário, nunca no chat):
  - pip install iara_genai_sdk
  - .env preenchido com IARA_CLIENT_ID / IARA_CLIENT_SECRET reais
  - ATENA_IARA_ADAPTER_TOKEN definido (recomendado)

Uso:
    # carregando o .env do projeto:
    set -a && source .env && set +a
    python -m testes.iara.smoke_iara_live
    python -m testes.iara.smoke_iara_live --tools    # exercita function-calling

NÃO commite saída deste script se ela contiver dados sensíveis.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import urllib.request

import auditor.iara_adapter as A


def _post_stream(port: int, body: dict, token: str | None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=120)
    return resp.read().decode("utf-8")


def _parse_sse(raw: str) -> list[dict]:
    events = []
    for block in raw.split("\n\n"):
        for line in block.splitlines():
            if line.startswith("data:"):
                events.append(json.loads(line[len("data:"):].strip()))
    return events


def main() -> int:
    use_tools = "--tools" in sys.argv

    missing = [k for k in ("IARA_CLIENT_ID", "IARA_CLIENT_SECRET")
               if not os.environ.get(k)]
    if missing:
        print(f"ERRO: variáveis ausentes: {', '.join(missing)}. "
              "Carregue o .env antes (set -a && source .env && set +a).")
        return 2

    try:
        import iaragenai  # noqa: F401
    except ImportError:
        print("ERRO: SDK iaragenai não instalado. Rode: pip install iara_genai_sdk")
        return 2

    model = os.environ.get("IARA_MODEL", "gpt-5.6-terra")
    token = (os.environ.get("ATENA_IARA_ADAPTER_TOKEN") or "").strip() or None
    print(f"Modelo: {model} | provider derivado: {A._provider_for(model)} | "
          f"environment: {os.environ.get('IARA_ENVIRONMENT', 'homol')}")

    httpd = A.serve(host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    print(f"Adaptador no ar em http://127.0.0.1:{port}/v1/responses\n")

    body: dict = {
        "model": model,
        "instructions": "Você é a Atena, assistente de auditoria. Responda em pt-BR.",
        "input": "Responda apenas com a frase: integração IARA funcionando.",
        "stream": True,
        "temperature": 0.2,
    }
    if use_tools:
        body["input"] = "Que horas são em São Paulo? Use a ferramenta disponível."
        body["tools"] = [{
            "type": "function",
            "name": "get_time",
            "description": "Retorna a hora atual de uma cidade.",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        }]
        body["tool_choice"] = "auto"

    try:
        raw = _post_stream(port, body, token)
    except Exception as e:
        print(f"FALHA na requisição: {e}")
        httpd.shutdown(); httpd.server_close()
        return 1

    events = _parse_sse(raw)
    print(f"Recebidos {len(events)} eventos SSE:")
    text_out = []
    fc_out = []
    for e in events:
        t = e.get("type")
        if t == "response.output_text.delta":
            text_out.append(e.get("delta", ""))
        elif t == "response.output_item.done" and \
                e.get("item", {}).get("type") == "function_call":
            item = e["item"]
            fc_out.append(f"{item.get('name')}({item.get('arguments')})")
        elif t == "response.failed":
            print("  response.failed:", e.get("response", {}).get("error"))

    print("  tipos:", [e.get("type") for e in events])
    if text_out:
        print("\nTexto do modelo:\n  " + "".join(text_out))
    if fc_out:
        print("\nFunction-calls emitidas:")
        for fc in fc_out:
            print("  " + fc)

    httpd.shutdown(); httpd.server_close()

    ok = events and events[-1].get("type") == "response.completed"
    print("\n" + ("OK: IARA respondeu via adaptador ✅" if ok
                  else "ATENÇÃO: stream não terminou em response.completed"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
