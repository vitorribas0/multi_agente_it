"""
Migra os agentes para Claude (Bedrock) e atualiza os system prompts a
partir dos arquivos de prompts/.

- Orquestrador: Opus 4.7 (mais capaz, lida com planejamento e delegação)
- Especialistas: Sonnet 4.6 (custo/performance ideal pra tools chain)
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


SLUG_TO_PROMPT_FILE = {
    "orquestrador": "orquestrador.md",
    "analista_dados": "analista_dados.md",
    "analista_documentos": "analista_documentos.md",
    "gerador_sql": "gerador_sql.md",
}


SLUG_TO_MODEL = {
    "orquestrador": "anthropic.claude-opus-4-7-v1",
    "analista_dados": "anthropic.claude-sonnet-4-6-v1",
    "analista_documentos": "anthropic.claude-sonnet-4-6-v1",
    "gerador_sql": "anthropic.claude-sonnet-4-6-v1",
}


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for agent in Agent.objects.all():
        changed = False

        new_model = SLUG_TO_MODEL.get(agent.slug)
        if new_model and agent.model != new_model:
            agent.model = new_model
            changed = True

        prompt_file = SLUG_TO_PROMPT_FILE.get(agent.slug)
        if prompt_file:
            path = PROMPTS_DIR / prompt_file
            if path.exists():
                new_prompt = path.read_text(encoding="utf-8").strip()
                if agent.system_prompt != new_prompt:
                    agent.system_prompt = new_prompt
                    changed = True

        if changed:
            agent.save(update_fields=["model", "system_prompt"])


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    Agent.objects.all().update(model="gpt-4o")


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0006_analista_documentos"),
    ]

    operations = [
        migrations.RunPython(upgrade, reverse_code=downgrade),
    ]
