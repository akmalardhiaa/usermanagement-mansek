package com.hcportal.adservice.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.Test;

class AdSettingsTest {

    private static final String TOKEN = "a-service-token-of-24-chars";

    private static Map<String, String> env(String... pairs) {
        Map<String, String> values = new HashMap<>();
        values.put("AD_SERVICE_TOKEN", TOKEN);
        for (int index = 0; index < pairs.length; index += 2) values.put(pairs[index], pairs[index + 1]);
        return values;
    }

    @Test
    void refusesToStartWithoutAServiceToken() {
        Map<String, String> values = env();
        values.put("AD_SERVICE_TOKEN", "short");
        assertThatThrownBy(() -> AdSettings.from(values)).hasMessageContaining("AD_SERVICE_TOKEN");
    }

    @Test
    void acceptsOnlyLdapsOn636() {
        assertThatThrownBy(() -> AdSettings.from(env("AD_LDAP_URL", "ldap://dc.corp.example.com", "AD_BASE_DN", "DC=corp")))
                .hasMessageContaining("ldaps://");
        assertThatThrownBy(() -> AdSettings.from(env("AD_LDAP_URL", "ldaps://dc.corp.example.com:3269", "AD_BASE_DN", "DC=corp")))
                .hasMessageContaining("636");

        AdSettings settings = AdSettings.from(env("AD_LDAP_URL", "ldaps://dc.corp.example.com", "AD_BASE_DN", "DC=corp"));
        assertThat(settings.ldap()).hasValueSatisfying(target -> {
            assertThat(target.host()).isEqualTo("dc.corp.example.com");
            assertThat(target.port()).isEqualTo(636);
        });
    }

    @Test
    void anEmptyAdLdapUrlFallsThroughToLdapUrl() {
        AdSettings settings = AdSettings.from(env("AD_LDAP_URL", " ", "LDAP_URL", "ldaps://dc.corp.example.com:636", "LDAP_BASE_DN", "DC=corp"));
        assertThat(settings.ldap()).isPresent();
    }

    @Test
    void neverOffersTheDemoAccountsInProduction() {
        assertThat(AdSettings.from(env("DEMO_LOGIN", "on")).demoLogin()).isTrue();
        assertThatThrownBy(() -> AdSettings.from(env("DEMO_LOGIN", "on", "APP_ENV", "production")))
                .hasMessageContaining("production");
        AdSettings production = AdSettings.from(env("DEMO_LOGIN", "on", "APP_ENV", "production",
                "AD_LDAP_URL", "ldaps://dc.corp.example.com", "AD_BASE_DN", "DC=corp"));
        assertThat(production.demoLogin()).isFalse();
    }

    @Test
    void readsTheAdminGroupFromEitherName() {
        AdSettings settings = AdSettings.from(env("AD_GROUP_ADMIN", "CN=Admins", "LDAP_ADMIN_GROUP", "CN=Old Admins"));
        assertThat(settings.roleGroups().get("SYSTEM_ADMIN")).containsExactly("CN=Admins", "CN=Old Admins");
    }
}
