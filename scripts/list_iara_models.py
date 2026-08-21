"""
Lista modelos disponíveis no provider corrente do IaraGenAI.

Uso:
    .venv/bin/python scripts/list_iara_models.py

Configure o provider no .env (IARA_PROVIDER) — ele lista o que está
disponível para esse provider. Para Claude, queremos provider=bedrock.
"""
import os
import sys
import uuid
from pathlib import Path

# Permite rodar de qualquer cwd
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

from iaragenai import IaraGenAI

provider = os.getenv("IARA_PROVIDER", "bedrock")
print(f"Provider: {provider}")
print(f"Environment: {os.getenv('IARA_ENVIRONMENT', 'homol')}")
print("-" * 60)

client = IaraGenAI(
    client_id=os.getenv("IARA_CLIENT_ID"),
    client_secret=os.getenv("IARA_CLIENT_SECRET"),
    environment=os.getenv("IARA_ENVIRONMENT", "homol"),
    provider=provider,
    correlation_id=str(uuid.uuid4()),
)

models = client.models.list()

print(f"Tipo retornado: {type(models).__name__}")
print()
print(models)
