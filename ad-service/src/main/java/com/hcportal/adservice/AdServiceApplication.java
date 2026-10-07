package com.hcportal.adservice;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

/**
 * The HC portal's Active Directory service.
 *
 * The portal (Python) never talks LDAP itself. Everything that touches the
 * directory — checking a password, reading an account, and from later stages
 * creating, moving and disabling accounts — goes through this service, over
 * an internal HTTP API guarded by a shared token. It is reachable only inside
 * the Podman network; nothing publishes its port.
 */
@SpringBootApplication
public class AdServiceApplication {

    public static void main(String[] args) {
        SpringApplication.run(AdServiceApplication.class, args);
    }
}
