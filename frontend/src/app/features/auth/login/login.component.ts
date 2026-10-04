import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AuthService } from '../../../core/auth/auth.service';

@Component({
  selector: 'app-login',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent implements OnInit {
  private auth = inject(AuthService);
  private router = inject(Router);
  private route = inject(ActivatedRoute);

  email = '';
  password = '';
  error = '';
  info = '';
  loading = false;

  ngOnInit(): void {
    const params = this.route.snapshot.queryParamMap;
    if (params.get('expired') === '1') {
      this.info = 'Your session has expired. Please sign in again.';
    }
    const oauthError = params.get('error');
    if (oauthError) {
      this.error = 'Unable to sign in with Google.';
    }
  }

  get googleUrl(): string {
    return this.auth.googleLoginUrl();
  }

  submit(): void {
    this.error = '';
    this.info = '';
    if (!this.email.trim() || !this.password) {
      this.error = 'Please enter your email and password.';
      return;
    }
    this.loading = true;
    this.auth.login({ email: this.email.trim(), password: this.password }).subscribe({
      next: () => {
        this.loading = false;
        const redirect = this.route.snapshot.queryParamMap.get('redirect') || '/dashboard';
        this.router.navigateByUrl(redirect);
      },
      error: (err) => {
        this.loading = false;
        this.error = AuthService.friendlyError(err);
      },
    });
  }
}
