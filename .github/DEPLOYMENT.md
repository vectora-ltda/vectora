# CI and deployment

This repository contains the Vectora desktop application. The public product
documentation is maintained separately in
[vectora-ltda/documentation](https://github.com/vectora-ltda/documentation)
and published at [docs.vectora.company](https://docs.vectora.company/).

## Desktop application

The `vectora.yml` workflow runs the application checks and creates native
installers. Its install-smoke jobs install the produced packages and launch the
installed app with `--tests` on supported runners. To publish the update
channel, it checks out the release publisher from the separate
`vectora-company/services` repository using `VECTORA_RELEASES_TOKEN`; Cloudflare
and R2 credentials are also required for publication.

The account, license, gateway, and update APIs remain remote product services.
This repository does not contain their Worker source code.
