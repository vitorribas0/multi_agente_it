import { AfterViewInit, Directive, ElementRef } from '@angular/core';

// Foca (e seleciona, se for input de texto) o elemento assim que ele entra no
// DOM. Usado no input de renomear conversa — espelha o focus()+select() do
// main.js legado.
@Directive({
  selector: '[appAutofocus]',
  standalone: true,
})
export class AutofocusDirective implements AfterViewInit {
  constructor(private el: ElementRef<HTMLElement>) {}

  ngAfterViewInit(): void {
    const node = this.el.nativeElement;
    node.focus();
    if (node instanceof HTMLInputElement) node.select();
  }
}
