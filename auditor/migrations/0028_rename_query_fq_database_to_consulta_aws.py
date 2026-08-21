"""Renomeia a tool `query_fq_database` -> `consulta_aws`.

A tool foi renomeada no código (slug = nome da função). Como o slug fica
persistido no `tools_enabled` (JSON) de cada agente, esta migration troca a
referência antiga pela nova nos agentes que a tinham habilitada
(`orquestrador`, `gerador_sql`) — sem duplicar caso já exista.
"""
from django.db import migrations

_OLD = "query_fq_database"
_NEW = "consulta_aws"


def _swap(tools, old, new):
    """Substitui `old` por `new` numa lista de slugs, preservando a ordem e
    sem duplicar. Retorna (nova_lista, mudou)."""
    tools = list(tools or [])
    if old not in tools:
        return tools, False
    novo = []
    for t in tools:
        if t == old:
            if new not in novo:
                novo.append(new)
        elif t not in novo:
            novo.append(t)
    return novo, True


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for agent in Agent.objects.all():
        tools, changed = _swap(agent.tools_enabled, _OLD, _NEW)
        if changed:
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for agent in Agent.objects.all():
        tools, changed = _swap(agent.tools_enabled, _NEW, _OLD)
        if changed:
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0027_buscar_na_web"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
