// Tipos que espelham o contrato JSON dos endpoints /api/config/ e /api/knowledge/.
// Mantidos alinhados com auditor/views.py (config_overview, knowledge_*).

export interface ToolParam {
  type?: string;
  [k: string]: unknown;
}

export interface ToolInfo {
  slug: string;
  name: string;
  description: string;
  icon: string;
  is_human_in_loop: boolean;
  uses_session: boolean;
  parameters: Record<string, ToolParam>;
  required: string[];
}

export interface AgentInfo {
  id: number;
  slug: string;
  name: string;
  description: string;
  icon: string;
  system_prompt: string;
  model: string;
  temperature: number;
  tools_enabled: string[];
  is_default: boolean;
}

export interface AppSettings {
  max_iterations: number;
  massiva_workers: number;
}

export interface ConfigOverview {
  agents: AgentInfo[];
  models: string[];
  tools: ToolInfo[];
  settings: AppSettings;
}

export interface Knowledge {
  id: number;
  icon: string;
  name: string;
  description: string;
  prompt: string;
}

// Envelope padrão das respostas de escrita (save/delete) do backend.
export interface WriteResult<T = unknown> {
  status: 'success' | 'error';
  message?: string;
  settings?: AppSettings;
  knowledge?: T;
}

// Payload de atualização de um agente (subset editável na tela).
export interface AgentSavePayload {
  model: string;
  temperature: number;
  system_prompt: string;
  tools_enabled: string[];
}

export interface KnowledgePayload {
  icon: string;
  name: string;
  description: string;
  prompt: string;
}

export interface Skill {
  slug: string;
  name: string;
  description: string;
  prompt: string;
}

export interface SkillPayload {
  slug?: string;
  name: string;
  description: string;
  prompt: string;
}
