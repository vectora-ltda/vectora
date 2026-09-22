const assert = require("node:assert/strict");
const test = require("node:test");

const { classifyPullRequest } = require("./migrate_release_line_prs.js");

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
