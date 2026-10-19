import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/** Global footer: product, trust and legal links on every page. */
@Component({
  selector: 'app-site-footer',
  standalone: true,
  imports: [RouterLink],
  template: `
    <footer class="site-foot">
      <div class="container foot-grid">
        <div class="brand">
          <strong>OyeInterview</strong>
          <p class="muted small">
            Real-time, resume-aware AI interview guidance. An independently developed
            software product.
          </p>
          <p class="muted small">Use responsibly and follow the rules of every interview process.</p>
        </div>
        <nav aria-label="Product">
          <h2>Product</h2>
          <a routerLink="/features">Features</a>
          <a routerLink="/how-it-works">How it works</a>
          <a routerLink="/pricing">Pricing</a>
          <a routerLink="/contact">Contact</a>
        </nav>
        <nav aria-label="Trust">
          <h2>Trust</h2>
          <a routerLink="/trust">Trust &amp; data practices</a>
          <a routerLink="/acceptable-use">Acceptable use</a>
        </nav>
        <nav aria-label="Legal">
          <h2>Legal</h2>
          <a routerLink="/terms">Terms</a>
          <a routerLink="/privacy-policy">Privacy Policy</a>
          <a routerLink="/refund-policy">Refund &amp; Cancellation</a>
        </nav>
      </div>
      <div class="container foot-base muted small">
        <span>© {{ year }} OyeInterview</span>
        <span>AI suggestions can be wrong — always verify before relying on them.</span>
      </div>
    </footer>
  `,
  styles: [
    `
      .site-foot { border-top: 1px solid var(--border); margin-top: 48px; padding: 36px 0 24px; }
      .foot-grid { display: grid; grid-template-columns: 2fr 1fr 1fr 1fr; gap: 28px; }
      .brand strong { font-size: 1.05rem; }
      .brand p { margin-top: 8px; max-width: 340px; line-height: 1.6; }
      nav { display: grid; gap: 8px; align-content: start; }
      nav h2 { font-size: .72rem; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); font-weight: 700; margin-bottom: 4px; }
      nav a { color: var(--text); font-size: .88rem; opacity: .85; }
      nav a:hover { opacity: 1; color: var(--accent); }
      .foot-base { display: flex; flex-wrap: wrap; justify-content: space-between; gap: 8px; margin-top: 28px; padding-top: 16px; border-top: 1px solid var(--border); }
      @media (max-width: 760px) { .foot-grid { grid-template-columns: 1fr 1fr; } .brand { grid-column: 1 / -1; } }
    `,
  ],
})
export class SiteFooterComponent {
  readonly year = new Date().getFullYear();
}
