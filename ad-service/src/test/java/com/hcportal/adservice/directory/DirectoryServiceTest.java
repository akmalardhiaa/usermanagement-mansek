package com.hcportal.adservice.directory;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.security.SecureRandom;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

import com.unboundid.ldap.listener.InMemoryDirectoryServer;
import com.unboundid.ldap.listener.InMemoryDirectoryServerConfig;
import com.unboundid.ldap.listener.interceptor.InMemoryInterceptedAddRequest;
import com.unboundid.ldap.listener.interceptor.InMemoryOperationInterceptor;
import com.unboundid.ldap.sdk.AddRequest;
import com.unboundid.ldap.sdk.Entry;
import com.unboundid.ldap.sdk.LDAPConnection;

/**
 * Every directory operation against a real LDAP server held in memory.
 *
 * AD gives each new object an objectGUID; the interceptor does the same here,
 * so accounts created by the code under test can be found again by GUID. AD
 * also maintains memberOf, which this server does not — group changes are
 * therefore checked on the group's `member` attribute.
 */
class DirectoryServiceTest {

    private static final String BASE = "DC=corp,DC=example,DC=com";
    private static final String KARYAWAN = "OU=Karyawan," + BASE;
    private static final String FINANCE = "OU=Finance,OU=Divisi," + KARYAWAN;
    private static final String KARANTINA = "OU=Karantina," + BASE;
    private static final String LUAR = "OU=Luar," + BASE;
    private static final String HC_BASE = "CN=HC-Base,OU=Groups," + BASE;
    private static final String HC_FINANCE = "CN=HC-Finance,OU=Groups," + BASE;
    private static final String OTHER = "CN=Other,OU=Groups," + BASE;
    private static final String CISO = "CN=IT Security Approvers,OU=Groups," + BASE;
    private static final String SVC = "CN=svc-hc-portal,OU=Service," + BASE;

    private static final SecureRandom RANDOM = new SecureRandom();
    private InMemoryDirectoryServer server;

    @BeforeEach
    void startDirectory() throws Exception {
        InMemoryDirectoryServerConfig config = new InMemoryDirectoryServerConfig(BASE);
        config.setSchema(null);
        config.addInMemoryOperationInterceptor(new InMemoryOperationInterceptor() {
            @Override
            public void processAddRequest(InMemoryInterceptedAddRequest request) {
                AddRequest add = request.getRequest().duplicate();
                if (!add.hasAttribute("objectGUID")) {
                    byte[] guid = new byte[16];
                    RANDOM.nextBytes(guid);
                    add.addAttribute("objectGUID", guid);
                }
                request.setRequest(add);
            }
        });
        server = new InMemoryDirectoryServer(config);
        server.add("dn: " + BASE, "objectClass: domain", "dc: corp");
        for (String ou : List.of("OU=Karyawan", "OU=Divisi,OU=Karyawan", "OU=Finance,OU=Divisi,OU=Karyawan",
                "OU=Karantina", "OU=Groups", "OU=Pimpinan", "OU=Luar", "OU=Service")) {
            server.add("dn: " + ou + "," + BASE, "objectClass: organizationalUnit");
        }
        for (String group : List.of(HC_BASE, HC_FINANCE, OTHER, CISO)) {
            server.add("dn: " + group, "objectClass: group");
        }
        server.add("dn: " + SVC, "objectClass: user", "sAMAccountName: svc-hc-portal", "userPassword: Svc!2026");
        seed("dn: CN=Bagus Nugroho,OU=Pimpinan," + BASE, "objectClass: user", "sAMAccountName: bagus.nugroho",
                "displayName: Bagus Nugroho", "mail: bagus.nugroho@corp.example.com", "userAccountControl: 512",
                "memberOf: " + CISO);
        seed("dn: CN=Rina Kartika,OU=Pimpinan," + BASE, "objectClass: user", "sAMAccountName: rina.ciso",
                "displayName: Rina Kartika", "mail: rina.ciso@corp.example.com", "userAccountControl: 512",
                "memberOf: " + CISO);
        seed("dn: CN=Budi Nonaktif,OU=Pimpinan," + BASE, "objectClass: user", "sAMAccountName: budi.off",
                "displayName: Budi Nonaktif", "mail: budi@corp.example.com", "userAccountControl: 514",
                "memberOf: " + CISO);
        seed("dn: CN=Dewi Lestari," + FINANCE, "objectClass: user", "sAMAccountName: dewi.lestari",
                "displayName: Dewi Lestari", "mail: dewi.lestari@corp.example.com", "department: Finance",
                "title: Financial Analyst", "userAccountControl: 512",
                "manager: CN=Bagus Nugroho,OU=Pimpinan," + BASE, "memberOf: " + HC_BASE, "memberOf: " + OTHER);
        server.startListening();
    }

    /** An account as AD would hold it: with an objectGUID of its own. */
    private void seed(String... ldif) throws Exception {
        Entry entry = new Entry(ldif);
        byte[] guid = new byte[16];
        RANDOM.nextBytes(guid);
        entry.addAttribute("objectGUID", guid);
        server.add(entry);
    }

    @AfterEach
    void stopDirectory() {
        server.shutDown(true);
    }

    private DirectoryService service(String... overrides) {
        Map<String, String> env = new HashMap<>();
        env.put("AD_BIND_DN", SVC);
        env.put("AD_BIND_PASSWORD", "Svc!2026");
        env.put("AD_LDAP_WRITE_ENABLED", "true");
        env.put("AD_MANAGED_OUS", KARYAWAN + ";" + KARANTINA);
        env.put("AD_ACCESS_GROUP_BASE", HC_BASE);
        env.put("AD_ACCESS_GROUP_FINANCE", HC_FINANCE);
        env.put("LDAP_PAGE_SIZE", "2");
        for (int index = 0; index < overrides.length; index += 2) env.put(overrides[index], overrides[index + 1]);
        return new DirectoryService(Optional.of(BASE), DirectorySettings.from(env),
                () -> new LDAPConnection("localhost", server.getListenPort()));
    }

    private static DirectoryService.CreateSpec spec(String name, String ou) {
        return new DirectoryService.CreateSpec(name, name + "@corp.example.com", "Uji Coba", "Uji", "Coba",
                name + "@corp.example.com", "Accounting", "Accounting Staff", "bagus.nugroho", ou);
    }

    @Test
    void createsADisabledAccountWithItsManagerThenEnablesIt() throws Exception {
        DirectoryService directory = service();
        AccountState created = directory.createAccount(spec("uji.coba", FINANCE));

        assertThat(created.enabled()).isFalse();
        assertThat(DirectoryService.sameDn(created.ou(), FINANCE)).isTrue();
        assertThat(created.manager()).isEqualTo("bagus.nugroho");
        assertThat(created.userPrincipalName()).isEqualTo("uji.coba@corp.example.com");
        assertThat(created.objectGUID()).matches("[0-9a-f-]{36}");

        directory.setEnabled(created.objectGUID(), true);
        assertThat(directory.findByGuid(created.objectGUID()).orElseThrow().enabled()).isTrue();
        Entry stored = server.getEntry("CN=Uji Coba," + FINANCE);
        assertThat(stored.getAttributeValue("userAccountControl")).isEqualTo("512");
    }

    @Test
    void refusesWritesOutsideTheRails() {
        assertThatThrownBy(() -> service("AD_LDAP_WRITE_ENABLED", "false").createAccount(spec("uji.coba", FINANCE)))
                .isInstanceOf(AdException.class).hasMessageContaining("dimatikan");
        assertThatThrownBy(() -> service().createAccount(spec("uji.coba", LUAR)))
                .isInstanceOf(AdException.class).hasMessageContaining("AD_MANAGED_OUS");
        assertThatThrownBy(() -> service().createAccount(spec("dewi.lestari", FINANCE)))
                .extracting(error -> ((AdException) error).kind()).isEqualTo(AdException.Kind.CONFLICT);
        DirectoryService.CreateSpec noManager = new DirectoryService.CreateSpec("x.y", "x.y@corp", "X Y", "X", "Y",
                "x.y@corp", "Finance", "Staff", "tidak.ada", FINANCE);
        assertThatThrownBy(() -> service().createAccount(noManager))
                .extracting(error -> ((AdException) error).kind()).isEqualTo(AdException.Kind.NOT_FOUND);
    }

    @Test
    void changesAttributesAndRemovesBlankOnes() throws Exception {
        DirectoryService directory = service();
        String guid = directory.findByAccountName("dewi.lestari").orElseThrow().objectGUID();

        directory.setAttributes(guid, new DirectoryService.AttributePatch(null, "Treasury", "Treasury Officer", null, null));
        AccountState after = directory.findByGuid(guid).orElseThrow();
        assertThat(after.department()).isEqualTo("Treasury");
        assertThat(after.title()).isEqualTo("Treasury Officer");
        assertThat(after.manager()).isEqualTo("bagus.nugroho");

        directory.setAttributes(guid, new DirectoryService.AttributePatch(null, null, "", "", null));
        Entry stored = server.getEntry("CN=Dewi Lestari," + FINANCE);
        assertThat(stored.hasAttribute("title")).isFalse();
        assertThat(stored.hasAttribute("manager")).isFalse();
    }

    @Test
    void movesInsideTheManagedOusOnly() throws Exception {
        DirectoryService directory = service();
        String guid = directory.findByAccountName("dewi.lestari").orElseThrow().objectGUID();

        directory.moveToOu(guid, KARANTINA);
        assertThat(server.getEntry("CN=Dewi Lestari," + KARANTINA)).isNotNull();
        directory.moveToOu(guid, KARANTINA); // already there: nothing to do

        assertThatThrownBy(() -> directory.moveToOu(guid, LUAR)).hasMessageContaining("AD_MANAGED_OUS");
    }

    @Test
    void changesOnlyCatalogueGroupsAndToleratesRepeats() throws Exception {
        DirectoryService directory = service();
        String guid = directory.findByAccountName("dewi.lestari").orElseThrow().objectGUID();
        String dn = "CN=Dewi Lestari," + FINANCE;

        directory.changeGroups(guid, List.of(HC_FINANCE), List.of());
        directory.changeGroups(guid, List.of(HC_FINANCE), List.of()); // a retried step lands in the same place
        assertThat(server.getEntry(HC_FINANCE).getAttributeValues("member")).containsExactly(dn);

        directory.changeGroups(guid, List.of(), List.of(HC_FINANCE));
        directory.changeGroups(guid, List.of(), List.of(HC_FINANCE));
        assertThat(server.getEntry(HC_FINANCE).hasAttribute("member")).isFalse();

        assertThatThrownBy(() -> directory.changeGroups(guid, List.of(OTHER), List.of()))
                .hasMessageContaining("katalog akses");
    }

    @Test
    void listsEveryAccountAPageAtATimeWithCatalogueGroupsOnly() {
        List<AccountState> accounts = service().listAccounts(BASE);
        assertThat(accounts).extracting(AccountState::sAMAccountName)
                .contains("bagus.nugroho", "rina.ciso", "budi.off", "dewi.lestari", "svc-hc-portal");
        AccountState dewi = accounts.stream().filter(a -> a.sAMAccountName().equals("dewi.lestari")).findFirst().orElseThrow();
        assertThat(dewi.groups()).containsExactly(HC_BASE);
        assertThat(dewi.manager()).isEqualTo("bagus.nugroho");
    }

    @Test
    void groupMembersAreEnabledPeopleWithAMailbox() {
        assertThat(service().groupMembers(CISO)).extracting(AccountState::sAMAccountName)
                .containsExactly("bagus.nugroho", "rina.ciso");
        assertThatThrownBy(() -> service().groupMembers("CN=Tidak Ada,OU=Groups," + BASE))
                .extracting(error -> ((AdException) error).kind()).isEqualTo(AdException.Kind.NOT_FOUND);
    }

    @Test
    void reportsStandingForTheSessionRecheck() {
        DirectoryService.Standing dewi = service().standing("dewi.lestari");
        assertThat(dewi.found()).isTrue();
        assertThat(dewi.enabled()).isTrue();
        assertThat(dewi.memberOf()).contains(HC_BASE);
        assertThat(service().standing("budi.off").enabled()).isFalse();
        assertThat(service().standing("tidak.ada").found()).isFalse();
    }

    @Test
    void saysSoWhenThereIsNoServiceAccount() {
        assertThatThrownBy(() -> service("AD_BIND_DN", "").listAccounts(BASE))
                .extracting(error -> ((AdException) error).kind()).isEqualTo(AdException.Kind.NOT_CONFIGURED);
    }

    @Test
    void guidsRoundTripInTheCanonicalForm() {
        String guid = "0f8fad5b-d9cb-469f-a165-70867728950e";
        assertThat(Guids.fromBytes(Guids.toBytes(guid))).isEqualTo(guid);
        assertThatThrownBy(() -> Guids.toBytes("bukan-guid")).isInstanceOf(AdException.class);
    }
}
