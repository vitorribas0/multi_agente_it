"""
Habilita a tool `buscar_na_web` no orquestrador e recarrega seu system_prompt,
que ganhou a seção sobre quando/como puxar a busca na web (só para dado
externo/atual que não está no dataset/documento/KB).

A tool encapsula a `enterpriseWebSearch` nativa da Vertex dentro de uma
function-tool comum (cliente Vertex isolado), de modo que o orquestrador —
mesmo rodando em Claude/Bedrock — possa chamá-la sob demanda, sem misturar a
tool nativa com as demais function tools nem travar o provider.
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Só o orquestrador decide buscar na web; os sub-agentes ficam no escopo deles.
_TARGET_SLUG = "buscar_na_web"

# slug -> arquivo .md cujo prompt mudou nesta migration
_PROMPT_RELOADS = {
    "orquestrador": "orquestrador.md",
}


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")

    # 1) Habilita buscar_na_web no orquestrador (idempotente).
    orq = Agent.objects.filter(slug="orquestrador").first()
    if orq is not None:
        tools = list(orq.tools_enabled or [])
        if _TARGET_SLUG not in tools:
            tools.append(_TARGET_SLUG)
            orq.tools_enabled = tools
            orq.save(update_fields=["tools_enabled"])

    # 2) Recarrega prompts que mudaram.
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
    orq = Agent.objects.filter(slug="orquestrador").first()
    if orq is not None:
        tools = [t for t in (orq.tools_enabled or []) if t != _TARGET_SLUG]
        if tools != (orq.tools_enabled or []):
            orq.tools_enabled = tools
            orq.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0026_message_attachments"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
