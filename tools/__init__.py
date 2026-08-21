"""
Tools do Multi-Agentes Auditoria — registradas via decorator @tool em registry.py.
"""
from .registry import tool, get_tool, all_tools, schemas_for, autodiscover

# Auto-importa todas as tools deste pacote para registrá-las
autodiscover()

__all__ = ["tool", "get_tool", "all_tools", "schemas_for", "autodiscover"]
