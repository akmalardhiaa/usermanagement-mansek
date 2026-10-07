package com.hcportal.adservice.auth;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.List;
import java.util.Map;

import org.junit.jupiter.api.Test;

class RoleMappingTest {

    private static final Map<String, List<String>> CONFIGURED = Map.of(
            "HC_REQUESTER", List.of("CN=HC Officers,OU=Groups,DC=corp,DC=example,DC=com"),
            "SYSTEM_ADMIN", List.of("HC Portal Admins"),
            "OPS_OPERATOR", List.of(),
            "AUDITOR", List.of("CN=Internal Audit"));

    @Test
    void matchesAFullDnRegardlessOfCaseAndSpacing() {
        List<String> roles = RoleMapping.rolesFromGroups(
                List.of("cn=hc officers, ou=Groups, dc=corp, dc=example, dc=com"), CONFIGURED);
        assertThat(roles).containsExactly("HC_REQUESTER");
    }

    @Test
    void matchesABareNameAgainstTheCnAndAPrefixAgainstTheDn() {
        List<String> roles = RoleMapping.rolesFromGroups(List.of(
                "CN=HC Portal Admins,OU=Groups,DC=corp,DC=example,DC=com",
                "CN=Internal Audit,OU=Groups,DC=corp,DC=example,DC=com"), CONFIGURED);
        assertThat(roles).containsExactly("SYSTEM_ADMIN", "AUDITOR");
    }

    @Test
    void givesNothingForGroupsThatOnlyLookAlike() {
        assertThat(RoleMapping.rolesFromGroups(List.of(
                "CN=HC Officers Archive,OU=Groups,DC=corp,DC=example,DC=com",
                "CN=Users,OU=HC Portal Admins,DC=corp"), CONFIGURED)).isEmpty();
    }
}
