#!/usr/bin/env python3
"""
Script de teste rápido para validar configuração AWS + SSL.

Uso:
    python test_aws_setup.py
"""
import os
import sys
from pathlib import Path

# Simular o carregamento do Django
sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
from auditor.proxy_config import apply_network_env

# 1. Carregar .env
base_dir = Path(__file__).parent
load_dotenv(base_dir / ".env")
print("[1] .env carregado")

# 2. Aplicar configuração de rede
apply_network_env(base_dir)
print("[2] apply_network_env() executado")

# 3. Verificar variáveis críticas
print("\n=== VARIAVEIS DE AMBIENTE ===")
vars_check = [
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "AWS_CA_BUNDLE",
    "AWS_DEFAULT_REGION",
    "HTTP_PROXY",
    "HTTPS_PROXY",
]
for var in vars_check:
    value = os.environ.get(var, "nao definido")
    # Truncar valores longos
    if len(value) > 50:
        value = value[:20] + "..." + value[-20:]
    print(f"  {var}: {value}")

# 4. Verificar CA bundle
print("\n=== CA BUNDLE ===")
ca_path = os.environ.get("AWS_CA_BUNDLE", "")
if ca_path and Path(ca_path).exists():
    size = Path(ca_path).stat().st_size
    print(f"  [OK] {ca_path} ({size} bytes)")
else:
    print(f"  [ERRO] CA bundle nao encontrado: {ca_path}")

# 5. Testar conexão com Athena
print("\n=== TESTE DE CONEXAO ATHENA ===")
try:
    import boto3
    client = boto3.client('athena', region_name='sa-east-1')
    response = client.list_work_groups()
    print(f"  [OK] Conexao estabelecida")
    print(f"  [OK] {len(response['WorkGroups'])} workgroups encontrados")
except Exception as e:
    print(f"  [ERRO] {e}")
    sys.exit(1)

# 6. Testar awswrangler
print("\n=== TESTE DE AWSWRANGLER ===")
try:
    import awswrangler as wr
    print(f"  [OK] awswrangler importado com sucesso")
except Exception as e:
    print(f"  [ERRO] {e}")
    sys.exit(1)

print("\n=== TUDO OK! ===")
print("Credenciais e CA bundle carregados do .env e arquivos_suporte/")
print("Voce pode agora chamar a tool consulta_aws com confianca.")
