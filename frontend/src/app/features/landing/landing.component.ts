import { DOCUMENT } from '@angular/common';
import { Component, OnDestroy, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { BillingService } from '../../core/services/billing.service';
import { TUTORIAL, tutorialEmbedUrl } from '../../core/tutorial';
import { Plan, durationLabel, formatMoney, hoursLabel, withoutHiddenFeatures, withoutScreenshotCopy } from '../../core/models/billing.models';

const FAQ_LD_ID = 'landing-faq-ld';

/**
 * FAQPage structured data for the home page only. It mirrors the visible FAQ
 * in landing.component.html — keep the two in sync (Google requires markup to
 * match on-page content). It used to live in index.html, which put it on
 * every page, including pages that show no FAQ.
 */
const LANDING_FAQ: ReadonlyArray<{ q: string; a: string }> = [
  { q: 'Will I be charged again?', a: 'No. Every plan is a one-time payment that simply ends. No auto-renewal and no stored cards.' },
  { q: 'Is my resume private?', a: 'Saved resumes and target roles are stored in your account. During a session, questions with your resume and job description go to the selected AI provider, and audio clips you share go to a speech-to-text provider. Transcripts and answers are not stored. See the Privacy Policy.' },
  { q: 'Can I try before paying?', a: 'Yes — the demo shows the full interview screen with sample answers, no payment needed. See the Refund Policy for payment questions.' },
  { q: 'How does the mock interview work?', a: 'Choose a role, interview type and difficulty. OyeInterview asks practice questions, you answer in your own words (typed or spoken), and you get a score out of 10 with what worked, what to improve and what a strong answer covers. Retry any question until it feels natural.' },
  { q: 'Do I need a microphone?', a: 'No. Questions are picked up from the interview-call audio you share, and you can always type questions. Your own microphone is optional.' },
  { q: 'Which browsers work best?', a: 'Chrome or Edge — they support sharing the audio of a call from a browser tab. Typed questions and mock interviews work in other modern browsers.' },
  { q: 'Can I use it in a real interview?', a: "Only where the interview's rules allow assistance tools. Check first — see our Acceptable Use Policy. AI suggestions can be wrong — always check them." },
];

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './landing.component.html',
  styleUrl: './landing.component.css',
})
export class LandingComponent implements OnInit, OnDestroy {
  private router = inject(Router);
  private doc = inject(DOCUMENT);
  private billing = inject(BillingService);

  readonly tutorial = TUTORIAL;

  /**
   * Click-to-play: the section shows our own thumbnail, and the YouTube player
   * (privacy-enhanced, fixed URL from core/tutorial.ts) is only created on
   * Play. Built with the DOM API so the landing bundle needs no iframe
   * sanitizer — it is part of the initial bundle.
   */
  playTutorial(box: HTMLElement, start = 0): void {
    const frame = document.createElement('iframe');
    frame.src = tutorialEmbedUrl(start);
    frame.title = 'OyeInterview full tutorial';
    frame.allow = 'autoplay; encrypted-media; picture-in-picture; fullscreen';
    frame.allowFullscreen = true;
    // inline: component styles don't reach elements created outside the template
    frame.style.cssText = 'position:absolute;inset:0;width:100%;height:100%;border:0';
    box.replaceChildren(frame);
  }

  /** Plans come from the backend (same source as /pricing) — never hard-coded. */
  readonly plans = signal<Plan[]>([]);
  readonly plansLoaded = signal(false);
  /** "Free trial" copy only when a plan really offers one. */
  readonly hasTrial = computed(() => this.plans().some((p) => p.trial_days > 0));
  readonly ctaLabel = computed(() => (this.hasTrial() ? 'Start your free trial' : 'Get started'));

  readonly money = formatMoney;

  perDay(p: Plan): string {
    return formatMoney(Math.round((p.price_minor * 24) / Math.max(p.duration_hours || p.duration_days * 24, 1)), p.currency);
  }
  readonly duration = durationLabel;
  readonly hours = hoursLabel;
  readonly planDescription = withoutScreenshotCopy;
  readonly visibleFeatures = withoutHiddenFeatures;

  ngOnInit(): void {
    this.addFaqLd();
    this.billing.plans().subscribe({
      next: (plans) => {
        this.plans.set(plans);
        this.plansLoaded.set(true);
      },
      error: () => this.plansLoaded.set(true),
    });
  }

  ngOnDestroy(): void {
    this.doc.getElementById(FAQ_LD_ID)?.remove();
  }

  /** Inject the home-page FAQPage JSON-LD (prerendered into the static HTML). */
  private addFaqLd(): void {
    this.doc.getElementById(FAQ_LD_ID)?.remove();
    const script = this.doc.createElement('script');
    script.setAttribute('type', 'application/ld+json');
    script.id = FAQ_LD_ID;
    script.textContent = JSON.stringify({
      '@context': 'https://schema.org',
      '@type': 'FAQPage',
      mainEntity: LANDING_FAQ.map((f) => ({
        '@type': 'Question',
        name: f.q,
        acceptedAnswer: { '@type': 'Answer', text: f.a },
      })),
    });
    this.doc.head.appendChild(script);
  }

  tryDemo(): void {
    localStorage.setItem('aia_demo', '1');
    if (!localStorage.getItem('aia_resumeText')) localStorage.setItem('aia_resumeText', 'Demo candidate — full-stack engineer');
    if (!localStorage.getItem('aia_jd')) localStorage.setItem('aia_jd', 'Demo role — senior backend engineer');
    this.router.navigate(['/interview']);
  }
}
