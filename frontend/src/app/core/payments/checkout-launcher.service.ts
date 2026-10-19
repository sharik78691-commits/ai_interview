import { DOCUMENT } from '@angular/common';
import { Injectable, NgZone, inject } from '@angular/core';
import { firstValueFrom } from 'rxjs';
import { CheckoutResponse } from '../models/billing.models';
import { BillingService } from '../services/billing.service';

/** What the browser hands back after the gateway UI closes. */
export type CheckoutOutcome =
  | { kind: 'completed'; payload: Record<string, string> } // send to /verify
  | { kind: 'redirected' } // gateway navigated away (Cashfree); verified on return
  | { kind: 'dismissed' }; // user closed the popup

const RAZORPAY_SRC = 'https://checkout.razorpay.com/v1/checkout.js';
const CASHFREE_SRC = 'https://sdk.cashfree.com/js/v3/cashfree.js';

interface RazorpayCtor {
  new (options: Record<string, unknown>): { open(): void; on(event: string, cb: (e: unknown) => void): void };
}
type CashfreeFactory = (opts: { mode: string }) => {
  checkout(opts: { paymentSessionId: string; redirectTarget?: string }): Promise<unknown>;
};

/**
 * Opens the selected gateway's checkout UI. Each gateway is handled
 * independently; scripts load on demand (pricing page only), so the gateway
 * SDKs never weigh on the rest of the app.
 *
 * The outcome is only a CLAIM — the caller must send it to the backend,
 * which verifies the payment with the gateway before activating anything.
 */
@Injectable({ providedIn: 'root' })
export class CheckoutLauncherService {
  private readonly doc = inject(DOCUMENT);
  private readonly zone = inject(NgZone);
  private readonly billing = inject(BillingService);
  private readonly loaded = new Map<string, Promise<void>>();

  async open(order: CheckoutResponse, prefillEmail = ''): Promise<CheckoutOutcome> {
    switch (order.gateway) {
      case 'razorpay':
        return this.openRazorpay(order, prefillEmail);
      case 'cashfree':
        return this.openCashfree(order);
      case 'mock':
        return this.openMock(order);
    }
  }

  // ------------------------------------------------------------ Razorpay
  private async openRazorpay(order: CheckoutResponse, email: string): Promise<CheckoutOutcome> {
    await this.loadScript(RAZORPAY_SRC);
    const Razorpay = (this.doc.defaultView as unknown as { Razorpay?: RazorpayCtor }).Razorpay;
    if (!Razorpay) throw new Error('Razorpay checkout failed to load.');
    const c = order.checkout as {
      key_id: string;
      order_id: string;
      amount: number;
      currency: string;
      name?: string;
      description?: string;
      prefill?: Record<string, string>;
    };
    return new Promise<CheckoutOutcome>((resolve) => {
      const rzp = new Razorpay({
        key: c.key_id,
        order_id: c.order_id,
        amount: c.amount,
        currency: c.currency,
        name: c.name ?? 'OyeInterview',
        description: c.description ?? '',
        prefill: { email, ...(c.prefill ?? {}) },
        handler: (resp: Record<string, string>) =>
          this.zone.run(() =>
            resolve({
              kind: 'completed',
              payload: {
                razorpay_order_id: resp['razorpay_order_id'] ?? '',
                razorpay_payment_id: resp['razorpay_payment_id'] ?? '',
                razorpay_signature: resp['razorpay_signature'] ?? '',
              },
            }),
          ),
        modal: { ondismiss: () => this.zone.run(() => resolve({ kind: 'dismissed' })) },
      });
      rzp.open();
    });
  }

  // ------------------------------------------------------------ Cashfree
  private async openCashfree(order: CheckoutResponse): Promise<CheckoutOutcome> {
    await this.loadScript(CASHFREE_SRC);
    const factory = (this.doc.defaultView as unknown as { Cashfree?: CashfreeFactory }).Cashfree;
    if (!factory) throw new Error('Cashfree checkout failed to load.');
    const c = order.checkout as { payment_session_id: string; mode: string };
    // Redirect flow: Cashfree returns to /billing?gateway=cashfree&order_id=…,
    // where the billing page asks the backend to verify the order.
    await factory({ mode: c.mode === 'production' ? 'production' : 'sandbox' }).checkout({
      paymentSessionId: c.payment_session_id,
      redirectTarget: '_self',
    });
    return { kind: 'redirected' };
  }

  // ---------------------------------------------------------------- mock
  private async openMock(order: CheckoutResponse): Promise<CheckoutOutcome> {
    const view = this.doc.defaultView;
    const ok = view?.confirm(
      'TEST PAYMENT (development only)\n\nNo money moves. OK = simulate a successful payment, Cancel = simulate a decline.',
    );
    const signed = await firstValueFrom(this.billing.simulateMock(order.order_id, !!ok));
    return { kind: 'completed', payload: signed };
  }

  // ------------------------------------------------------------- helpers
  private loadScript(src: string): Promise<void> {
    const existing = this.loaded.get(src);
    if (existing) return existing;
    const p = new Promise<void>((resolve, reject) => {
      const el = this.doc.createElement('script');
      el.src = src;
      el.async = true;
      el.onload = () => resolve();
      el.onerror = () => {
        this.loaded.delete(src);
        reject(new Error('Could not load the payment provider. Check your connection or ad blocker.'));
      };
      this.doc.head.appendChild(el);
    });
    this.loaded.set(src, p);
    return p;
  }
}
