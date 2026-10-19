import { Component, OnInit, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { HttpErrorResponse } from '@angular/common/http';
import { firstValueFrom, forkJoin } from 'rxjs';
import { AuthService } from '../../core/auth/auth.service';
import { CheckoutLauncherService } from '../../core/payments/checkout-launcher.service';
import { BillingService } from '../../core/services/billing.service';
import {
  Gateway,
  GatewayKey,
  Plan,
  durationLabel,
  formatMoney,
} from '../../core/models/billing.models';

/**
 * Public pricing page — every plan, price, duration and promotion comes from
 * the backend (`GET /api/plans`), so admin edits show up without a frontend
 * release. Purchases are one-time prepaid periods; there is NO auto-renewal.
 */
@Component({
  selector: 'app-pricing',
  standalone: true,
  imports: [RouterLink, FormsModule],
  templateUrl: './pricing.component.html',
  styleUrl: './pricing.component.css',
})
export class PricingComponent implements OnInit {
  private readonly billing = inject(BillingService);
  private readonly launcher = inject(CheckoutLauncherService);
  private readonly auth = inject(AuthService);
  private readonly router = inject(Router);

  readonly plans = signal<Plan[]>([]);
  readonly gateways = signal<Gateway[]>([]);
  readonly loading = signal(true);
  readonly loadError = signal('');

  /** Plan the checkout panel is open for. */
  readonly selected = signal<Plan | null>(null);
  gateway: GatewayKey | '' = '';
  phone = '';
  readonly busy = signal(false);
  readonly message = signal('');
  readonly messageKind = signal<'info' | 'error'>('info');

  readonly needsPhone = computed(() => this.selectedGateway() === 'cashfree');
  private readonly selectedGateway = signal<GatewayKey | ''>('');

  readonly money = formatMoney;
  readonly duration = durationLabel;

  ngOnInit(): void {
    forkJoin({ plans: this.billing.plans(), gateways: this.billing.gateways() }).subscribe({
      next: ({ plans, gateways }) => {
        this.plans.set(plans);
        this.gateways.set(gateways);
        this.gateway = gateways[0]?.key ?? '';
        this.selectedGateway.set(this.gateway);
        this.loading.set(false);
      },
      error: () => {
        this.loadError.set('Pricing could not be loaded right now. Please refresh in a moment.');
        this.loading.set(false);
      },
    });
  }

  perDay(p: Plan): string {
    return formatMoney(Math.round(p.price_minor / p.duration_days), p.currency);
  }

  choose(p: Plan): void {
    if (!this.auth.isAuthenticated) {
      this.router.navigate(['/register'], { queryParams: { redirect: '/pricing' } });
      return;
    }
    this.message.set('');
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
      const order = await firstValueFrom(this.billing.checkout(plan.id, this.gateway, this.phone));
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
      this.show(this.errorText(err), 'error');
    } finally {
      this.busy.set(false);
    }
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
