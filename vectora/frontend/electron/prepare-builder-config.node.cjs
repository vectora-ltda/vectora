const assert = require("node:assert/strict");
const { readFileSync } = require("node:fs");
const test = require("node:test");
const { parse } = require("yaml");
const { configureMacSigning } = require("./prepare-builder-config.cjs");

const source = readFileSync(require.resolve("./electron-builder.yml"), "utf8");

test("macOS uses ad-hoc signing and skips notarization without credentials", () => {
  const config = parse(configureMacSigning(source, {}));
  assert.equal(config.mac.identity, "-");
  assert.equal("notarize" in config.mac, false);
});

test("macOS notarizes only when all Apple credentials are configured", () => {
  const credentials = {
    appleId: "developer@example.test",
    appSpecificPassword: "secret",
    teamId: "TEAM123456",
  };
  const config = parse(configureMacSigning(source, credentials));
  assert.equal("identity" in config.mac, false);
  assert.equal(config.mac.notarize, true);
});

test("partial Apple credentials cannot enable notarization", () => {
  const config = parse(
    configureMacSigning(source, { appleId: "developer@example.test" }),
  );
  assert.equal(config.mac.identity, "-");
  assert.equal("notarize" in config.mac, false);
});
