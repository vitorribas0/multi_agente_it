// Tipos que espelham o contrato JSON dos endpoints /api/playbooks/.
// Mantidos alinhados com auditor/views.py (_playbook_summary/_playbook_detail)
// e o modelo Playbook em auditor/models.py.

export interface CanvasPos {
  x: number;
  y: number;
}

// Um nó-agente do grafo. Duck-types a superfície RuntimeAgent do backend.
export interface PlaybookNode {
  slug: string;
  name: string;
  description: string;
  icon: string;
  system_prompt: string;
  model: string;
  temperature: number;
  tools_enabled: string[];
  skills_enabled: string[];
  expected_output: string;
  requires_approval: boolean;
  allow_user_questions: boolean;
  max_retries: number;
  on_error: 'stop' | 'continue';
  is_root: boolean;
  canvas: CanvasPos;
  // id de cliente usado só no editor para casar arestas antes de o backend
  // canonizar os slugs. Não persiste.
  id?: string;
}

export interface PlaybookEdge {
  source: string;
  target: string;
}

export interface PlaybookSuggestion {
  title: string;
  text: string;
}

// Resumo (listagem / pickers).
export interface PlaybookSummary {
  id: number;
  name: string;
  description: string;
  icon: string;
  node_count: number;
  stage_count: number;
  status: 'draft' | 'published';
  version: number;
  revision_count: number;
}

export interface PlaybookExecutionPolicy {
  final_synthesis: boolean;
  require_stage_confirmation: boolean;
  stop_on_error: boolean;
}

// Grafo completo (editor de canvas).
export interface PlaybookDetail {
  id: number;
  name: string;
  description: string;
  icon: string;
  status: 'draft' | 'published';
  version: number;
  revision_count: number;
  execution_policy: PlaybookExecutionPolicy;
  nodes: PlaybookNode[];
  edges: PlaybookEdge[];
  suggestions: PlaybookSuggestion[];
}

// Payload de create/update (mesmo shape do detail, sem id).
export interface PlaybookSavePayload {
  name: string;
  description: string;
  icon: string;
  status: 'draft' | 'published';
  execution_policy: PlaybookExecutionPolicy;
  nodes: PlaybookNode[];
  edges: PlaybookEdge[];
  suggestions: PlaybookSuggestion[];
}

export interface PlaybookValidationStage {
  slug: string;
  name: string;
  requires_approval: boolean;
  skills: string[];
}

export interface PlaybookValidationResult {
  status: 'success' | 'error';
  valid: boolean;
  message?: string;
  warnings?: string[];
  stages?: PlaybookValidationStage[];
}

export interface PlaybookRevision {
  version: number;
  status: 'draft' | 'published';
  created_at: string;
}

export interface PlaybookWriteResult {
  status: 'success' | 'error';
  message?: string;
  playbook?: PlaybookDetail;
  warnings?: string[];
}
