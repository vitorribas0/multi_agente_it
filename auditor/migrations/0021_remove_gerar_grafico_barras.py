"""Descontinua a tool `gerar_grafico_barras`.

A tool genérica `gerar_grafico` já cobre barras (mesma assinatura e mesmo
card no frontend), então a dedicada virou redundante. Esta migration remove
o slug `gerar_grafico_barras` de todos os agentes (globais e de sessão); o
arquivo `tools/gerar_grafico_barras.py` foi removido junto.
"""
from django.db import migrations

OLD_SLUG = "gerar_grafico_barras"
GENERIC_SLUG = "gerar_grafico"


def _strip_slug(qs):
    """Remove OLD_SLUG de tools_enabled de cada objeto do queryset."""
    for agent in qs:
        tools = list(agent.tools_enabled or [])
        if OLD_SLUG in tools:
            agent.tools_enabled = [t for t in tools if t != OLD_SLUG]
            agent.save(update_fields=["tools_enabled"])


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    SessionAgent = apps.get_model("auditor", "SessionAgent")
    _strip_slug(Agent.objects.all())
    _strip_slug(SessionAgent.objects.all())


def downgrade(apps, schema_editor):
    """Best-effort: devolve o slug a quem tem a genérica `gerar_grafico`."""
    Agent = apps.get_model("auditor", "Agent")
    SessionAgent = apps.get_model("auditor", "SessionAgent")
    for Model in (Agent, SessionAgent):
        for agent in Model.objects.all():
            tools = list(agent.tools_enabled or [])
            if GENERIC_SLUG in tools and OLD_SLUG not in tools:
                tools.append(OLD_SLUG)
                agent.tools_enabled = tools
                agent.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0020_cientista_dados"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
