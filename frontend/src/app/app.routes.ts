import { Routes } from '@angular/router';
import { adminGuard, authGuard, guestGuard } from './core/auth/auth.guard';
import { interviewGuard } from './core/guards/interview.guard';
import { desktopOnlyGuard } from './core/screenshot/desktop-only.guard';
import { LandingComponent } from './features/landing/landing.component';
import { LoginComponent } from './features/auth/login/login.component';
import { RegisterComponent } from './features/auth/register/register.component';

/**
 * Route map.
 *
 * Only the landing and auth pages are eagerly loaded — they are what a first
 * visit paints. Dashboard and interview are lazy chunks flagged
 * `data.preload: true`: `SelectivePreloadingStrategy` (app.config.ts) fetches
 * them in the background right after first paint, so the signed-in flow is
 * still instant while landing-page visitors no longer download the whole
 * live-interview pipeline up front. Everything else (admin, settings, legal,
 * pricing, contact, password recovery) loads only on navigation.
 */
export const routes: Routes = [
  {
    path: '',
    component: LandingComponent,
    // Public marketing page — the primary SEO target.
    data: {
      seo: {
        title: 'OyeInterview — AI Interview Assistant for Practice & Preparation',
        description:
          'AI interview assistant for practice and preparation: resume-aware answers, STAR stories and code hints for technical, behavioral and coding interviews.',
        path: '/',
      },
    },
  },
  // Auth pages: already-authenticated users are redirected to /dashboard.
  {
    path: 'login',
    component: LoginComponent,
    canActivate: [guestGuard],
    data: {
      seo: {
        title: 'Sign in — OyeInterview AI Interview Assistant',
        description:
          'Sign in to OyeInterview for resume-aware AI guidance to prepare for your next technical, behavioral or coding interview.',
        path: '/login',
      },
    },
  },
  {
    path: 'register',
    component: RegisterComponent,
    canActivate: [guestGuard],
    data: {
      seo: {
        title: 'Create your free account — OyeInterview',
        description:
          'Create a free OyeInterview account and start practising interviews with an AI assistant that adapts to your resume and target role.',
        path: '/register',
      },
    },
  },
  {
    path: 'forgot-password',
    loadComponent: () =>
      import('./features/auth/forgot-password/forgot-password.component').then(
        (m) => m.ForgotPasswordComponent,
      ),
    canActivate: [guestGuard],
    data: {
      seo: {
        title: 'Reset your password — OyeInterview',
        description: 'Reset the password for your OyeInterview account.',
        path: '/forgot-password',
        noindex: true,
      },
    },
  },
  {
    path: 'reset-password',
    loadComponent: () =>
      import('./features/auth/reset-password/reset-password.component').then(
        (m) => m.ResetPasswordComponent,
      ),
    data: {
      seo: {
        title: 'Set a new password — OyeInterview',
        description: 'Choose a new password for your OyeInterview account.',
        path: '/reset-password',
        noindex: true,
      },
    },
  },
  // Protected application routes — kept out of the search index.
  {
    path: 'dashboard',
    loadComponent: () =>
      import('./features/dashboard/dashboard.component').then((m) => m.DashboardComponent),
    canActivate: [authGuard],
    data: {
      preload: true,
      seo: {
        title: 'Dashboard — OyeInterview',
        description: 'Your OyeInterview dashboard.',
        path: '/dashboard',
        noindex: true,
      },
    },
  },
  {
    path: 'interview',
    loadComponent: () =>
      import('./features/interview/interview.component').then((m) => m.InterviewComponent),
    canActivate: [authGuard, interviewGuard],
    data: {
      preload: true,
      seo: {
        title: 'Live interview — OyeInterview',
        description: 'Your live OyeInterview session.',
        path: '/interview',
        noindex: true,
      },
    },
  },
  {
    path: 'settings',
    loadComponent: () =>
      import('./features/settings/settings.component').then((m) => m.SettingsComponent),
    canActivate: [authGuard],
    data: {
      seo: {
        title: 'Settings — OyeInterview',
        description: 'Manage your OyeInterview settings.',
        path: '/settings',
        noindex: true,
      },
    },
  },
  {
    path: 'billing',
    loadComponent: () =>
      import('./features/billing/billing.component').then((m) => m.BillingComponent),
    canActivate: [authGuard],
    data: {
      seo: {
        title: 'Billing — OyeInterview',
        description: 'Your OyeInterview plan, expiry date and payment history.',
        path: '/billing',
        noindex: true,
      },
    },
  },
  // Desktop-only (packaged .exe): screenshot capture + analysis. The guard
  // redirects the web build to /dashboard, so the route is inert in browsers.
  {
    path: 'screenshot-analysis',
    loadComponent: () =>
      import('./features/screenshot-analysis/screenshot-analysis.component').then(
        (m) => m.ScreenshotAnalysisComponent,
      ),
    canActivate: [authGuard, desktopOnlyGuard],
    data: {
      seo: {
        title: 'Screenshot analysis — OyeInterview',
        description: 'Analyze screenshots of interview questions.',
        path: '/screenshot-analysis',
        noindex: true,
      },
    },
  },
  // Admin panel — adminGuard redirects anonymous users to /login and
  // non-admins to /dashboard. Kept out of the search index.
  {
    path: 'admin',
    loadComponent: () =>
      import('./features/admin/admin.component').then((m) => m.AdminComponent),
    canActivate: [authGuard, adminGuard],
    data: {
      seo: {
        title: 'Admin panel — OyeInterview',
        description: 'Usage statistics and user management.',
        path: '/admin',
        noindex: true,
      },
    },
  },
  // Public legal pages — indexed for SEO.
  {
    path: 'terms',
    loadComponent: () =>
      import('./features/legal/terms/terms.component').then((m) => m.TermsComponent),
    data: {
      seo: {
        title: 'Terms of Service — OyeInterview',
        description:
          'Terms of Service for OyeInterview: accounts, prepaid plans without auto-renewal, acceptable use, AI-generated content and liability.',
        path: '/terms',
      },
    },
  },
  // Old URL kept working for existing links and search results.
  { path: 'terms-and-conditions', redirectTo: 'terms', pathMatch: 'full' },
  {
    path: 'acceptable-use',
    loadComponent: () =>
      import('./features/legal/acceptable-use/acceptable-use.component').then(
        (m) => m.AcceptableUseComponent,
      ),
    data: {
      seo: {
        title: 'Acceptable Use Policy — OyeInterview',
        description:
          'What is and is not allowed when using OyeInterview, including following the rules of every interview process.',
        path: '/acceptable-use',
      },
    },
  },
  // Public informational pages — indexed for SEO.
  {
    path: 'features',
    loadComponent: () =>
      import('./features/info/features-page/features-page.component').then(
        (m) => m.FeaturesPageComponent,
      ),
    data: {
      seo: {
        title: 'Features — OyeInterview AI Interview Assistant',
        description:
          'Interviewer audio detection, streaming resume-aware answers, STAR outlines, code hints, resume library and prepaid plans — what OyeInterview does today.',
        path: '/features',
      },
    },
  },
  {
    path: 'how-it-works',
    loadComponent: () =>
      import('./features/info/how-it-works/how-it-works.component').then(
        (m) => m.HowItWorksComponent,
      ),
    data: {
      seo: {
        title: 'How it works — OyeInterview',
        description:
          'Set up your resume and target role, share interviewer audio, and get streaming AI answer suggestions — plus where your data goes at each step.',
        path: '/how-it-works',
      },
    },
  },
  {
    path: 'trust',
    loadComponent: () =>
      import('./features/info/trust/trust.component').then((m) => m.TrustComponent),
    data: {
      seo: {
        title: 'Trust & data practices — OyeInterview',
        description:
          'What OyeInterview stores and does not store, which providers process data, and the security controls in place.',
        path: '/trust',
      },
    },
  },
  {
    path: 'privacy-policy',
    loadComponent: () =>
      import('./features/legal/privacy/privacy.component').then((m) => m.PrivacyComponent),
    data: {
      seo: {
        title: 'Privacy Policy — OyeInterview',
        description:
          'How OyeInterview collects, uses, and protects your data — including payment processing by Cashfree.',
        path: '/privacy-policy',
      },
    },
  },
  {
    path: 'refund-policy',
    loadComponent: () =>
      import('./features/legal/refund/refund.component').then((m) => m.RefundComponent),
    data: {
      seo: {
        title: 'Refund & Cancellation Policy — OyeInterview',
        description:
          'How cancellations and refunds work for OyeInterview prepaid plans (no auto-renewal).',
        path: '/refund-policy',
      },
    },
  },
  {
    path: 'shipping-policy',
    loadComponent: () =>
      import('./features/legal/shipping/shipping.component').then((m) => m.ShippingComponent),
    data: {
      seo: {
        title: 'Shipping & Delivery Policy — OyeInterview',
        description:
          'OyeInterview is a digital service: no physical shipping, plans are activated instantly after payment.',
        path: '/shipping-policy',
      },
    },
  },
  { path: 'delivery-policy', redirectTo: 'shipping-policy', pathMatch: 'full' },
  {
    path: 'about',
    loadComponent: () =>
      import('./features/info/about/about.component').then((m) => m.AboutComponent),
    data: {
      seo: {
        title: 'About us — OyeInterview',
        description:
          'Who operates OyeInterview, an AI interview practice and preparation assistant, and how to reach us.',
        path: '/about',
      },
    },
  },
  { path: 'about-us', redirectTo: 'about', pathMatch: 'full' },
  {
    path: 'pricing',
    loadComponent: () =>
      import('./features/pricing/pricing.component').then((m) => m.PricingComponent),
    data: {
      seo: {
        title: 'Pricing — OyeInterview AI Interview Assistant',
        description:
          'OyeInterview plans: one-time prepaid payments for a fixed number of days. No auto-renewal, no stored cards.',
        path: '/pricing',
      },
    },
  },
  {
    path: 'contact',
    loadComponent: () =>
      import('./features/contact/contact.component').then((m) => m.ContactComponent),
    data: {
      seo: {
        title: 'Contact Us — OyeInterview',
        description:
          'Contact OyeInterview support for billing, subscription, and product questions.',
        path: '/contact',
      },
    },
  },
  { path: '**', redirectTo: '' },
];
