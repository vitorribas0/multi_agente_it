import { Directive, ElementRef, Input, OnChanges } from '@angular/core';
import { marked } from 'marked';
// Só as linguagens comuns (subset) — o import default de 'highlight.js' traz
// TODAS as linguagens e infla o bundle em ~1 MB.
import hljs from 'highlight.js/lib/common';

// Renderiza markdown dentro do elemento-host, replicando o renderMarkdownInto
// do chat.js: marked (gfm + breaks), tabelas embrulhadas em .table-scroll e
// realce de código com highlight.js. Uso: <div [appMarkdown]="content"></div>
@Directive({
  selector: '[appMarkdown]',
  standalone: true,
})
export class MarkdownDirective implements OnChanges {
  @Input('appMarkdown') content = '';

  constructor(private host: ElementRef<HTMLElement>) {
    marked.setOptions({ breaks: true, gfm: true });
  }

  ngOnChanges(): void {
    const el = this.host.nativeElement;
    el.innerHTML = marked.parse(this.content || '') as string;

    el.querySelectorAll('table').forEach((table) => {
      if (table.parentElement && table.parentElement.classList.contains('table-scroll')) return;
      const scroller = document.createElement('div');
      scroller.className = 'table-scroll';
      table.parentNode?.insertBefore(scroller, table);
      scroller.appendChild(table);
    });

    el.querySelectorAll('pre code').forEach((code) => hljs.highlightElement(code as HTMLElement));
  }
}
