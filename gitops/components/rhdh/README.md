# Red Hat Developer Hub

## Placeholders you must replace

- `CLUSTER_DOMAIN` — run `scripts/01-set-cluster-domain.sh`.
- `CHANGEME` in the catalog URLs — your fork of this repo, so RHDH can read
  the catalog and template over HTTPS.

## Catalog is loaded over HTTPS, not from a ConfigMap

`catalog.locations` points at raw.githubusercontent.com for your fork. That
means the catalog and the template live in git and are editable without
touching the cluster — but it also means **RHDH needs egress to GitHub**. On a
disconnected cluster, push this repo to the in-cluster Gitea instead and point
`catalog.locations` at `https://gitea-gitea.apps.<domain>/demo/rh1-demo/raw/branch/main/...`.

## The template needs a Gitea scaffolder action

`template-quarkus.yaml` uses `publish:gitea`. Whether that action is
registered depends on your RHDH version and enabled plugins. Check before you
rely on it:

```sh
oc -n rhdh exec deploy/backstage-rhdh -- \
  curl -s localhost:7007/api/scaffolder/v2/actions | grep -o 'publish:gitea' || \
  echo "publish:gitea NOT available"
```

If it is missing, do not fight it on the day. The walkthrough works fine
starting from the pre-seeded `demo/accounts-api` repo that
`scripts/04-seed-gitea.sh` creates — you simply show the template page as
"this is the golden path" and open the existing component instead of
scaffolding live. The demo loses nothing substantive; scaffolding is scene
setting, and the security gate is the content.

## The skeleton directory

`template-quarkus.yaml` references `./skeleton`, which is not included here —
a Backstage template fetches its skeleton relative to the template's own
location, which for an HTTPS-hosted template means it must also be hosted
alongside it. If you want live scaffolding to work end to end, copy
`components/vuln-demo-app/` to `gitops/components/rhdh/catalog/skeleton/` and
parameterise `pom.xml` and `catalog-info.yaml` with `${{ values.name }}`.
Otherwise leave it: the pre-seeded repo path above needs none of this.
