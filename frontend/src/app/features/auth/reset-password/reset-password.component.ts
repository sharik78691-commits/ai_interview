import { Component, OnInit, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import { AuthService } from '../../../core/auth/auth.service';

@Component({
  selector: 'app-reset-password',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './reset-password.component.html',
  styleUrl: './reset-password.component.css',
})
export class ResetPasswordComponent implements OnInit {
  private auth = inject(AuthService);
  private route = inject(ActivatedRoute);
  private router = inject(Router);

  token = '';
  password = '';
  confirm = '';
  message = '';
  error = '';
  loading = false;

  ngOnInit(): void {
    this.token = this.route.snapshot.queryParamMap.get('token') || '';
    if (!this.token) {
      this.error = 'Password reset link is invalid or expired.';
    }
  }

  submit(): void {
    this.error = '';
    this.message = '';
    if (!this.token) {
      this.error = 'Password reset link is invalid or expired.';
      return;
    }
    if (this.password.length < 8) {
      this.error = 'Password must be at least 8 characters.';
      return;
    }
    if (!/[A-Za-z]/.test(this.password) || !/\d/.test(this.password)) {
      this.error = 'Password must contain at least one letter and one number.';
      return;
    }
    if (this.password !== this.confirm) {
      this.error = 'Passwords do not match.';
      return;
    }
    this.loading = true;
    this.auth.resetPassword(this.token, this.password).subscribe({
      next: (r) => {
        this.loading = false;
        this.message = r.message;
        setTimeout(() => this.router.navigate(['/login']), 1500);
      },
      error: (err) => {
        this.loading = false;
        this.error = AuthService.friendlyError(err);
      },
    });
  }
}
