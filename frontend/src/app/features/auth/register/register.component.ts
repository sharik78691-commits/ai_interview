import { Component, inject } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router, RouterLink } from '@angular/router';
import { AuthService } from '../../../core/auth/auth.service';

@Component({
  selector: 'app-register',
  standalone: true,
  imports: [FormsModule, RouterLink],
  templateUrl: './register.component.html',
  styleUrl: './register.component.css',
})
export class RegisterComponent {
  private auth = inject(AuthService);
  private router = inject(Router);

  name = '';
  email = '';
  password = '';
  confirm = '';
  error = '';
  loading = false;

  get googleUrl(): string {
    return this.auth.googleLoginUrl();
  }

  submit(): void {
    this.error = '';
    if (!this.email.trim() || !this.password) {
      this.error = 'Please fill in all fields.';
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
    this.auth
      .register({ name: this.name.trim(), email: this.email.trim(), password: this.password })
      .subscribe({
        next: () => {
          this.loading = false;
          this.router.navigate(['/dashboard']);
        },
        error: (err) => {
          this.loading = false;
          this.error = AuthService.friendlyError(err);
        },
      });
  }
}
