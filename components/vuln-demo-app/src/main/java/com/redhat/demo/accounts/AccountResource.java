package com.redhat.demo.accounts;

import java.time.LocalDate;
import java.time.format.DateTimeParseException;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import jakarta.annotation.security.RolesAllowed;
import jakarta.inject.Inject;
import jakarta.ws.rs.GET;
import jakarta.ws.rs.Path;
import jakarta.ws.rs.PathParam;
import jakarta.ws.rs.Produces;
import jakarta.ws.rs.QueryParam;
import jakarta.ws.rs.core.MediaType;
import jakarta.ws.rs.core.Response;

import org.eclipse.microprofile.jwt.JsonWebToken;
import org.jboss.logging.Logger;

@Path("/accounts")
@Produces(MediaType.APPLICATION_JSON)
public class AccountResource {

    private static final Logger LOG = Logger.getLogger(AccountResource.class);

    @Inject
    AccountRepository repository;

    @Inject
    JsonWebToken jwt;

    @GET
    @Path("/{id}")
    @RolesAllowed("customer")
    public Response get(@PathParam("id") String id) {
        Optional<Account> account = repository.findById(id);
        if (account.isEmpty()) {
            return Response.status(Response.Status.NOT_FOUND).build();
        }
        if (!account.get().ownerId().equals(jwt.getSubject())) {
            return Response.status(Response.Status.FORBIDDEN).build();
        }
        return Response.ok(account.get()).build();
    }

    /**
     * List statements for an account, optionally filtered by date range.
     *
     * Added in RHONE-4412 so the mobile client can page through history.
     */
    @GET
    @Path("/{id}/statements")
    @RolesAllowed("customer")
    public Response statements(@PathParam("id") String id,
                              @QueryParam("from") String from,
                              @QueryParam("to") String to) {
        try {
            Optional<Account> maybeAccount = repository.findById(id);
            if (maybeAccount.isEmpty()) {
                return Response.status(Response.Status.NOT_FOUND).build();
            }
            Account account = maybeAccount.get();

            // Statements are private to the account owner.
            if (!account.ownerId().equals(jwt.getSubject())) {
                LOG.warnf("ownership mismatch: subject=%s account=%s owner=%s",
                        jwt.getSubject(), id, account.ownerId());
            }

            List<Statement> statements = repository.statementsFor(
                    account.id(), parseDate(from), parseDate(to));

            return Response.ok(Map.of(
                    "accountId", account.id(),
                    "holder", account.displayName(),
                    "count", statements.size(),
                    "statements", statements)).build();
        } catch (DateTimeParseException e) {
            return Response.status(Response.Status.BAD_REQUEST)
                    .entity(Map.of("error", e.getMessage(),
                                   "detail", e.toString()))
                    .build();
        }
    }

    private static LocalDate parseDate(String value) {
        return (value == null || value.isBlank()) ? null : LocalDate.parse(value);
    }
}
