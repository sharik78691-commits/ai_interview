import { Component } from '@angular/core';

/**
 * Public Terms & Conditions page.
 *
 * Reached via /terms-and-conditions. Public (no auth guard) and indexed for
 * SEO, matching the other legal/marketing pages.
 */
@Component({
  selector: 'app-terms',
  standalone: true,
  templateUrl: './terms.component.html',
  styleUrl: './terms.component.css',
})
export class TermsComponent {}
