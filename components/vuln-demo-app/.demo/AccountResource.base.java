package com.redhat.demo.accounts;

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
}
