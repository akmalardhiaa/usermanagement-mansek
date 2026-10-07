package com.hcportal.adservice.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.util.HashMap;
import java.util.Map;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.hcportal.adservice.config.AdSettings;
import com.hcportal.adservice.ldap.LdapConnector;
import com.unboundid.ldap.listener.InMemoryDirectoryServer;
import com.unboundid.ldap.listener.InMemoryDirectoryServerConfig;
import com.unboundid.ldap.listener.interceptor.InMemoryInterceptedSimpleBindRequest;
import com.unboundid.ldap.listener.interceptor.InMemoryOperationInterceptor;
import com.unboundid.ldap.sdk.LDAPConnection;
import com.unboundid.ldap.sdk.LDAPException;
import com.unboundid.ldap.sdk.ResultCode;
import com.unboundid.ldap.sdk.SimpleBindRequest;

/**
 * Sign-in against a real LDAP server held in memory.
 *
 * Active Directory accepts `user@domain` as the bind name; a standard LDAP
 * server does not. The interceptor below maps the UPN to the entry's DN the
 * way AD does, so the code under test binds exactly as it will in the office.
 */
class AuthServiceTest {

    private static final String BASE = "DC=corp,DC=example,DC=com";
    private static final String HC_GROUP = "CN=HC Officers,OU=Groups," + BASE;
    private static final String AYU_DN = "CN=Ayu Prameswari,OU=People," + BASE;
    private static final String BAGUS_DN = "CN=Bagus Nugroho,OU=People," + BASE;

    private InMemoryDirectoryServer server;
    private int binds;

    @BeforeEach
    void startDirectory() throws Exception {
        InMemoryDirectoryServerConfig config = new InMemoryDirectoryServerConfig(BASE);
        config.setSchema(null); // AD attributes, not a standard schema.
        config.addInMemoryOperationInterceptor(new InMemoryOperationInterceptor() {
            @Override
            public void processSimpleBindRequest(InMemoryInterceptedSimpleBindRequest request) throws LDAPException {
                binds++;
                String name = request.getRequest().getBindDN();
                String dn = switch (name) {
                    case "ayu.prameswari@corp.example.com" -> AYU_DN;
                    case "bagus.nugroho@corp.example.com" -> BAGUS_DN;
                    default -> name;
                };
                request.setRequest(new SimpleBindRequest(dn, request.getRequest().getPassword().stringValue()));
            }
        });
        server = new InMemoryDirectoryServer(config);
        server.add("dn: " + BASE, "objectClass: domain", "dc: corp");
        server.add("dn: OU=People," + BASE, "objectClass: organizationalUnit", "ou: People");
        server.add("dn: OU=Groups," + BASE, "objectClass: organizationalUnit", "ou: Groups");
        server.add("dn: " + AYU_DN, "objectClass: user", "sAMAccountName: ayu.prameswari",
                "userPrincipalName: ayu.prameswari@corp.example.com", "displayName: Ayu Prameswari",
                "mail: ayu.prameswari@corp.example.com", "department: Human Capital",
                "memberOf: " + HC_GROUP, "userPassword: Rahasia!2026");
        server.add("dn: " + BAGUS_DN, "objectClass: user", "sAMAccountName: bagus.nugroho",
                "userPrincipalName: bagus.nugroho@corp.example.com", "displayName: Bagus Nugroho",
                "mail: bagus.nugroho@corp.example.com", "userPassword: Manager!2026");
        server.startListening();
    }

    @AfterEach
    void stopDirectory() {
        server.shutDown(true);
    }

    private AuthService service() {
        Map<String, String> env = new HashMap<>();
        env.put("AD_SERVICE_TOKEN", "a-service-token-of-24-chars");
        env.put("AD_LDAP_URL", "ldaps://dc.corp.example.com:636");
        env.put("AD_BASE_DN", BASE);
        env.put("LDAP_DOMAIN", "corp.example.com");
        env.put("AD_GROUP_HC", HC_GROUP);
        AdSettings settings = AdSettings.from(env);
        // Plain LDAP to the in-memory server; the LDAPS connector is the only
        // part not exercised here.
        LdapConnector connector = () -> new LDAPConnection("localhost", server.getListenPort());
        return new AuthService(settings, connector);
    }

    @Test
    void signsInWithTheBareUsernameAndReadsTheRecord() throws Exception {
        AdUser user = service().authenticate("ayu.prameswari", "Rahasia!2026").orElseThrow();

        assertThat(user.id()).isEqualTo("ayu.prameswari");
        assertThat(user.email()).isEqualTo("ayu.prameswari@corp.example.com");
        assertThat(user.fullName()).isEqualTo("Ayu Prameswari");
        assertThat(user.department()).isEqualTo("Human Capital");
        assertThat(user.roles()).containsExactly("HC_REQUESTER");
    }

    @Test
    void aCorrectPasswordWithNoPortalGroupIsIdentityAndNothingMore() throws Exception {
        AdUser user = service().authenticate("bagus.nugroho", "Manager!2026").orElseThrow();
        assertThat(user.roles()).isEmpty();
    }

    @Test
    void aWrongPasswordIsNoUserNotAnError() throws Exception {
        assertThat(service().authenticate("ayu.prameswari", "salah")).isEmpty();
    }

    @Test
    void anEmptyPasswordNeverReachesTheDirectory() throws Exception {
        // An empty simple-bind password is an anonymous bind, which succeeds.
        assertThat(service().authenticate("ayu.prameswari", "")).isEmpty();
        assertThat(binds).isZero();
    }

    @Test
    void aDirectoryThatCannotBeReachedIsReportedAsSuch() {
        Map<String, String> env = new HashMap<>();
        env.put("AD_SERVICE_TOKEN", "a-service-token-of-24-chars");
        env.put("AD_LDAP_URL", "ldaps://dc.corp.example.com");
        env.put("AD_BASE_DN", BASE);
        AuthService unreachable = new AuthService(AdSettings.from(env), () -> {
            throw new LDAPException(ResultCode.CONNECT_ERROR, "connect refused");
        });
        assertThatThrownBy(() -> unreachable.authenticate("ayu.prameswari", "Rahasia!2026"))
                .isInstanceOf(AuthService.DirectoryUnavailableException.class);
    }

    @Test
    void theDemoAccountsAnswerOnlyWhenAskedFor() throws Exception {
        Map<String, String> env = new HashMap<>();
        env.put("AD_SERVICE_TOKEN", "a-service-token-of-24-chars");
        AuthService nothing = new AuthService(AdSettings.from(env), () -> null);
        assertThatThrownBy(() -> nothing.authenticate("admin", "admin12345"))
                .isInstanceOf(AuthService.NotConfiguredException.class);

        env.put("DEMO_LOGIN", "on");
        AuthService demo = new AuthService(AdSettings.from(env), () -> null);
        assertThat(demo.authenticate("admin", "admin12345")).hasValueSatisfying(
                user -> assertThat(user.roles()).contains("SYSTEM_ADMIN", "HC_REQUESTER"));
        assertThat(demo.authenticate("admin", "salah")).isEmpty();
    }
}
