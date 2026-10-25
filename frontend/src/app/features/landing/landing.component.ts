import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { BillingService } from '../../core/services/billing.service';
import { Plan, durationLabel, formatMoney, hoursLabel, withoutHiddenFeatures, withoutScreenshotCopy } from '../../core/models/billing.models';

@Component({
  selector: 'app-landing',
  standalone: true,
  imports: [RouterLink],
  templateUrl: './landing.component.html',
  styleUrl: './landing.component.css',
})
export class LandingComponent implements OnInit {
  private router = inject(Router);
  private billing = inject(BillingService);

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
    this.billing.plans().subscribe({
      next: (plans) => {
        this.plans.set(plans);
        this.plansLoaded.set(true);
      },
      error: () => this.plansLoaded.set(true),
    });
  }

  tryDemo(): void {
    localStorage.setItem('aia_demo', '1');
    if (!localStorage.getItem('aia_resumeText')) localStorage.setItem('aia_resumeText', 'Demo candidate — full-stack engineer');
    if (!localStorage.getItem('aia_jd')) localStorage.setItem('aia_jd', 'Demo role — senior backend engineer');
    this.router.navigate(['/interview']);
  }
}
