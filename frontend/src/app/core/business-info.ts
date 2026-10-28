/**
 * Operator / business details shown on the public legal and contact pages
 * (required by payment gateways such as Razorpay and Cashfree for merchant
 * approval). Edit ONLY this file: every page reads from here.
 *
 * Only the operator's name and country are published (no state, city, street
 * address or phone); the full details are given to the gateway privately during KYC.
 * The name must match the PAN / bank account used for KYC.
 */
export const BUSINESS = {
  brand: 'OyeInterview',
  /** Full legal name of the sole proprietor (as on PAN). */
  operatorName: 'Mohammed Raees Shaikh',
  businessType: 'Sole Proprietorship',
  /** Published location (country only — no state, city or street address). */
  location: 'India',
  email: 'support@oyeinterview.com',
  website: 'https://www.oyeinterview.com',
  /** Not registered under GST (turnover below the registration threshold). */
  gstRegistered: false,
  gstNote: 'We are not registered under GST. Prices shown are final, and no GST is charged.',
} as const;
