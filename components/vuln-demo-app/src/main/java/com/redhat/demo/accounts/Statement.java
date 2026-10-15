package com.redhat.demo.accounts;

import java.time.LocalDate;

public record Statement(String accountId, LocalDate date, String description,
                        String amount, String currency) {
}
