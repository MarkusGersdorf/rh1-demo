package com.redhat.demo.accounts;

/**
 * A customer account. {@code ownerId} is the subject of the JWT that is allowed
 * to read it - cross-tenant access is the thing we must never permit.
 */
public record Account(String id, String ownerId, String displayName, String iban) {
}
