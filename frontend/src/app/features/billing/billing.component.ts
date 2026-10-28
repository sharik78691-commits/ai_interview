import { DatePipe } from '@angular/common';
import { HttpErrorResponse } from '@angular/common/http';
import { Component, OnInit, inject, signal } from '@angular/core';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { firstValueFrom, forkJoin } from 'rxjs';
import { BUSINESS } from '../../core/business-info';
import { BillingService } from '../../core/services/billing.service';
import {
  GatewayKey,
  MyBilling,
  PaymentRecord,
  Plan,
  durationLabel,
  formatMoney,
  hoursLabel,
} from '../../core/models/billing.models';

/**
 * The signed-in user's own subscription and payment history.
 *
 * Everything shown comes from `GET /api/billing/me`, which the backend scopes
 * to the session user — no other user's records can be requested from here.
 * Also handles the return leg of redirect-based gateways (Cashfree): the
 * order id in the URL is only a hint; the backend verifies it with the gateway.
 */
@Component({
  selector: 'app-billing',
  standalone: true,
  imports: [RouterLink, DatePipe],
  templateUrl: './billing.component.html',
  styleUrl: './billing.component.css',
})
export class BillingComponent implements OnInit {
  private readonly billing = inject(BillingService);
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  readonly data = signal<MyBilling | null>(null);
  readonly plans = signal<Plan[]>([]);
  readonly loading = signal(true);
  readonly error = signal('');
  readonly notice = signal('');
  readonly busy = signal(false);

  readonly money = formatMoney;
  readonly duration = durationLabel;
  readonly hours = hoursLabel;

  ngOnInit(): void {
    const q = this.route.snapshot.queryParamMap;
    if (q.get('purchased')) this.notice.set('Payment confirmed — your plan is active. Thank you!');
    const gateway = q.get('gateway') as GatewayKey | null;
    const orderId = q.get('order_id');
    if (gateway && orderId) {
      void this.verifyReturn(gateway, orderId);
    } else {
      this.load();
    }
  }

  load(): void {
    this.loading.set(true);
    forkJoin({ me: this.billing.myBilling(), plans: this.billing.plans() }).subscribe({
      next: ({ me, plans }) => {
        this.data.set(me);
        this.plans.set(plans);
        this.loading.set(false);
      },
      error: () => {
        this.error.set('Could not load your billing details. Please refresh.');
        this.loading.set(false);
      },
    });
  }

  private async verifyReturn(gateway: GatewayKey, orderId: string): Promise<void> {
    this.notice.set('Confirming your payment with the gateway…');
    try {
      const r = await firstValueFrom(this.billing.verify(gateway, orderId, { order_id: orderId }));
      this.notice.set(
        r.status === 'paid'
          ? 'Payment confirmed — your plan is active. Thank you!'
          : r.status === 'pending'
            ? 'Payment is still processing. Your plan activates automatically once the gateway confirms it.'
            : r.message || 'The payment did not go through.',
      );
    } catch (err) {
      const detail = err instanceof HttpErrorResponse ? err.error?.detail : null;
      this.notice.set(typeof detail === 'string' ? detail : 'We could not confirm this payment yet.');
    } finally {
      // Drop the gateway params so a refresh doesn't re-verify.
      this.router.navigate([], { relativeTo: this.route, queryParams: {}, replaceUrl: true });
      this.load();
    }
  }

  trialPlans(): Plan[] {
    return this.data()?.trial_used ? [] : this.plans().filter((p) => p.trial_days > 0);
  }

  startTrial(plan: Plan): void {
    if (this.busy()) return;
    this.busy.set(true);
    this.billing.startTrial(plan.id).subscribe({
      next: () => {
        this.busy.set(false);
        this.notice.set(`Your ${durationLabel(plan.trial_days)} free trial has started.`);
        this.load();
      },
      error: (err: HttpErrorResponse) => {
        this.busy.set(false);
        this.notice.set(typeof err.error?.detail === 'string' ? err.error.detail : 'Could not start the trial.');
      },
    });
  }

  daysLeft(iso: string | null): number {
    if (!iso) return 0;
    return Math.max(0, Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000));
  }

  /** "2 days 4 h" / "5 h 12 min" until an exact instant (server-provided). */
  timeLeft(iso: string | null): string {
    if (!iso) return '—';
    const ms = Math.max(0, new Date(iso).getTime() - Date.now());
    const mins = Math.floor(ms / 60_000);
    const d = Math.floor(mins / 1440);
    const h = Math.floor((mins % 1440) / 60);
    const m = mins % 60;
    if (d >= 2) return `${d} days ${h} h`;
    if (d === 1) return `1 day ${h} h`;
    return h ? `${h} h ${m} min` : `${m} min`;
  }

  /** The viewer's time-zone name, shown next to local expiry times. */
  readonly timeZone = (() => {
    try {
      return Intl.DateTimeFormat().resolvedOptions().timeZone || 'local time';
    } catch {
      return 'local time';
    }
  })();

  usagePercent(used: number, allowance: number): number {
    return allowance > 0 ? Math.min(100, Math.round((used / allowance) * 100)) : 0;
  }

  /** Other live plans besides the applicable one (each keeps its own credits). */
  otherLive(d: MyBilling): number {
    return d.subscriptions.filter((s) => s.status === 'active' && s.id !== d.current?.id).length;
  }

  readonly supportEmail = BUSINESS.email;

  /** Pre-filled support email for one payment (reference + amount), so help is one click away. */
  helpLink(p: PaymentRecord): string {
    const ref = p.gateway_payment_id || p.gateway_order_id;
    const subject = `Payment help: ${ref}`;
    const body = [
      'Hi OyeInterview support,',
      '',
      `I need help with this payment (${p.plan_name}, ${formatMoney(p.amount_minor, p.currency)}).`,
      `Order ID: ${p.gateway_order_id}`,
      `Payment ID: ${p.gateway_payment_id || '-'}`,
      '',
      'What happened / what I would like (for example a refund):',
      '',
    ].join('\n');
    return `mailto:${BUSINESS.email}?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`;
  }

  paymentStatus(status: string): string {
    return status === 'created' ? 'pending' : status;
  }

  sourceLabel(source: string): string {
    return source === 'manual' ? 'Granted by support' : source === 'trial' ? 'Free trial' : 'Purchase';
  }
}
