"""Torna o **Cientista de Dados** um especialista não-supervisionado completo.

A config efetiva dos agentes vive no banco (não nos .md) — por isso as novas
tools e o prompt revisado só passam a valer aqui:

1. Habilita no `cientista_dados` as novas tools de ciência de dados:
   - `executar_agglomerative` — clustering hierárquico (outro modelo).
   - `comparar_clusters`      — varredura de K (elbow + métricas) p/ escolher K.
   - `avaliar_clusters`       — métricas internas ricas + balanceamento.
   - `executar_pca`           — PCA (variância explicada, loadings).
   - `detectar_outliers`      — Isolation Forest / LOF / z-score / IQR.
   - `selecionar_features`    — seleção de features não-supervisionada.
2. Recarrega o system_prompt do cientista_dados a partir do .md revisado.

Estende a 0020 (que criou o agente só com K-Means/DBSCAN/silhueta).
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Tools novas que o cientista_dados passa a enxergar.
CIENTISTA_NEW_TOOLS = [
    "executar_agglomerative",
    "comparar_clusters",
    "avaliar_clusters",
    "executar_pca",
    "detectar_outliers",
    "selecionar_features",
]


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

    agent = Agent.objects.filter(slug="cientista_dados").first()
    if not agent:
        return

    _add_tools(agent, CIENTISTA_NEW_TOOLS)

    path = PROMPTS_DIR / "cientista_dados.md"
    if path.exists():
        new_prompt = path.read_text(encoding="utf-8").strip()
        if agent.system_prompt != new_prompt:
            agent.system_prompt = new_prompt
            agent.save(update_fields=["system_prompt"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    agent = Agent.objects.filter(slug="cientista_dados").first()
    if not agent:
        return
    tools = [t for t in (agent.tools_enabled or [])
             if t not in CIENTISTA_NEW_TOOLS]
    if tools != (agent.tools_enabled or []):
        agent.tools_enabled = tools
        agent.save(update_fields=["tools_enabled"])
    # O prompt não é revertido (não guardamos a versão anterior aqui).


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0022_query_qualquer_base"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
