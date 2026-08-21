"""
Tool de delegação entre agentes.

Permite que o orquestrador chame outro agente especialista — esse
sub-agente roda seu próprio loop com suas próprias tools, compartilha a
mesma sessão (dataframes, arquivos carregados) e enxerga o histórico da
conversa, e devolve sua resposta como resultado da tool.
"""
from tools.registry import tool


MAX_DEPTH = 3


@tool(
    description=(
        "Delega uma tarefa a um sub-agente especialista. "
        "USE quando: a tarefa exige uma capacidade de outro agente "
        "(SQL, análise estatística, leitura de documento). "
        "NÃO use: para tarefa que se resolve com uma tool sua direta, "
        "ou em chat trivial — adiciona latência sem ganho. "
        "\n\n"
        "Sub-agentes disponíveis (passe o slug exato em 'agent_slug'):\n"
        "• 'gerador_sql' — escreve E executa queries Athena na base FQ "
        "(reclamações). Use para extrair dados.\n"
        "• 'analista_dados' — análise estatística/categórica sobre o "
        "dataset corrente em sessão (uploads CSV/XLSX ou resultado de "
        "query). Use para descrever, agrupar, contar termos, regex.\n"
        "• 'analista_documentos' — leitura/busca em PDFs/DOCX/imagens "
        "já extraídos via OCR e disponíveis na sessão. Use para citar "
        "trechos, buscar termos, extrair tabelas.\n"
        "\n"
        "PARALELISMO: você pode emitir múltiplos call_agent no MESMO "
        "turno quando as tarefas forem independentes — eles rodam em "
        "sequência interna mas você libera todos de uma vez.\n"
        "\n"
        "Sub-agentes JÁ enxergam o estado de sessão e o histórico — em "
        "'task' passe APENAS a instrução nova e específica, sem repetir "
        "contexto."
    ),
    icon="🤝",
)
def call_agent(agent_slug: str, task: str, _session: dict) -> str:
    """Delega uma tarefa a um sub-agente especialista.

    Args:
        agent_slug: Slug exato do sub-agente. Ex.: 'gerador_sql', 'analista_dados', 'analista_documentos'.
        task: Instrução clara e específica do que o sub-agente deve fazer. Não repita contexto já visível no histórico — ele já enxerga.
    """
    depth = int(_session.get("__agent_call_depth", 0))
    if depth >= MAX_DEPTH:
        return (
            f"[ERRO] Profundidade máxima de chamadas entre agentes atingida "
            f"({MAX_DEPTH}). Cadeia detectada — finalize a tarefa diretamente."
        )

    # Imports tardios evitam ciclo (ai_service importa tools no topo).
    from auditor.models import Agent, SessionAgent
    from auditor.ai_service import (
        run_agent, build_runtime_agent_from_session, _node_to_runtime_agent,
    )

    # ── Modo playbook ────────────────────────────────────────────────────
    # Se a conversa roda um playbook, a resolução é ISOLADA aos nós do grafo:
    # a tabela Agent global fica invisível e cada nó só delega para os alvos
    # das suas próprias arestas de saída.
    pb_nodes = _session.get("__playbook_nodes")
    if pb_nodes is not None:
        adjacency = _session.get("__playbook_adjacency") or {}
        caller = _session.get("__current_node") or _session.get("__playbook_root")
        allowed = set(adjacency.get(caller, []))
        if agent_slug not in pb_nodes:
            disponiveis = ", ".join(sorted(allowed)) or "nenhum"
            return (
                f"[ERRO] Agente '{agent_slug}' não existe neste playbook. "
                f"Delegáveis a partir de '{caller}': {disponiveis}."
            )
        if agent_slug not in allowed:
            disponiveis = ", ".join(sorted(allowed)) or "nenhum"
            return (
                f"[ERRO] Agente '{agent_slug}' não é alcançável a partir de "
                f"'{caller}' neste playbook (sem aresta). "
                f"Delegáveis: {disponiveis}."
            )
        sub_agent = _node_to_runtime_agent(pb_nodes[agent_slug])
        history = _session.get("__history") or []

        # Marca o nó chamado como o "corrente" durante sua execução, para que a
        # injeção de especialista dele use as PRÓPRIAS arestas. Restaura depois.
        prev_node = _session.get("__current_node")
        _session["__current_node"] = agent_slug
        _session["__agent_call_depth"] = depth + 1
        try:
            result = run_agent(
                agent=sub_agent, user_message=task, history=history,
                session=_session,
            )
        finally:
            _session["__agent_call_depth"] = depth
            _session["__current_node"] = prev_node

        if result.awaiting_human:
            _session["__awaiting_human"] = True
            _session["__human_question"] = result.human_question
            return (
                f"[Sub-agente '{agent_slug}' precisa de input do usuário antes "
                f"de continuar. Pergunta: {result.human_question}]"
            )
        if result.tool_calls:
            _session["__nested_tool_calls"] = result.tool_calls
        return result.answer or ""

    # Slug reservado: o agente criado só para esta conversa (não democratizado).
    # Resolvido em runtime a partir do SessionAgent da conversa corrente.
    if agent_slug == SessionAgent.SLUG:
        conv_id = _session.get("__conversation_id")
        session_agent = (
            SessionAgent.objects.filter(conversation_id=conv_id).first()
            if conv_id else None
        )
        if session_agent is None:
            return (
                "[ERRO] Esta conversa não tem um agente de sessão configurado. "
                "Resolva a tarefa com os sub-agentes globais ou suas tools."
            )
        sub_agent = build_runtime_agent_from_session(session_agent)
    else:
        try:
            sub_agent = Agent.objects.get(slug=agent_slug)
        except Agent.DoesNotExist:
            disponiveis = ", ".join(
                Agent.objects.values_list("slug", flat=True)
            ) or "(nenhum)"
            disponiveis = f"{disponiveis}, {SessionAgent.SLUG} (se criado)"
            return (
                f"[ERRO] Agente '{agent_slug}' não encontrado. "
                f"Disponíveis: {disponiveis}."
            )

    history = _session.get("__history") or []

    _session["__agent_call_depth"] = depth + 1
    try:
        result = run_agent(
            agent=sub_agent,
            user_message=task,
            history=history,
            session=_session,
        )
    finally:
        _session["__agent_call_depth"] = depth

    if result.awaiting_human:
        # Sinaliza para o run_agent pai que precisa pausar e propagar a
        # pergunta ao usuário. O loop pai detecta esses flags na sessão
        # logo após esta tool retornar.
        _session["__awaiting_human"] = True
        _session["__human_question"] = result.human_question
        return (
            f"[Sub-agente '{agent_slug}' precisa de input do usuário antes "
            f"de continuar. Pergunta: {result.human_question}]"
        )

    # Expõe as tool calls aninhadas via _session — o run_agent pai lê e
    # anexa ao próprio tool_call de call_agent. Assim o frontend pode
    # renderizar a árvore (call_agent → analise_massiva_llm, etc).
    if result.tool_calls:
        _session["__nested_tool_calls"] = result.tool_calls

    return result.answer or ""
