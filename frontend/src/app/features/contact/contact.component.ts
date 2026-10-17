import { Component } from '@angular/core';

/**
 * Public Contact page — required for Razorpay / Cashfree merchant approval
 * (gateways verify a visible support/contact channel).
 */
@Component({
  selector: 'app-contact',
  standalone: true,
  templateUrl: './contact.component.html',
  styleUrl: './contact.component.css',
})
export class ContactComponent {
  supportEmail = 'support@oyeinterview.com';
}
