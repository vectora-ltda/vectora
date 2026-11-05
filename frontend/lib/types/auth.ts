/** Tipos de autenticação — espelham os schemas Python do backend. */

export type Role = "root" | "admin" | "member" | "viewer";

export interface AuthUser {
  id: string;
  /** Identidade do app (ex.: "bruno"; colisão vira "bruno#1234"). */
  username?: string;
  /** Opcional — o app local não usa email (pertence ao company/services). */
  email?: string;
  role: Role;
  /** Nome de exibição do usuário (UTF-8 livre, espaços permitidos). */
  name?: string;
  created_at: string;
  last_login_at?: string | null;
  /**
   * `exp` (epoch seconds) do access token corrente, repassado pelo
   * backend (ver `UserResponse.token_expires_at` em `src/api/schemas.py`).
   * O JWT em si é opaco para o JS (cookie httpOnly) — este campo é a única
   * forma de o frontend saber quando agendar o aviso "sessão expira em breve".
   */
  token_expires_at?: number | null;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
  user: AuthUser;
}

export interface SignupPayload {
  email: string;
  password: string;
  name?: string;
}

export interface SigninPayload {
  email: string;
  password: string;
}

export const ROLE_LABELS: Record<Role, string> = {
  root: "Root",
  admin: "Admin",
  member: "Member",
  viewer: "Viewer",
};

export const ROLE_COLORS: Record<Role, string> = {
  root: "text-yellow-400",
  admin: "text-blue-400",
  member: "text-green-400",
  viewer: "text-muted-foreground",
};
