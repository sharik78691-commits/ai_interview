/** Authentication models shared across the auth layer. */

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  provider: 'local' | 'google' | string;
  email_verified?: boolean;
}

export interface AuthStatus {
  authenticated: boolean;
  user: AuthUser | null;
}

export interface RegisterPayload {
  name: string;
  email: string;
  password: string;
}

export interface LoginPayload {
  email: string;
  password: string;
}
