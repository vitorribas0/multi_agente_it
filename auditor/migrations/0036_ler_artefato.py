"""
Habilita a tool `ler_artefato` em TODOS os agentes.

Fecha o ciclo da memória externa: as tools de geração já salvam arquivos em
exports/ e devolvem só o download_url; esta tool permite reabrir o CONTEÚDO
de um artefato produzido em um turno anterior (revisar/corrigir/reaproveitar).
Leitura restrita a exports/ (guard anti-path-traversal na própria tool).
"""
from django.db import migrations


TOOL_SLUG = "ler_artefato"


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for agent in Agent.objects.all():
        tools = list(agent.tools_enabled or [])
        if TOOL_SLUG not in tools:
            tools.append(TOOL_SLUG)
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for agent in Agent.objects.all():
        tools = [t for t in (agent.tools_enabled or []) if t != TOOL_SLUG]
        if tools != (agent.tools_enabled or []):
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0035_analista_dados_batch_tools"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
