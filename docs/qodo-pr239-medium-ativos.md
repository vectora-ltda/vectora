# Findings Medium ativos do Qodo no PR #239

Este relatório foi gerado a partir de todas as threads inline retornadas pela API GraphQL do GitHub para o PR #239. Ele inclui somente findings do Qodo que continuam com `isResolved=false` e `isOutdated=false`, e cujo comentário original contém o selo **Medium / Remediation recommended**.

Foram encontrados **22 findings Medium ativos**. Threads resolvidas ou obsoletas foram excluídas, mesmo que seus comentários antigos continuem visíveis no histórico do PR.

## 1. Atualizações de catálogo legado sem testes

- Thread: [4058559348](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4058559348)
- Local: `.github/workflows/edge.yml:355`
- Problema: o workflow adiciona três colunas de `mcp_catalog` e um índice de ranking por comandos de deployment, mas a suíte de regressão D1 não cobre esse caminho. Os testes de compatibilidade existentes verificam apenas `skills_catalog`, deixando sem cobertura a tabela legada, a idempotência e as falhas dos comandos.
- Remediação: adicionar testes que simulem um `mcp_catalog` legado, verifiquem a criação das colunas e do índice, confirmem que a execução repetida é idempotente e garantam que falhas interrompam o deployment.

## 2. Novo teste de cache mantém helpers sem tipagem

- Thread: [4068456597](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4068456597)
- Local: `vectora/tests/unit/test_registry_client.py:446`
- Problema: o teste não tipa o fixture `monkeypatch` nem seu retorno, e os métodos falsos de `Response` e `Client` também não têm tipos. Alterações futuras podem quebrar o protocolo assíncrono simulado sem que a verificação estática detecte.
- Remediação: tipar `monkeypatch` como `pytest.MonkeyPatch`, adicionar `-> None` ao teste e tipar parâmetros, retornos e gerenciadores assíncronos dos helpers.

## 3. Filtros vazios do catálogo sem cobertura

- Thread: [4077917142](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4077917142)
- Local: `services/src/registry/routes.ts:57`
- Problema: o endpoint aceita `q` e `category`, mas os testes não cobrem requisições como `?q=` e `?category=`. Mudanças na normalização ou no binding SQL podem alterar esse comportamento sem contrato testado.
- Remediação: adicionar testes separados para cada filtro vazio, verificando status e resposta esperados.

## 4. Regressões no agrupamento de workspaces passam despercebidas

- Thread: [4077917152](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4077917152)
- Local: `vectora/frontend/components/sidebar/__tests__/sidebar-collapse-layout.test.tsx:52`
- Problema: o teste substitui `groupThreadsByWorkspace` por um stub que sempre cria o mesmo grupo e ignora os workspaces fornecidos. As asserções deixam de exercitar correspondência, placeholders e threads órfãs da implementação real.
- Remediação: remover o mock de `sidebar-utils` e usar as implementações reais com fixtures de threads e workspaces.

## 5. Comentário de tratamento de erro preserva histórico de auditoria

- Thread: [4083492649](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083492649)
- Local: `services/src/index.ts:76`
- Problema: o inventário de módulos ainda descreve o handler global como resultado de uma auditoria datada. O comentário histórico ficou misturado à explicação atual da fronteira de exceções.
- Remediação: reescrever o comentário para explicar quais falhas são capturadas e como são reportadas, removendo datas e comparações históricas.

## 6. URL padrão dos buckets sem anotação de tipo

- Thread: [4083566630](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083566630)
- Local: `vectora/backend/services/memory_buckets.py:24`
- Problema: `DEFAULT_MEMORY_BUCKETS_URL` é uma constante pública sem anotação explícita.
- Remediação: declarar a constante com tipo `str`, preservando o valor atual.

## 7. Endpoints personalizados de buckets são ignorados

- Thread: [4083566662](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083566662)
- Local: `vectora/backend/services/memory_buckets.py:34`
- Problema: o código lê apenas `VECTORA_MEMORY_BUCKETS_URL`. Instalações existentes que ainda usam `VECTORA_RAG_LIBRARY_URL` caem silenciosamente no serviço público padrão.
- Remediação: usar `VECTORA_MEMORY_BUCKETS_URL`, depois `VECTORA_RAG_LIBRARY_URL` como fallback legado e, por fim, a URL padrão; adicionar testes de precedência.

## 8. Detalhes de uso sem teste de interface realista

- Thread: [4083611988](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083611988)
- Local: `vectora/frontend/components/chat/features/usage-popover.tsx:205`
- Problema: `UsagePopover` exibe janela de contexto, cota de mídia e uso do provedor, mas não há teste que entregue dados representativos e valide o diálogo renderizado.
- Remediação: criar teste RTL com tokens, modelo, cota de mídia e dados do provedor, abrir o popover e verificar totais, cotas e linhas do provedor.

## 9. Chamadores anônimos podem instalar buckets

- Thread: [4083929602](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083929602)
- Local: `vectora/backend/api/handlers/memory_buckets.py:35`
- Problema: a rota `/memory-buckets` não está nos prefixos autenticados do middleware. Requisições anônimas podem alcançar instalação e publicação, incluindo download e extração local.
- Remediação: registrar `/memory-buckets` como prefixo autenticado ou aplicar dependência de autenticação ao router; testar chamadas anônimas e autenticadas.

## 10. Falha na instalação de bucket aparece como sucesso

- Thread: [4083929610](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083929610)
- Local: `vectora/frontend/components/workbench/tabs/library-memory-buckets-section.tsx:25`
- Problema: `installBucket` não verifica `res.ok`, e qualquer payload cujo status não seja exatamente `error` pode marcar o card como instalado, inclusive uma resposta HTTP 500.
- Remediação: verificar `res.ok`, exigir `result.status === "installed"` e transformar respostas inválidas em erro localizado mantendo a possibilidade de retry.

## 11. Buscas rápidas mantêm buckets obsoletos

- Thread: [4083929620](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4083929620)
- Local: `vectora/frontend/components/workbench/tabs/library-memory-buckets-section.tsx:154`
- Problema: uma nova busca é descartada enquanto a anterior está carregando e nunca é refeita. A interface continua exibindo o resultado antigo até outra edição.
- Remediação: rastrear requisições por consulta ou geração, buscar a consulta mais recente após a anterior terminar e impedir respostas antigas de sobrescrever resultados novos.

## 12. Instalações existentes não recebem estado de sincronização

- Thread: [4085098893](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4085098893)
- Local: `services/migrations/0002_registry_sync_state.sql:5`
- Problema: a migration reutiliza a sequência `0002`, já usada por compatibilidade de issues. Bancos D1 existentes podem considerar a migration aplicada e ficar sem `registry_sync_state`.
- Remediação: usar a próxima sequência disponível, manter a criação da tabela como migration independente e testar upgrade de um banco que já possui o `0002` anterior.

## 13. Status malformado chega à interface

- Thread: [4085098917](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4085098917)
- Local: `vectora/backend/services/registry_client.py:214`
- Problema: `fetch_catalog_status` copia diretamente o JSON remoto para um `TypedDict`, sem validação em runtime. Valores incompatíveis podem atravessar as fronteiras de serviço e API.
- Remediação: substituir o `TypedDict` por um modelo Pydantic, validar a resposta remota e usar o modelo como contrato de resposta dos handlers.

## 14. Indisponibilidade do catálogo exibe estado antigo

- Thread: [4085098933](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4085098933)
- Local: `vectora/frontend/lib/stores/library-store.ts:222`
- Problema: o estado de status só é salvo quando a requisição do catálogo também tem sucesso. Em uma falha inicial ou posterior, a interface pode continuar como `never` ou `ready`.
- Remediação: resolver catálogo e status independentemente, sempre persistir o status retornado e testar falhas iniciais e de refresh.

## 15. Sincronização de skills informa sucesso falso

- Thread: [4085098954](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4085098954)
- Local: `services/src/registry/discovery.ts:486`
- Problema: falhas individuais de escrita são suprimidas e a fonte é sempre marcada como `ready`, mesmo com catálogo parcial ou vazio.
- Remediação: comparar entradas aceitas com entradas persistidas, marcar `ready` somente quando todas forem gravadas e usar `unavailable` em falhas parciais; adicionar regressão de falha de escrita.

## 16. Teste wide não renderiza o modelo

- Thread: [4089064378](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4089064378)
- Local: `vectora/frontend/components/chat/__tests__/chat-input.test.tsx:367`
- Problema: o teste não fornece `agentConfig`, `onAgentConfigChange` nem `modelId`. O wrapper existe, mas o seletor de modelo e o limite de contexto não são renderizados.
- Remediação: fornecer props representativas e verificar que o seletor e o controle de contexto permanecem dentro de `wide-model-controls`.

## 17. Teste de resize do chat usa ponto simétrico

- Thread: [4089195050](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4089195050)
- Local: `vectora/frontend/lib/layout/__tests__/resize-geometry.test.ts:28`
- Problema: o ponteiro `300` fica exatamente no meio do retângulo `100..500`, produzindo `200` para os dois lados. O teste não detecta inversão do lado físico.
- Remediação: usar posição assimétrica, como `250`, e esperar larguras diferentes para os lados esquerdo e direito; testar também a seleção da borda na sessão.

## 18. Balanceamento de largura do composer sem teste efetivo

- Thread: [4089257990](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4089257990)
- Local: `vectora/frontend/components/chat/__tests__/chat-input.test.tsx:373`
- Problema: o teste verifica apenas classes. Não define `clientWidth` positivo nem dispara o `ResizeObserver`, portanto `measureAndBalance` sai pelo guard de largura zero.
- Remediação: simular medidas positivas, disparar o observer e verificar as larguras balanceadas dos grupos compacto e wide.

## 19. Labels alterados permanecem truncados

- Thread: [4089257995](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4089257995)
- Local: `vectora/frontend/components/chat/chat-input.tsx:231`
- Problema: `ControlGroup` fixa larguras em pixels e observa apenas o grupo. Ao trocar permission, effort ou model por label de outro tamanho, a largura anterior permanece até o container redimensionar.
- Remediação: medir novamente quando labels descendentes mudarem, observar mutações relevantes ou trocar o esquema imperativo por CSS responsivo; adicionar teste de troca de label com largura constante.

## 20. Controls escalados ultrapassam o footer

- Thread: [4089257997](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4089257997)
- Local: `vectora/frontend/components/chat/chat-input.tsx:210`
- Problema: o cálculo sempre subtrai 16px para dois `gap-2`, embora o gap use `rem` e acompanhe a escala da interface. Em escalas diferentes de 100%, controles podem ser cortados ou sobrar espaço.
- Remediação: ler `columnGap` computado e subtrair `columnGap * (items.length - 1)`; testar com uma escala de fonte diferente da padrão.

## 21. Controles da rail permanecem em português

- Thread: [4094439454](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4094439454)
- Local: `vectora/frontend/components/layout/ide-mode-layout.tsx:130`
- Problema: `IdeModeLayout` e `SessionPage` passam os textos literais `Abrir chat` e `Abrir workbench`. Em inglês ou espanhol, leitores de tela anunciam português.
- Remediação: adicionar chaves de tradução em todos os locales e substituir os literais por chamadas de `m()`; atualizar o teste para consultar o label localizado.

## 22. Regressão do navegador recolhido sem cobertura

- Thread: [4094918780](https://github.com/vectora-ltda/vectora/pull/239#discussion_r4094918780)
- Local: `vectora/frontend/components/workbench/tabs/browser-tab.tsx:655`
- Problema: o novo ramo de medição esconde a view nativa e zera seus bounds quando a coluna é recolhida, mas nenhum teste verifica `setVisible(false)` e largura/altura zero. A cobertura atual testa apenas a prop explícita `visible`.
- Remediação: adicionar teste separado com container de tamanho zero, verificar visibilidade falsa e bounds zerados, e manter um caso separado para bounds positivos e visibilidade restaurada.
