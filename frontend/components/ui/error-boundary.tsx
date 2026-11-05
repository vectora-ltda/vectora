"use client";

/**
 * ErrorBoundary — captura erros de render de uma subárvore e mostra um
 * `ErrorBanner` recuperável, em vez de derrubar a rota inteira (o
 * errorComponent do router). Usado ao redor de conteúdo code-split (dialogs de
 * settings) para que uma falha de chunk/render fique contida e o resto do app
 * siga funcionando.
 */

import { Component, type ErrorInfo, type ReactNode } from "react";

import { ErrorBanner } from "@/components/ui/error-banner";

interface Props {
  children: ReactNode;
  /** Mensagem amigável; default usa a do erro. */
  fallbackMessage?: string;
  /** Permite "tentar de novo" remontando os filhos (reseta o estado de erro). */
  onReset?: () => void;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    // Logging estruturado para diagnóstico (não derruba o app).
    console.error("ErrorBoundary capturou erro de render:", error, info);
  }

  handleReset = (): void => {
    this.setState({ error: null });
    this.props.onReset?.();
  };

  render(): ReactNode {
    const { error } = this.state;
    if (error) {
      return (
        <div className="p-4">
          <ErrorBanner
            message={this.props.fallbackMessage ?? error.message}
            onRetry={this.handleReset}
          />
        </div>
      );
    }
    return this.props.children;
  }
}
