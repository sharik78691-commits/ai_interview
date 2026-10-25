import { HttpClient, HttpParams } from '@angular/common/http';
import { Injectable, inject } from '@angular/core';
import { Observable } from 'rxjs';
import { API_BASE_URL } from '../api/api-config';
import {
  AdminPaymentSettings,
  AdminPlan,
  AdminSubscriptionRow,
  AdminUserBilling,
  AuditEntry,
  BillingOverview,
  Currency,
  Paged,
  PaymentRecord,
  PaymentSettingsUpdate,
  PlanInput,
  PlanPrice,
  PriceInput,
  Subscription,
  UsageRecord,
  VerifyResponse,
} from '../models/billing.models';

export interface SubscriptionFilters {
  q?: string;
  plan_id?: number | null;
  status?: string;
  source?: string;
  gateway?: string;
  currency?: string;
  payment_status?: string;
  created_from?: string;
  created_to?: string;
  starts_from?: string;
  starts_to?: string;
  expires_from?: string;
  expires_to?: string;
  sort?: string;
  order?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}

export interface PaymentFilters {
  q?: string;
  plan_id?: number | null;
  status?: string;
  gateway?: string;
  currency?: string;
  date_from?: string;
  date_to?: string;
  sort?: string;
  order?: 'asc' | 'desc';
  page?: number;
  page_size?: number;
}

export interface UsageFilters {
  q?: string;
  plan_id?: number | null;
  subscription_id?: number | null;
  status?: string;
  retried?: boolean;
  date_from?: string;
  date_to?: string;
  page?: number;
  page_size?: number;
}

export interface AuditFilters {
  user_id?: number | null;
  action?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
}

export interface PaymentDetail {
  payment: PaymentRecord;
  subscription: Subscription | null;
  webhook_events: {
    id: number;
    event_id: string;
    event_type: string;
    received_at: string | null;
    processed_at: string | null;
    processing_error: string;
  }[];
}

function params(filters: object): HttpParams {
  let p = new HttpParams();
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== null && v !== '' && v !== false) p = p.set(k, String(v));
  }
  return p;
}

/**
 * Admin pricing, subscription, payment and usage management (admin session +
 * CSRF on writes). Authorization is enforced by the backend on every call.
 */
@Injectable({ providedIn: 'root' })
export class AdminBillingService {
  private readonly http = inject(HttpClient);
  private readonly base = inject(API_BASE_URL);

  overview(range: { date_from?: string; date_to?: string } = {}): Observable<BillingOverview> {
    return this.http.get<BillingOverview>(`${this.base}/api/admin/billing/overview`, { params: params(range) });
  }

  plans(): Observable<AdminPlan[]> {
    return this.http.get<AdminPlan[]>(`${this.base}/api/admin/plans`);
  }

  createPlan(input: PlanInput): Observable<AdminPlan> {
    return this.http.post<AdminPlan>(`${this.base}/api/admin/plans`, input);
  }

  updatePlan(id: number, input: PlanInput): Observable<AdminPlan> {
    return this.http.patch<AdminPlan>(`${this.base}/api/admin/plans/${id}`, input);
  }

  priceHistory(planId: number): Observable<PlanPrice[]> {
    return this.http.get<PlanPrice[]>(`${this.base}/api/admin/plans/${planId}/prices`);
  }

  setPrice(planId: number, input: PriceInput): Observable<AdminPlan> {
    return this.http.put<AdminPlan>(`${this.base}/api/admin/plans/${planId}/prices`, input);
  }

  currencies(): Observable<Currency[]> {
    return this.http.get<Currency[]>(`${this.base}/api/admin/currencies`);
  }

  updateCurrency(
    code: string,
    body: { display_enabled?: boolean; payments_enabled?: boolean; reason?: string },
  ): Observable<Currency[]> {
    return this.http.patch<Currency[]>(`${this.base}/api/admin/currencies/${encodeURIComponent(code)}`, body);
  }

  /** Site-wide online-payments switch and the active gateway (admin only). */
  paymentSettings(): Observable<AdminPaymentSettings> {
    return this.http.get<AdminPaymentSettings>(`${this.base}/api/admin/payment-settings`);
  }

  updatePaymentSettings(body: PaymentSettingsUpdate): Observable<AdminPaymentSettings> {
    return this.http.patch<AdminPaymentSettings>(`${this.base}/api/admin/payment-settings`, body);
  }

  subscriptions(filters: SubscriptionFilters): Observable<Paged<AdminSubscriptionRow>> {
    return this.http.get<Paged<AdminSubscriptionRow>>(`${this.base}/api/admin/subscriptions`, {
      params: params(filters),
    });
  }

  payments(filters: PaymentFilters): Observable<Paged<PaymentRecord>> {
    return this.http.get<Paged<PaymentRecord>>(`${this.base}/api/admin/payments`, {
      params: params(filters),
    });
  }

  paymentDetail(id: number): Observable<PaymentDetail> {
    return this.http.get<PaymentDetail>(`${this.base}/api/admin/payments/${id}`);
  }

  usage(filters: UsageFilters): Observable<Paged<UsageRecord>> {
    return this.http.get<Paged<UsageRecord>>(`${this.base}/api/admin/screenshot-usage`, {
      params: params(filters),
    });
  }

  userBilling(userId: number): Observable<AdminUserBilling> {
    return this.http.get<AdminUserBilling>(`${this.base}/api/admin/users/${userId}/billing`);
  }

  grant(
    userId: number,
    body: { plan_id?: number | null; days?: number | null; hours?: number | null; screenshot_credits?: number | null; reason: string },
  ): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/users/${userId}/subscriptions`, body);
  }

  extend(subId: number, days: number, reason: string, hours = 0): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/subscriptions/${subId}/extend`, { days, hours, reason });
  }

  revoke(subId: number, reason: string): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/subscriptions/${subId}/revoke`, { reason });
  }

  cancel(subId: number, reason: string): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/subscriptions/${subId}/cancel`, { reason });
  }

  setCredits(subId: number, allowance: number, reason: string): Observable<Subscription> {
    return this.http.post<Subscription>(`${this.base}/api/admin/subscriptions/${subId}/credits`, { allowance, reason });
  }

  reconcile(paymentId: number): Observable<VerifyResponse> {
    return this.http.post<VerifyResponse>(`${this.base}/api/admin/payments/${paymentId}/reconcile`, {});
  }

  audit(filters: AuditFilters | number = {}): Observable<AuditEntry[]> {
    const f = typeof filters === 'number' ? { user_id: filters } : filters;
    return this.http.get<AuditEntry[]>(`${this.base}/api/admin/subscription-audit`, { params: params(f) });
  }
}
