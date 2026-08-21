"""
Habilita a tool `gerar_fluxograma` em TODOS os agentes (qualquer um pode
desenhar o fluxo do que executou) e recarrega o system_prompt do
orquestrador, que ganhou a seção sobre quando/como usar o fluxograma.

A tool publica um attachment `kind: "mermaid"`; o frontend (chat.js + mermaid.js)
renderiza o diagrama com botões de download PNG / SVG / .mmd.
"""
from pathlib import Path

from django.db import migrations


PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# slug -> arquivo .md cujo prompt mudou nesta migration
_PROMPT_RELOADS = {
    "orquestrador": "orquestrador.md",
}


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")

    # 1) Habilita gerar_fluxograma em todos os agentes (idempotente).
    for agent in Agent.objects.all():
        tools = list(agent.tools_enabled or [])
        if "gerar_fluxograma" not in tools:
            tools.append("gerar_fluxograma")
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])

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
    for agent in Agent.objects.all():
        tools = [t for t in (agent.tools_enabled or []) if t != "gerar_fluxograma"]
        if tools != (agent.tools_enabled or []):
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0015_pergunta_quantidade"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
