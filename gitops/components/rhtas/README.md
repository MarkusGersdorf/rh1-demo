# Red Hat Trusted Artifact Signer

The `Securesign` CR stands up Fulcio, Rekor, CTlog and TUF.

Two things to know before the demo:

1. **There is no timestamp authority, on purpose.** The demo signs with a
   cosign key and never passes `--timestamp-server-url`, so a TSA adds a
   component and a certificate chain to get wrong and nothing to the story.
   If you add it back, the field is **`tsa`** — `timestampAuthority` is in no
   served version of the CRD, and server-side apply rejects an undeclared
   field by failing the entire Application, not just that field. Once `tsa`
   exists, `tsa.signer.certificateChain` is required: either point
   `certificateChainRef` at a secret you have actually created, or give it
   `rootCA`/`intermediateCA`/`leafCA` and let the operator generate one.
   `scripts/02-signing-setup.sh` still checks whether Securesign is stuck on
   `tsa-cert-chain`, as a net for exactly that case.

2. **The demo signs with a key, not keyless.** `cosign-keypair` in the
   `lightwell-inward` namespace, created by `scripts/02-signing-setup.sh`.
   Keyless signing with Fulcio and workload identity is the production
   pattern and the exact flags are commented in the `sign-and-attest` task —
   it is avoided here only because OIDC trust misconfiguration is a bad way to
   lose ten minutes on stage.

Either way the signature and the attestation go into this Rekor instance, so
the transparency-log part of the story is real. Show it in the Rekor Search UI:

```sh
oc get route -n trusted-artifact-signer rekor-search-ui -o jsonpath='{.spec.host}'
```
