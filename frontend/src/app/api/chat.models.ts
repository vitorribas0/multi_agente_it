// Tipos do contrato de chat, alinhados com auditor/views.py:
//   _message_payload, conversation_detail, conversation_list, chat_stream.
// Nesta fase (núcleo) só tratamos texto + histórico + streaming. Os anexos,
// tool_calls e artefatos vêm nas próximas fases, mas já declaramos os campos
// para não perder dado quando chegarem.

// Tool-call como o backend devolve (_message_payload): note que a chave é
// `tool` (não `name`), e as sub-chamadas ficam em `nested_tool_calls`.
export interface ToolCall {
  tool: string;
  args?: Record<string, unknown>;
  result?: string;
  error?: string;
  duration_ms?: number;
  nested_tool_calls?: ToolCall[];
}

// Artefato de export (CSV/XLSX/PDF baixável).
export interface ExportAttachment {
  kind: 'export';
  formato?: string;
  filename?: string;
  download_url?: string;
  titulo?: string;
  size_kb?: number | null;
  paginas?: number | null;
  linhas?: number | null;
  colunas?: number | null;
}

// Gráfico (imagem matplotlib em base64/data-url).
export interface ChartAttachment {
  kind: 'chart';
  chart_type?: string;
  tipo?: string;
  titulo?: string;
  image?: string;
  n_categorias?: number | null;
  n_series?: number | null;
  empilhado?: boolean;
  orientacao?: string;
}

// Fluxograma Mermaid (código do diagrama).
export interface MermaidAttachment {
  kind: 'mermaid';
  code?: string;
  titulo?: string;
  linhas?: number | null;
}

// Anexos que o backend devolve. Nesta fase tratamos 'table' e 'document';
// os demais (export/mermaid/chart) vêm depois — o campo kind roteia o render.
export type AttachmentKind = 'table' | 'document' | 'export' | 'mermaid' | 'chart';

export interface TableAttachment {
  kind: 'table';
  filename: string;
  rows: number;
  columns: string[];
  dtypes: Record<string, string>;
  preview: Array<Record<string, unknown>>;
  preview_rows?: number;
  truncated?: boolean;
}

export interface DocumentAttachment {
  kind: 'document';
  filename: string;
  char_count: number;
  page_count?: number | null;
  preview: string;
}

export interface Attachment {
  kind?: AttachmentKind;
  [k: string]: unknown;
}

// Resposta de /api/conversations/<id>/dataset/ (paginação do dataset corrente).
export interface DatasetPage {
  total: number;
  offset: number;
  limit: number;
  columns: string[];
  rows: Array<Record<string, unknown>>;
}

// Envelope das respostas de upload (single/batch).
export interface UploadResult {
  status: 'success' | 'error';
  message?: ChatMessage | string;
  conversation_id?: number;
  conversation_title?: string;
  agent_slug?: string;
}

export interface ChatMessage {
  id?: number;
  role: 'user' | 'assistant';
  content: string;
  attachment?: Attachment | null;
  attachments?: Attachment[];
  tool_calls?: ToolCall[];
}

// Resumo de conversa (lista da sidebar) — _conversation_summary no backend.
export interface ConversationSummary {
  id: number;
  title: string;
  agent_slug: string | null;
  awaiting_human_input: boolean;
  has_session_agent: boolean;
  updated_at: string;
}

// Detalhe completo — conversation_detail no backend.
export interface ConversationDetail {
  id: number;
  title: string;
  agent_slug: string | null;
  awaiting_human_input: boolean;
  has_session_agent: boolean;
  playbook_id: number | null;
  playbook_name: string | null;
  messages: ChatMessage[];
}

// Payload do evento SSE 'done' (chat_stream).
export interface ChatDonePayload {
  status: 'success' | 'error';
  message?: string;
  conversation_id: number;
  conversation_title?: string;
  agent_slug?: string;
  awaiting_human_input?: boolean;
  human_question?: string | null;
  stopped?: boolean;
  reply?: ChatMessage;
}

// Evento de progresso ao vivo (balão "pensando"). Reproduz o que o
// updateTypingProgress do chat.js consome. Os campos extras (tool_call_id,
// parent_id, args…) alimentam o painel de execução ao vivo (árvore) — o
// backend os envia em stage 'tool' (início) e 'tool_result' (término).
export interface ChatProgressEvent {
  type: 'progress';
  stage?: 'thinking' | 'massiva' | 'tool' | 'tool_result' | string;
  icon?: string;
  text?: string;
  current?: number;
  total?: number;
  // Enriquecimento p/ a árvore ao vivo:
  agent?: string;                 // slug do agente que roda a tool
  tool?: string;                  // nome da tool
  tool_call_id?: string;          // id p/ casar início↔término
  parent_id?: string | null;      // id do call_agent que gerou este agente
  depth?: number;                 // 0 = agente de topo
  args?: Record<string, unknown>; // "código"/argumentos sendo processados
  error?: string;
  duration_ms?: number;
  result_preview?: string;
}

export interface CodexPlanItem {
  step: string;
  status: 'pending' | 'inProgress' | 'completed';
}

export interface CodexPlanEvent {
  type: 'plan';
  explanation?: string;
  plan: CodexPlanItem[];
}

export interface CodexInteractionOption {
  label: string;
  description?: string;
}

export interface CodexInteractionQuestion {
  id: string;
  header?: string;
  question: string;
  isOther?: boolean;
  isSecret?: boolean;
  options?: CodexInteractionOption[];
}

export interface CodexInteraction {
  token: string;
  kind: 'question' | 'command_approval' | 'file_approval' | 'permission_approval';
  title: string;
  reason?: string;
  command?: string;
  cwd?: string;
  grantRoot?: string;
  network?: { host?: string; protocol?: string } | null;
  permissions?: Record<string, unknown>;
  availableDecisions?: string[];
  questions?: CodexInteractionQuestion[];
}

// Nó da árvore de execução ao vivo, montada incrementalmente a partir dos
// eventos de progresso. Recursivo: um call_agent tem as tools do sub-agente
// em `children`.
export interface LiveNode {
  id: string;
  parentId: string | null;
  agent: string;
  tool: string;
  icon: string;
  label: string;
  args?: Record<string, unknown>;
  status: 'running' | 'done' | 'error';
  durationMs?: number;
  resultPreview?: string;
  error?: string;
  children: LiveNode[];
}

// Uma linha do log de progresso já pronta para render no template.
export interface ProgressLine {
  icon: string;
  text: string;
  massiva: boolean;
  pct?: number;
}

// ── Modais do chat (KBs / Conhecimentos / Agente da sessão) ────────

// Base de conhecimento vinda da API externa IARA (/api/kbs/). O que fica
// gravado em conversation.state["active_kbs"] é só {id,name,description}.
export interface KnowledgeBase {
  id: string;
  name: string;
  description: string;
}

// Item guardado em conversation.state["active_knowledge"] — só o id.
export interface ActiveKnowledgeRef {
  id: number;
}

// Documento anexado ao agente da sessão. Ao extrair (extract-document) vem
// com `markdown`; o payload de detalhe do agente NÃO traz markdown (só resumo).
export interface SessionAgentDoc {
  filename: string;
  markdown?: string;
  char_count?: number;
  page_count?: number | null;
}

// Shape do agente da sessão (_session_agent_payload no backend).
export interface SessionAgent {
  name: string;
  icon: string;
  system_prompt: string;
  model: string;
  temperature: number;
  tools_enabled: string[];
  guardrails: string;
  documents: SessionAgentDoc[];
}

// Payload de escrita do agente da sessão (save / create-conversation).
export interface SessionAgentSavePayload {
  name: string;
  icon: string;
  system_prompt: string;
  guardrails: string;
  model: string;
  temperature: number;
  tools_enabled: string[];
  documents: SessionAgentDoc[];
}
