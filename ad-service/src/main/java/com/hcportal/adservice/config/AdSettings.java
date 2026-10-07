package com.hcportal.adservice.config;

import java.net.URI;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * Everything this service reads from the environment, read once and checked.
 *
 * The names are the ones the portal has always used (AD_LDAP_URL, LDAP_DOMAIN,
 * AD_GROUP_HC, …), so an office .env written for the earlier version keeps
 * meaning the same thing.
 */
public record AdSettings(
        Optional<LdapTarget> ldap,
        Optional<String> domain,
        Optional<String> caCertPath,
        Map<String, List<String>> roleGroups,
        boolean demoLogin,
        boolean production,
        String serviceToken) {

    /** Where the domain controller is: LDAPS on 636 only. */
    public record LdapTarget(String url, String host, int port, String baseDn) {}

    public static final List<String> PORTAL_ROLES = List.of("HC_REQUESTER", "SYSTEM_ADMIN", "OPS_OPERATOR", "AUDITOR");

    private static final Map<String, String> ROLE_GROUP_ENV = Map.of(
            "HC_REQUESTER", "AD_GROUP_HC",
            "SYSTEM_ADMIN", "AD_GROUP_ADMIN",
            "OPS_OPERATOR", "AD_GROUP_OPS",
            "AUDITOR", "AD_GROUP_AUDITOR");

    /** Every variable this service reads. */
    public static final List<String> ENV_NAMES = List.of(
            "APP_ENV", "AD_SERVICE_TOKEN", "AD_LDAP_URL", "LDAP_URL", "AD_BASE_DN", "LDAP_BASE_DN",
            "LDAP_DOMAIN", "LDAP_CA_CERT_PATH", "AD_GROUP_HC", "AD_GROUP_ADMIN", "AD_GROUP_OPS",
            "AD_GROUP_AUDITOR", "LDAP_ADMIN_GROUP", "DEMO_LOGIN");

    public static AdSettings from(Map<String, String> env) {
        boolean production = "production".equals(trimmed(env, "APP_ENV").toLowerCase(Locale.ROOT));

        String token = trimmed(env, "AD_SERVICE_TOKEN");
        if (token.length() < 24) {
            throw new IllegalStateException(
                    "AD_SERVICE_TOKEN wajib diisi, minimal 24 karakter acak. Nilai yang sama dipakai portal untuk memanggil layanan ini.");
        }

        String url = first(env, "AD_LDAP_URL", "LDAP_URL");
        Optional<LdapTarget> ldap = Optional.empty();
        if (!url.isEmpty()) {
            String baseDn = first(env, "AD_BASE_DN", "LDAP_BASE_DN");
            if (baseDn.isEmpty()) {
                throw new IllegalStateException("AD_BASE_DN (atau LDAP_BASE_DN) wajib diisi bersama AD_LDAP_URL.");
            }
            ldap = Optional.of(parseUrl(url, baseDn));
        } else if (production) {
            throw new IllegalStateException("AD_LDAP_URL/LDAP_URL wajib diisi di production.");
        }

        Map<String, List<String>> groups = new java.util.LinkedHashMap<>();
        for (String role : PORTAL_ROLES) {
            List<String> configured = new ArrayList<>();
            String value = trimmed(env, ROLE_GROUP_ENV.get(role));
            if (!value.isEmpty()) configured.add(value);
            if (role.equals("SYSTEM_ADMIN")) {
                String admin = trimmed(env, "LDAP_ADMIN_GROUP");
                if (!admin.isEmpty()) configured.add(admin);
            }
            groups.put(role, List.copyOf(configured));
        }

        String demo = trimmed(env, "DEMO_LOGIN").toLowerCase(Locale.ROOT);
        boolean demoLogin = !production && List.of("on", "true", "1", "yes").contains(demo);

        return new AdSettings(
                ldap,
                optional(trimmed(env, "LDAP_DOMAIN")),
                optional(trimmed(env, "LDAP_CA_CERT_PATH")),
                Map.copyOf(groups),
                demoLogin,
                production,
                token);
    }

    /** ldaps:// and port 636, or nothing: the user's password travels over this connection. */
    static LdapTarget parseUrl(String url, String baseDn) {
        URI uri;
        try {
            uri = URI.create(url);
        } catch (IllegalArgumentException error) {
            throw new IllegalStateException("AD_LDAP_URL/LDAP_URL=\"" + url + "\" bukan URL yang sah.", error);
        }
        if (!"ldaps".equalsIgnoreCase(uri.getScheme())) {
            throw new IllegalStateException(
                    "Login Active Directory hanya menerima ldaps://; koneksi LDAP tanpa TLS ditolak (diberi \"" + url + "\").");
        }
        if (uri.getHost() == null || uri.getHost().isBlank()) {
            throw new IllegalStateException("AD_LDAP_URL/LDAP_URL=\"" + url + "\" tidak menyebut host.");
        }
        int port = uri.getPort() == -1 ? 636 : uri.getPort();
        if (port != 636) {
            throw new IllegalStateException("Login Active Directory hanya menerima port 636 (diberi " + port + ").");
        }
        return new LdapTarget(url, uri.getHost(), port, baseDn);
    }

    private static String trimmed(Map<String, String> env, String name) {
        String value = env.get(name);
        return value == null ? "" : value.trim();
    }

    /** The first of the names that is set; `||`, not `??` — an empty value falls through. */
    private static String first(Map<String, String> env, String... names) {
        for (String name : names) {
            String value = trimmed(env, name);
            if (!value.isEmpty()) return value;
        }
        return "";
    }

    private static Optional<String> optional(String value) {
        return value.isEmpty() ? Optional.empty() : Optional.of(value);
    }
}
