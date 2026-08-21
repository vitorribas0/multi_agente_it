"""
AI Service — orquestra o loop multiagente com IaraGenAI.

Fluxo:
1. Carrega o Agent (do banco) + tools habilitadas (do registry).
2. Monta mensagens com histórico da conversa.
3. Chama o modelo com tools. Se ele pedir tool calls:
   a. Executa as tools EM PARALELO (exceto ask_human, que pausa).
   b. Devolve resultados e chama de novo (até MAX_ITERATIONS).
4. Retorna a resposta final + lista de tool calls executadas.

Configuração de modelo (via .env):
  IARA_PROVIDER       — bedrock (Claude) | azure_openai (GPT)
  IARA_MODEL_DEFAULT  — modelo default para agentes especialistas
  IARA_MODEL_ORQUESTRADOR — modelo do orquestrador (mais capaz)
"""
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
from dataclasses import dataclass, field

from dotenv import load_dotenv

import tools as tools_pkg  # noqa: F401  — autodiscover roda no import
from tools.registry import get_tool, schemas_for

load_dotenv()


# Fallback usado quando o banco ainda não tem a linha de AppSettings
# (ex.: durante migrações) — o valor real é editável na tela de Configurações.
DEFAULT_MAX_ITERATIONS = 18
MAX_PARALLEL_TOOLS = 6


def _max_iterations() -> int:
    """Lê o limite de iterações da config global (com fallback seguro)."""
    try:
        from auditor.models import AppSettings
        return int(AppSettings.get_solo().max_iterations)
    except Exception:
        return DEFAULT_MAX_ITERATIONS


# ── Stop: interrupção de geração disparada pelo usuário ─────────────────
#
# Cada turno em streaming registra um threading.Event indexado pela conversa.
# O endpoint de stop seta o event; o loop do agente (incluindo sub-agentes,
# que compartilham a sessão) checa-o a cada iteração e encerra com os
# resultados parciais já obtidos.
_STOP_EVENTS: "dict[int, threading.Event]" = {}
_STOP_LOCK = threading.Lock()


def register_stop(conv_id: int) -> "threading.Event":
    """Cria/zera o event de stop para uma conversa e o retorna."""
    ev = threading.Event()
    with _STOP_LOCK:
        _STOP_EVENTS[conv_id] = ev
    return ev


def request_stop(conv_id: int) -> bool:
    """Sinaliza para interromper o turno em andamento. True se havia um ativo."""
    with _STOP_LOCK:
        ev = _STOP_EVENTS.get(conv_id)
    if ev is not None:
        ev.set()
        return True
    return False


def clear_stop(conv_id: int) -> None:
    """Remove o registro de stop ao fim do turno."""
    with _STOP_LOCK:
        _STOP_EVENTS.pop(conv_id, None)


# Modelos disponíveis na tela de configuração.
# Bedrock (Claude) tem thinking adaptive + effort max.
MODEL_OPTIONS = [
    # Claude (provider=bedrock) — recomendado p/ orquestração e tools
    "anthropic.claude-opus-4-6-v1",
    "us.anthropic.claude-opus-4-8",
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
    "anthropic.claude-sonnet-4-20250514-v1:0",
    # OpenAI (provider=azure_openai)
    "gpt-5.2",
    "gpt-5.1",
    "gpt-5",
    "gpt-5-mini",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4o",
    # Gemini (provider=vertex)
    "gemini-2.5-pro",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
]


def _is_claude(model: str) -> bool:
    return bool(model) and "claude" in (model or "").lower()


def _provider_for(model: str) -> str:
    """Deriva o provider correto do iaragenai a partir do ID do modelo.

    Cada provider só aceita um conjunto fixo de modelos — escolher
    'bedrock' com um id 'gpt-5' (ou vice-versa) retorna 400 do Bedrock.
    Em vez de exigir que o usuário ajuste IARA_PROVIDER no .env quando
    troca o modelo na tela, derivamos pelo prefixo do id.
    """
    m = (model or "").lower()
    if not m:
        return os.getenv("IARA_PROVIDER", "bedrock")

    # Anthropic via Bedrock — pode vir com prefixo regional 'us.'
    if "anthropic." in m or "claude" in m:
        return "bedrock"
    # Gemini / Vertex
    if m.startswith("gemini") or m.startswith("vertex"):
        return "vertex"
    # OpenAI via Azure (gpt-*, o1, o3, o4-mini, openai.gpt-oss-*)
    if (m.startswith("gpt") or m.startswith("o1") or m.startswith("o3")
            or m.startswith("o4") or m.startswith("openai.")):
        return "azure_openai"
    # Demais modelos do catálogo Bedrock (Llama, Nova, Mistral, Qwen,
    # Gemma, Titan, Deepseek)
    if m.split(".")[0] in {
        "amazon", "meta", "mistral", "qwen", "google", "deepseek"
    }:
        return "bedrock"

    # Fallback: respeita IARA_PROVIDER se setado, senão bedrock.
    return os.getenv("IARA_PROVIDER", "bedrock")


@dataclass
class AgentRunResult:
    """Resultado de uma execução do agente."""
    answer: str
    tool_calls: list[dict] = field(default_factory=list)
    awaiting_human: bool = False
    human_question: str = ""
    state_changed: bool = False
    stopped: bool = False


# ════════════════════════════════════════════════════════════════════════
# Agente da sessão (criado só para uma conversa, não democratizado)
# ════════════════════════════════════════════════════════════════════════

# Camada oculta de boas práticas — anexada POR TRÁS ao prompt que o usuário
# escreve no formulário. O usuário não vê isso; serve para alinhar o agente
# da sessão ao rigor do auditor (não inventar, validar números, citar fonte).
SESSION_AGENT_BEST_PRACTICES = """\
# Diretrizes de base (auditoria técnica)

Você é um agente especialista criado para esta sessão. Independentemente da
sua especialidade, siga SEMPRE estas boas práticas de auditoria:

- **Rigor factual:** nunca invente dados, números, nomes ou conclusões. Se a
  informação não está no dataset/documento em sessão nem no histórico, diga
  explicitamente que não há base — não preencha lacunas com conhecimento geral.
- **Valide antes de afirmar:** confira somas, contagens e proporções antes de
  reportá-las. Se um resultado vier vazio ou estranho, trate como possível erro
  e investigue, não como resposta final.
- **Use as tools, não a memória:** quando precisar de um dado, calcule com as
  ferramentas disponíveis em vez de estimar de cabeça.
- **Rastreabilidade:** ao concluir, indique brevemente como chegou lá (quais
  tools/fontes usou). Cite trechos/fontes quando fizer afirmações sobre dados.
- **Escopo:** atue dentro da tarefa recebida. Não repita contexto já visível
  no histórico — ele já está disponível para você.
- **Objetividade:** responda de forma direta e estruturada, sem floreio.
"""


# Teto de caracteres do conteúdo de documentos injetado no prompt — evita
# estourar o contexto/custo silenciosamente quando o usuário anexa arquivos
# grandes. Ao ultrapassar, trunca e avisa o agente do corte.
_SESSION_DOCS_MAX_CHARS = 120_000


def _session_documents_block(documents) -> str:
    """Monta a camada de contexto com os documentos anexados ao agente.

    Os documentos (PDF/TXT de política etc.) já vêm extraídos como markdown.
    São colados no system_prompt para que o agente SEMPRE os enxergue.
    Aplica um teto global de caracteres para não estourar o contexto.
    """
    if not documents or not isinstance(documents, list):
        return ""

    chunks = []
    used = 0
    truncated = False
    for d in documents:
        if not isinstance(d, dict):
            continue
        filename = d.get("filename") or "documento"
        md = (d.get("markdown") or "").strip()
        if not md:
            continue
        restante = _SESSION_DOCS_MAX_CHARS - used
        if restante <= 0:
            truncated = True
            break
        if len(md) > restante:
            md = md[:restante]
            truncated = True
        used += len(md)
        chunks.append(f"## Documento: {filename}\n\n{md}")

    if not chunks:
        return ""

    header = (
        "# Documentos de referência (anexados ao agente)\n\n"
        "Os documentos abaixo foram anexados pelo usuário e fazem parte do seu "
        "conhecimento base. Use-os como fonte ao responder e cite o documento "
        "quando se apoiar nele. Trate-os como autoritativos dentro do escopo."
    )
    if truncated:
        header += (
            "\n\n> ⚠️ O conteúdo foi truncado por limite de tamanho — se precisar "
            "de um trecho que não está aqui, avise que não há base para responder."
        )
    return header + "\n\n" + "\n\n---\n\n".join(chunks)


@dataclass
class RuntimeAgent:
    """Agente montado em memória (não persiste como os Agent globais).

    Mesma "superfície" que run_agent espera de auditor.models.Agent
    (slug, name, system_prompt, model, temperature, tools_enabled), mas
    construído a partir de um SessionAgent. Assim o motor não precisa saber
    se está rodando um agente global ou um agente de sessão.
    """
    slug: str
    name: str
    system_prompt: str
    model: str
    temperature: float
    tools_enabled: list


def build_runtime_agent_from_session(session_agent) -> RuntimeAgent:
    """Constrói o agente executável a partir de um auditor.models.SessionAgent.

    O system_prompt efetivo combina, nesta ordem:
      1. As boas práticas ocultas (SESSION_AGENT_BEST_PRACTICES).
      2. O prompt que o usuário escreveu no formulário.
      3. Os guardrails do usuário (limites/regras), se houver.
    """
    parts = [SESSION_AGENT_BEST_PRACTICES.strip()]
    user_prompt = (session_agent.system_prompt or "").strip()
    if user_prompt:
        parts.append("# Sua especialidade (definida pelo usuário)\n\n" + user_prompt)
    guardrails = (session_agent.guardrails or "").strip()
    if guardrails:
        parts.append("# Guardrails — regras que você NUNCA pode violar\n\n" + guardrails)

    docs_block = _session_documents_block(getattr(session_agent, "documents", None))
    if docs_block:
        parts.append(docs_block)

    return RuntimeAgent(
        slug=session_agent.SLUG,
        name=session_agent.name or "Agente da sessão",
        system_prompt="\n\n".join(parts),
        model=session_agent.model or "gpt-4o",
        temperature=session_agent.temperature,
        tools_enabled=list(session_agent.tools_enabled or []),
    )


def _session_agent_for(session: dict):
    """Recupera o SessionAgent da conversa corrente (ou None).

    A conversa é identificada por __conversation_id, que as views colocam
    na sessão. Import tardio evita ciclo (models importa pouco, mas mantemos
    o padrão dos outros pontos do motor).
    """
    conv_id = session.get("__conversation_id")
    if not conv_id:
        return None
    try:
        from auditor.models import SessionAgent
        return SessionAgent.objects.filter(conversation_id=conv_id).first()
    except Exception:
        return None


def _session_specialist_injection(session_agent) -> str:
    """System message que ENSINA o orquestrador, em runtime, que existe um
    especialista da sessão e como delegar para ele — sem editar o prompt
    democratizado do orquestrador (orquestrador.md)."""
    nome = session_agent.name or "Agente da sessão"
    desc = (session_agent.system_prompt or "").strip()
    resumo = (desc[:280] + "…") if len(desc) > 280 else desc
    linha_resumo = f"\nEspecialidade: {resumo}" if resumo else ""
    return (
        f"[Especialista da sessão disponível] Esta conversa tem um agente "
        f"especialista criado sob medida pelo usuário: **{nome}** "
        f"(slug `agente_sessao`).{linha_resumo}\n"
        f"Delegue a ele via `call_agent(agent_slug='agente_sessao', task=...)` "
        f"sempre que a tarefa cair na especialidade dele. Ele compartilha a "
        f"mesma sessão (datasets/documentos) e enxerga o histórico. Avalie "
        f"criticamente o que ele devolver, como faria com qualquer sub-agente."
    )


# ════════════════════════════════════════════════════════════════════════
# Playbooks (grafo multi-agente isolado, autorado no canvas)
# ════════════════════════════════════════════════════════════════════════

def _node_to_runtime_agent(node: dict) -> RuntimeAgent:
    """Constrói o agente executável a partir de um nó de playbook.

    Espelha build_runtime_agent_from_session, mas sem a camada oculta de boas
    práticas/guardrails: o prompt do nó vai como o usuário escreveu — o
    playbook é o próprio contrato de comportamento. Duck-types a superfície
    que run_agent espera (slug/name/system_prompt/model/temperature/
    tools_enabled).
    """
    return RuntimeAgent(
        slug=node.get("slug") or "no",
        name=node.get("name") or "Agente",
        system_prompt=node.get("system_prompt") or "",
        model=node.get("model") or "gpt-4o",
        temperature=float(node.get("temperature", 0.7) or 0.7),
        tools_enabled=list(node.get("tools_enabled") or []),
    )


def _node_short_desc(node: dict) -> str:
    """Uma linha do que o nó faz (para ensinar o orquestrador a delegar)."""
    desc = (node.get("description") or "").strip()
    if desc:
        return desc[:200]
    prompt = (node.get("system_prompt") or "").strip()
    if prompt:
        first = prompt.splitlines()[0].strip()
        return (first[:200] + "…") if len(first) > 200 else first
    return "(sem descrição)"


def _playbook_specialist_injection(nodes: dict, callable_slugs: list) -> str:
    """System message que ENSINA um nó (orquestrador/mid-graph) quais
    especialistas do playbook ele pode chamar via call_agent.

    Substitui, em modo playbook, a lista hardcoded de sub-agentes que a
    description da tool call_agent traz. Estes são os ÚNICOS delegáveis a
    partir deste nó — a resolução em call_agent também recusa qualquer outro.
    """
    linhas = []
    for slug in callable_slugs:
        node = nodes.get(slug)
        if not node:
            continue
        linhas.append(f"• '{slug}' — {node.get('name') or slug}: {_node_short_desc(node)}")
    catalogo = "\n".join(linhas) if linhas else "(nenhum)"
    return (
        "[Playbook ativo] Você está rodando dentro de um pipeline multi-agente "
        "isolado. Os ÚNICOS sub-agentes que você pode acionar via "
        "`call_agent(agent_slug=..., task=...)` são os listados abaixo (passe o "
        "slug exato). Qualquer outro slug será recusado — não invente nomes nem "
        "conte com agentes globais.\n\n"
        f"Sub-agentes disponíveis a partir de você:\n{catalogo}\n\n"
        "Delegue quando a tarefa cair na especialidade de um deles; eles "
        "compartilham a mesma sessão (datasets/documentos) e enxergam o "
        "histórico. Avalie criticamente o que devolverem."
    )


def _get_iara_client(provider: str):
    from iaragenai import IaraGenAI
    return IaraGenAI(
        client_id=os.getenv("IARA_CLIENT_ID"),
        client_secret=os.getenv("IARA_CLIENT_SECRET"),
        environment=os.getenv("IARA_ENVIRONMENT", "homol"),
        provider=provider,
        correlation_id=str(uuid4()),
    )


# Limites para o painel de execução ao vivo — o frame SSE não pode carregar
# args/resultados gigantes (ex.: SQL de 10 mil linhas ou um dataframe inteiro).
_LIVE_ARG_MAX = 4000
_LIVE_RESULT_MAX = 2000


def _truncate_text(value, limit: int = _LIVE_RESULT_MAX) -> str:
    """Serializa e trunca um valor para exibição ao vivo (best-effort)."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    if len(text) > limit:
        return text[:limit] + f"\n… (+{len(text) - limit} caracteres truncados)"
    return text


def _truncate_args(args: dict) -> dict:
    """Copia os args truncando strings longas — preserva o 'código' que o
    painel mostra sem estourar o frame SSE."""
    if not isinstance(args, dict):
        return {}
    out = {}
    for k, v in args.items():
        # Não vaza campos de controle internos (prefixados com _).
        if isinstance(k, str) and k.startswith("_"):
            continue
        out[k] = _truncate_text(v, _LIVE_ARG_MAX) if isinstance(v, str) else v
    return out


def _friendly_tool_label(name: str, args: dict, spec, session: dict | None = None) -> str:
    """Texto amigável para o log de progresso ao vivo."""
    if name == "call_agent":
        slug = (args or {}).get("agent_slug", "sub-agente")
        nomes = {
            "gerador_sql": "Gerador SQL",
            "analista_dados": "Analista de Dados",
            "analista_documentos": "Analista de Documentos",
        }
        # Em modo playbook o alvo é um nó do grafo; usa o nome do nó em vez do
        # slug (que pode ser um id gerado, ex. 'n2_41530', em playbooks antigos).
        pb_nodes = (session or {}).get("__playbook_nodes") or {}
        node = pb_nodes.get(slug)
        label = (node.get("name") if isinstance(node, dict) else None) or nomes.get(slug, slug)
        return f"Delegando para {label}…"
    if name == "consulta_aws":
        return "Consultando a base na AWS (Athena)…"
    if name == "analise_massiva_llm":
        return "Preparando análise massiva por IA…"
    if name == "analise_massiva_batch":
        return "Preparando análise massiva em lote (batch)…"
    if name == "buscar_resultado_batch":
        return "Buscando resultado do job de batch…"
    if name == "exportar_dataset":
        return "Exportando dataset…"
    if name == "thinking":
        return "Pensando…"
    # Default: usa o nome de exibição da tool.
    return f"{getattr(spec, 'name', name)}…"


def _execute_tool(spec, args: dict, session: dict) -> tuple[str, str, int]:
    """Executa uma tool. Retorna (result, error, duration_ms)."""
    start = time.perf_counter()
    try:
        kwargs = dict(args)
        if spec.uses_session:
            kwargs["_session"] = session
        result = spec.func(**kwargs)
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False, default=str)
        return result, "", int((time.perf_counter() - start) * 1000)
    except Exception as e:
        return "", str(e), int((time.perf_counter() - start) * 1000)


def _build_completion_kwargs(agent, messages, tool_schemas, *, tool_choice="auto") -> dict:
    """Monta kwargs do client.chat.completions.create — adapta por provider."""
    kwargs = {
        "model": agent.model,
        "messages": messages,
    }
    if tool_schemas:
        kwargs["tools"] = tool_schemas
        kwargs["tool_choice"] = tool_choice

    if _is_claude(agent.model):
        # Claude no Bedrock: thinking adaptive + effort high + max_tokens generoso.
        # 'effort' aqui passa pelo litellm, que só aceita high/medium/low —
        # 'max' (do exemplo da Anthropic direta) dá ValueError. Temperature 1.0
        # é o default recomendado quando thinking está ativo.
        kwargs["thinking"] = {"type": "adaptive"}
        kwargs["output_config"] = {"effort": "high"}
        kwargs["max_tokens"] = 64000
        kwargs["temperature"] = 1.0
    else:
        kwargs["temperature"] = agent.temperature

    return kwargs


def run_agent(
    *,
    agent,
    user_message: str,
    history: list[dict],
    session: dict,
    progress=None,
) -> AgentRunResult:
    """
    Executa um turno do agente.

    Args:
        agent: instância de auditor.models.Agent
        user_message: mensagem nova do usuário (ou resposta a ask_human)
        history: lista [{'role','content'}] das mensagens anteriores
        session: dict mutável de estado da conversa (será atualizado in-place)
        progress: callable opcional progress(dict) para eventos ao vivo (SSE).
            Se None, tenta reaproveitar o que estiver em session["__progress"]
            (caso este run_agent seja um sub-agente chamado via call_agent).

    Returns:
        AgentRunResult com answer e tool_calls executadas neste turno.
    """
    # Propaga o callback de progresso para tools e sub-agentes via sessão.
    if progress is None:
        progress = session.get("__progress")
    else:
        session["__progress"] = progress

    def _emit(payload: dict) -> None:
        if progress is None:
            return
        try:
            # parent_id liga cada evento ao call_agent que gerou este agente
            # (None = tool do agente de topo). Torna a árvore ao vivo do front
            # inequívoca mesmo com fan-out de vários call_agent no mesmo turno.
            progress({
                "agent": agent.slug,
                "parent_id": session.get("__emit_parent_id"),
                **payload,
            })
        except Exception:
            pass  # progresso é best-effort — nunca derruba o agente

    provider = _provider_for(agent.model)
    try:
        client = _get_iara_client(provider)
    except Exception as e:
        return AgentRunResult(
            answer=f"⚠️ Falha ao iniciar cliente IaraGenAI ({provider}): {e}",
        )

    # Mensagens base
    messages = [{"role": "system", "content": agent.system_prompt}]

    # Modo playbook: ensina o nó em execução quais especialistas do grafo ele
    # pode acionar (os alvos das suas arestas de saída), sem tocar em prompt
    # global. Vale para o root E para nós intermediários que tenham arestas.
    # Tem prioridade sobre o caminho do agente de sessão — num playbook a
    # resolução é isolada aos nós do grafo.
    pb_nodes = session.get("__playbook_nodes")
    if pb_nodes is not None:
        adjacency = session.get("__playbook_adjacency") or {}
        callable_slugs = adjacency.get(agent.slug) or []
        if callable_slugs:
            messages.append({
                "role": "system",
                "content": _playbook_specialist_injection(pb_nodes, callable_slugs),
            })
    # Se a conversa tem um agente de sessão e quem está rodando é o
    # orquestrador, ensina-o em runtime a delegar para esse especialista
    # (sem tocar no prompt democratizado). Não injeta quando o próprio
    # agente em execução já é o agente da sessão (evita auto-referência).
    elif agent.slug == "orquestrador":
        sess_agent = _session_agent_for(session)
        if sess_agent is not None:
            messages.append({
                "role": "system",
                "content": _session_specialist_injection(sess_agent),
            })

    for m in history:
        role = m.get("role")
        content = m.get("content", "")
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    messages.append({"role": "user", "content": user_message})

    # Disponibiliza histórico para tools de delegação (call_agent).
    session["__history"] = history

    enabled_tools = list(agent.tools_enabled or [])

    # Se há Knowledge Bases ativas na conversa, habilita a tool consultar_kb
    # (mesmo que o agente não a tenha em tools_enabled) e injeta o catálogo
    # das KBs disponíveis para o modelo saber o que pode consultar.
    active_kbs = [
        k for k in (session.get("active_kbs") or [])
        if isinstance(k, dict) and k.get("id")
    ]
    if active_kbs:
        if "consultar_kb" not in enabled_tools:
            enabled_tools.append("consultar_kb")
        catalogo = "\n".join(
            f"- {kb['name'] or kb['id']}: {kb.get('description') or '(sem descrição)'}"
            for kb in active_kbs
        )
        messages.append({
            "role": "system",
            "content": (
                "Bases de conhecimento ATIVAS nesta conversa (use a tool "
                "`consultar_kb` quando a pergunta exigir conteúdo delas):\n"
                f"{catalogo}\n\n"
                "Ao usar trechos retornados, cite-os no formato [n]."
            ),
        })

    # Conhecimentos ativos na conversa (prompts de especialista/processo
    # cadastrados na tela). Diferente das KBs — que são consultadas via tool —
    # o conteúdo dos conhecimentos vai DIRETO no contexto, para o orquestrador
    # incorporar como instrução/base de trabalho neste turno.
    active_knowledge = [
        k for k in (session.get("__active_knowledge") or [])
        if isinstance(k, dict) and (k.get("prompt") or "").strip()
    ]
    if active_knowledge:
        blocos = "\n\n".join(
            f"### Conhecimento: {k.get('name') or 'Sem nome'}\n{k['prompt'].strip()}"
            for k in active_knowledge
        )
        aviso = ""
        if len(active_knowledge) > 1:
            nomes = ", ".join(k.get("name") or "Sem nome" for k in active_knowledge)
            aviso = (
                f"\n\nHá {len(active_knowledge)} conhecimentos ativos "
                f"simultaneamente ({nomes}). Combine-os de forma coerente; se "
                "houver conflito entre eles, priorize o mais específico para a "
                "tarefa e explicite a escolha."
            )
        messages.append({
            "role": "system",
            "content": (
                "CONHECIMENTOS ATIVOS nesta conversa — o usuário selecionou os "
                "seguintes conhecimentos de especialista para orientar este "
                "trabalho. Incorpore-os como parte das suas instruções e siga "
                f"os processos/bases descritos:\n\n{blocos}{aviso}"
            ),
        })

    tool_schemas = schemas_for(enabled_tools)
    tool_calls_log: list[dict] = []

    is_orquestrador = agent.slug == "orquestrador"

    # Event de stop da conversa — compartilhado com sub-agentes via sessão.
    stop_event = session.get("__stop_event")

    def _stopped() -> bool:
        return stop_event is not None and stop_event.is_set()

    def _stop_result() -> AgentRunResult:
        """Encerra o turno preservando os resultados parciais já obtidos."""
        _emit({"stage": "stopped", "text": "Geração interrompida pelo usuário."})
        return AgentRunResult(
            answer=(
                "⏹️ Geração interrompida pelo usuário."
                if not tool_calls_log else
                "⏹️ Geração interrompida pelo usuário. Os resultados parciais "
                "obtidos até aqui foram mantidos."
            ),
            tool_calls=tool_calls_log,
            state_changed=bool(tool_calls_log),
            stopped=True,
        )

    try:
        for _ in range(_max_iterations()):
            if _stopped():
                return _stop_result()
            _emit({
                "stage": "thinking",
                "text": "Orquestrador decidindo…" if is_orquestrador else f"{agent.name} pensando…",
            })
            response = client.chat.completions.create(
                **_build_completion_kwargs(agent, messages, tool_schemas)
            )
            msg = response.choices[0].message

            # Sem tool calls = resposta final
            if not getattr(msg, "tool_calls", None):
                return AgentRunResult(
                    answer=msg.content or "",
                    tool_calls=tool_calls_log,
                    state_changed=bool(tool_calls_log),
                )

            # Adiciona a mensagem do assistente com as tool_calls
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ],
            })

            # ── Detecta ask_human entre as calls e pausa ANTES de executar
            #    qualquer outra. Isso preserva a semântica antiga (a primeira
            #    ask_human pausa) sem rodar tools "em vão" se o turno será
            #    interrompido.
            human_call = next(
                (tc for tc in msg.tool_calls
                 if (s := get_tool(tc.function.name)) and s.is_human_in_loop),
                None,
            )
            if human_call is not None:
                try:
                    args = json.loads(human_call.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                question = args.get("question", "")
                tool_calls_log.append({
                    "tool": human_call.function.name,
                    "args": args,
                    "result": f"⏸️ Aguardando resposta: {question}",
                    "error": "",
                    "duration_ms": 0,
                })
                return AgentRunResult(
                    answer="",
                    tool_calls=tool_calls_log,
                    awaiting_human=True,
                    human_question=question,
                    state_changed=True,
                )

            # Stop pedido durante o "pensar" do modelo: não inicia as tools
            # desta iteração — encerra com o que já houver.
            if _stopped():
                return _stop_result()

            # ── Prepara o batch de execução paralela ─────────────────────
            #
            # Cada item: (tc, name, args, spec_or_none)
            batch = []
            for tc in msg.tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}
                batch.append((tc, name, args, get_tool(name)))

            # Tools desconhecidas viram erro imediato (sem ocupar worker).
            results: dict[str, dict] = {}
            executable = []
            for tc, name, args, spec in batch:
                if spec is None:
                    err = f"Tool '{name}' não registrada."
                    results[tc.id] = {
                        "tool": name, "args": args, "result": "", "error": err,
                        "duration_ms": 0, "tool_call_id": tc.id,
                    }
                    continue
                executable.append((tc, name, args, spec))

            # call_agent precisa rodar serialmente — o sub-agente lê e escreve
            # campos de controle em _session (__agent_call_depth,
            # __awaiting_human) e dois rodando juntos corromperiam esse
            # estado. As demais tools rodam em paralelo.
            serial = [t for t in executable if t[1] == "call_agent"]
            parallel = [t for t in executable if t[1] != "call_agent"]

            # Profundidade de aninhamento (0 = agente raiz; +1 por call_agent).
            # Deixa o painel de execução ao vivo montar a árvore agente→sub-tools.
            depth = int(session.get("__agent_call_depth", 0))

            # Anuncia ao vivo o que vai rodar nesta iteração (exceto thinking,
            # que já foi sinalizado). Além do label amigável, manda tool_call_id
            # (para casar com o evento de término), os args (o "código" que o
            # painel ao vivo mostra) e a profundidade/slug do agente.
            for tc, name, args, spec in executable:
                if name in ("thinking",):
                    continue
                _emit({
                    "stage": "tool",
                    "tool": name,
                    "tool_call_id": tc.id,
                    "args": _truncate_args(args),
                    "depth": depth,
                    "icon": getattr(spec, "icon", "⚡"),
                    "text": _friendly_tool_label(name, args, spec, session),
                })

            def _run_one(item):
                tc, name, args, spec = item
                result, error, duration = _execute_tool(spec, args, session)
                return tc.id, {
                    "tool": name, "args": args, "result": result,
                    "error": error, "duration_ms": duration,
                    "tool_call_id": tc.id,
                }

            # Emite o término de uma tool para o painel ao vivo (resultado,
            # erro e duração). Pula thinking. Para call_agent emitimos também —
            # é o que fecha o ramo de delegação na árvore (as sub-tools já
            # emitiram seus próprios start/result em depth+1 antes deste).
            def _emit_tool_result(name: str, payload: dict) -> None:
                if name == "thinking":
                    return
                _emit({
                    "stage": "tool_result",
                    "tool": name,
                    "tool_call_id": payload["tool_call_id"],
                    "depth": depth,
                    "error": payload.get("error") or "",
                    "duration_ms": payload.get("duration_ms", 0),
                    "result_preview": _truncate_text(payload.get("result") or ""),
                })

            if parallel:
                workers = min(MAX_PARALLEL_TOOLS, len(parallel))
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    # ex.map preserva a ordem de entrada; emitimos o término no
                    # thread principal (não nos workers) conforme cada um volta.
                    for (tcid, payload), (_, name, _, _) in zip(
                        ex.map(_run_one, parallel), parallel
                    ):
                        results[tcid] = payload
                        _emit_tool_result(name, payload)

            for item in serial:
                tc_serial = item[0]
                # Durante a execução deste call_agent, os eventos ao vivo do
                # sub-agente devem apontar para ele (parent_id = id desta tool).
                # Restauramos o parent anterior antes de emitir o próprio result.
                prev_parent = session.get("__emit_parent_id")
                session["__emit_parent_id"] = tc_serial.id
                try:
                    tcid, payload = _run_one(item)
                finally:
                    session["__emit_parent_id"] = prev_parent
                # call_agent expõe as tool calls do sub-agente via session;
                # capturamos aqui pra que o frontend renderize a árvore.
                nested = session.pop("__nested_tool_calls", None)
                if nested:
                    payload["nested_tool_calls"] = nested
                results[tcid] = payload
                _emit_tool_result(item[1], payload)
                # call_agent pode pedir input humano via _session — propagar
                # imediatamente preserva o comportamento original.
                if session.pop("__awaiting_human", False):
                    question = session.pop("__human_question", "")
                    # Persiste tudo que já rodou nesta iteração.
                    for tc in msg.tool_calls:
                        if tc.id in results:
                            r = results[tc.id]
                            tool_calls_log.append({k: v for k, v in r.items()
                                                   if k != "tool_call_id"})
                    return AgentRunResult(
                        answer="",
                        tool_calls=tool_calls_log,
                        awaiting_human=True,
                        human_question=question,
                        state_changed=True,
                    )

            # ── Anexa resultados aos messages na ORDEM original do modelo
            for tc in msg.tool_calls:
                r = results.get(tc.id)
                if r is None:  # nunca deveria ocorrer
                    continue
                tool_calls_log.append({k: v for k, v in r.items()
                                       if k != "tool_call_id"})
                content = r["result"] if not r["error"] else f"[ERRO] {r['error']}"
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": content,
                })

        # ── Orçamento de iterações esgotado ─────────────────────────────
        #
        # O trabalho pesado normalmente já rodou (queries, clustering,
        # gráficos); o que faltou foi um turno final para o modelo CONCLUIR.
        # Em vez de descartar tudo com uma mensagem canônica, fazemos uma
        # última chamada SEM tools — forçando o modelo a sintetizar uma
        # resposta a partir dos resultados que já estão em `messages`.
        if tool_calls_log:
            _emit({
                "stage": "thinking",
                "text": f"{agent.name} concluindo…",
            })
            messages.append({
                "role": "user",
                "content": (
                    "Você atingiu o limite de passos com ferramentas. NÃO chame "
                    "mais nenhuma tool. Com base apenas nos resultados que já "
                    "obteve nesta conversa, escreva agora a resposta final e "
                    "conclusiva para o usuário (resumo, números reais já "
                    "apurados e interpretação). Se algo ficou incompleto, diga "
                    "explicitamente o que falta — mas entregue o que já tem."
                ),
            })
            try:
                # Mantemos tools= na request (a conversa já referencia tool
                # calls, e a Anthropic exige tools= nesse caso), mas forçamos
                # tool_choice="none" para proibir o modelo de chamar mais alguma.
                final = client.chat.completions.create(
                    **_build_completion_kwargs(
                        agent, messages, tool_schemas, tool_choice="none"
                    )
                )
                conclusao = (final.choices[0].message.content or "").strip()
            except Exception as e:  # noqa: BLE001
                import logging
                logging.getLogger("auditor").exception(
                    "Falha na chamada final de conclusão: %s", e
                )
                conclusao = ""
            if conclusao:
                return AgentRunResult(
                    answer=conclusao,
                    tool_calls=tool_calls_log,
                    state_changed=True,
                )

        return AgentRunResult(
            answer="⚠️ Limite de iterações atingido sem uma resposta conclusiva.",
            tool_calls=tool_calls_log,
            state_changed=bool(tool_calls_log),
        )

    except Exception as e:
        import traceback
        traceback.print_exc()
        return AgentRunResult(
            answer=f"⚠️ Erro durante execução do agente: {e}",
            tool_calls=tool_calls_log,
        )
