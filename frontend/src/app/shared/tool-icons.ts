// Ícone por tool — espelha o iconForTool do chat.js.
const TOOL_ICONS: Record<string, string> = {
  thinking: '🧠',
  ask_human: '❓',
  consulta_aws: '🗄️',
  descrever_dataset: '🔎',
  normalizar_coluna: '✨',
  filtrar_por_termo: '🔬',
  contar_keywords: '🔠',
  contem_termo: '✅',
  agrupar: '📊',
  regex_extrair: '🧩',
  call_agent: '🤝',
  analise_massiva_llm: '🚀',
  executar_pandas: '🐍',
  exportar_dataset: '💾',
  gerar_fluxograma: '🗺️',
  descrever_documento: '📄',
  ler_documento: '📖',
  buscar_no_documento: '🔎',
  extrair_tabelas_do_documento: '📊',
};

export function iconForTool(name: string): string {
  return TOOL_ICONS[name] || '⚡';
}

export function truncate(s: string, max: number): string {
  return s.length <= max ? s : s.slice(0, max) + '…';
}
