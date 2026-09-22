/** Decide o destino convergente de uma pull request durante a rotação. */

function classifyPullRequest(pr, config, previous) {
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

module.exports = { classifyPullRequest };
