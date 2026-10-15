# accounts-api

The demo application: a small Quarkus REST service for retail banking accounts
and statements. It exists to carry one specific, realistic security bug.

## The bug

`AccountResource.statements()` evaluates account ownership and discards the
result:

```java
if (!account.ownerId().equals(jwt.getSubject())) {
    LOG.warnf("ownership mismatch: subject=%s account=%s owner=%s", ...);
}   // <- no return
return Response.ok(repository.statementsFor(...)).build();
```

Any authenticated customer can read any account's statements. `CWE-639`,
`OWASP A01:2021`.

Chosen because:

- **SAST cannot see it.** No taint flow, no dangerous sink, no injection. The
  check is present and the Java is valid. There is nothing to pattern-match.
- **The tests pass.** `AccountResourceTest` covers the owner's happy path, a
  404, and date filtering. There is no cross-tenant test — which is the real
  reason this class of bug ships.
- **`get()` ten lines above does it correctly**, returning 403. The asymmetry
  makes it read as what it is: a lost early return.
- **It reads in four seconds on a slide.**

There is a second, lesser finding — `DateTimeParseException`'s message and type
going back to the client (`CWE-209`) — so the model has something to rank.

## Layout

```
src/main/java/.../AccountResource.java   the vulnerable version
src/test/java/.../AccountResourceTest.java  three tests, all green
.demo/AccountResource.base.java          before the feature existed
.demo/AccountResource.fixed.java         with the fix
.demo/01-introduce-statements-endpoint.diff  the reviewed diff (vulnerable)
.demo/02-fix-ownership-check.diff            the reviewed diff (clean)
Containerfile                            multi-stage Quarkus JVM build
```

The `.demo/` variants are what `scripts/04-seed-gitea.sh` and the
`demo-push-*.sh` scripts use to construct the git history on stage. The diffs
are also the input to `scripts/capture-replay.sh`.

## Running it locally

```sh
mvn quarkus:dev
```

Then, with a JWT whose `sub` is `user-alice`:

```sh
# Alice's own account - correct, 200
curl -H "Authorization: Bearer $ALICE" localhost:8080/accounts/ACC-1001/statements

# Bob's account - should be 403, returns 200 and Bob's transactions
curl -H "Authorization: Bearer $ALICE" localhost:8080/accounts/ACC-2002/statements
```

The second call is the demo.
