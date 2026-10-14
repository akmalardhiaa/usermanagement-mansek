package com.hcportal.adservice.directory;

import java.util.List;

/**
 * An account as the portal needs it.
 *
 * `manager` is the manager's sAMAccountName, not their DN; `ou` is derived from
 * the DN; `groups` holds only what the access catalogue issues — a membership
 * added by hand is not this application's to report or to revoke.
 */
public record AccountState(
        String objectGUID,
        String sAMAccountName,
        String userPrincipalName,
        String displayName,
        String cn,
        String givenName,
        String sn,
        String mail,
        String department,
        String title,
        String manager,
        String company,
        String office,
        String description,
        String accountExpires,
        String distinguishedName,
        boolean enabled,
        String ou,
        List<String> groups) {}
