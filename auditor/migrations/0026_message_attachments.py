# Generated for multi-card support (PDF + Excel, múltiplos gráficos, etc).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditor", "0025_appsettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="message",
            name="attachments",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text=(
                    "Lista de cards de artefato produzidos NO turno (export, "
                    "chart, mermaid, table). Permite vários por mensagem — ex.: "
                    "PDF + Excel ou dois gráficos. `attachment` (singular) segue "
                    "para o anexo único do upload do usuário."
                ),
            ),
        ),
    ]
