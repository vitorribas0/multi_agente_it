import { Component, HostListener, Input, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';

import { ChartAttachment } from '../../api/chat.models';

interface ChartMeta {
  icon: string;
  label: string;
}

const CHART_META: Record<string, ChartMeta> = {
  barras: { icon: '📊', label: 'Gráfico de barras' },
  linha: { icon: '📈', label: 'Gráfico de linhas' },
  area: { icon: '📈', label: 'Gráfico de área' },
  pizza: { icon: '🥧', label: 'Gráfico de pizza' },
  dispersao: { icon: '✳️', label: 'Gráfico de dispersão' },
  histograma: { icon: '📊', label: 'Histograma' },
  boxplot: { icon: '📦', label: 'Boxplot' },
  heatmap: { icon: '🌡️', label: 'Mapa de calor' },
};

// Card de gráfico (imagem matplotlib). Expandir = overlay tela cheia; PNG baixa
// a própria data-url. Espelha o renderChartCard do chat.js.
@Component({
  selector: 'app-chart-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './chart-card.component.html',
  styles: [`:host { display: block; min-width: 0; max-width: 100%; }`],
})
export class ChartCardComponent implements OnInit {
  @Input({ required: true }) att!: ChartAttachment;

  meta: ChartMeta = CHART_META['barras'];
  titulo = '';
  sub = '';
  expanded = false;

  ngOnInit(): void {
    const type = this.att.chart_type || this.att.tipo || 'barras';
    this.meta = CHART_META[type] || CHART_META['barras'];
    this.titulo = this.att.titulo || this.meta.label;

    const bits: string[] = [];
    if (this.att.n_categorias != null) {
      bits.push(`${this.att.n_categorias} categoria${this.att.n_categorias === 1 ? '' : 's'}`);
    }
    if (this.att.n_series && this.att.n_series > 1) {
      bits.push(`${this.att.n_series} séries`);
      if (this.att.empilhado) bits.push('empilhado');
    }
    if (this.att.orientacao === 'horizontal') bits.push('horizontal');
    this.sub = bits.length ? bits.join(' · ') : this.meta.label;
  }

  get img(): string {
    return this.att.image || '';
  }

  toggle(): void {
    this.expanded = !this.expanded;
    document.body.classList.toggle('modal-open', this.expanded);
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.expanded) this.toggle();
  }

  private safeStem(): string {
    return (
      (this.titulo || 'grafico')
        .toLowerCase()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 60) || 'grafico'
    );
  }

  downloadPng(): void {
    if (!this.img) return;
    const a = document.createElement('a');
    a.href = this.img;
    a.download = `${this.safeStem()}.png`;
    document.body.appendChild(a);
    a.click();
    a.remove();
  }
}
