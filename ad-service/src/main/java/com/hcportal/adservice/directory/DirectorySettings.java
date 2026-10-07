package com.hcportal.adservice.directory;

import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Optional;

/**
 * What the service account may do, read from the same environment as the rest.
 *
 * Two safety rails sit in front of every write, checked at the moment of the
 * write rather than once at start-up:
 *
 *   1. AD_LDAP_WRITE_ENABLED must be true. Until it is, every write refuses
 *      and reads keep working — the state to run in while a deployment is
 *      being verified.
 *   2. The object being changed, and any OU it is moved into, must sit inside
 *      AD_MANAGED_OUS. Group membership is bounded more tightly: only the
 *      exact group DNs of the access catalogue (AD_ACCESS_GROUP_*).
 */
public record DirectorySettings(
        Optional<String> bindDn,
        String bindPassword,
        boolean writeEnabled,
        List<String> managedOus,
        List<String> catalogueGroups,
        boolean nestedGroups,
        int pageSize) {

    public static final List<String> ENV_NAMES = List.of(
            "AD_BIND_DN", "AD_BIND_PASSWORD", "AD_LDAP_WRITE_ENABLED", "AD_MANAGED_OUS",
            "AD_ACCESS_GROUP_BASE", "AD_ACCESS_GROUP_ENGINEERING", "AD_ACCESS_GROUP_FINANCE",
            "AD_ACCESS_GROUP_SECURITY", "LDAP_NESTED_GROUPS", "LDAP_PAGE_SIZE");

    private static final List<String> CATALOGUE_ENV = List.of(
            "AD_ACCESS_GROUP_BASE", "AD_ACCESS_GROUP_ENGINEERING", "AD_ACCESS_GROUP_FINANCE", "AD_ACCESS_GROUP_SECURITY");

    public static DirectorySettings from(Map<String, String> env) {
        String bindDn = trimmed(env, "AD_BIND_DN");
        boolean writeEnabled = "true".equals(trimmed(env, "AD_LDAP_WRITE_ENABLED").toLowerCase(Locale.ROOT));
        List<String> managed = split(trimmed(env, "AD_MANAGED_OUS"));
        if (writeEnabled && managed.isEmpty()) {
            throw new IllegalStateException("AD_MANAGED_OUS wajib diisi saat AD_LDAP_WRITE_ENABLED=true.");
        }
        int pageSize = 200;
        String rawPage = trimmed(env, "LDAP_PAGE_SIZE");
        if (!rawPage.isEmpty()) pageSize = Integer.parseInt(rawPage);

        return new DirectorySettings(
                bindDn.isEmpty() ? Optional.empty() : Optional.of(bindDn),
                env.getOrDefault("AD_BIND_PASSWORD", ""),
                writeEnabled,
                managed,
                CATALOGUE_ENV.stream().map(name -> trimmed(env, name)).filter(value -> !value.isEmpty()).distinct().toList(),
                "true".equals(trimmed(env, "LDAP_NESTED_GROUPS").toLowerCase(Locale.ROOT)),
                pageSize);
    }

    private static List<String> split(String value) {
        return Arrays.stream(value.split(";")).map(String::trim).filter(part -> !part.isEmpty()).toList();
    }

    private static String trimmed(Map<String, String> env, String name) {
        String value = env.get(name);
        return value == null ? "" : value.trim();
    }
}
