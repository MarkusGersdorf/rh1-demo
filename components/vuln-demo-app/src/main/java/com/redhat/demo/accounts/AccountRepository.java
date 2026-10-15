package com.redhat.demo.accounts;

import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.stream.Collectors;

import jakarta.enterprise.context.ApplicationScoped;

/**
 * In-memory store. Two accounts owned by two different customers, which is the
 * minimum needed to demonstrate a cross-tenant read.
 */
@ApplicationScoped
public class AccountRepository {

    private static final Map<String, Account> ACCOUNTS = Map.of(
            "ACC-1001", new Account("ACC-1001", "user-alice", "Alice Henderson", "DE89 3704 0044 0532 0130 00"),
            "ACC-2002", new Account("ACC-2002", "user-bob", "Bob Ruiz", "DE44 5001 0517 5407 3249 31")
    );

    private static final List<Statement> STATEMENTS = List.of(
            new Statement("ACC-1001", LocalDate.of(2026, 9, 2), "Salary", "4210.00", "EUR"),
            new Statement("ACC-1001", LocalDate.of(2026, 9, 7), "Rent", "-1480.00", "EUR"),
            new Statement("ACC-1001", LocalDate.of(2026, 9, 19), "Transfer to savings", "-900.00", "EUR"),
            new Statement("ACC-2002", LocalDate.of(2026, 9, 3), "Consulting invoice 4471", "12750.00", "EUR"),
            new Statement("ACC-2002", LocalDate.of(2026, 9, 11), "Office lease", "-3100.00", "EUR")
    );

    public Optional<Account> findById(String id) {
        return Optional.ofNullable(ACCOUNTS.get(id));
    }

    public List<Statement> statementsFor(String accountId, LocalDate from, LocalDate to) {
        return STATEMENTS.stream()
                .filter(s -> s.accountId().equals(accountId))
                .filter(s -> from == null || !s.date().isBefore(from))
                .filter(s -> to == null || !s.date().isAfter(to))
                .collect(Collectors.toList());
    }
}
