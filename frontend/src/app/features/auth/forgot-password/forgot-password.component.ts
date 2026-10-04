import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AuthService } from '../../../core/auth/auth.service';

@Component({
  selector: 'app-forgot-password',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './forgot-password.component.html',
  styleUrl: './forgot-password.component.css',
})
export class ForgotPasswordComponent {
  private auth = inject(AuthService);

  email = '';
  message = '';
  error = '';
  loading = false;

  submit(): void {
    this.error = '';
    this.message = '';
    if (!this.email.trim()) {
      this.error = 'Please enter your email.';
      return;
    }
    this.loading = true;
    this.auth.forgotPassword(this.email.trim()).subscribe({
      next: (r) => {
        this.loading = false;
        // Generic message — never reveals whether the email exists.
        this.message = r.message;
      },
      error: (err) => {
        this.loading = false;
        this.error = AuthService.friendlyError(err);
      },
    });
  }
}
