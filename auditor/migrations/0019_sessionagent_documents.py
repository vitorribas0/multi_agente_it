"""
Adiciona o campo `documents` ao SessionAgent — lista de documentos
(PDF/TXT de política etc.) anexados ao agente da sessão, já extraídos como
markdown. O conteúdo é injetado no contexto do agente em toda execução.

Cada item da lista: {filename, markdown, char_count, page_count}.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0018_gerar_documentacao_pdf"),
    ]

    operations = [
        migrations.AddField(
            model_name="sessionagent",
            name="documents",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Documentos anexados ao agente (PDF/TXT de política etc.), "
                    "já extraídos como markdown. Cada item: "
                    "{filename, markdown, char_count, page_count}. O conteúdo é "
                    "injetado no contexto do agente em toda execução."
                ),
            ),
        ),
    ]
