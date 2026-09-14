export interface AuthToken {
  provider: string;
  scopes: readonly string[];
  expiresAt: number;
}
export interface AuthProvider {
  authorize(scopes: readonly string[]): Promise<AuthToken>;
  revoke(token: AuthToken): Promise<void>;
}
