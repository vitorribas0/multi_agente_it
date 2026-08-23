import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("auditor", "0037_execution"),
    ]

    operations = [
        migrations.AddField(
            model_name="execution",
            name="attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="execution",
            name="claimed_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="execution",
            name="request_payload",
            field=models.JSONField(blank=True, default=dict, help_text="Payload necessário para um worker retomar a execução fora do request HTTP."),
        ),
        migrations.CreateModel(
            name="ExecutionInteraction",
            fields=[
                ("token", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("method", models.CharField(max_length=120)),
                ("params", models.JSONField(blank=True, default=dict)),
                ("response", models.JSONField(blank=True, default=dict)),
                ("status", models.CharField(choices=[("pending", "Pendente"), ("responded", "Respondida"), ("expired", "Expirada"), ("cancelled", "Cancelada")], db_index=True, default="pending", max_length=16)),
                ("expires_at", models.DateTimeField(blank=True, null=True)),
                ("responded_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("execution", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="interactions", to="auditor.execution")),
            ],
            options={"ordering": ["created_at"]},
        ),
    ]
