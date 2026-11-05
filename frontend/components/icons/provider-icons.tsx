/** Ícones oficiais (simplificados) dos provedores de LLM, usados no seletor de modelo. */

export function GeminiIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M12 2c0 4.4-1.1 7.4-3.2 9.5C6.7 13.6 4.2 14.7 0 14.7c4.4 0 7.4 1.1 9.5 3.2 2.1 2.1 3.2 5.1 3.2 9.5 0-4.4 1.1-7.4 3.2-9.5 2.1-2.1 5.1-3.2 9.5-3.2-4.4 0-7.4-1.1-9.5-3.2C13.1 9.4 12 6.4 12 2z"
        fill="currentColor"
        transform="translate(1.4 -2.7) scale(0.92)"
      />
    </svg>
  );
}

export function OpenAiIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M22.28 9.82a5.98 5.98 0 0 0-.52-4.91 6.05 6.05 0 0 0-6.51-2.9A6.07 6.07 0 0 0 10.8 0a6.06 6.06 0 0 0-5.78 4.2 5.98 5.98 0 0 0-4 2.9 6.05 6.05 0 0 0 .75 7.1 5.98 5.98 0 0 0 .51 4.92 6.05 6.05 0 0 0 6.52 2.9A6.06 6.06 0 0 0 13.2 24a6.06 6.06 0 0 0 5.78-4.21 5.98 5.98 0 0 0 4-2.9 6.05 6.05 0 0 0-.7-7.07ZM13.2 22.43a4.5 4.5 0 0 1-2.9-1.05l.14-.08 4.81-2.78a.78.78 0 0 0 .4-.69v-6.78l2.03 1.18a.07.07 0 0 1 .04.06v5.61a4.53 4.53 0 0 1-4.52 4.53ZM3.6 18.36a4.5 4.5 0 0 1-.54-3.04l.14.09 4.81 2.78a.78.78 0 0 0 .79 0l5.88-3.39v2.35a.08.08 0 0 1-.03.07l-4.86 2.81a4.53 4.53 0 0 1-6.19-1.67ZM2.34 7.9a4.5 4.5 0 0 1 2.36-1.98v5.71a.77.77 0 0 0 .39.68l5.88 3.39-2.03 1.18a.08.08 0 0 1-.07 0L3.98 14.07A4.53 4.53 0 0 1 2.34 7.9Zm16.73 3.88-5.88-3.4 2.03-1.17a.08.08 0 0 1 .07 0l4.89 2.82a4.52 4.52 0 0 1-.7 8.16v-5.71a.78.78 0 0 0-.41-.7Zm2.02-3.04-.14-.09-4.81-2.79a.79.79 0 0 0-.79 0L9.47 9.25V6.9a.07.07 0 0 1 .03-.07l4.86-2.8a4.52 4.52 0 0 1 6.73 4.7ZM8.36 12.85l-2.03-1.17a.08.08 0 0 1-.04-.06V5.99a4.52 4.52 0 0 1 7.42-3.47l-.14.08-4.81 2.78a.78.78 0 0 0-.4.69v6.78ZM9.47 10.4 12 8.94l2.53 1.46v2.92L12 14.78l-2.53-1.46Z"
        fill="currentColor"
      />
    </svg>
  );
}

export function AnthropicIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M13.83 3.5h3.3L24 20.5h-3.3l-6.87-17ZM6.87 3.5h3.4l6.87 17h-3.36l-1.4-3.6H4.97l-1.4 3.6H.21l6.66-17Zm-.86 10.6h4.46l-2.23-5.78-2.23 5.78Z"
        fill="currentColor"
      />
    </svg>
  );
}

export function CohereIcon({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <path
        d="M9.5 3C5.36 3 2 6.36 2 10.5S5.36 18 9.5 18H14a4.5 4.5 0 0 0 0-9H10a3 3 0 1 0 0 6h3.5a1.5 1.5 0 0 0 0-3H10"
        stroke="currentColor"
        strokeWidth="2.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function OllamaIcon({ className }: { className?: string }) {
  // Sem ícone de marca oficial simplificado — servidor local genérico
  // (Ollama roda no host do usuário, não é um provider de nuvem).
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <rect
        x="3"
        y="4"
        width="18"
        height="7"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="2"
      />
      <rect
        x="3"
        y="13"
        width="18"
        height="7"
        rx="1.5"
        stroke="currentColor"
        strokeWidth="2"
      />
      <circle cx="7" cy="7.5" r="1" fill="currentColor" />
      <circle cx="7" cy="16.5" r="1" fill="currentColor" />
    </svg>
  );
}

export function OpenRouterIcon({ className }: { className?: string }) {
  // Sem ícone de marca oficial simplificado — proxy multi-provider
  // representado como setas convergindo pra um nó central (roteamento).
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      className={className}
      aria-hidden="true"
    >
      <circle cx="12" cy="12" r="2.5" fill="currentColor" />
      <path
        d="M12 9.5V4M12 14.5V20M9.5 12H4M14.5 12H20M7.6 7.6 4 4M16.4 7.6 20 4M7.6 16.4 4 20M16.4 16.4 20 20"
        stroke="currentColor"
        strokeWidth="1.6"
        strokeLinecap="round"
      />
    </svg>
  );
}

import type { ModelConfig } from "@/lib/config/deployment-config";

const PROVIDER_ICONS: Record<
  ModelConfig["provider"],
  React.ComponentType<{ className?: string }>
> = {
  "google-genai": GeminiIcon,
  openai: OpenAiIcon,
  anthropic: AnthropicIcon,
  cohere: CohereIcon,
  ollama: OllamaIcon,
  openrouter: OpenRouterIcon,
  nine_router: OpenRouterIcon,
  llamacpp: OllamaIcon,
  dmr: OllamaIcon,
};

/** Ícone do provedor para um dado provider — usado no seletor de modelo. */
export function ProviderIcon({
  provider,
  className,
}: {
  provider: ModelConfig["provider"];
  className?: string;
}) {
  const Icon = PROVIDER_ICONS[provider] ?? GeminiIcon;
  return <Icon className={className} />;
}
