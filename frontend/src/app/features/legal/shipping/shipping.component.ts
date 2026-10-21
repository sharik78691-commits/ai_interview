import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BUSINESS } from '../../../core/business-info';

/**
 * /shipping-policy — Shipping & Delivery Policy. OyeInterview is a digital
 * service, but Razorpay lists this page as required for activation.
 */
@Component({
  selector: 'app-shipping',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="container legal">
      <header class="legal-head">
        <h1>Shipping &amp; Delivery Policy</h1>
        <p class="muted">Last updated: October 22, 2026</p>
        <p class="lead">How purchases of {{ biz.brand }} plans are delivered.</p>
      </header>

      <section class="legal-section">
        <h2>1. Digital service — no physical shipping</h2>
        <p>
          {{ biz.brand }} is an online software service. We do not sell or ship any physical goods,
          so there are no shipping charges and no delivery address is needed.
        </p>
      </section>

      <section class="legal-section">
        <h2>2. Delivery of your plan</h2>
        <ul>
          <li>Your plan is delivered electronically to your {{ biz.brand }} account.</li>
          <li>It is <strong>activated automatically and instantly</strong> once the payment gateway confirms your payment — usually within a few seconds.</li>
          <li>You can see the active plan, its validity and remaining screenshot analyses on the Billing page after signing in.</li>
          <li>The payment confirmation is shown on screen and recorded on your Billing page.</li>
        </ul>
      </section>

      <section class="legal-section">
        <h2>3. Delayed or failed activation</h2>
        <p>
          If money was deducted but your plan is not active within 30 minutes, email
          <a [href]="'mailto:' + biz.email">{{ biz.email }}</a> with your registered email and the
          payment reference. We will activate the plan or refund the payment as described in our
          <a routerLink="/refund-policy">Refund &amp; Cancellation Policy</a>.
        </p>
      </section>

      <section class="legal-section">
        <h2>4. Service area</h2>
        <p>The service is delivered online and is available wherever {{ biz.website }} can be accessed. Prices are charged in Indian Rupees (INR).</p>
      </section>

      <section class="legal-section">
        <h2>5. Contact</h2>
        <p>
          {{ biz.brand }} is operated by {{ biz.operatorName }}, {{ biz.location }}.<br />
          Email: <a [href]="'mailto:' + biz.email">{{ biz.email }}</a>
        </p>
      </section>
    </div>
  `,
})
export class ShippingComponent {
  readonly biz = BUSINESS;
}
