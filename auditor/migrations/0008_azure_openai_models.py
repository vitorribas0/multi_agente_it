"""
Reverte os agentes para Azure OpenAI (gpt-4o) em vez de Bedrock/Claude.
"""
from django.db import migrations


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.all().update(model="gpt-4o")


def downgrade(apps, schema_editor):
    # Volta para Claude se necessário
    SLUG_TO_MODEL = {
        "orquestrador": "anthropic.claude-opus-4-7-v1",
        "analista_dados": "anthropic.claude-sonnet-4-6-v1",
        "analista_documentos": "anthropic.claude-sonnet-4-6-v1",
        "gerador_sql": "anthropic.claude-sonnet-4-6-v1",
    }
    Agent = apps.get_model("auditor", "Agent")
    for slug, model in SLUG_TO_MODEL.items():
        Agent.objects.filter(slug=slug).update(model=model)


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0007_claude_models"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
