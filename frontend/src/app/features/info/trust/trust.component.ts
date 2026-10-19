import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';

/**
 * /trust — factual description of data handling and security controls that
 * exist in the codebase. No certifications, audits or guarantees are claimed
 * because none exist; keep it that way unless they are obtained.
 */
@Component({
  selector: 'app-trust',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="container info">
      <header class="info-head">
        <h1>Trust &amp; data practices</h1>
        <p class="lead">
          OyeInterview is an independently developed product. Here is plainly what we store,
          who processes it, how it is protected — and what we don’t claim.
        </p>
      </header>

      <section>
        <h2>What we store — and what we don’t</h2>
        <div class="info-grid">
          <div class="card">
            <h3>✔ Stored in your account</h3>
            <p class="muted">Name and email, a hashed password (never the password), resumes and target roles you save, your plans and payment records, and basic activity events (for example “asked a question” with a time — not the question itself).</p>
          </div>
          <div class="card">
            <h3>✖ Not stored</h3>
            <p class="muted">Interview transcripts, your questions and the AI answers, interviewer audio clips, your microphone audio, and card, UPI or bank details.</p>
          </div>
          <div class="card">
            <h3>↗ Shared to provide the service</h3>
            <p class="muted">Questions with your resume and job description go to the AI provider of the model you use; interviewer audio clips go to a speech-to-text provider; payments are handled by Razorpay or Cashfree.</p>
          </div>
        </div>
        <p class="muted small">Full details, including every third party, are in the <a routerLink="/privacy-policy">Privacy Policy</a>.</p>
      </section>

      <section>
        <h2>Security controls in place</h2>
        <div class="info-grid">
          <div class="card"><h3>Accounts</h3><p class="muted">Argon2id password hashing, sign-in rate limits per network and per account, sessions in HttpOnly cookies, and sign-out of all sessions after a password reset.</p></div>
          <div class="card"><h3>Requests</h3><p class="muted">HTTPS, cross-site request forgery tokens on every change, a strict list of allowed website origins, and security headers on API responses.</p></div>
          <div class="card"><h3>Your records</h3><p class="muted">Every resume, role, subscription and payment lookup is restricted to the signed-in owner on the server. Admin actions on subscriptions are logged with who did what and why.</p></div>
          <div class="card"><h3>Payments</h3><p class="muted">Prices are set by the server, plans activate only after the gateway confirms payment, and payment notifications are accepted only with a valid gateway signature.</p></div>
        </div>
      </section>

      <section>
        <h2>What we don’t claim</h2>
        <ul class="muted">
          <li>We do not hold security certifications (such as ISO 27001 or SOC 2) and have not had a third-party audit.</li>
          <li>No online service is “unhackable”; we reduce risk but cannot guarantee absolute security.</li>
          <li>AI answers can be inaccurate. They are suggestions, not facts or professional advice.</li>
        </ul>
      </section>

      <section>
        <h2>Your control</h2>
        <ul class="muted">
          <li>Edit or delete your resumes and target roles yourself at any time.</li>
          <li>See your plan, expiry and payments on the Billing page.</li>
          <li>Ask us for a copy of your data or to delete your account by emailing <a href="mailto:support@oyeinterview.com">support&#64;oyeinterview.com</a>.</li>
        </ul>
      </section>

      <section class="callout">
        <p>
          <strong>Found a security issue?</strong> Please email
          <a href="mailto:support@oyeinterview.com">support&#64;oyeinterview.com</a> with details and
          give us a reasonable chance to fix it before sharing it publicly.
        </p>
      </section>
    </div>
  `,
})
export class TrustComponent {}
