"""
Cria BatchJob: persiste jobs de análise massiva em BATCH (Batch API do IARA).

Guarda o job_id + o blob de merge (meta) necessário para casar os resultados
de volta no dataset. Objetivo: o job sobrevive a queda de conexão/restart —
enquanto o job_id estiver salvo, buscar_resultado_batch recupera o resultado.
"""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0033_playbook"),
    ]

    operations = [
        migrations.CreateModel(
            name="BatchJob",
            fields=[
                ("id", models.AutoField(auto_created=True, primary_key=True,
                                        serialize=False, verbose_name="ID")),
                ("job_id", models.CharField(max_length=128, unique=True)),
                ("status", models.CharField(default="PENDING", max_length=24)),
                ("meta", models.JSONField(
                    default=dict,
                    help_text=(
                        "Contexto do job para casar resultados de volta: modelo, "
                        "coluna_texto, colunas_saida, id_to_index (custom_id→índice), "
                        "vazias, total, presigned_env."
                    ),
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "verbose_name": "Job de batch",
                "verbose_name_plural": "Jobs de batch",
                "ordering": ["-created_at"],
            },
        ),
    ]
