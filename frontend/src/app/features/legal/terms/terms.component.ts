import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * Public Terms of Service (/terms; /terms-and-conditions redirects here).
 * Draft — see docs/LEGAL_REVIEW.md.
 */
@Component({
  selector: 'app-terms',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './terms.component.html',
})
export class TermsComponent {}
