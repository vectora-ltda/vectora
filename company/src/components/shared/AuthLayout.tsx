import { Link } from "@tanstack/react-router";
import { ArrowLeft } from "lucide-react";
import Logo from "#/components/shared/Logo";
import { m } from "#/paraglide/messages";

/**
 * AuthLayout — casca centralizada das telas de autenticação (login, signup).
 *
 * Card estreito (`max-w-sm`) centrado na viewport, com logo e título padrão.
 * Fonte única do shell para login e signup ficarem idênticos. Um link "voltar"
 * (seta ←) no canto superior esquerdo leva de volta para a home.
 */

interface AuthLayoutProps {
  heading: string;
  subheading?: React.ReactNode;
  children: React.ReactNode;
}

export default function AuthLayout({
  heading,
  subheading,
  children,
}: AuthLayoutProps) {
  return (
    <div className="relative flex min-h-screen items-center justify-center px-4">
      <Link
        to="/"
        className="absolute start-4 top-4 inline-flex items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground sm:start-6 sm:top-6"
      >
        {/* A seta permanece apontada para a esquerda por ser uma ação visual de retorno. */}
        <ArrowLeft className="h-4 w-4" />
        {m.nav_back()}
      </Link>

      <div className="w-full max-w-sm">
        <div className="mb-6 text-center">
          <Logo size="sm" className="justify-center" />
          <h1 className="mt-2 text-xl font-semibold text-foreground">
            {heading}
          </h1>
          {subheading}
        </div>
        {children}
      </div>
    </div>
  );
}
