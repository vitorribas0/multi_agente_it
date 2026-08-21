"""
Habilita a tool `gerar_html` em TODOS os agentes.

A tool recebe do agente um documento HTML completo (apresentação/relatório/
documentação com números), salva em exports/ e publica um attachment
`kind: "export"` (formato "html"); o frontend renderiza o mesmo card de
download usado para CSV/XLSX/PDF.
"""
from django.db import migrations


TOOL_SLUG = "gerar_html"


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
        ("auditor", "0031_message_input_tokens_message_output_tokens_and_more"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
