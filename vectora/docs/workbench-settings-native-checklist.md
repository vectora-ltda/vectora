# Checklist manual das configurações nativas

Esta lista valida o comportamento que não pode ser comprovado por jsdom ou pelos
fakes do Electron. Ela deve ser executada em cada sistema operacional suportado
antes de declarar o contrato de settings concluído.

## Preparação

Use um workspace de teste com duas sessões, dois perfis Browser e pelo menos uma
aba HTTP e uma aba HTTPS. Registre a versão do Electron e o sistema operacional.

- [ ] iniciar o Vectora em modo desktop;
- [ ] confirmar que as duas sessões recebem `profileId` diferentes;
- [ ] abrir `Settings > Workbenches` sem sessão ativa;
- [ ] confirmar o estado vazio para opções que exigem sessão;
- [ ] abrir a categoria Browser no runtime web e confirmar o fallback Vectora.

## Superfície nativa

Valide a view nativa no Browser Workbench, mantendo o foco e a sessão observados
durante cada passo.

- [ ] abrir a superfície nativa no perfil A;
- [ ] confirmar que a rota inicial é `chrome://settings`;
- [ ] testar somente as rotas permitidas pelo registry Electron;
- [ ] confirmar rejeição de `chrome://settings/flags`, `help` e outras rotas não listadas;
- [ ] redimensionar o painel e confirmar que a view acompanha o container;
- [ ] recolher a workbench e confirmar que a view fica invisível;
- [ ] pressionar Escape com foco dentro da view e confirmar fechamento;
- [ ] fechar a superfície e confirmar hide antes de destroy;
- [ ] abrir Settings global e confirmar que nenhuma view nativa permanece visível;
- [ ] reabrir a aba e confirmar restauração da URL e do histórico.

## Isolamento e limpeza

Os testes seguintes verificam que dados sensíveis permanecem no perfil correto e
que as ações destrutivas informam exatamente o que será removido.

- [ ] gravar cookie no perfil A e confirmar ausência no perfil B;
- [ ] salvar uma credencial no perfil A e confirmar ausência no perfil B;
- [ ] remover um cookie por domínio e confirmar feedback;
- [ ] limpar storage, cache e credenciais separadamente;
- [ ] confirmar que a limpeza do perfil A não altera o perfil B;
- [ ] autorizar e negar uma permissão por origem;
- [ ] abrir um popup permitido e confirmar que ele vira uma aba gerenciada;
- [ ] abrir um popup não permitido e confirmar que ele é bloqueado;
- [ ] interromper uma limpeza e confirmar mensagem de erro sem estado falso de sucesso.

## Critério de aprovação

A checklist só pode ser aprovada quando todos os itens aplicáveis passarem, a
versão do Electron estiver registrada e qualquer rota que falhar for removida da
allowlist ou receber fallback explícito.
