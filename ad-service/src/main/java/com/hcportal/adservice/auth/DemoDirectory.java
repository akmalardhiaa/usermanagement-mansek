package com.hcportal.adservice.auth;

import java.util.List;
import java.util.Locale;
import java.util.Optional;

/**
 * The demo accounts, for a laptop with no directory.
 *
 * Only ever consulted when no AD address is configured AND DEMO_LOGIN=on, and
 * never in production (AdSettings refuses it there). The passwords are plain
 * on purpose: they are throwaway demo credentials. Only `admin` holds a portal
 * role; the others show what a manager sees — a refusal that points them to
 * the email they were sent.
 */
public final class DemoDirectory {

    private record DemoAccount(String username, String password, String fullName, String email,
                               String department, List<String> roles) {}

    private static final List<DemoAccount> ACCOUNTS = List.of(
            new DemoAccount("admin", "admin12345", "akmalardhia", "admin@example.com", "Human Capital",
                    List.of("SYSTEM_ADMIN", "HC_REQUESTER")),
            new DemoAccount("dimas", "dimas12345", "Dimas Anggara", "dimas.anggara@example.com", "Human Capital", List.of()),
            new DemoAccount("sarah", "sarah12345", "Sarah Wijaya", "sarah.wijaya@example.com", "IT — Engineering", List.of()),
            new DemoAccount("bagus", "bagus12345", "Bagus Nugroho", "bagus.nugroho@example.com", "IT — Security", List.of()),
            new DemoAccount("rina", "rina12345", "Rina Pratiwi", "rina.pratiwi@example.com", "Finance", List.of()));

    private DemoDirectory() {}

    public static Optional<AdUser> authenticate(String username, String password) {
        String key = username.trim().toLowerCase(Locale.ROOT);
        return ACCOUNTS.stream()
                .filter(account -> account.username().equals(key) || account.email().equalsIgnoreCase(key))
                .filter(account -> account.password().equals(password))
                .findFirst()
                .map(account -> new AdUser(account.username(), account.username(), account.email(),
                        account.fullName(), account.department(), account.roles()));
    }
}
