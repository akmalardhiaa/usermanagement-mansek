package com.hcportal.adservice.config;

import java.util.HashMap;
import java.util.Map;

import org.springframework.boot.web.servlet.FilterRegistrationBean;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.env.Environment;

import com.hcportal.adservice.auth.AuthService;
import com.hcportal.adservice.directory.DirectoryService;
import com.hcportal.adservice.directory.DirectorySettings;
import com.hcportal.adservice.ldap.LdapConnector;
import com.hcportal.adservice.security.ServiceTokenFilter;

/**
 * Wiring. The settings are read from the environment once, at start-up: a
 * configuration that cannot work stops the service with its reason in the log,
 * rather than failing each sign-in later.
 */
@Configuration
public class ServiceConfig {

    private static final int LDAP_TIMEOUT_MILLIS = 8_000;

    /**
     * Read through Spring's Environment, which includes the process
     * environment — so a test can hand in its own values as properties.
     */
    @Bean
    AdSettings adSettings(Environment environment) {
        Map<String, String> values = new HashMap<>();
        for (String name : AdSettings.ENV_NAMES) {
            String value = environment.getProperty(name);
            if (value != null) values.put(name, value);
        }
        return AdSettings.from(values);
    }

    @Bean
    LdapConnector ldapConnector(AdSettings settings) {
        return settings.ldap()
                .map(target -> LdapConnector.ldaps(target, settings.caCertPath().orElse(null), LDAP_TIMEOUT_MILLIS))
                .orElse(() -> {
                    throw new IllegalStateException("Tidak ada domain controller yang dikonfigurasi.");
                });
    }

    @Bean
    AuthService authService(AdSettings settings, LdapConnector connector) {
        return new AuthService(settings, connector);
    }

    @Bean
    DirectorySettings directorySettings(Environment environment) {
        Map<String, String> values = new HashMap<>();
        for (String name : DirectorySettings.ENV_NAMES) {
            String value = environment.getProperty(name);
            if (value != null) values.put(name, value);
        }
        return DirectorySettings.from(values);
    }

    @Bean
    DirectoryService directoryService(AdSettings settings, DirectorySettings directory, LdapConnector connector) {
        return new DirectoryService(settings.ldap().map(AdSettings.LdapTarget::baseDn), directory, connector);
    }

    @Bean
    FilterRegistrationBean<ServiceTokenFilter> serviceTokenFilter(AdSettings settings) {
        FilterRegistrationBean<ServiceTokenFilter> registration = new FilterRegistrationBean<>(
                new ServiceTokenFilter(settings.serviceToken()));
        registration.addUrlPatterns("/v1/*");
        return registration;
    }
}
