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
- [ ] abrir a categoria Browser no runtime web e confirmar o estado de indisponibilidade.

## Superfície de configurações

A superfície oficial de configurações é o formulário Vectora compartilhado. O
adapter `WebContentsView` permanece isolado para runtimes que realmente
forneçam uma página interna compatível; Electron puro não deve ser tratado como
se trouxesse a aplicação `chrome://settings` do Chrome.

- [ ] abrir as configurações pelo Browser Workbench no perfil A;
- [ ] abrir as mesmas configurações em `Settings > Workbenches`;
- [ ] confirmar que ambas as entradas renderizam o mesmo formulário;
- [ ] confirmar que o estado da abertura não é salvo no `windows-store`;
- [ ] redimensionar o modal e confirmar rolagem interna sem overflow horizontal;
- [ ] pressionar Escape e confirmar fechamento com retorno do foco;
- [ ] abrir Settings global e confirmar que nenhum adapter nativo permanece visível;
- [ ] quando o adapter nativo estiver habilitado pelo runtime, confirmar hide antes de destroy;
- [ ] quando o runtime não suportar a página interna, confirmar o fallback explícito para o formulário Vectora.

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
versão do Electron estiver registrada e qualquer rota interna sem suporte tiver
fallback explícito para a superfície Vectora.
