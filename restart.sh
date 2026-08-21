#!/bin/bash
# Script para reiniciar o servidor Django

echo "Parando servidor Django..."
pkill -f "manage.py runserver" 2>/dev/null

echo "Aguardando..."
sleep 2

echo "Iniciando servidor Django..."
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/manage.py" runserver 0.0.0.0:8000

echo "Servidor rodando em http://localhost:8000"
echo "SageMaker URL: https://lyafedijaq67nmx.studio.sa-east-1.sagemaker.aws/codeeditor/default/ports/8000/"
