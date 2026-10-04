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

export const routes: Routes = [
  { path: '', component: LandingComponent },
  // Auth pages: already-authenticated users are redirected to /dashboard.
  { path: 'login', component: LoginComponent, canActivate: [guestGuard] },
  { path: 'register', component: RegisterComponent, canActivate: [guestGuard] },
  { path: 'forgot-password', component: ForgotPasswordComponent, canActivate: [guestGuard] },
  { path: 'reset-password', component: ResetPasswordComponent },
  // Protected application routes.
  { path: 'dashboard', component: DashboardComponent, canActivate: [authGuard] },
  {
    path: 'interview',
    component: InterviewComponent,
    canActivate: [authGuard, interviewGuard],
  },
  { path: 'settings', component: SettingsComponent, canActivate: [authGuard] },
  { path: '**', redirectTo: '' },
];
