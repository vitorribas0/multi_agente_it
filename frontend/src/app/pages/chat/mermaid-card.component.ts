import {
  AfterViewInit,
  Component,
  ElementRef,
  HostListener,
  Input,
  OnInit,
  ViewChild,
} from '@angular/core';
import { CommonModule } from '@angular/common';

import { MermaidAttachment } from '../../api/chat.models';

// Contador global para ids únicos de render (o mermaid.render exige id único).
let mermaidSeq = 0;

// Card de fluxograma Mermaid: render assíncrono do SVG + zoom/pan (scroll,
// arraste, pinch) + downloads (PNG/SVG/.mmd) + expandir tela cheia.
// Espelha o renderMermaidCard do chat.js. O mermaid é importado dinamicamente
// (só quando um card aparece) para não pesar no bundle inicial.
@Component({
  selector: 'app-mermaid-card',
  standalone: true,
  imports: [CommonModule],
  templateUrl: './mermaid-card.component.html',
  styles: [`:host { display: block; min-width: 0; max-width: 100%; }`],
})
export class MermaidCardComponent implements OnInit, AfterViewInit {
  @Input({ required: true }) att!: MermaidAttachment;

  @ViewChild('viewport') viewportRef!: ElementRef<HTMLElement>;
  @ViewChild('stage') stageRef!: ElementRef<HTMLElement>;
  @ViewChild('renderTarget') renderRef!: ElementRef<HTMLElement>;

  titulo = '';
  linhas = 0;
  expanded = false;
  zoomPct = 100;

  private uid = 'mmd-' + ++mermaidSeq;
  private svg = '';

  private readonly MIN_SCALE = 0.2;
  private readonly MAX_SCALE = 5;
  private view = { scale: 1, x: 0, y: 0 };
  private drag = { active: false, sx: 0, sy: 0, ox: 0, oy: 0 };
  private pointers = new Map<number, { x: number; y: number }>();
  private pinchDist = 0;

  ngOnInit(): void {
    this.titulo = this.att.titulo || 'Fluxograma do processo';
    this.linhas = this.att.linhas ?? (this.att.code || '').split('\n').length;
  }

  get code(): string {
    return this.att.code || '';
  }

  async ngAfterViewInit(): Promise<void> {
    const target = this.renderRef.nativeElement;
    try {
      const mermaid = (await import('mermaid')).default;
      mermaid.initialize({ startOnLoad: false, securityLevel: 'loose' });
      const { svg } = await mermaid.render(this.uid + '-svg', this.code);
      this.svg = svg;
      target.innerHTML = svg;
      requestAnimationFrame(() => this.fitToView());
    } catch {
      target.innerHTML = '<div class="mermaid-error">⚠️ Não consegui renderizar o diagrama.</div><pre class="mermaid-fallback"></pre>';
      const pre = target.querySelector('pre');
      if (pre) pre.textContent = this.code;
      this.viewportRef.nativeElement.classList.add('no-pan');
    }
  }

  // ── Zoom & pan ─────────────────────────────────────────────────
  private applyTransform(): void {
    this.stageRef.nativeElement.style.transform =
      `translate(${this.view.x}px, ${this.view.y}px) scale(${this.view.scale})`;
    this.zoomPct = Math.round(this.view.scale * 100);
  }

  private clampScale(s: number): number {
    return Math.min(this.MAX_SCALE, Math.max(this.MIN_SCALE, s));
  }

  private zoomAt(cx: number, cy: number, factor: number): void {
    const next = this.clampScale(this.view.scale * factor);
    const ratio = next / this.view.scale;
    this.view.x = cx - (cx - this.view.x) * ratio;
    this.view.y = cy - (cy - this.view.y) * ratio;
    this.view.scale = next;
    this.applyTransform();
  }

  zoomCentered(factor: number): void {
    const r = this.viewportRef.nativeElement.getBoundingClientRect();
    this.zoomAt(r.width / 2, r.height / 2, factor);
  }

  fitToView(): void {
    const svgEl = this.renderRef.nativeElement.querySelector('svg') as SVGSVGElement | null;
    if (!svgEl) return;
    const vp = this.viewportRef.nativeElement.getBoundingClientRect();
    let natW = svgEl.viewBox?.baseVal?.width || 0;
    let natH = svgEl.viewBox?.baseVal?.height || 0;
    if (!natW || !natH) {
      try {
        const b = svgEl.getBBox();
        natW = b.width;
        natH = b.height;
      } catch {
        /* ignore */
      }
    }
    if (!natW || !natH) return;
    const pad = 32;
    const s = this.clampScale(Math.min((vp.width - pad) / natW, (vp.height - pad) / natH, this.MAX_SCALE));
    this.view.scale = s;
    this.view.x = (vp.width - natW * s) / 2;
    this.view.y = (vp.height - natH * s) / 2;
    this.applyTransform();
  }

  onWheel(e: WheelEvent): void {
    e.preventDefault();
    const r = this.viewportRef.nativeElement.getBoundingClientRect();
    const factor = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    this.zoomAt(e.clientX - r.left, e.clientY - r.top, factor);
  }

  onPointerDown(e: PointerEvent): void {
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if (this.pointers.size === 1) {
      this.drag.active = true;
      this.drag.sx = e.clientX;
      this.drag.sy = e.clientY;
      this.drag.ox = this.view.x;
      this.drag.oy = this.view.y;
      this.viewportRef.nativeElement.classList.add('grabbing');
    } else if (this.pointers.size === 2) {
      this.drag.active = false;
      const pts = [...this.pointers.values()];
      this.pinchDist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
    }
    this.viewportRef.nativeElement.setPointerCapture?.(e.pointerId);
  }

  onPointerMove(e: PointerEvent): void {
    if (!this.pointers.has(e.pointerId)) return;
    this.pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if (this.pointers.size === 2) {
      const pts = [...this.pointers.values()];
      const dist = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
      if (this.pinchDist > 0) {
        const r = this.viewportRef.nativeElement.getBoundingClientRect();
        const midX = (pts[0].x + pts[1].x) / 2 - r.left;
        const midY = (pts[0].y + pts[1].y) / 2 - r.top;
        this.zoomAt(midX, midY, dist / this.pinchDist);
      }
      this.pinchDist = dist;
      return;
    }

    if (this.drag.active) {
      this.view.x = this.drag.ox + (e.clientX - this.drag.sx);
      this.view.y = this.drag.oy + (e.clientY - this.drag.sy);
      this.applyTransform();
    }
  }

  onPointerUp(e: PointerEvent): void {
    this.pointers.delete(e.pointerId);
    if (this.pointers.size < 2) this.pinchDist = 0;
    if (this.pointers.size === 0) {
      this.drag.active = false;
      this.viewportRef.nativeElement.classList.remove('grabbing');
    }
  }

  // ── Expandir ───────────────────────────────────────────────────
  toggle(): void {
    this.expanded = !this.expanded;
    document.body.classList.toggle('modal-open', this.expanded);
    requestAnimationFrame(() => this.fitToView());
  }

  @HostListener('document:keydown.escape')
  onEsc(): void {
    if (this.expanded) this.toggle();
  }

  // ── Downloads ──────────────────────────────────────────────────
  private safeStem(): string {
    return (
      (this.titulo || 'fluxograma')
        .toLowerCase()
        .normalize('NFD')
        .replace(/[\u0300-\u036f]/g, '')
        .replace(/[^a-z0-9]+/g, '_')
        .replace(/^_+|_+$/g, '')
        .slice(0, 50) || 'fluxograma'
    );
  }

  private triggerDownload(href: string, filename: string, revoke: boolean): void {
    const a = document.createElement('a');
    a.href = href;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    if (revoke) setTimeout(() => URL.revokeObjectURL(href), 1000);
  }

  private downloadBlob(content: string, mime: string, ext: string): void {
    const url = URL.createObjectURL(new Blob([content], { type: mime }));
    this.triggerDownload(url, `${this.safeStem()}.${ext}`, true);
  }

  downloadSvg(): void {
    if (this.svg) this.downloadBlob(this.svg, 'image/svg+xml;charset=utf-8', 'svg');
  }

  downloadMmd(): void {
    this.downloadBlob(this.code, 'text/plain;charset=utf-8', 'mmd');
  }

  downloadPng(): void {
    if (!this.svg) return;
    const svgEl = this.renderRef.nativeElement.querySelector('svg') as SVGSVGElement | null;
    let width = 1200;
    let height = 800;
    if (svgEl?.viewBox?.baseVal?.width) {
      width = svgEl.viewBox.baseVal.width;
      height = svgEl.viewBox.baseVal.height;
    } else if (svgEl) {
      try {
        const b = svgEl.getBBox();
        if (b.width) {
          width = b.width;
          height = b.height;
        }
      } catch {
        /* ignore */
      }
    }
    const scale = 2;
    const url = URL.createObjectURL(new Blob([this.svg], { type: 'image/svg+xml;charset=utf-8' }));
    const img = new Image();
    img.onload = () => {
      const canvas = document.createElement('canvas');
      canvas.width = Math.round(width * scale);
      canvas.height = Math.round(height * scale);
      const ctx = canvas.getContext('2d');
      if (ctx) {
        ctx.fillStyle = '#ffffff';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      }
      URL.revokeObjectURL(url);
      canvas.toBlob((blob) => {
        if (!blob) return;
        this.triggerDownload(URL.createObjectURL(blob), `${this.safeStem()}.png`, true);
      }, 'image/png');
    };
    img.onerror = () => URL.revokeObjectURL(url);
    img.src = url;
  }
}
