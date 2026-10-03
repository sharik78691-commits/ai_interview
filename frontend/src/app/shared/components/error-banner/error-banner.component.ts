import { Component, EventEmitter, Input, Output } from '@angular/core';

@Component({
  selector: 'app-error-banner',
  standalone: true,
  template: `
    @if (message) {
      <div class="banner error">
        <span>{{ message }}</span>
        <button class="x" (click)="dismiss.emit()" aria-label="Dismiss">✕</button>
      </div>
    }
  `,
  styles: [
    `.banner { display: flex; justify-content: space-between; align-items: center; gap: 12px; margin-bottom: 14px; }
     .x { background: none; border: none; color: inherit; cursor: pointer; font-size: 1rem; }`,
  ],
})
export class ErrorBannerComponent {
  @Input() message = '';
  @Output() dismiss = new EventEmitter<void>();
}
