"""
Cria o agente **Cientista de Dados** (clustering + visualização) e cabeia
as novas tools.

O que esta migration faz (a config efetiva dos agentes vive no banco, não
nos .md — por isso as mudanças de prompt/tools só passam a valer aqui):

1. Cria/atualiza o agente `cientista_dados` (Opus 4.6, como os demais):
   K-Means, DBSCAN, silhueta, a tool genérica `gerar_grafico`, mais o
   canivete pandas/export e thinking/ask_human.
2. Habilita a tool genérica `gerar_grafico` (e o atalho de barras) no
   `analista_dados`, que ganha capacidade de plotar.
3. Garante `call_agent` no orquestrador (delegar p/ o novo sub-agente).
4. Recarrega os system_prompts que mudaram (orquestrador, analista_dados)
   a partir dos .md.

As tools de cluster (`executar_kmeans`/`executar_dbscan`/`calcular_silhouette`)
foram adicionadas via commit anterior mas nunca habilitadas em nenhum agente
— aqui elas passam a existir de fato, sob o cientista_dados.
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

OPUS = "anthropic.claude-opus-4-6-v1"

CIENTISTA_TOOLS = [
    "thinking",
    "ask_human",
    "descrever_dataset",
    "executar_kmeans",
    "executar_dbscan",
    "calcular_silhouette",
    "gerar_grafico",
    "gerar_grafico_barras",
    "executar_pandas",
    "exportar_dataset",
]

# Tools de gráfico que o analista_dados também passa a enxergar.
ANALISTA_NEW_TOOLS = ["gerar_grafico", "gerar_grafico_barras"]

# slug -> arquivo .md cujo prompt mudou nesta migration.
_PROMPT_RELOADS = {
    "orquestrador": "orquestrador.md",
    "analista_dados": "analista_dados.md",
}


def _load_prompt(filename: str) -> str:
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return "Você é o Cientista de Dados."


def _add_tools(agent, slugs):
    tools = list(agent.tools_enabled or [])
    changed = False
    for slug in slugs:
        if slug not in tools:
            tools.append(slug)
            changed = True
    if changed:
        agent.tools_enabled = tools
        agent.save(update_fields=["tools_enabled"])


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")

    # 1) Cria/atualiza o cientista_dados.
    Agent.objects.update_or_create(
        slug="cientista_dados",
        defaults={
            "name": "Cientista de Dados",
            "description": "Clustering (K-Means/DBSCAN), detecção de outliers e gráficos.",
            "icon": "🔬",
            "system_prompt": _load_prompt("cientista_dados.md"),
            "model": OPUS,
            "temperature": 0.3,
            "tools_enabled": CIENTISTA_TOOLS,
            "is_default": False,
        },
    )

    # 2) analista_dados ganha as tools de gráfico.
    analista = Agent.objects.filter(slug="analista_dados").first()
    if analista:
        _add_tools(analista, ANALISTA_NEW_TOOLS)

    # 3) Orquestrador precisa de call_agent p/ delegar.
    orq = Agent.objects.filter(slug="orquestrador").first()
    if orq:
        _add_tools(orq, ["call_agent"])

    # 4) Recarrega prompts que mudaram.
    for slug, filename in _PROMPT_RELOADS.items():
        agent = Agent.objects.filter(slug=slug).first()
        if not agent:
            continue
        path = PROMPTS_DIR / filename
        if not path.exists():
            continue
        new_prompt = path.read_text(encoding="utf-8").strip()
        if agent.system_prompt != new_prompt:
            agent.system_prompt = new_prompt
            agent.save(update_fields=["system_prompt"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.filter(slug="cientista_dados").delete()

    analista = Agent.objects.filter(slug="analista_dados").first()
    if analista:
        tools = [t for t in (analista.tools_enabled or [])
                 if t not in ANALISTA_NEW_TOOLS]
        if tools != (analista.tools_enabled or []):
            analista.tools_enabled = tools
            analista.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0019_sessionagent_documents"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
