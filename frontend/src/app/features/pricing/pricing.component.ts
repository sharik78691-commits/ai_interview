import { NgTemplateOutlet } from '@angular/common';
import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { catchError, firstValueFrom, forkJoin, of } from 'rxjs';
import { AuthService } from '../../core/auth/auth.service';
import { BUSINESS } from '../../core/business-info';
import { CheckoutLauncherService } from '../../core/payments/checkout-launcher.service';
import { BillingService } from '../../core/services/billing.service';
import {
  Currency,
  Gateway,
  GatewayKey,
  PaymentStatusInfo,
  Plan,
  billingTypeLabel,
  formatMoney,
  hoursLabel,
  newRequestId,
} from '../../core/models/billing.models';

const CURRENCY_KEY = 'oi_currency';

/** INR for visitors whose browser says India; otherwise USD when offered. */
function defaultCurrency(available: string[]): string {
  try {
    const saved = localStorage.getItem(CURRENCY_KEY);
    if (saved && available.includes(saved)) return saved;
  } catch {
    /* storage blocked — fall through */
  }
  let india = false;
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone || '';
    const langs = (typeof navigator !== 'undefined' && navigator.languages) || [];
    india = tz === 'Asia/Kolkata' || tz === 'Asia/Calcutta' || langs.some((l) => /-IN$/i.test(l));
  } catch {
    india = true;
  }
  if (!india && available.includes('USD')) return 'USD';
  return available.includes('INR') ? 'INR' : available[0] ?? 'INR';
}

/**
 * Public pricing page — every plan, price, duration, screenshot allowance and
 * promotion comes from the backend (`GET /api/plans?currency=`), so admin
 * edits show up without a frontend release. Purchases are prepaid; there is
 * NO auto-renewal (Monthly/Quarterly are paid upfront for 30/90 days).
 */
@Component({
  selector: 'app-pricing',
  standalone: true,
  imports: [RouterLink, FormsModule, NgTemplateOutlet],
  templateUrl: './pricing.component.html',
  styleUrl: './pricing.component.css',
})
export class PricingComponent implements OnInit {
  /** Shown in the taxes FAQ (operator is not GST-registered). */
  readonly gstNote = BUSINESS.gstNote;
  private readonly billing = inject(BillingService);
  private readonly launcher = inject(CheckoutLauncherService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly plans = signal<Plan[]>([]);
  readonly gateways = signal<Gateway[]>([]);
  readonly currencies = signal<Currency[]>([]);
  readonly currency = signal('INR');
  readonly loading = signal(true);
  readonly loadError = signal('');
  /** Site-wide switch set by the admin (null = older backend: assume open). */
  readonly paymentStatus = signal<PaymentStatusInfo | null>(null);
  /** Admin switched online payments off (or the active gateway is not set up). */
  readonly paymentsOff = computed(() => this.paymentStatus()?.available === false);
  readonly unavailableText = computed(
    () => this.paymentStatus()?.message || 'Online payments are temporarily unavailable. Please try again later.',
  );
  /** The gateway(s) named in the FAQ ("our payment partner, …"). */
  readonly partnerNames = computed(() => {
    const st = this.paymentStatus();
    if (st?.gateway_names?.length) return st.gateway_names;
    return [st?.active_gateway_name || (st?.active_gateway === 'razorpay' ? 'Razorpay' : 'Cashfree')];
  });
  readonly partnerLabel = computed(() => {
    const names = this.partnerNames();
    return names.length > 1
      ? `payment partners, ${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`
      : `payment partner, ${names[0]}`;
  });

  readonly passes = computed(() => this.plans().filter((p) => p.billing_type !== 'recurring'));
  readonly longPlans = computed(() => this.plans().filter((p) => p.billing_type === 'recurring'));
  readonly noneAvailable = computed(() => this.plans().length > 0 && !this.plans().some((p) => p.available));
  readonly currencyInfo = computed(() => this.currencies().find((c) => c.code === this.currency()) ?? null);
  /** Gateways that can charge the selected currency. */
  readonly currencyGateways = computed(() =>
    this.gateways().filter((g) => !g.currencies?.length || g.currencies.includes(this.currency())),
  );

  /** Plan the checkout panel is open for. */
  readonly selected = signal<Plan | null>(null);
  gateway: GatewayKey | '' = '';
  phone = '';
  readonly busy = signal(false);
  readonly message = signal('');
  readonly messageKind = signal<'info' | 'error'>('info');

  readonly needsPhone = computed(() => this.selectedGateway() === 'cashfree');
  private readonly selectedGateway = signal<GatewayKey | ''>('');
  /** One idempotency key per checkout attempt (reset when the panel opens). */
  private attemptKey = '';

  readonly money = formatMoney;
  readonly hours = hoursLabel;
  readonly billingLabel = billingTypeLabel;

  ngOnInit(): void {
    this.billing
      .paymentStatus()
      .pipe(catchError(() => of(null)))
      .subscribe((st) => this.paymentStatus.set(st));
    forkJoin({ currencies: this.billing.currencies(), gateways: this.billing.gateways() }).subscribe({
      next: ({ currencies, gateways }) => {
        this.currencies.set(currencies);
        this.gateways.set(gateways);
        this.loadPlans(defaultCurrency(currencies.map((c) => c.code)));
      },
      error: () => {
        // Older backends without /pricing/currencies: INR only.
        this.billing.gateways().subscribe({ next: (g) => this.gateways.set(g), error: () => undefined });
        this.loadPlans('INR');
      },
    });
  }

  loadPlans(code: string): void {
    this.currency.set(code);
    this.loading.set(true);
    this.loadError.set('');
    this.billing.plans(code).subscribe({
      next: (plans) => {
        this.plans.set(plans);
        this.pickGateway();
        this.loading.set(false);
      },
      error: () => {
        this.loadError.set('Pricing could not be loaded right now. Please refresh in a moment.');
        this.loading.set(false);
      },
    });
  }

  setCurrency(code: string): void {
    if (code === this.currency()) return;
    try {
      localStorage.setItem(CURRENCY_KEY, code);
    } catch {
      /* per-viewer convenience only */
    }
    this.cancel();
    this.loadPlans(code);
  }

  private pickGateway(): void {
    const gws = this.currencyGateways();
    if (!gws.some((g) => g.key === this.gateway)) this.gateway = gws[0]?.key ?? '';
    this.selectedGateway.set(this.gateway);
  }

  choose(p: Plan): void {
    if (this.paymentsOff() || !p.available || !p.payable) return;
    if (!this.auth.isAuthenticated) {
      this.router.navigate(['/register'], { queryParams: { redirect: '/pricing' } });
      return;
    }
    this.message.set('');
    this.attemptKey = newRequestId('chk');
    this.pickGateway();
    this.selected.set(p);
  }

  onGatewayChange(key: GatewayKey | ''): void {
    this.gateway = key;
    this.selectedGateway.set(key);
  }

  cancel(): void {
    this.selected.set(null);
    this.message.set('');
  }

  /** Effective price per day of access (access under a day counts as one day). */
  perDay(p: Plan): string {
    const days = Math.max(p.duration_hours / 24, 1);
    return formatMoney(Math.round(p.price_minor / days), p.currency);
  }

  ctaLabel(p: Plan): string {
    if (!p.available) return `Not yet available in ${p.currency}`;
    if (this.paymentsOff()) return 'Payments temporarily unavailable';
    if (!p.payable) return `${p.currency} payments coming soon`;
    return `Get ${p.name}`;
  }

  async pay(): Promise<void> {
    const plan = this.selected();
    if (!plan || !this.gateway || this.busy()) return;
    if (this.needsPhone() && this.phone.replace(/\D/g, '').length < 10) {
      this.show('Cashfree needs a 10-digit mobile number for the payment.', 'error');
      return;
    }
    this.busy.set(true);
    this.show('Opening secure checkout…', 'info');
    try {
      const order = await firstValueFrom(
        this.billing.checkout(plan.id, this.gateway, this.phone, plan.currency, this.attemptKey),
      );
      // A new attempt after this one (e.g. after closing the popup) is a new order.
      this.attemptKey = newRequestId('chk');
      const outcome = await this.launcher.open(order, this.auth.currentUser?.email ?? '');
      if (outcome.kind === 'dismissed') {
        this.show('Checkout closed. You have not been charged by us for this attempt.', 'info');
        return;
      }
      if (outcome.kind === 'redirected') return; // verified on /billing after return
      this.show('Confirming your payment with the gateway…', 'info');
      const result = await firstValueFrom(this.billing.verify(order.gateway, order.order_id, outcome.payload));
      if (result.status === 'paid') {
        this.router.navigate(['/billing'], { queryParams: { purchased: 1 } });
      } else if (result.status === 'pending') {
        this.show('Payment is still processing. Your plan activates automatically once the gateway confirms it — check Billing in a few minutes.', 'info');
      } else {
        this.show(result.message || 'The payment did not go through.', 'error');
      }
    } catch (err) {
      // 503 = the admin switched payments off since the page loaded.
      if (err instanceof HttpErrorResponse && err.status === 503) this.markUnavailable(this.errorText(err));
      this.show(this.errorText(err), 'error');
    } finally {
      this.busy.set(false);
    }
  }

  private markUnavailable(message: string): void {
    const st = this.paymentStatus();
    this.paymentStatus.set({
      payments_enabled: false, active_gateway: st?.active_gateway ?? 'cashfree',
      active_gateway_name: st?.active_gateway_name ?? '', available: false, message,
    });
  }

  private show(text: string, kind: 'info' | 'error'): void {
    this.message.set(text);
    this.messageKind.set(kind);
  }

  private errorText(err: unknown): string {
    if (err instanceof HttpErrorResponse) {
      const detail = err.error?.detail;
      if (typeof detail === 'string') return detail;
      if (err.status === 0) return 'Network error — please check your connection.';
    }
    return err instanceof Error ? err.message : 'Something went wrong. Please try again.';
  }
}
