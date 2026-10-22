import { HttpClient } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../api/api-config';
import {
  CheckoutResponse,
  Currency,
  Gateway,
  GatewayKey,
  MyBilling,
  Plan,
  Subscription,
  Usage,
  VerifyResponse,
  FreeTrial,
} from '../models/billing.models';

/**
 * Pricing + the signed-in user's own subscription/payment data.
 *
 * Prices always come from the backend (database-driven). Nothing here can
 * grant access: the backend activates a plan only after it has verified the
 * payment with the gateway itself.
 */
@Injectable({ providedIn: 'root' })
export class BillingService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  /** Plans priced in `currency` (server-side amounts; never computed here). */
  plans(currency = 'INR'): Observable<Plan[]> {
    return this.http.get<Plan[]>(`${this.base}/api/plans`, { params: { currency } });
  }

  currencies(): Observable<Currency[]> {
    return this.http.get<Currency[]>(`${this.base}/api/pricing/currencies`);
  }

  /** One-time free trial: live minutes (web / desktop) and screenshot credits left. */
  freeTrial(): Observable<FreeTrial> {
    return this.http.get<FreeTrial>(`${this.base}/api/billing/me/free-trial`);
  }

  myUsage(): Observable<Usage> {
    return this.http.get<Usage>(`${this.base}/api/billing/me/usage`);
  }

  gateways(): Observable<Gateway[]> {
    return this.http.get<Gateway[]>(`${this.base}/api/payments/gateways`);
  }

  myBilling(): Observable<MyBilling> {
    return this.http.get<MyBilling>(`${this.base}/api/billing/me`);
  }

  startTrial(planId: number): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/billing/trial`, { plan_id: planId });
  }

  /**
   * Start a checkout. Only the plan, currency and gateway are sent — the
   * amount is decided by the server. `idempotencyKey` (one per attempt)
   * makes a double-click or retry open no second order.
   */
  checkout(
    planId: number,
    gateway: GatewayKey,
    phone = '',
    currency = 'INR',
    idempotencyKey?: string,
  ): Observable<CheckoutResponse> {
    return this.http.post<CheckoutResponse>(`${this.base}/api/payments/checkout`, {
      plan_id: planId,
      gateway,
      phone,
      currency,
      ...(idempotencyKey ? { idempotency_key: idempotencyKey } : {}),
    });
  }

  verify(gateway: GatewayKey, orderId: string, payload: Record<string, string>): Observable<VerifyResponse> {
    return this.http.post<VerifyResponse>(
      `${this.base}/api/payments/${encodeURIComponent(gateway)}/verify`,
      { order_id: orderId, payload },
    );
  }

  /** Development only: stands in for a gateway popup (404 when disabled). */
  simulateMock(orderId: string, succeed = true): Observable<Record<string, string>> {
    return this.http.post<Record<string, string>>(`${this.base}/api/payments/mock/simulate`, {
      order_id: orderId,
      succeed,
    });
  }
}
