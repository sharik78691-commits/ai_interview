import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { BUSINESS } from '../../../core/business-info';

/**
 * /about — who operates OyeInterview and what the service is. Required by
 * payment gateways (Razorpay / Cashfree) for merchant approval. Operator
 * details come from core/business-info.ts.
 */
@Component({
  selector: 'app-about',
  standalone: true,
  imports: [RouterLink],
  template: `
    <div class="container legal">
      <header class="legal-head">
        <h1>About us</h1>
        <p class="lead">
          {{ biz.brand }} is an AI-powered interview practice and preparation assistant that helps
          job seekers get ready for technical, behavioral and coding interviews.
        </p>
      </header>

      <section class="legal-section">
        <h2>What we do</h2>
        <p>
          You add your resume and the job you are preparing for. During practice sessions and mock
          interviews, {{ biz.brand }} turns each question into structured answer guidance — key
          points, STAR outlines for behavioral questions, and approach, code hints and complexity
          notes for coding questions — grounded in your own experience. The goal is to help you
          practise, find your best examples and answer confidently in your own words.
        </p>
      </section>

      <section class="legal-section">
        <h2>What you get</h2>
        <ul>
          <li>Resume- and job-aware answer guidance for practice questions.</li>
          <li>Behavioral (STAR), technical and coding question support.</li>
          <li>Screenshot analysis of practice questions in the desktop app.</li>
          <li>Prepaid plans with a fixed validity — no auto-renewal and no stored cards. See <a routerLink="/pricing">Pricing</a>.</li>
        </ul>
      </section>

      <section class="legal-section">
        <h2>Who we are</h2>
        <p>
          {{ biz.brand }} is an independently developed software product operated by
          <strong>{{ biz.operatorName }}</strong>, {{ biz.location }}.
        </p>
        <ul>
          <li><strong>Operator:</strong> {{ biz.operatorName }}, {{ biz.location }}</li>
          <li><strong>Email:</strong> <a [href]="'mailto:' + biz.email">{{ biz.email }}</a></li>
        </ul>
      </section>

      <section class="legal-section">
        <h2>Our principles</h2>
        <ul>
          <li>Use the service responsibly and follow the rules of every interview or assessment you take part in (see the <a routerLink="/acceptable-use">Acceptable Use Policy</a>).</li>
          <li>Your data is handled as described in our <a routerLink="/privacy-policy">Privacy Policy</a>; payments are processed by Cashfree and we never see card, UPI or bank details.</li>
          <li>Questions or problems? <a routerLink="/contact">Contact us</a> — we reply to every message.</li>
        </ul>
      </section>
    </div>
  `,
})
export class AboutComponent {
  readonly biz = BUSINESS;
}
