"""Generaliza a consulta Athena para qualquer base.

- Habilita a nova tool `descrever_tabela` no `gerador_sql` (schema + preview
  de uma tabela, para o agente entender a base antes de filtrar).
- Recarrega os prompts `gerador_sql.md` e `orquestrador.md`, agora agnósticos
  à base FQ (a FQ vira o exemplo/default, não a única base).

A tool `query_fq_database` ganhou o parâmetro `database` (default = FQ) no
código — sem mudança de slug, então nada a fazer aqui além de garantir que o
`descrever_tabela` esteja disponível ao mesmo agente.
"""
from pathlib import Path

from django.db import migrations

PROMPTS_DIR = Path(__file__).resolve().parent.parent.parent / "prompts"

# Tool nova que o gerador_sql passa a enxergar.
GERADOR_SQL_NEW_TOOLS = ["descrever_tabela"]

# slug -> arquivo .md cujo prompt mudou nesta migration.
_PROMPT_RELOADS = {
    "gerador_sql": "gerador_sql.md",
    "orquestrador": "orquestrador.md",
}


def _add_tools(agent, slugs):
    tools = list(agent.tools_enabled or [])
    changed = False
    for slug in slugs:
        if slug not in tools:
            tools.append(slug)
            changed = True
    if changed:
        agent.tools_enabled = tools
        agent.save(update_fields=["tools_enabled"])


def _reload_prompts(Agent):
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


def upgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")

    gerador = Agent.objects.filter(slug="gerador_sql").first()
    if gerador:
        _add_tools(gerador, GERADOR_SQL_NEW_TOOLS)

    _reload_prompts(Agent)


def downgrade(apps, schema_editor):
    Agent = apps.get_model("auditor", "Agent")

    gerador = Agent.objects.filter(slug="gerador_sql").first()
    if gerador:
        tools = [t for t in (gerador.tools_enabled or [])
                 if t not in GERADOR_SQL_NEW_TOOLS]
        if tools != (gerador.tools_enabled or []):
            gerador.tools_enabled = tools
            gerador.save(update_fields=["tools_enabled"])
    # Os prompts não são revertidos (não guardamos a versão anterior aqui).


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0021_remove_gerar_grafico_barras"),
    ]

    operations = [
        migrations.RunPython(upgrade, downgrade),
    ]
