/** Decide o destino convergente de uma pull request durante a rotação. */

function parseRotationMetadata(body) {
  const metadata = String(body ?? "").match(
    /<!-- rotation: previous_maintenance_branch=([^;]+); previous_maintenance_milestone=([^;]+); previous_development_milestone=([^ ]+) -->/,
  );
  if (!metadata) return null;
  return {
    maintenanceBranch: metadata[1],
    maintenanceMilestone: metadata[2],
    developmentMilestone: metadata[3],
  };
}

function findMigrationTargets(config, milestones) {
  if (!config?.maintenance?.milestone || !config?.development?.milestone) {
    return null;
  }
  const maintenance = milestones.find(
    ({ title }) => title === config.maintenance.milestone,
  );
  const development = milestones.find(
    ({ title }) => title === config.development.milestone,
  );
  if (!maintenance || !development) return null;
  return { maintenance, development };
}

function migrationUpdates(pr, migration, targets) {
  if (
    !pr?.base?.ref ||
    !migration ||
    !targets?.maintenance ||
    !targets?.development
  ) {
    return [];
  }
  const updates = [];
  if (pr.base.ref !== migration.base) {
    updates.push({ kind: "base", value: migration.base });
  }
  const target =
    migration.milestone === targets.maintenance.title
      ? targets.maintenance
      : targets.development;
  if (pr.milestone?.number !== target.number) {
    updates.push({ kind: "milestone", value: target.number });
  }
  return updates;
}

async function applyMigrationUpdates(pr, migration, targets, api) {
  const updates = migrationUpdates(pr, migration, targets);
  for (const update of updates) {
    if (update.kind === "base") {
      await api.pulls.update({
        owner: api.owner,
        repo: api.repo,
        pull_number: pr.number,
        base: update.value,
      });
    } else {
      await api.issues.update({
        owner: api.owner,
        repo: api.repo,
        issue_number: pr.number,
        milestone: update.value,
      });
    }
  }
  return updates;
}

function classifyPullRequest(pr, config, previous) {
  if (
    !pr?.base?.ref ||
    !config?.development?.branch ||
    !config?.development?.milestone ||
    !config?.maintenance?.branch ||
    !config?.maintenance?.milestone ||
    !previous?.maintenanceBranch ||
    !previous?.maintenanceMilestone ||
    !previous?.developmentMilestone
  ) {
    return null;
  }
  const isMaintenancePullRequest =
    pr.base.ref === previous.maintenanceBranch ||
    (pr.base.ref === config.maintenance.branch &&
      pr.milestone?.title === previous.maintenanceMilestone);
  const isDevelopmentPullRequest =
    !isMaintenancePullRequest &&
    pr.base.ref === config.development.branch &&
    pr.milestone?.title === previous.developmentMilestone;

  if (!isMaintenancePullRequest && !isDevelopmentPullRequest) return null;

  return {
    base: isMaintenancePullRequest
      ? config.maintenance.branch
      : config.development.branch,
    milestone: isMaintenancePullRequest
      ? config.maintenance.milestone
      : config.development.milestone,
  };
}

module.exports = {
  classifyPullRequest,
  applyMigrationUpdates,
  findMigrationTargets,
  migrationUpdates,
  parseRotationMetadata,
};
