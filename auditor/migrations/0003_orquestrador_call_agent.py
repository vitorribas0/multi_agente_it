"""Habilita a tool call_agent no orquestrador."""
from django.db import migrations


def add_call_agent(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    orq = Agent.objects.filter(slug="orquestrador").first()
    if not orq:
        return
    tools = list(orq.tools_enabled or [])
    if "call_agent" not in tools:
        tools.append("call_agent")
        orq.tools_enabled = tools
        orq.save(update_fields=["tools_enabled"])


def remove_call_agent(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    orq = Agent.objects.filter(slug="orquestrador").first()
    if not orq:
        return
    tools = [t for t in (orq.tools_enabled or []) if t != "call_agent"]
    orq.tools_enabled = tools
    orq.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0002_agents_state"),
    ]

    operations = [
        migrations.RunPython(add_call_agent, reverse_code=remove_call_agent),
    ]
