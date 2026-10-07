package com.hcportal.adservice.ldap;

import com.unboundid.ldap.sdk.Filter;

/**
 * LDAP search filters, built from values that are always escaped.
 *
 * A username with `*`, `(`, `)` or `\` cannot change the shape of a filter:
 * the SDK's equality filter escapes them (RFC 4515), so `*` is never "every
 * object".
 */
public final class LdapFilters {

    private LdapFilters() {}

    /** The user a sign-in names: by UPN, or by account name. */
    public static Filter signInUser(String bindName, String accountName) {
        return Filter.createANDFilter(
                Filter.createEqualityFilter("objectClass", "user"),
                Filter.createORFilter(
                        Filter.createEqualityFilter("userPrincipalName", bindName),
                        Filter.createEqualityFilter("sAMAccountName", accountName)));
    }
}
