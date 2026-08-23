import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0036_ler_artefato"),
    ]

    operations = [
        migrations.CreateModel(
            name="Execution",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("engine", models.CharField(default="codex-app-server", max_length=40)),
                ("backend", models.CharField(default="local", max_length=24)),
                ("status", models.CharField(choices=[("queued", "Na fila"), ("starting", "Iniciando"), ("running", "Executando"), ("waiting_user", "Aguardando usuário"), ("stopping", "Interrompendo"), ("completed", "Concluída"), ("stopped", "Interrompida"), ("failed", "Falhou")], db_index=True, default="queued", max_length=24)),
                ("runtime_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("thread_id", models.CharField(blank=True, default="", max_length=160)),
                ("turn_id", models.CharField(blank=True, default="", max_length=160)),
                ("events", models.JSONField(blank=True, default=list)),
                ("plan", models.JSONField(blank=True, default=list)),
                ("plan_explanation", models.TextField(blank=True, default="")),
                ("error", models.TextField(blank=True, default="")),
                ("stop_requested_at", models.DateTimeField(blank=True, null=True)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("last_heartbeat_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("conversation", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="executions", to="auditor.conversation")),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddConstraint(
            model_name="execution",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ["queued", "starting", "running", "waiting_user", "stopping"])),
                fields=("conversation",),
                name="unique_active_execution_per_conversation",
            ),
        ),
    ]
