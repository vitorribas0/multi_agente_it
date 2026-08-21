"""
Atualiza prompts dos agentes para serem mais autônomos:
- Analista de dados: não pergunta mais sobre critérios óbvios
- Orquestrador: reforça princípio de autonomia
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"


SLUG_TO_PROMPT_FILE = {
    "orquestrador": "orquestrador.md",
    "analista_dados": "analista_dados.md",
}


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")
    for slug, filename in SLUG_TO_PROMPT_FILE.items():
        agent = Agent.objects.filter(slug=slug).first()
        if not agent:
            continue
        path = PROMPTS_DIR / filename
        if path.exists():
            new_prompt = path.read_text(encoding="utf-8").strip()
            if agent.system_prompt != new_prompt:
                agent.system_prompt = new_prompt
                agent.save(update_fields=["system_prompt"])


def downgrade(apps, schema_editor):
    pass  # Não reverte prompts


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0010_update_orquestrador_prompt"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
