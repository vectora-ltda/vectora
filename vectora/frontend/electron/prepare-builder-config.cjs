const { readFileSync, writeFileSync } = require("node:fs");
const { parse, stringify } = require("yaml");

function configureMacSigning(source, appleCredentials) {
  const config = parse(source);
  if (!config.mac)
    throw new Error("electron-builder config is missing mac settings");

  const hasCompleteCredentials = [
    appleCredentials.appleId,
    appleCredentials.appSpecificPassword,
    appleCredentials.teamId,
  ].every((value) => typeof value === "string" && value.trim().length > 0);

  if (hasCompleteCredentials) {
    delete config.mac.identity;
    config.mac.notarize = true;
  } else {
    config.mac.identity = "-";
    delete config.mac.notarize;
  }

  return stringify(config);
}

if (require.main === module) {
  const [sourcePath, targetPath] = process.argv.slice(2);
  if (!sourcePath || !targetPath) {
    throw new Error("usage: prepare-builder-config.cjs SOURCE.yml TARGET.yml");
  }
  writeFileSync(
    targetPath,
    configureMacSigning(readFileSync(sourcePath, "utf8"), {
      appleId: process.env.APPLE_ID,
      appSpecificPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
      teamId: process.env.APPLE_TEAM_ID,
    }),
  );
}

module.exports = { configureMacSigning };
