import { Component } from '@angular/core';

/**
 * Public Refund & Cancellation Policy — required for Razorpay / Cashfree
 * merchant approval (gateways verify a clearly published refund policy).
 */
@Component({
  selector: 'app-refund',
  standalone: true,
  templateUrl: './refund.component.html',
  styleUrl: './refund.component.css',
})
export class RefundComponent {}
