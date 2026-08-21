import { Component, Input } from '@angular/core';
import { CommonModule } from '@angular/common';
import hljs from 'highlight.js/lib/common';

import { LiveNode } from '../../api/chat.models';

interface ArgEntry {
  key: string;
  val: string;
}

// Args "de prosa" — a tarefa/contexto/instrução que o orquestrador passa ao
// sub-agente (call_agent) ou o critério da análise. São textos longos que
// ficam ilegíveis espremidos numa linha só; renderizamos num bloco à parte.
const PROSE_KEYS = new Set([
  'tarefa', 'task', 'contexto', 'instrucao', 'instrucoes', 'prompt',
  'pergunta', 'mensagem', 'descricao', 'texto', 'objetivo', 'criterio',
]);
// Rótulo amigável (capitalizado, com acento) para o cabeçalho do bloco.
const PROSE_LABELS: Record<string, string> = {
  tarefa: 'Tarefa', task: 'Tarefa', contexto: 'Contexto',
  instrucao: 'Instrução', instrucoes: 'Instruções', prompt: 'Prompt',
  pergunta: 'Pergunta', mensagem: 'Mensagem', descricao: 'Descrição',
  texto: 'Texto', objetivo: 'Objetivo', criterio: 'Critério',
};

// Nó da árvore de execução AO VIVO — recursivo (children = tools do sub-agente
// chamado via call_agent). Diferente do app-tool-call (que renderiza o
// histórico já concluído), este mostra o estado em tempo real: rodando /
// concluído / erro, com o "código" (args) sendo processado — num bloco com
// syntax highlighting, cabeçalho de linguagem e botão de copiar (estilo
// Claude Desktop).
@Component({
  selector: 'app-live-node',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './live-node.component.html',
  styles: [`:host { display: block; min-width: 0; }`],
})
export class LiveNodeComponent {
  @Input({ required: true }) node!: LiveNode;
  // Nós de topo já vêm abertos; o usuário pode recolher.
  @Input() depth = 0;

  open = true;
  copied = false;

  toggle(): void {
    this.open = !this.open;
  }

  // Chaves candidatas a "código/documento", em ordem de prioridade.
  private static readonly CODE_KEYS = [
    'codigo', 'sql', 'query_sql', 'query', 'code', 'command', 'html', 'markdown', 'conteudo',
  ];

  // Qual chave dos args foi usada como código (ou '' se nenhuma).
  get codeKey(): string {
    const args = this.node.args || {};
    for (const k of LiveNodeComponent.CODE_KEYS) {
      if (typeof args[k] === 'string') return k;
    }
    return '';
  }

  // Argumento "principal" quando é código/documento (pandas, SQL, HTML,
  // markdown, conteúdo) — mostrado em bloco estilo editor.
  get codeArg(): string {
    const k = this.codeKey;
    const v = k ? (this.node.args || {})[k] : undefined;
    return typeof v === 'string' ? v : '';
  }

  // Linguagem do bloco de código, inferida pela tool / chave do arg.
  get codeLang(): string {
    const t = this.node.tool || '';
    const args = this.node.args || {};
    if (t === 'codex_command' || 'command' in args) return 'bash';
    if (t === 'executar_pandas' || 'codigo' in args || 'code' in args) return 'python';
    if (t === 'consulta_aws' || 'sql' in args || 'query_sql' in args || 'query' in args) return 'sql';
    if ('html' in args) return 'xml';
    if ('markdown' in args) return 'markdown';
    return 'plaintext';
  }

  // Código pronto pra exibir/copiar — SQL ganha formatação multi-linha
  // (uma query numa linha só fica feia); demais linguagens vão como estão.
  get prettyCode(): string {
    const code = this.codeArg;
    if (this.codeLang === 'sql') return LiveNodeComponent.formatSql(code);
    return code;
  }

  // HTML do código já com highlight aplicado (highlight.js).
  get codeHtml(): string {
    const code = this.prettyCode;
    if (!code) return '';
    const lang = this.codeLang;
    try {
      if (lang !== 'plaintext' && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    } catch {
      // Fallback: escapa manualmente pra nunca injetar HTML cru.
      return code.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] || c));
    }
  }

  // A "tarefa"/contexto que o orquestrador dá ao sub-agente — texto de prosa
  // longo. Renderizado num bloco legível (multi-linha), não numa linha só.
  // Só entra aqui o que NÃO foi tratado como código (codeArg tem prioridade).
  get proseArgs(): ArgEntry[] {
    const args = this.node.args || {};
    const codeKey = this.codeKey;
    return Object.entries(args)
      .filter(([k, v]) => PROSE_KEYS.has(k) && k !== codeKey && typeof v === 'string' && v.trim().length > 0)
      .map(([k, v]) => ({ key: PROSE_LABELS[k] || k, val: String(v).trim() }));
  }

  // Demais args escalares, em linha (chave: valor curto). Fora daqui: código
  // (codeArg) e prosa (proseArgs), que têm blocos próprios.
  get otherArgs(): ArgEntry[] {
    const args = this.node.args || {};
    const skip = new Set(['codigo', 'sql', 'query_sql', 'query', 'code', 'command', 'html', 'markdown', 'conteudo']);
    return Object.entries(args)
      .filter(([k]) => !skip.has(k) && !PROSE_KEYS.has(k))
      .map(([k, v]) => {
        const val = typeof v === 'string' ? v : JSON.stringify(v);
        const s = String(val);
        return { key: k, val: s.length > 140 ? s.slice(0, 140) + '…' : s };
      });
  }

  get hasArgs(): boolean {
    return !!this.codeArg || this.proseArgs.length > 0 || this.otherArgs.length > 0;
  }

  get durationStr(): string {
    return this.node.durationMs ? `${this.node.durationMs} ms` : '';
  }

  // Formata SQL de uma linha em bloco legível: cada cláusula principal na sua
  // linha, condições AND/OR indentadas, palavras-chave em maiúsculas. Simples
  // de propósito — só o suficiente pra ler bem no painel, sem parser completo.
  static formatSql(sql: string): string {
    if (!sql) return sql;
    // Já vem multi-linha e indentado? respeita o que o autor escreveu.
    if (/\n\s+\S/.test(sql)) return sql.trim();

    let s = sql.replace(/\s+/g, ' ').trim();

    // Cláusulas que começam nova linha (sem indentar).
    const breaks = [
      'SELECT', 'FROM', 'WHERE', 'GROUP BY', 'ORDER BY', 'HAVING',
      'LIMIT', 'UNION ALL', 'UNION', 'INNER JOIN', 'LEFT JOIN',
      'RIGHT JOIN', 'FULL JOIN', 'JOIN',
    ];
    for (const kw of breaks) {
      s = s.replace(new RegExp(`\\s*\\b${kw.replace(/ /g, '\\s+')}\\b`, 'gi'), `\n${kw}`);
    }
    // AND / OR entram indentados sob o WHERE.
    s = s.replace(/\s+\b(AND|OR)\b/gi, '\n  $1');
    // Vírgulas da lista de colunas: quebra + indenta (só quando a linha é longa).
    s = s.replace(/,\s*/g, ',\n  ');

    return s
      .split('\n')
      .map((l) => l.replace(/\r/g, '').replace(/\s+$/g, ''))
      .filter((l, i) => !(i === 0 && l === ''))
      .join('\n')
      .trim();
  }

  async copyCode(): Promise<void> {
    try {
      await navigator.clipboard.writeText(this.prettyCode);
      this.copied = true;
      setTimeout(() => (this.copied = false), 1500);
    } catch {
      /* clipboard bloqueado — silencioso */
    }
  }
}
