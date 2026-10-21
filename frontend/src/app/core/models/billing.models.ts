/** Billing API models. Money is always integer minor units (paise / cents). */

export type BillingType = 'one_time' | 'recurring';
export type CurrencyCode = string;

export interface Plan {
  id: number;
  code: string;
  name: string;
  description: string;
  /** Currency of price_minor (the one requested from /api/plans). */
  currency: CurrencyCode;
  duration_days: number;
  /** Exact length of one purchase. */
  duration_hours: number;
  duration_unit: 'hours' | 'days' | string;
  screenshot_allowance: number;
  billing_type: BillingType | string;
  billing_interval_days: number | null;
  /** True only once automatic renewal is implemented (it is not yet). */
  auto_renew_available: boolean;
  trial_days: number;
  /** False when the plan has no price in this currency yet. */
  available: boolean;
  /** True when this price can actually be paid right now. */
  payable: boolean;
  /** What checkout charges right now (promo-aware, server-computed). */
  price_minor: number;
  regular_price_minor: number;
  is_promo: boolean;
  promo_label: string;
  promo_ends_at: string | null;
  features: string[];
  badge: string;
  is_highlighted: boolean;
}

export interface PlanPrice {
  id: number;
  plan_id: number;
  currency: CurrencyCode;
  amount_minor: number;
  is_active: boolean;
  effective_from: string;
  effective_to: string | null;
  provider_refs: Record<string, string>;
  is_current: boolean;
}

export interface AdminPlan extends Plan {
  is_active: boolean;
  is_public: boolean;
  sort_order: number;
  promo_price_minor: number | null;
  promo_starts_at: string | null;
  created_at: string | null;
  updated_at: string | null;
  prices: PlanPrice[];
}

export interface Currency {
  code: CurrencyCode;
  name: string;
  minor_digits: number;
  display_enabled: boolean;
  payments_enabled: boolean;
  /** payments_enabled AND a configured gateway accepts it. */
  payable_now: boolean;
  gateways: string[];
  sort_order: number;
  live_prices: number;
}

export type SubscriptionStatus = 'active' | 'scheduled' | 'expired' | 'revoked' | 'cancelled' | 'refunded';
export type SubscriptionSource = 'payment' | 'manual' | 'trial';
export type GatewayKey = 'razorpay' | 'cashfree' | 'mock';
export type PaymentStatus = 'created' | 'paid' | 'failed' | 'cancelled' | 'refunded' | 'disputed';

export interface Subscription {
  id: number;
  user_id: number;
  user_email: string;
  plan_id: number | null;
  plan_code: string;
  plan_name: string;
  status: SubscriptionStatus;
  source: SubscriptionSource;
  starts_at: string;
  expires_at: string;
  amount_minor: number;
  currency: string;
  is_renewal: boolean;
  payment_id: number | null;
  screenshot_allowance: number;
  screenshots_used: number;
  screenshots_remaining: number;
  billing_type: BillingType | string;
  auto_renew: boolean;
  notes: string;
  revoked_at: string | null;
  cancelled_at: string | null;
  created_at: string | null;
}

export interface AdminSubscriptionRow extends Subscription {
  gateway: GatewayKey | null;
  payment_status: PaymentStatus | null;
  gateway_payment_id: string | null;
}

export interface PaymentRecord {
  id: number;
  user_id: number;
  user_email: string;
  plan_id: number;
  plan_code: string;
  plan_name: string;
  duration_days: number;
  duration_hours: number;
  screenshot_allowance: number;
  amount_minor: number;
  currency: string;
  gateway: GatewayKey;
  receipt: string;
  gateway_order_id: string;
  gateway_payment_id: string | null;
  status: PaymentStatus;
  method: string;
  failure_reason: string;
  verified_via: string;
  is_renewal: boolean;
  created_at: string | null;
  paid_at: string | null;
  refunded_at: string | null;
  refund_reference: string;
  subscription_id: number | null;
}

/** Screenshot credits of the applicable subscription (server-computed). */
export interface Usage {
  subscription_id: number | null;
  plan_name: string;
  status: string;
  allowance: number;
  used: number;
  remaining: number;
  expires_at: string | null;
  /** Remaining across all live subscriptions (each usable on its own). */
  total_remaining: number;
}

export interface MyBilling {
  current: Subscription | null;
  access_ends_at: string | null;
  subscriptions: Subscription[];
  payments: PaymentRecord[];
  trial_used: boolean;
  usage: Usage | null;
}

export interface Gateway {
  key: GatewayKey;
  display_name: string;
  currencies: string[];
}

export interface CheckoutResponse {
  payment_id: number;
  gateway: GatewayKey;
  order_id: string;
  amount_minor: number;
  currency: string;
  /** Public, gateway-specific data to open the checkout (never secrets). */
  checkout: Record<string, unknown>;
}

export interface VerifyResponse {
  status: 'paid' | 'pending' | 'failed' | string;
  payment_id: number;
  subscription_id: number | null;
  message: string;
}

export interface AuditEntry {
  id: number;
  user_id: number | null;
  user_email: string;
  plan_id: number | null;
  subscription_id: number | null;
  payment_id: number | null;
  actor_user_id: number | null;
  action: string;
  reason: string;
  details: Record<string, unknown>;
  created_at: string | null;
}

export interface Paged<T> {
  total: number;
  page: number;
  page_size: number;
  items: T[];
}

export interface AdminUserBilling {
  user_id: number;
  email: string;
  name: string;
  current: Subscription | null;
  access_ends_at: string | null;
  subscriptions: Subscription[];
  payments: PaymentRecord[];
  audit: AuditEntry[];
  usage: Usage | null;
}

export interface UsageRecord {
  id: number;
  user_id: number;
  user_email: string;
  subscription_id: number | null;
  plan_name: string;
  request_id: string;
  status: 'reserved' | 'completed' | 'failed' | string;
  credits: number;
  credits_consumed: number;
  attempts: number;
  failure_code: string;
  created_at: string | null;
  completed_at: string | null;
}

export interface CurrencyAmount {
  currency: string;
  amount_minor: number;
  count: number;
}

/** Revenue is per currency — never summed across currencies. */
export interface BillingOverview {
  date_from: string | null;
  date_to: string | null;
  total_users: number;
  active_subscriptions: number;
  expired_subscriptions: number;
  ended_subscriptions: number;
  payments_by_status: Record<string, number>;
  revenue_by_currency: CurrencyAmount[];
  refunds_by_currency: CurrencyAmount[];
  subscriptions_by_plan: Record<string, number>;
  screenshot_requests: number;
  screenshots_analyzed: number;
  screenshot_failures: number;
  payment_success_rate: number | null;
}

/** Editable plan fields (all optional on update). */
export interface PlanInput {
  code?: string;
  name?: string;
  description?: string;
  price_minor?: number;
  currency?: string;
  duration_days?: number;
  duration_hours?: number;
  duration_unit?: 'hours' | 'days';
  screenshot_allowance?: number;
  billing_type?: BillingType;
  billing_interval_days?: number | null;
  trial_days?: number;
  promo_price_minor?: number | null;
  promo_label?: string;
  promo_starts_at?: string | null;
  promo_ends_at?: string | null;
  features?: string[];
  badge?: string;
  is_highlighted?: boolean;
  is_active?: boolean;
  is_public?: boolean;
  sort_order?: number;
  clear_promo?: boolean;
  reason?: string;
}

export interface PriceInput {
  currency: string;
  amount_minor: number;
  is_active?: boolean;
  effective_from?: string | null;
  effective_to?: string | null;
  provider_refs?: Record<string, string> | null;
  reason?: string;
}

/** "₹1,234" / "$1.99" from minor units, locale-aware. */
export function formatMoney(minor: number, currency = 'INR'): string {
  const major = minor / 100;
  const hasFraction = minor % 100 !== 0;
  try {
    return new Intl.NumberFormat(currency === 'INR' ? 'en-IN' : 'en-US', {
      style: 'currency',
      currency,
      minimumFractionDigits: hasFraction ? 2 : 0,
      maximumFractionDigits: 2,
    }).format(major);
  } catch {
    return `${currency} ${major.toFixed(hasFraction ? 2 : 0)}`;
  }
}

/** "3 days" / "1 month" style label for a duration in days. */
export function durationLabel(days: number): string {
  if (days % 365 === 0) return days === 365 ? '1 year' : `${days / 365} years`;
  if (days % 30 === 0) return days === 30 ? '1 month' : `${days / 30} months`;
  if (days % 7 === 0 && days >= 7) return days === 7 ? '1 week' : `${days / 7} weeks`;
  return days === 1 ? '1 day' : `${days} days`;
}

/**
 * Exact duration label from hours, in the plan's display unit:
 * 24 → "24 hours", 168 (days) → "7 days", 720 (days) → "30 days".
 * Never says "1 month" for 30 days — durations are exact, not calendar.
 */
export function hoursLabel(hours: number, unit: string = 'days'): string {
  if (unit === 'hours' || hours % 24 !== 0) return hours === 1 ? '1 hour' : `${hours} hours`;
  const days = hours / 24;
  return days === 1 ? '1 day' : `${days} days`;
}

export function billingTypeLabel(type: string): string {
  return type === 'recurring' ? 'Subscription plan · paid upfront' : 'One-time pass';
}

/** A fresh idempotency key for one checkout / analysis attempt. */
export function newRequestId(prefix = 'req'): string {
  const rand =
    typeof crypto !== 'undefined' && 'randomUUID' in crypto
      ? crypto.randomUUID().replace(/-/g, '')
      : `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 12)}`;
  return `${prefix}-${rand}`.slice(0, 64);
}
