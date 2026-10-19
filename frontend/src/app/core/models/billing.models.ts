/** Billing API models. Money is always integer minor units (paise). */

export interface Plan {
  id: number;
  code: string;
  name: string;
  description: string;
  currency: string;
  duration_days: number;
  trial_days: number;
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

export interface AdminPlan extends Plan {
  is_active: boolean;
  sort_order: number;
  promo_price_minor: number | null;
  promo_starts_at: string | null;
  created_at: string | null;
  updated_at: string | null;
}

export type SubscriptionStatus = 'active' | 'scheduled' | 'expired' | 'revoked';
export type SubscriptionSource = 'payment' | 'manual' | 'trial';
export type GatewayKey = 'razorpay' | 'cashfree' | 'mock';
export type PaymentStatus = 'created' | 'paid' | 'failed' | 'cancelled';

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
  notes: string;
  revoked_at: string | null;
  created_at: string | null;
}

export interface AdminSubscriptionRow extends Subscription {
  gateway: GatewayKey | null;
}

export interface PaymentRecord {
  id: number;
  user_id: number;
  user_email: string;
  plan_id: number;
  plan_code: string;
  plan_name: string;
  duration_days: number;
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
}

export interface MyBilling {
  current: Subscription | null;
  access_ends_at: string | null;
  subscriptions: Subscription[];
  payments: PaymentRecord[];
  trial_used: boolean;
}

export interface Gateway {
  key: GatewayKey;
  display_name: string;
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
  status: 'paid' | 'pending' | 'failed';
  payment_id: number;
  subscription_id: number | null;
  message: string;
}

export interface AuditEntry {
  id: number;
  user_id: number | null;
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
}

/** Editable plan fields (all optional on update). */
export interface PlanInput {
  code?: string;
  name?: string;
  description?: string;
  price_minor?: number;
  currency?: string;
  duration_days?: number;
  trial_days?: number;
  promo_price_minor?: number | null;
  promo_label?: string;
  promo_starts_at?: string | null;
  promo_ends_at?: string | null;
  features?: string[];
  badge?: string;
  is_highlighted?: boolean;
  is_active?: boolean;
  sort_order?: number;
  clear_promo?: boolean;
}

/** "₹1,234" / "₹1,234.50" from minor units. */
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
