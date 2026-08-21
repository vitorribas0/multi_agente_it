"""Adapter de API para o API Gateway do Itaú.

O style guide corporativo de APIs REST (validação `ci-openapi/validate` na
esteira do contrato) exige duas coisas que as views de `views.py` não seguem:

1. atributos em **camelCase** (as views falam snake_case: `agent_slug`,
   `conversation_id`, `tool_calls`, ...);
2. respostas 2XX envelopadas em **`{"data": ...}`**.

Como a integração no Gateway é `http_proxy` (repassa o payload sem
transformar), o contrato só pode declarar camelCase se o serviço realmente
devolver camelCase. Em vez de reescrever as views — o que quebraria o
frontend Angular, que já consome snake_case — este módulo expõe as MESMAS
views sob rotas paralelas (`/gateway/...`), traduzindo apenas a borda:

    front Angular  ──>  /api/...      (snake_case, sem envelope)   inalterado
    API Gateway    ──>  /gateway/...  (camelCase, envelope data)   este módulo

Assim o contrato passa no style guide sem mentir sobre a resposta, e nada do
que já funciona é tocado. A tradução é mecânica (`_camelize`), então views
novas não precisam de trabalho manual aqui.
"""

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_http_methods
import json
import re

from . import views


# ── Tradução snake_case -> camelCase ─────────────────────────────────

_SNAKE_RE = re.compile(r"_([a-z0-9])")

# Chaves cujo VALOR é dado livre do usuário, não atributo de contrato: as
# chaves de dentro são nomes de coluna do CSV, argumentos de tool, etc.
# Camelizá-las corromperia o dado (uma coluna "valor_total" da planilha do
# usuário viraria "valorTotal" e o front não acharia mais a coluna).
_OPAQUE_VALUES = {
    "dtypes",      # {nome_da_coluna: tipo}
    "preview",     # [{nome_da_coluna: valor}, ...]
    "args",        # argumentos da tool, formato livre por tool
    "result",      # retorno da tool, formato livre
    "state",       # estado de sessão, chaves internas com __
    "nodes",       # grafo do playbook, autorado pelo usuário
    "edges",
}


def _camel_key(key: str) -> str:
    """`agent_slug` -> `agentSlug`. Chaves já camelCase passam intactas."""
    return _SNAKE_RE.sub(lambda m: m.group(1).upper(), key)


def _camelize(value):
    """Converte recursivamente as CHAVES de dicts para camelCase.

    Valores são preservados como estão — só nomes de atributo mudam. Listas
    são percorridas item a item. Chaves em `_OPAQUE_VALUES` têm o valor
    copiado sem recursão, porque ali as chaves internas são dado do usuário
    (nomes de coluna, args de tool) e não atributos do contrato.
    """
    if isinstance(value, dict):
        return {
            _camel_key(k): (v if k in _OPAQUE_VALUES else _camelize(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_camelize(v) for v in value]
    return value


# ── Chaves que NÃO são payload de negócio ────────────────────────────
# `status` é controle interno das views ("success"/"error"); no contrato do
# Gateway o sucesso é expresso pelo HTTP 200 e o erro pelo schema de erro,
# então ele sai do envelope de dados.
_CONTROL_KEYS = {"status"}


def _adapt(response, *, data_key=None):
    """Traduz a resposta de uma view para o formato exigido pelo Gateway.

    - 2XX  -> `{"data": <payload camelCase>}`
    - erro -> repassa o corpo original (o contrato referencia errorResponse)

    `data_key`: quando informado, desembrulha essa chave do payload para virar
    o conteúdo de `data` — usado nas coleções, em que a view devolve
    `{"conversations": [...]}` e o contrato espera `data: [...]`.
    """
    # Views que streamam (SSE) ou devolvem arquivo não passam por aqui.
    if not isinstance(response, JsonResponse):
        return response

    if response.status_code >= 400:
        return response

    payload = json.loads(response.content)

    if isinstance(payload, dict):
        payload = {k: v for k, v in payload.items() if k not in _CONTROL_KEYS}
        if data_key is not None:
            payload = payload.get(data_key, [])

    return JsonResponse({"data": _camelize(payload)}, status=response.status_code)


# ── Tradução da ENTRADA: camelCase -> snake_case ──────────────────────

_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def _snake_key(key: str) -> str:
    """`conversationId` -> `conversation_id`."""
    return _CAMEL_RE.sub(r"\1_\2", key).lower()


def _decamelize_body(request):
    """Reescreve o corpo JSON da request de camelCase para snake_case.

    O contrato declara o requestBody em camelCase (exigência do style guide),
    mas as views leem `conversation_id`/`agent_slug`. Sem esta tradução os
    campos chegariam vazios e toda mensagem criaria uma conversa nova.

    Só as chaves de primeiro nível são traduzidas: os valores aninhados
    (`activeKbs`, `activeKnowledge`) são listas de IDs, não objetos de
    contrato. Corpo ausente ou não-JSON passa intacto.
    """
    if not request.body:
        return request
    try:
        data = json.loads(request.body)
    except (ValueError, UnicodeDecodeError):
        return request
    if not isinstance(data, dict):
        return request

    request._body = json.dumps(
        {_snake_key(k): v for k, v in data.items()}
    ).encode("utf-8")
    return request


def _decamelize_form(request):
    """Idem para campos de formulário (`multipart/form-data`).

    O upload lê `request.POST.get("conversation_id")`, e o contrato declara
    `conversationId`. Aceita as duas grafias: a chave snake_case só é criada
    quando ainda não existe, então um cliente que já mande snake_case continua
    funcionando. Arquivos (`request.FILES`) não são tocados.
    """
    post = request.POST.copy()
    for key in list(post.keys()):
        snake = _snake_key(key)
        if snake != key and snake not in post:
            post[snake] = post[key]
    post._mutable = False
    request.POST = post
    return request


# ── Rotas expostas ao Gateway ────────────────────────────────────────
# Cada função delega para a view real e só adapta a borda. Nenhuma regra de
# negócio vive neste módulo — de propósito.

@csrf_exempt
@require_http_methods(["POST"])
def chat_message(request):
    """POST /gateway/chats — envia mensagem ao agente, devolve o turno."""
    return _adapt(views.chat_message(_decamelize_body(request)))


@require_GET
def conversation_list(request):
    """GET /gateway/conversations — lista de conversas em `data`."""
    return _adapt(views.conversation_list(request), data_key="conversations")


@require_GET
def conversation_detail(request, conv_id):
    """GET /gateway/conversations/<id> — conversa com mensagens."""
    return _adapt(views.conversation_detail(request, conv_id))


@csrf_exempt
@require_http_methods(["POST"])
def upload_table(request):
    """POST /gateway/uploads — upload multipart de tabela/documento."""
    return _adapt(views.upload_table(_decamelize_form(request)))
