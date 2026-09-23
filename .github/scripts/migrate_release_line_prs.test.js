const assert = require("node:assert/strict");
const test = require("node:test");

const {
  classifyPullRequest,
  applyMigrationUpdates,
  findMigrationTargets,
  migrationUpdates,
  parseRotationMetadata,
  releaseLineTransition,
} = require("./migrate_release_line_prs.js");

const config = {
  development: { branch: "master", milestone: "0.3" },
  maintenance: { branch: "release/0.2", milestone: "0.2.x" },
};
const previous = {
  maintenanceBranch: "release/0.1",
  maintenanceMilestone: "0.1.x",
  developmentMilestone: "0.2",
};

test("migra uma PR de manutenção antiga para a nova linha", () => {
  assert.deepEqual(
    classifyPullRequest(
      { base: { ref: "release/0.1" }, milestone: { title: "0.1.x" } },
      config,
      previous,
    ),
    { base: "release/0.2", milestone: "0.2.x" },
  );
});

test("recupera uma PR parcialmente migrada com milestone antiga", () => {
  assert.deepEqual(
    classifyPullRequest(
      { base: { ref: "release/0.2" }, milestone: { title: "0.1.x" } },
      config,
      previous,
    ),
    { base: "release/0.2", milestone: "0.2.x" },
  );
});

test("migra uma PR de desenvolvimento para a próxima milestone", () => {
  assert.deepEqual(
    classifyPullRequest(
      { base: { ref: "master" }, milestone: { title: "0.2" } },
      config,
      previous,
    ),
    { base: "master", milestone: "0.3" },
  );
});

test("ignora uma PR fora das linhas ativas", () => {
  assert.equal(
    classifyPullRequest(
      { base: { ref: "feat/outro" }, milestone: null },
      config,
      previous,
    ),
    null,
  );
});

test("retorna nulo para uma PR ausente", () => {
  assert.equal(classifyPullRequest(null, config, previous), null);
});

test("retorna nulo para objetos de configuração vazios", () => {
  assert.equal(classifyPullRequest({}, {}, {}), null);
});

test("rejeita metadados de rotação ausentes ou inválidos", () => {
  assert.equal(parseRotationMetadata(""), null);
  assert.equal(parseRotationMetadata("texto sem marcador"), null);
});

test("rejeita metadados de rotação nulos", () => {
  assert.equal(parseRotationMetadata(null), null);
});

test("deriva a rotação somente de configurações versionadas", () => {
  assert.deepEqual(
    releaseLineTransition(
      {
        development: { branch: "master", milestone: "0.2" },
        maintenance: { branch: "release/0.1", milestone: "0.1.x" },
      },
      config,
    ),
    previous,
  );
});

test("rejeita uma configuração que não representa rotação", () => {
  assert.equal(releaseLineTransition(config, config), null);
  assert.equal(
    releaseLineTransition(
      {
        development: { branch: "feature/falsa", milestone: "0.2" },
        maintenance: { branch: "release/0.1", milestone: "0.1.x" },
      },
      config,
    ),
    null,
  );
});

test("rejeita milestones ativas incompletas", () => {
  assert.equal(findMigrationTargets(config, []), null);
  assert.equal(findMigrationTargets({}, []), null);
});

test("produz somente as atualizações que ainda faltam", () => {
  const pr = { base: { ref: "release/0.1" }, milestone: { number: 10 } };
  const migration = { base: "release/0.2", milestone: "0.2.x" };
  const targets = {
    maintenance: { title: "0.2.x", number: 20 },
    development: { title: "0.3", number: 30 },
  };

  assert.deepEqual(migrationUpdates(pr, migration, targets), [
    { kind: "base", value: "release/0.2" },
    { kind: "milestone", value: 20 },
  ]);
});

test("aplica a base e a milestone usando as operações correspondentes", async () => {
  const calls = [];
  const api = {
    owner: "vectora-ltda",
    repo: "vectora",
    pulls: { update: async (payload) => calls.push(["base", payload]) },
    issues: { update: async (payload) => calls.push(["milestone", payload]) },
  };
  await applyMigrationUpdates(
    { number: 42, base: { ref: "release/0.1" }, milestone: { number: 10 } },
    { base: "release/0.2", milestone: "0.2.x" },
    {
      maintenance: { title: "0.2.x", number: 20 },
      development: { title: "0.3", number: 30 },
    },
    api,
  );
  assert.deepEqual(calls, [
    [
      "base",
      {
        owner: "vectora-ltda",
        repo: "vectora",
        pull_number: 42,
        base: "release/0.2",
      },
    ],
    [
      "milestone",
      {
        owner: "vectora-ltda",
        repo: "vectora",
        issue_number: 42,
        milestone: 20,
      },
    ],
  ]);
});

test("propaga falha da API para não confirmar migração incompleta", async () => {
  const api = {
    owner: "vectora-ltda",
    repo: "vectora",
    pulls: {
      update: async () => {
        throw new Error("base rejeitada");
      },
    },
    issues: { update: async () => undefined },
  };
  await assert.rejects(
    applyMigrationUpdates(
      { number: 42, base: { ref: "release/0.1" }, milestone: { number: 10 } },
      { base: "release/0.2", milestone: "0.2.x" },
      {
        maintenance: { title: "0.2.x", number: 20 },
        development: { title: "0.3", number: 30 },
      },
      api,
    ),
    /base rejeitada/,
  );
});
