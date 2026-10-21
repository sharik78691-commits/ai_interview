import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BUSINESS } from '../../../core/business-info';

/** Public Privacy Policy (/privacy-policy). Draft — see docs/LEGAL_REVIEW.md. */
@Component({
  selector: 'app-privacy',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './privacy.component.html',
})
export class PrivacyComponent {
  readonly biz = BUSINESS;
}
