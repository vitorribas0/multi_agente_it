"""
Habilita a tool `gerar_documentacao_pdf` em TODOS os agentes e recarrega o
system_prompt do orquestrador, que ganhou a seção sobre quando/como gerar a
documentação em PDF (perguntar antes se o usuário quer o PDF).

A tool publica um attachment `kind: "export"` (formato "pdf"); o frontend
(chat.js) renderiza o mesmo card de download usado para CSV/XLSX.
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

    # 1) Habilita gerar_documentacao_pdf em todos os agentes (idempotente).
    for agent in Agent.objects.all():
        tools = list(agent.tools_enabled or [])
        if "gerar_documentacao_pdf" not in tools:
            tools.append("gerar_documentacao_pdf")
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
        tools = [t for t in (agent.tools_enabled or []) if t != "gerar_documentacao_pdf"]
        if tools != (agent.tools_enabled or []):
            agent.tools_enabled = tools
            agent.save(update_fields=["tools_enabled"])


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0017_sessionagent"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
