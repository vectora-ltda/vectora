# Release artifact signing and verification

The release pipeline publishes desktop installers, detached integrity files,
and the update manifests consumed by `electron-updater`. Signing is optional:
missing signing credentials never make a release build silently claim that an
artifact is trusted by the operating system.

## Windows

`WIN_CERTIFICATE_BASE64` and `WIN_CERTIFICATE_PASSWORD` can provide a
PFX-backed Authenticode identity. CI signs bundled Windows sidecars before
Electron packaging and lets `electron-builder` sign the final app and installer
before it calculates `latest.yml` and `.blockmap`.

For internal testing only, `utils/signing/generate-windows-cert.ps1` creates a
self-signed certificate and a Base64 PFX payload. The generated directory is
ignored by Git. Never distribute its private key or describe its signature as
public publisher trust.

A self-signed certificate is suitable only for internal testing after its
public certificate has been installed as trusted on each test machine. It does
not establish public publisher trust or remove SmartScreen warnings. Public
distribution needs a certificate and signing service trusted by Windows.

## macOS

With all three Apple credentials (`APPLE_ID`,
`APPLE_APP_SPECIFIC_PASSWORD`, and `APPLE_TEAM_ID`), CI uses the available
Developer ID identity and notarizes the app. Without the complete set, CI
applies ad-hoc signatures to Mach-O sidecars and the app. Ad-hoc signing is not
Developer ID signing and does not notarize the download. macOS may block the
first launch of a downloaded build; use Finder's **Open** confirmation or
remove quarantine from a trusted local copy with `xattr -cr Vectora.app`.

## Linux and integrity checks

Every release gets a `SHA256SUMS` file. When `GPG_PRIVATE_KEY` is configured,
CI also publishes detached ASCII-armored signatures for artifacts and the
checksum file, plus the corresponding public key. `GPG_PASSPHRASE` is optional
for an unencrypted CI key; protect the private key in GitHub Actions secrets
and publish its fingerprint through a separate trusted channel.

Verify a downloaded file from its release directory:

```sh
sha256sum --check SHA256SUMS
gpg --import vectora-release-key.asc
gpg --verify SHA256SUMS.asc SHA256SUMS
gpg --verify Vectora-0.3.1-linux-x64.AppImage.asc Vectora-0.3.1-linux-x64.AppImage
```

The `.asc` and checksum files are sidecars. CI does not rewrite installers
after `electron-builder` creates their SHA-512 update manifest and blockmap, so
the differential updater continues to validate the original installer bytes.
