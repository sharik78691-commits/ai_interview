import { Component } from '@angular/core';

interface Plan {
  key: string;
  name: string;
  price: number;
  period: string;
  perLabel: string;
  tagline: string;
  highlight?: boolean;
  badge?: string;
  features: string[];
  renews: string;
}

/**
 * Public pricing page — required for Razorpay / Cashfree merchant approval.
 * Plan prices and durations are displayed plainly in INR with auto-renewal
 * terms stated clearly (payment gateways reject sites with hidden pricing).
 */
@Component({
  selector: 'app-pricing',
  standalone: true,
  templateUrl: './pricing.component.html',
  styleUrl: './pricing.component.css',
})
export class PricingComponent {
  plans: Plan[] = [
    {
      key: 'trial',
      name: '3 Days',
      price: 150,
      period: '3 days',
      perLabel: 'one-time access',
      tagline: 'A single interview, fully assisted.',
      features: [
        'Real-time AI guidance',
        'Resume + job-description aware answers',
        'Technical, behavioral & coding support',
        'Meeting/tab audio transcription',
      ],
      renews: 'Auto-renews every 3 days until cancelled.',
    },
    {
      key: 'week',
      name: '7 Days',
      price: 300,
      period: '7 days',
      perLabel: 'one-time access',
      tagline: 'A week of interviews and practice.',
      features: [
        'Everything in 3 Days',
        'Unlimited interview sessions',
        'Answer-length control (short / medium / long)',
        'Copy answers + follow-up questions',
      ],
      renews: 'Auto-renews every 7 days until cancelled.',
    },
    {
      key: 'month',
      name: '1 Month',
      price: 900,
      period: 'month',
      perLabel: 'billed monthly',
      tagline: 'A full job-hunt month of coverage.',
      highlight: true,
      badge: 'Most popular',
      features: [
        'Everything in 7 Days',
        'All AI models (Groq, Gemini, Qwen, OpenRouter)',
        'Priority access during peak hours',
        'Session history & saved resumes',
      ],
      renews: 'Auto-renews monthly at ₹900 until cancelled.',
    },
    {
      key: 'quarter',
      name: '3 Months',
      price: 2000,
      period: '3 months',
      perLabel: 'billed quarterly',
      tagline: 'Save ~26% vs paying monthly.',
      badge: 'Best value',
      features: [
        'Everything in 1 Month',
        '3 months of continuous coverage',
        'Save ₹700 vs three monthly payments',
        'Ideal for long interview cycles',
      ],
      renews: 'Auto-renews every 3 months at ₹2000 until cancelled.',
    },
  ];

  /** INR, Indian digit grouping. */
  inr(n: number): string {
    return '₹' + n.toLocaleString('en-IN');
  }
}
