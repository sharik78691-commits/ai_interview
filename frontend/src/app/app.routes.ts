import { Routes } from '@angular/router';
import { authGuard, guestGuard } from './core/auth/auth.guard';
import { interviewGuard } from './core/guards/interview.guard';
import { DashboardComponent } from './features/dashboard/dashboard.component';
import { InterviewComponent } from './features/interview/interview.component';
import { LandingComponent } from './features/landing/landing.component';
import { SettingsComponent } from './features/settings/settings.component';
import { ForgotPasswordComponent } from './features/auth/forgot-password/forgot-password.component';
import { LoginComponent } from './features/auth/login/login.component';
import { RegisterComponent } from './features/auth/register/register.component';
import { ResetPasswordComponent } from './features/auth/reset-password/reset-password.component';
import { TermsComponent } from './features/legal/terms/terms.component';

export const routes: Routes = [
  {
    path: '',
    component: LandingComponent,
    // Public marketing page — the primary SEO target.
    data: {
      seo: {
        title: 'OyeInterview — AI Interview Assistant | Real-Time Interview Copilot',
        description:
          'OyeInterview is a real-time AI interview assistant that gives resume-aware answers, STAR stories and code hints during technical, behavioral and coding interviews.',
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
          'Sign in to OyeInterview to get real-time, resume-aware AI guidance for your next technical, behavioral or coding interview.',
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
          'Create a free OyeInterview account and start practising interviews with a real-time AI copilot that adapts to your resume and target role.',
        path: '/register',
      },
    },
  },
  {
    path: 'forgot-password',
    component: ForgotPasswordComponent,
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
    component: ResetPasswordComponent,
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
    component: DashboardComponent,
    canActivate: [authGuard],
    data: {
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
    component: InterviewComponent,
    canActivate: [authGuard, interviewGuard],
    data: {
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
    component: SettingsComponent,
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
  // Public legal page — indexed for SEO.
  {
    path: 'terms-and-conditions',
    component: TermsComponent,
    data: {
      seo: {
        title: 'Terms and Conditions — OyeInterview',
        description:
          'The terms and conditions for OyeInterview, the real-time AI interview assistant: account eligibility, licence, responsible use, and liability.',
        path: '/terms-and-conditions',
      },
    },
  },
  { path: '**', redirectTo: '' },
];
