import { Component, Input, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import hljs from 'highlight.js/lib/common';

import { ToolCall } from '../../api/chat.models';
import { iconForTool, truncate } from '../../shared/tool-icons';

interface ArgEntry {
  key: string;
  val: string;
}

// Bloco de código de um argumento (html/código/sql/…): já com highlight,
// linguagem inferida e controles de copiar/expandir — estilo IDE.
interface CodeBlock {
  key: string;
  lang: string;
  code: string;
  html: string;
  expanded: boolean;
  copied: boolean;
}

// Chaves de argumento cujo VALOR é código/documento e merece bloco de editor
// (em vez de texto inline truncado). Espelha o codeArg do live-node, mais os
// campos de conteúdo da caixa/geradores.
const CODE_KEYS = new Set([
  'codigo', 'code', 'sql', 'query',
  'html', 'conteudo', 'markdown', 'trecho_antigo', 'trecho_novo', 'command',
]);

// Card de uma tool-call, colapsável, recursivo (nested_tool_calls = sub-agente).
@Component({
  selector: 'app-tool-call',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './tool-call.component.html',
  styles: [`:host { display: block; min-width: 0; max-width: 100%; }`],
})
export class ToolCallComponent implements OnInit {
  @Input({ required: true }) tc!: ToolCall;

  open = false;

  icon = '⚡';
  hasError = false;
  codeBlocks: CodeBlock[] = [];
  otherArgs: ArgEntry[] = [];
  prettyResult = '';

  ngOnInit(): void {
    this.icon = iconForTool(this.tc.tool);
    this.hasError = !!(this.tc.error && this.tc.error.length > 0);

    const args = this.tc.args || {};
    const fileHint = typeof args['nome_arquivo'] === 'string' ? args['nome_arquivo'] : '';

    for (const [k, v] of Object.entries(args)) {
      const isCode = CODE_KEYS.has(k) && typeof v === 'string' && v.length > 0;
      if (isCode) {
        const code = String(v);
        const lang = this.inferLang(k, fileHint);
        this.codeBlocks.push({
          key: k,
          lang,
          code,
          html: this.highlight(code, lang),
          expanded: false,
          copied: false,
        });
      } else {
        const val = typeof v === 'string' ? v : JSON.stringify(v);
        this.otherArgs.push({ key: k, val: truncate(String(val), 80) });
      }
    }

    const resultStr = this.hasError ? this.tc.error || '' : this.tc.result || '';
    let pretty = resultStr;
    try {
      pretty = JSON.stringify(JSON.parse(resultStr), null, 2);
    } catch {
      /* mantém raw */
    }
    this.prettyResult = truncate(pretty, 2000);
  }

  // Linguagem do bloco, inferida pela chave do arg e pela extensão do arquivo.
  private inferLang(key: string, fileHint: string): string {
    const ext = (fileHint.split('.').pop() || '').toLowerCase();
    const byExt: Record<string, string> = {
      py: 'python', js: 'javascript', ts: 'typescript', json: 'json',
      html: 'xml', htm: 'xml', css: 'css', scss: 'scss', sql: 'sql',
      md: 'markdown', yml: 'yaml', yaml: 'yaml', sh: 'bash',
    };
    if (byExt[ext]) return byExt[ext];
    if (key === 'codigo' || key === 'code') return 'python';
    if (key === 'sql' || key === 'query') return 'sql';
    if (key === 'html') return 'xml';
    if (key === 'markdown') return 'markdown';
    if (key === 'command') return 'bash';
    return 'plaintext';
  }

  private highlight(code: string, lang: string): string {
    try {
      if (lang !== 'plaintext' && hljs.getLanguage(lang)) {
        return hljs.highlight(code, { language: lang }).value;
      }
      return hljs.highlightAuto(code).value;
    } catch {
      return code.replace(/[&<>]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] || c));
    }
  }

  get hasArgs(): boolean {
    return this.codeBlocks.length > 0 || this.otherArgs.length > 0;
  }

  get durationStr(): string {
    return this.tc.duration_ms ? ` · ${this.tc.duration_ms}ms` : '';
  }

  get nested(): ToolCall[] {
    return this.tc.nested_tool_calls || [];
  }

  toggle(): void {
    this.open = !this.open;
  }

  toggleExpand(block: CodeBlock, ev: Event): void {
    ev.stopPropagation();
    block.expanded = !block.expanded;
  }

  async copyCode(block: CodeBlock, ev: Event): Promise<void> {
    ev.stopPropagation();
    try {
      await navigator.clipboard.writeText(block.code);
      block.copied = true;
      setTimeout(() => (block.copied = false), 1500);
    } catch {
      /* clipboard bloqueado — silencioso */
    }
  }
}
