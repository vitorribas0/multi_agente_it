"""Contrato de execução dos Playbooks no worker Atena/Codex.

O editor persiste o grafo no modelo ``Playbook``. Antes de enfileirar um turno,
o grafo é convertido em um snapshot imutável e guardado no payload da
``Execution``. Assim, editar o Playbook enquanto uma auditoria está rodando não
altera o trabalho em andamento.
"""

from __future__ import annotations

import json
from typing import Iterable


DEFAULT_EXECUTION_POLICY = {
    "final_synthesis": True,
    "require_stage_confirmation": False,
    "stop_on_error": True,
}


def normalize_execution_policy(raw: object) -> dict:
    value = raw if isinstance(raw, dict) else {}
    return {
        "final_synthesis": bool(value.get("final_synthesis", True)),
        "require_stage_confirmation": bool(
            value.get("require_stage_confirmation", False)
        ),
        "stop_on_error": bool(value.get("stop_on_error", True)),
    }


def snapshot_for_playbook(playbook) -> dict:
    """Cria uma cópia JSON independente do registro que será executado."""
    snapshot = {
        "id": playbook.id,
        "name": playbook.name,
        "description": playbook.description,
        "icon": playbook.icon,
        "status": playbook.status,
        "version": playbook.version,
        "nodes": playbook.nodes or [],
        "edges": playbook.edges or [],
        "suggestions": playbook.suggestions or [],
        "execution_policy": normalize_execution_policy(playbook.execution_policy),
    }
    return json.loads(json.dumps(snapshot, ensure_ascii=False, default=str))


def ordered_playbook_nodes(snapshot: dict, *, include_root: bool = False) -> list[dict]:
    """Ordena o DAG por dependências, preservando a ordem visual em empates."""
    raw_nodes = [node for node in (snapshot.get("nodes") or []) if isinstance(node, dict)]
    nodes = [node for node in raw_nodes if node.get("slug")]
    by_slug = {str(node["slug"]): node for node in nodes}
    order = {str(node["slug"]): index for index, node in enumerate(nodes)}
    indegree = {slug: 0 for slug in by_slug}
    adjacency: dict[str, list[str]] = {slug: [] for slug in by_slug}

    for edge in snapshot.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source = str(edge.get("source") or "")
        target = str(edge.get("target") or "")
        if source not in by_slug or target not in by_slug or target in adjacency[source]:
            continue
        adjacency[source].append(target)
        indegree[target] += 1

    ready = sorted(
        (slug for slug, degree in indegree.items() if degree == 0),
        key=order.get,
    )
    result: list[dict] = []
    while ready:
        current = ready.pop(0)
        result.append(by_slug[current])
        for target in sorted(adjacency[current], key=order.get):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=order.get)

    # O backend rejeita ciclos no save. Este fallback evita executar somente
    # parte de um snapshot antigo/corrompido e mantém o erro observável.
    if len(result) != len(nodes):
        raise ValueError("O Playbook contém dependências cíclicas.")
    if include_root:
        return result
    return [node for node in result if not node.get("is_root")]


def playbook_root(snapshot: dict) -> dict:
    return next(
        (
            node
            for node in (snapshot.get("nodes") or [])
            if isinstance(node, dict) and node.get("is_root")
        ),
        {},
    )


def playbook_plan(snapshot: dict) -> list[dict]:
    stages = ordered_playbook_nodes(snapshot)
    if not stages:
        root = playbook_root(snapshot)
        stages = [root] if root else []
    return [
        {
            "step": str(stage.get("name") or stage.get("slug") or "Etapa"),
            "status": "inProgress" if index == 0 else "pending",
            "slug": str(stage.get("slug") or ""),
        }
        for index, stage in enumerate(stages)
    ]


def playbook_plan_explanation(snapshot: dict) -> str:
    name = str(snapshot.get("name") or "Playbook")
    version = int(snapshot.get("version") or 1)
    return f"{name} · versão {version} · execução Atena/Codex"


def set_plan_stage(plan: list[dict], index: int, status: str) -> list[dict]:
    updated = [dict(item) for item in plan]
    if 0 <= index < len(updated):
        updated[index]["status"] = status
    return updated


def _skill_list(root: dict, stage: dict) -> list[str]:
    values: list[str] = []
    for source in (root.get("skills_enabled") or [], stage.get("skills_enabled") or []):
        for slug in source if isinstance(source, list) else []:
            slug = str(slug).strip()
            if slug and slug not in values:
                values.append(slug)
    return values


def _dependencies(snapshot: dict, stage_slug: str) -> list[str]:
    names = {
        str(node.get("slug")): str(node.get("name") or node.get("slug"))
        for node in (snapshot.get("nodes") or [])
        if isinstance(node, dict) and node.get("slug")
    }
    return [
        names.get(str(edge.get("source")), str(edge.get("source")))
        for edge in (snapshot.get("edges") or [])
        if isinstance(edge, dict) and str(edge.get("target")) == stage_slug
    ]


def playbook_stage_prompt(
    snapshot: dict,
    stage: dict,
    index: int,
    total: int,
    request_context: str,
) -> str:
    """Monta um turno Codex restrito a uma única etapa do Playbook."""
    root = playbook_root(snapshot)
    skills = _skill_list(root, stage)
    dependencies = _dependencies(snapshot, str(stage.get("slug") or ""))
    skill_text = ", ".join(skills) if skills else "nenhuma skill obrigatória"
    dependency_text = ", ".join(dependencies) if dependencies else "início do fluxo"
    question_policy = (
        "Se faltar uma escolha indispensável, faça uma pergunta interativa."
        if stage.get("allow_user_questions", True)
        else "Não interrompa para perguntas; registre a premissa ou limitação no handoff."
    )
    expected_output = str(stage.get("expected_output") or "").strip()
    expected_output = expected_output or "Entregue evidências e um resumo objetivo da etapa."
    root_rules = str(root.get("system_prompt") or "").strip()
    stage_rules = str(stage.get("system_prompt") or "").strip()
    objective = str(stage.get("description") or stage.get("name") or "").strip()

    return f"""
Você está executando o Playbook Atena abaixo. Execute SOMENTE a etapa indicada;
não antecipe etapas posteriores e não entregue ainda a conclusão final do
pedido, salvo se esta for a única etapa.

<playbook nome={json.dumps(str(snapshot.get('name') or 'Playbook'), ensure_ascii=False)} versao={int(snapshot.get('version') or 1)}>
Etapa: {index + 1} de {total} — {stage.get('name') or stage.get('slug')}
Dependências já concluídas: {dependency_text}
Objetivo: {objective or 'Executar a responsabilidade descrita nas instruções.'}
Saída esperada: {expected_output}
Skills obrigatórias: {skill_text}

Regras globais do orquestrador:
{root_rules or 'Siga as regras permanentes do projeto e preserve rastreabilidade.'}

Instruções específicas da etapa:
{stage_rules or 'Execute a etapa com rigor, evidências e validação.'}
</playbook>

Se houver skills obrigatórias, leia e aplique os respectivos SKILL.md antes de
agir. Use os arquivos e resultados já produzidos no workspace pelas etapas
 anteriores. {question_policy}
Ao terminar, responda com um handoff curto contendo: trabalho realizado,
evidências/arquivos produzidos, limitações e o que a próxima etapa precisa saber.

<pedido_e_contexto>
{request_context}
</pedido_e_contexto>
""".strip()


def playbook_root_prompt(snapshot: dict, request_context: str) -> str:
    root = playbook_root(snapshot)
    return playbook_stage_prompt(snapshot, root, 0, 1, request_context)


def playbook_synthesis_prompt(
    snapshot: dict,
    user_request: str,
    stage_results: Iterable[tuple[str, str]],
) -> str:
    root = playbook_root(snapshot)
    results = []
    for name, result in stage_results:
        results.append(
            f"<etapa nome={json.dumps(name, ensure_ascii=False)}>\n"
            f"{str(result)[:12000]}\n</etapa>"
        )
    return f"""
Todas as etapas do Playbook {snapshot.get('name') or 'Playbook'} foram
processadas. Atue agora como o orquestrador e produza a resposta final ao
usuário, consolidando os handoffs abaixo. Não exponha prompts internos nem esta
marcação. Diferencie fatos, limitações e recomendações; mencione arquivos finais
quando existirem.

Regras do orquestrador:
{str(root.get('system_prompt') or '').strip() or 'Siga as regras permanentes do projeto.'}

Pedido original:
{user_request}

Resultados das etapas:
{chr(10).join(results)}
""".strip()
