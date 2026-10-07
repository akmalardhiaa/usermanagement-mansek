package com.hcportal.adservice.auth;

import java.util.Arrays;
import java.util.List;
import java.util.Optional;

import com.hcportal.adservice.config.AdSettings;
import com.hcportal.adservice.ldap.LdapConnector;
import com.hcportal.adservice.ldap.LdapFilters;
import com.unboundid.ldap.sdk.LDAPConnection;
import com.unboundid.ldap.sdk.LDAPException;
import com.unboundid.ldap.sdk.ResultCode;
import com.unboundid.ldap.sdk.SearchResult;
import com.unboundid.ldap.sdk.SearchResultEntry;
import com.unboundid.ldap.sdk.SearchScope;

/**
 * Checks a person's password against Active Directory.
 *
 * Their own username and password are used to bind: if AD accepts the bind,
 * they are who they say they are, and this service never stores the password.
 * Name, email, department and groups are then read from their own record, as
 * them. A successful bind is identity only; what they may do in the portal
 * comes from their groups (RoleMapping), and the portal refuses anyone with
 * no portal role.
 */
public final class AuthService {

    /** The directory could not answer — not "wrong password". */
    public static final class DirectoryUnavailableException extends Exception {
        public DirectoryUnavailableException(String message, Throwable cause) {
            super(message, cause);
        }
    }

    /** No AD and no demo: nobody can sign in. */
    public static final class NotConfiguredException extends Exception {
        public NotConfiguredException(String message) {
            super(message);
        }
    }

    private static final String[] ATTRIBUTES = {
        "displayName", "mail", "userPrincipalName", "sAMAccountName", "department", "memberOf"
    };

    private final AdSettings settings;
    private final LdapConnector connector;

    public AuthService(AdSettings settings, LdapConnector connector) {
        this.settings = settings;
        this.connector = connector;
    }

    /**
     * The person behind these credentials, or empty when they are wrong.
     *
     * An empty password is refused before AD is asked: LDAP treats a simple
     * bind with an empty password as an anonymous bind, which "succeeds".
     */
    public Optional<AdUser> authenticate(String username, String password)
            throws DirectoryUnavailableException, NotConfiguredException {
        String user = username == null ? "" : username.trim();
        if (user.isEmpty() || password == null || password.isEmpty()) return Optional.empty();

        if (settings.ldap().isPresent()) return viaLdap(user, password);
        if (settings.demoLogin()) return DemoDirectory.authenticate(user, password);
        throw new NotConfiguredException(
                "Login Active Directory belum dikonfigurasi (AD_LDAP_URL/LDAP_URL kosong) dan DEMO_LOGIN tidak dinyalakan.");
    }

    private Optional<AdUser> viaLdap(String username, String password) throws DirectoryUnavailableException {
        // AD accepts a UPN (name@domain) or the DOMAIN\name form. A bare
        // username gets the configured domain appended.
        String bindName = username.contains("@") || username.contains("\\") || settings.domain().isEmpty()
                ? username
                : username + "@" + settings.domain().get();
        String[] afterSlash = username.split("\\\\");
        String account = afterSlash[afterSlash.length - 1].split("@")[0];

        try (LDAPConnection connection = connector.open()) {
            try {
                connection.bind(bindName, password);
            } catch (LDAPException error) {
                if (error.getResultCode() == ResultCode.INVALID_CREDENTIALS) return Optional.empty();
                throw error;
            }

            SearchResult result = connection.search(
                    settings.ldap().get().baseDn(), SearchScope.SUB, LdapFilters.signInUser(bindName, account), ATTRIBUTES);
            SearchResultEntry entry = result.getSearchEntries().isEmpty() ? null : result.getSearchEntries().get(0);

            List<String> groups = entry == null || entry.getAttributeValues("memberOf") == null
                    ? List.of()
                    : Arrays.asList(entry.getAttributeValues("memberOf"));

            String id = value(entry, "sAMAccountName").orElse(account);
            return Optional.of(new AdUser(
                    id,
                    account,
                    value(entry, "mail").or(() -> value(entry, "userPrincipalName")).orElse(bindName),
                    value(entry, "displayName").orElse(account),
                    value(entry, "department").orElse(null),
                    RoleMapping.rolesFromGroups(groups, settings.roleGroups())));
        } catch (LDAPException error) {
            throw new DirectoryUnavailableException("Server Active Directory tidak bisa dihubungi: " + error.getMessage(), error);
        }
    }

    private static Optional<String> value(SearchResultEntry entry, String attribute) {
        if (entry == null) return Optional.empty();
        String value = entry.getAttributeValue(attribute);
        return value == null || value.isBlank() ? Optional.empty() : Optional.of(value);
    }
}
