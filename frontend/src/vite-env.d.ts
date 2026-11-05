/// <reference types="vite/client" />
/// <reference types="vite-plugin-pwa/client" />

interface ImportMetaEnv {
  readonly VITE_VECTORA_AUTH_REQUIRED?: string;
  readonly VITE_VECTORA_API_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
