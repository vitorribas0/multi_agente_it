"""
Registry de tools com decorator @tool.

Como criar uma nova tool:

    from tools.registry import tool

    @tool(description="Soma dois números")
    def somar(a: int, b: int) -> str:
        return str(a + b)

A função vira automaticamente:
- Disponível no schema OpenAI function calling
- Visível na tela de configuração
- Chamável por qualquer agente que tenha o slug habilitado

Para acessar o estado da sessão (compartilhado entre tools de uma conversa),
adicione `_session: dict` como parâmetro:

    @tool(description="Carrega CSV")
    def load_csv(path: str, _session: dict) -> str:
        df = pd.read_csv(path)
        _session["df"] = df.to_dict(orient="records")
        return f"Carregado: {len(df)} linhas"
"""
import inspect
import re
import typing
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


_REGISTRY: dict[str, "ToolSpec"] = {}


@dataclass
class ToolSpec:
    """Especificação de uma tool registrada."""
    slug: str
    name: str
    description: str
    func: Callable
    parameters: dict = field(default_factory=dict)
    required: list[str] = field(default_factory=list)
    uses_session: bool = False
    is_human_in_loop: bool = False
    icon: str = "⚡"

    def to_openai_schema(self) -> dict:
        """Retorna o schema no formato OpenAI function calling."""
        return {
            "type": "function",
            "function": {
                "name": self.slug,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": self.parameters,
                    "required": self.required,
                },
            },
        }


_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json(annotation) -> str:
    """Converte type hint Python em tipo JSON Schema."""
    if annotation is inspect.Parameter.empty:
        return "string"
    origin = typing.get_origin(annotation)
    if origin is None:
        return _PY_TO_JSON.get(annotation, "string")
    if origin in (list, tuple):
        return "array"
    if origin is dict:
        return "object"
    if origin is typing.Union:
        # Optional[X] = Union[X, None] — pega o primeiro não-None
        args = [a for a in typing.get_args(annotation) if a is not type(None)]
        if args:
            return _python_type_to_json(args[0])
    return "string"


def _array_item_type(annotation) -> str:
    """Tipo JSON dos elementos de um list[...]. Default 'string'."""
    args = typing.get_args(annotation)
    if args:
        first = args[0]
        if first is not type(None):
            return _python_type_to_json(first)
    return "string"


_DOCSTRING_ARGS_RE = re.compile(
    r"^\s*Args:\s*\n((?:\s+\w[^\n]*\n?)+)",
    flags=re.MULTILINE,
)
_DOCSTRING_PARAM_RE = re.compile(r"^\s+(\w+)\s*:\s*(.+?)$", flags=re.MULTILINE)


def _parse_param_descriptions(func: Callable) -> dict[str, str]:
    """Extrai descrições por parâmetro do docstring no estilo Google.

    Suporta:
        Args:
            nome: Descrição.
            outro: Outra descrição.
    """
    doc = inspect.getdoc(func) or ""
    if not doc:
        return {}
    m = _DOCSTRING_ARGS_RE.search(doc)
    if not m:
        return {}
    block = m.group(1)
    return {
        name: desc.strip()
        for name, desc in _DOCSTRING_PARAM_RE.findall(block)
    }


def _build_parameters_from_signature(func: Callable) -> tuple[dict, list[str], bool]:
    """Inspeciona a assinatura e gera schema JSON dos parâmetros."""
    sig = inspect.signature(func)
    param_docs = _parse_param_descriptions(func)
    properties: dict = {}
    required: list[str] = []
    uses_session = False

    for pname, param in sig.parameters.items():
        # _session é injetado pelo runtime, não vai pro schema
        if pname.startswith("_session"):
            uses_session = True
            continue
        if pname.startswith("_"):
            continue

        json_type = _python_type_to_json(param.annotation)
        prop: dict[str, Any] = {"type": json_type}

        # JSON Schema exige 'items' em arrays — Azure/OpenAI rejeitam array
        # sem ele. Inferimos o tipo dos elementos do hint (list[str]) e
        # caímos para "string" quando não há anotação.
        if json_type == "array":
            prop["items"] = {"type": _array_item_type(param.annotation)}

        prop["description"] = param_docs.get(pname, "")

        properties[pname] = prop

        if param.default is inspect.Parameter.empty:
            required.append(pname)

    return properties, required, uses_session


def tool(
    description: str,
    slug: Optional[str] = None,
    name: Optional[str] = None,
    icon: str = "⚡",
    is_human_in_loop: bool = False,
):
    """Decorator que registra uma tool."""
    def decorator(func: Callable) -> Callable:
        _slug = slug or func.__name__
        _name = name or func.__name__.replace("_", " ").title()

        properties, required, uses_session = _build_parameters_from_signature(func)

        spec = ToolSpec(
            slug=_slug,
            name=_name,
            description=description,
            func=func,
            parameters=properties,
            required=required,
            uses_session=uses_session,
            is_human_in_loop=is_human_in_loop,
            icon=icon,
        )
        _REGISTRY[_slug] = spec
        return func

    return decorator


def publish_attachment(session: Optional[dict], payload: dict) -> None:
    """Publica um card de artefato (export/chart/mermaid/table) na mensagem.

    Acumula numa LISTA em vez de sobrescrever uma chave única — assim um
    mesmo turno pode produzir vários cards (ex.: PDF + Excel, ou dois
    gráficos) e todos aparecem no chat. O run_agent/view drena
    `__pending_attachments` ao persistir o turno.
    """
    if session is None:
        return
    session.setdefault("__pending_attachments", []).append(payload)


def get_tool(slug: str) -> Optional[ToolSpec]:
    return _REGISTRY.get(slug)


def all_tools() -> list[ToolSpec]:
    return list(_REGISTRY.values())


def schemas_for(slugs: list[str]) -> list[dict]:
    """Retorna schemas OpenAI das tools cujos slugs estão na lista."""
    return [
        _REGISTRY[s].to_openai_schema()
        for s in slugs
        if s in _REGISTRY
    ]


def autodiscover():
    """Importa todos os módulos de tools/ para registrar as @tool."""
    import importlib
    import pkgutil
    import tools as tools_pkg

    for _, modname, _ in pkgutil.iter_modules(tools_pkg.__path__):
        if modname in {"registry", "__init__"}:
            continue
        try:
            importlib.import_module(f"tools.{modname}")
        except ImportError as e:
            # Dependência opcional ausente (ex.: scikit-learn) não deve
            # derrubar o registro das demais tools — só essa fica de fora.
            import logging
            logging.getLogger(__name__).warning(
                "Tool '%s' não registrada (dependência ausente): %s", modname, e)
