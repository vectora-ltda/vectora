/**
 * Sinal one-shot: set pelo PreAuthWizard antes de navegar para /auth/signup
 * no caminho VPS (token Pro já validado), para que signup.tsx saiba que essa
 * chegada é legítima mesmo com has-users ainda false — sem o sinal, qualquer
 * chegada direta em /auth/signin ou /auth/signup com has-users false deve
 * voltar para o wizard (/onboarding), não mostrar o formulário antigo direto.
 */
let vpsGatePassedFlag = false;

export function signalVpsGatePassed(): void {
  vpsGatePassedFlag = true;
}

export function consumeVpsGatePassed(): boolean {
  const val = vpsGatePassedFlag;
  vpsGatePassedFlag = false;
  return val;
}
