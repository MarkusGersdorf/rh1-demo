# Red Hat Trusted Artifact Signer

The `Securesign` CR stands up Fulcio, Rekor, CTlog, TUF and the timestamp
authority.

Two things to know before the demo:

1. **The TSA signer needs a certificate chain.** The CR references a secret
   `tsa-cert-chain`. The operator can generate one for you depending on
   version; if your `Securesign` sits in a pending state complaining about it,
   either drop the `timestampAuthority` block (the demo does not use
   timestamping) or generate a chain per the RHTAS docs. `scripts/02-signing-setup.sh`
   checks for this and tells you which case you are in.

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
