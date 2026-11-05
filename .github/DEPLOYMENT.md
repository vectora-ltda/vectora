# CI and deployment

This repository contains the Vectora desktop application and the public Hugo
documentation site. Their workflows are independent: `vectora.yml` validates
and packages the app, while `docs.yml` builds and deploys the documentation.

## Documentation site

The `docs.yml` workflow builds `docs/` for pull requests and deploys it to
Vercel after pushes to `master`. A manual run can retry a production deploy, but
the job is restricted to `master`. The deploy is skipped when Vercel
credentials are not configured.

Required repository or `Production` environment secrets are `VERCEL_ORG_ID`,
`VERCEL_PROJECT_ID_DOCS`, and `VERCEL_TOKEN`.

## Desktop application

The `vectora.yml` workflow runs the application checks and creates native
installers. Its install-smoke jobs install the produced packages and launch the
installed app with `--tests` on supported runners. To publish the update
channel, it checks out the release publisher from the separate
`vectora-ltda/services` repository using `VECTORA_RELEASES_TOKEN`; Cloudflare
and R2 credentials are also required for publication.

The account, license, gateway, and update APIs remain remote product services.
This repository does not contain their Worker source code.
