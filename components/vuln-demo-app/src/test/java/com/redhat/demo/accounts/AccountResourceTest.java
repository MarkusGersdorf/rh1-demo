package com.redhat.demo.accounts;

import static io.restassured.RestAssured.given;
import static org.hamcrest.CoreMatchers.is;

import io.quarkus.test.junit.QuarkusTest;
import io.quarkus.test.security.jwt.Claim;
import io.quarkus.test.security.TestSecurity;
import io.quarkus.test.security.jwt.JwtSecurity;
import org.junit.jupiter.api.Test;

/**
 * The tests that shipped with RHONE-4412.
 *
 * Note what is here: the owner's happy path, and a 404. Note what is NOT here:
 * any assertion that a customer cannot read someone else's statements. That
 * omission is not a contrivance for the demo - it is the single most common
 * reason broken access control reaches production.
 */
@QuarkusTest
class AccountResourceTest {

    @Test
    @TestSecurity(user = "user-alice", roles = "customer")
    @JwtSecurity(claims = { @Claim(key = "sub", value = "user-alice") })
    void ownerCanListOwnStatements() {
        given()
          .when().get("/accounts/ACC-1001/statements")
          .then().statusCode(200)
          .body("accountId", is("ACC-1001"))
          .body("count", is(3));
    }

    @Test
    @TestSecurity(user = "user-alice", roles = "customer")
    @JwtSecurity(claims = { @Claim(key = "sub", value = "user-alice") })
    void unknownAccountIsNotFound() {
        given()
          .when().get("/accounts/ACC-9999/statements")
          .then().statusCode(404);
    }

    @Test
    @TestSecurity(user = "user-alice", roles = "customer")
    @JwtSecurity(claims = { @Claim(key = "sub", value = "user-alice") })
    void dateFilterNarrowsResults() {
        given()
          .when().get("/accounts/ACC-1001/statements?from=2026-09-05")
          .then().statusCode(200)
          .body("count", is(2));
    }
}
