package com.hcportal.adservice.directory;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;

import com.hcportal.adservice.ldap.LdapConnector;
import com.unboundid.asn1.ASN1OctetString;
import com.unboundid.ldap.sdk.Attribute;
import com.unboundid.ldap.sdk.DN;
import com.unboundid.ldap.sdk.Entry;
import com.unboundid.ldap.sdk.Filter;
import com.unboundid.ldap.sdk.LDAPConnection;
import com.unboundid.ldap.sdk.LDAPException;
import com.unboundid.ldap.sdk.Modification;
import com.unboundid.ldap.sdk.ModificationType;
import com.unboundid.ldap.sdk.RDN;
import com.unboundid.ldap.sdk.ResultCode;
import com.unboundid.ldap.sdk.SearchRequest;
import com.unboundid.ldap.sdk.SearchResult;
import com.unboundid.ldap.sdk.SearchResultEntry;
import com.unboundid.ldap.sdk.SearchScope;
import com.unboundid.ldap.sdk.controls.SimplePagedResultsControl;

/**
 * Reads and writes user accounts, as the service account (AD_BIND_DN).
 *
 * A connection is opened, bound and closed per operation: the worker performs a
 * handful per request, and a pooled socket that died quietly between two steps
 * would fail the second for reasons that have nothing to do with it.
 *
 * Every write passes the rails in DirectorySettings at the moment it runs.
 * Writes are idempotent where the directory allows: adding a member already
 * there, or removing one already gone, is success — so a step retried after a
 * timeout lands in the same place.
 */
public final class DirectoryService {

    private static final String[] ATTRIBUTES = {
        "objectGUID", "sAMAccountName", "userPrincipalName", "displayName", "givenName", "sn",
        "mail", "department", "title", "manager", "userAccountControl", "memberOf"
    };
    private static final int NORMAL_ACCOUNT = 0x200;
    private static final int ACCOUNTDISABLE = 0x2;

    /** What the portal asks for when it creates an account. */
    public record CreateSpec(String sAMAccountName, String userPrincipalName, String displayName,
                             String givenName, String sn, String mail, String department,
                             String title, String manager, String ou) {}

    /** Null leaves an attribute alone; blank removes it. */
    public record AttributePatch(String displayName, String department, String title, String manager, String mail) {}

    /** Whether a signed-in person still should be: for the portal's session recheck. */
    public record Standing(boolean found, boolean enabled, List<String> memberOf) {}

    private final Optional<String> baseDn;
    private final DirectorySettings settings;
    private final LdapConnector connector;

    public DirectoryService(Optional<String> baseDn, DirectorySettings settings, LdapConnector connector) {
        this.baseDn = baseDn;
        this.settings = settings;
        this.connector = connector;
    }

    @FunctionalInterface
    private interface Body<T> {
        T run(LDAPConnection connection, Runnable writing) throws LDAPException;
    }

    private <T> T run(String what, Body<T> body) {
        if (baseDn.isEmpty()) {
            throw new AdException(AdException.Kind.NOT_CONFIGURED,
                    "Active Directory belum dikonfigurasi (AD_LDAP_URL/LDAP_URL kosong).");
        }
        if (settings.bindDn().isEmpty() || settings.bindPassword().isEmpty()) {
            throw new AdException(AdException.Kind.NOT_CONFIGURED,
                    "Akun layanan belum dikonfigurasi: AD_BIND_DN dan AD_BIND_PASSWORD wajib diisi.");
        }
        boolean[] writing = {false};
        try (LDAPConnection connection = connector.open()) {
            connection.bind(settings.bindDn().get(), settings.bindPassword());
            return body.run(connection, () -> writing[0] = true);
        } catch (LDAPException error) {
            throw AdException.from(error, writing[0], what);
        }
    }

    // ------------------------------------------------------------------ reads

    public Optional<AccountState> findByAccountName(String sAMAccountName) {
        return run("mencari akun " + sAMAccountName, (connection, writing) ->
                findOne(connection, accountFilter(sAMAccountName), "sAMAccountName " + sAMAccountName)
                        .map(entry -> toAccount(connection, entry, new HashMap<>())));
    }

    public Optional<AccountState> findByGuid(String objectGUID) {
        return run("membaca objek " + objectGUID, (connection, writing) ->
                byGuid(connection, objectGUID).map(entry -> toAccount(connection, entry, new HashMap<>())));
    }

    public List<AccountState> listAccounts(String base) {
        return run("membaca akun di " + base, (connection, writing) -> {
            Map<String, String> managers = new HashMap<>();
            List<AccountState> accounts = new ArrayList<>();
            for (SearchResultEntry entry : paged(connection, base, anyUser())) {
                accounts.add(toAccount(connection, entry, managers));
            }
            accounts.sort(Comparator.comparing(AccountState::sAMAccountName, String.CASE_INSENSITIVE_ORDER));
            return accounts;
        });
    }

    /** Enabled members with a mailbox: the people an approval email can go to. */
    public List<AccountState> groupMembers(String groupDn) {
        return run("membaca anggota group " + groupDn, (connection, writing) -> {
            if (!exists(connection, groupDn)) {
                throw new AdException(AdException.Kind.NOT_FOUND, "Group " + groupDn + " tidak ditemukan di direktori.");
            }
            Filter members = settings.nestedGroups()
                    ? Filter.createExtensibleMatchFilter("memberOf", "1.2.840.113556.1.4.1941", false, groupDn)
                    : Filter.createEqualityFilter("memberOf", groupDn);
            Map<String, String> managers = new HashMap<>();
            List<AccountState> found = new ArrayList<>();
            for (SearchResultEntry entry : paged(connection, baseDn.get(), Filter.createANDFilter(anyUser(), members))) {
                AccountState account = toAccount(connection, entry, managers);
                if (account.enabled() && account.mail() != null && !account.mail().isBlank()) found.add(account);
            }
            found.sort(Comparator.comparing(AccountState::sAMAccountName, String.CASE_INSENSITIVE_ORDER));
            return found;
        });
    }

    public Standing standing(String sAMAccountName) {
        return run("memeriksa akun " + sAMAccountName, (connection, writing) -> {
            Optional<SearchResultEntry> entry = findOne(connection, accountFilter(sAMAccountName), "sAMAccountName " + sAMAccountName);
            if (entry.isEmpty()) return new Standing(false, false, List.of());
            String[] memberOf = entry.get().getAttributeValues("memberOf");
            return new Standing(true, isEnabled(entry.get()), memberOf == null ? List.of() : Arrays.asList(memberOf));
        });
    }

    /** Whether an entry exists, for the status page's OU checks. */
    public boolean entryExists(String dn) {
        return run("membaca " + dn, (connection, writing) -> exists(connection, dn));
    }

    // ----------------------------------------------------------------- writes

    public AccountState createAccount(CreateSpec spec) {
        assertWritable("membuat akun " + spec.sAMAccountName());
        assertManaged(spec.ou(), "OU tempat akun dibuat");
        return run("membuat akun " + spec.sAMAccountName(), (connection, writing) -> {
            if (findOne(connection, accountFilter(spec.sAMAccountName()), "sAMAccountName " + spec.sAMAccountName()).isPresent()) {
                throw new AdException(AdException.Kind.CONFLICT, "Akun " + spec.sAMAccountName() + " sudah ada di direktori.");
            }
            String managerDn = blank(spec.manager()) ? null : requireManagerDn(connection, spec.manager());
            DN dn = new DN(new RDN("CN", spec.displayName()), new DN(spec.ou()));

            Entry entry = new Entry(dn);
            entry.addAttribute("objectClass", "top", "person", "organizationalPerson", "user");
            entry.addAttribute("cn", spec.displayName());
            entry.addAttribute("sAMAccountName", spec.sAMAccountName());
            // Created disabled: it is enabled last, after every required step.
            entry.addAttribute("userAccountControl", String.valueOf(NORMAL_ACCOUNT | ACCOUNTDISABLE));
            optional(entry, "userPrincipalName", spec.userPrincipalName());
            optional(entry, "displayName", spec.displayName());
            optional(entry, "givenName", spec.givenName());
            optional(entry, "sn", spec.sn());
            optional(entry, "mail", spec.mail());
            optional(entry, "department", spec.department());
            optional(entry, "title", spec.title());
            optional(entry, "manager", managerDn);

            writing.run();
            connection.add(entry);
            return findOne(connection, accountFilter(spec.sAMAccountName()), "sAMAccountName " + spec.sAMAccountName())
                    .map(created -> toAccount(connection, created, new HashMap<>()))
                    .orElseThrow(() -> new AdException(AdException.Kind.TIMEOUT_AFTER_WRITE,
                            "Akun " + spec.sAMAccountName() + " dibuat tetapi tidak bisa dibaca ulang. Periksa direktori sebelum mencoba lagi."));
        });
    }

    public void setAttributes(String objectGUID, AttributePatch patch) {
        assertWritable("mengubah atribut " + objectGUID);
        run("mengubah atribut " + objectGUID, (connection, writing) -> {
            SearchResultEntry entry = requireByGuid(connection, objectGUID);
            assertManaged(parentOf(entry), "Objek yang diubah");
            List<Modification> changes = new ArrayList<>();
            set(changes, entry, "displayName", patch.displayName());
            set(changes, entry, "department", patch.department());
            set(changes, entry, "title", patch.title());
            set(changes, entry, "mail", patch.mail());
            if (patch.manager() != null) {
                if (patch.manager().isBlank()) {
                    if (entry.hasAttribute("manager")) changes.add(new Modification(ModificationType.DELETE, "manager"));
                } else {
                    changes.add(new Modification(ModificationType.REPLACE, "manager", requireManagerDn(connection, patch.manager())));
                }
            }
            if (changes.isEmpty()) return null;
            writing.run();
            connection.modify(entry.getDN(), changes);
            return null;
        });
    }

    public void changeGroups(String objectGUID, List<String> add, List<String> remove) {
        if (add.isEmpty() && remove.isEmpty()) return;
        assertWritable("mengubah group " + objectGUID);
        assertCatalogue(add);
        assertCatalogue(remove);
        run("mengubah group " + objectGUID, (connection, writing) -> {
            SearchResultEntry entry = requireByGuid(connection, objectGUID);
            assertManaged(parentOf(entry), "Objek yang diubah keanggotaannya");
            for (String group : add) member(connection, writing, group, entry.getDN(), ModificationType.ADD);
            for (String group : remove) member(connection, writing, group, entry.getDN(), ModificationType.DELETE);
            return null;
        });
    }

    public void moveToOu(String objectGUID, String ou) {
        assertWritable("memindahkan " + objectGUID);
        assertManaged(ou, "OU tujuan");
        run("memindahkan " + objectGUID + " ke " + ou, (connection, writing) -> {
            SearchResultEntry entry = requireByGuid(connection, objectGUID);
            String current = parentOf(entry);
            assertManaged(current, "OU asal");
            if (sameDn(current, ou)) return null;
            writing.run();
            connection.modifyDN(entry.getDN(), entry.getRDN().toString(), true, ou);
            return null;
        });
    }

    public void setEnabled(String objectGUID, boolean enabled) {
        String what = (enabled ? "mengaktifkan " : "menonaktifkan ") + objectGUID;
        assertWritable(what);
        run(what, (connection, writing) -> {
            SearchResultEntry entry = requireByGuid(connection, objectGUID);
            assertManaged(parentOf(entry), "Objek yang diubah status aktifnya");
            int current = userAccountControl(entry);
            int next = enabled ? current & ~ACCOUNTDISABLE : current | ACCOUNTDISABLE;
            if (next == current) return null;
            writing.run();
            connection.modify(entry.getDN(), new Modification(ModificationType.REPLACE, "userAccountControl", String.valueOf(next)));
            return null;
        });
    }

    // ---------------------------------------------------------------- helpers

    private void assertWritable(String what) {
        if (settings.writeEnabled()) return;
        throw new AdException(AdException.Kind.PERMISSION, "Penulisan ke Active Directory dimatikan, jadi " + what
                + " tidak dijalankan. Set AD_LDAP_WRITE_ENABLED=true bila koneksi sudah diverifikasi.");
    }

    private void assertManaged(String dn, String what) {
        if (isManaged(dn, settings.managedOus())) return;
        throw new AdException(AdException.Kind.PERMISSION, what + " (" + dn + ") berada di luar AD_MANAGED_OUS, jadi tidak ada yang ditulis.");
    }

    private void assertCatalogue(List<String> groups) {
        for (String group : groups) {
            if (settings.catalogueGroups().stream().noneMatch(known -> sameDn(known, group))) {
                throw new AdException(AdException.Kind.PERMISSION,
                        "Group " + group + " tidak diterbitkan katalog akses, jadi layanan ini tidak menyentuhnya.");
            }
        }
    }

    public static boolean isManaged(String dn, List<String> managedOus) {
        try {
            DN target = new DN(dn);
            for (String managed : managedOus) {
                DN container = new DN(managed);
                if (target.equals(container) || target.isDescendantOf(container, false)) return true;
            }
            return false;
        } catch (LDAPException error) {
            return false;
        }
    }

    public static boolean sameDn(String left, String right) {
        try {
            return new DN(left).equals(new DN(right));
        } catch (LDAPException error) {
            return left.equalsIgnoreCase(right);
        }
    }

    private void member(LDAPConnection connection, Runnable writing, String group, String userDn, ModificationType type)
            throws LDAPException {
        writing.run();
        try {
            connection.modify(group, new Modification(type, "member", userDn));
        } catch (LDAPException error) {
            ResultCode code = error.getResultCode();
            // Already a member / already gone: the outcome the step wanted.
            if (type == ModificationType.ADD && (code == ResultCode.ATTRIBUTE_OR_VALUE_EXISTS || code == ResultCode.ENTRY_ALREADY_EXISTS)) return;
            if (type == ModificationType.DELETE && (code == ResultCode.NO_SUCH_ATTRIBUTE || code == ResultCode.UNWILLING_TO_PERFORM)) return;
            throw error;
        }
    }

    private static void set(List<Modification> changes, Entry entry, String attribute, String value) {
        if (value == null) return;
        if (value.isBlank()) {
            if (entry.hasAttribute(attribute)) changes.add(new Modification(ModificationType.DELETE, attribute));
            return;
        }
        changes.add(new Modification(ModificationType.REPLACE, attribute, value));
    }

    private static void optional(Entry entry, String attribute, String value) {
        if (!blank(value)) entry.addAttribute(attribute, value);
    }

    private static boolean blank(String value) {
        return value == null || value.isBlank();
    }

    private static Filter anyUser() {
        return Filter.createANDFilter(Filter.createEqualityFilter("objectClass", "user"),
                Filter.createNOTFilter(Filter.createEqualityFilter("objectClass", "computer")));
    }

    private static Filter accountFilter(String sAMAccountName) {
        return Filter.createANDFilter(anyUser(), Filter.createEqualityFilter("sAMAccountName", sAMAccountName));
    }

    private Optional<SearchResultEntry> findOne(LDAPConnection connection, Filter filter, String what) throws LDAPException {
        SearchResult result = connection.search(baseDn.get(), SearchScope.SUB, filter, ATTRIBUTES);
        if (result.getEntryCount() > 1) {
            throw new AdException(AdException.Kind.CONFLICT,
                    what + " cocok dengan " + result.getEntryCount() + " objek di " + baseDn.get() + ". Persempit AD_BASE_DN.");
        }
        return result.getSearchEntries().stream().findFirst();
    }

    private Optional<SearchResultEntry> byGuid(LDAPConnection connection, String objectGUID) throws LDAPException {
        Filter filter = Filter.createANDFilter(anyUser(), Filter.createEqualityFilter("objectGUID", Guids.toBytes(objectGUID)));
        return findOne(connection, filter, "objectGUID " + objectGUID);
    }

    private SearchResultEntry requireByGuid(LDAPConnection connection, String objectGUID) throws LDAPException {
        return byGuid(connection, objectGUID).orElseThrow(() ->
                new AdException(AdException.Kind.NOT_FOUND, "Objek " + objectGUID + " tidak ditemukan di direktori."));
    }

    private String requireManagerDn(LDAPConnection connection, String accountName) throws LDAPException {
        return findOne(connection, accountFilter(accountName), "manager " + accountName)
                .map(Entry::getDN)
                .orElseThrow(() -> new AdException(AdException.Kind.NOT_FOUND,
                        "Manager " + accountName + " tidak ada di direktori, jadi tidak ada yang bisa dijadikan atasan."));
    }

    private static boolean exists(LDAPConnection connection, String dn) throws LDAPException {
        try {
            return connection.getEntry(dn, "1.1") != null;
        } catch (LDAPException error) {
            if (error.getResultCode() == ResultCode.NO_SUCH_OBJECT) return false;
            throw error;
        }
    }

    /** All entries, a page at a time: AD answers at most MaxPageSize per request. */
    private List<SearchResultEntry> paged(LDAPConnection connection, String base, Filter filter) throws LDAPException {
        List<SearchResultEntry> entries = new ArrayList<>();
        ASN1OctetString cookie = null;
        do {
            SearchRequest request = new SearchRequest(base, SearchScope.SUB, filter, ATTRIBUTES);
            request.addControl(new SimplePagedResultsControl(settings.pageSize(), cookie));
            SearchResult result = connection.search(request);
            entries.addAll(result.getSearchEntries());
            SimplePagedResultsControl page = SimplePagedResultsControl.get(result);
            cookie = page == null || !page.moreResultsToReturn() ? null : page.getCookie();
        } while (cookie != null);
        return entries;
    }

    private AccountState toAccount(LDAPConnection connection, SearchResultEntry entry, Map<String, String> managers) {
        String manager = null;
        String managerDn = entry.getAttributeValue("manager");
        if (managerDn != null) {
            manager = managers.computeIfAbsent(managerDn, dn -> {
                try {
                    Entry found = connection.getEntry(dn, "sAMAccountName");
                    return found == null ? null : found.getAttributeValue("sAMAccountName");
                } catch (LDAPException error) {
                    return null;
                }
            });
        }
        List<String> catalogue = new ArrayList<>();
        String[] memberOf = entry.getAttributeValues("memberOf");
        if (memberOf != null) {
            for (String group : memberOf) {
                if (settings.catalogueGroups().stream().anyMatch(known -> sameDn(known, group))) catalogue.add(group);
            }
        }
        byte[] guid = entry.getAttributeValueBytes("objectGUID");
        return new AccountState(
                guid == null ? null : Guids.fromBytes(guid),
                entry.getAttributeValue("sAMAccountName"),
                text(entry, "userPrincipalName"),
                text(entry, "displayName"),
                text(entry, "givenName"),
                text(entry, "sn"),
                text(entry, "mail"),
                text(entry, "department"),
                text(entry, "title"),
                manager,
                isEnabled(entry),
                parentOf(entry),
                catalogue);
    }

    private static String parentOf(Entry entry) {
        try {
            return entry.getParentDNString();
        } catch (LDAPException error) {
            throw new AdException(AdException.Kind.UNKNOWN, "DN objek tidak terbaca: " + entry.getDN());
        }
    }

    private static String text(Entry entry, String attribute) {
        String value = entry.getAttributeValue(attribute);
        return value == null ? "" : value;
    }

    private static int userAccountControl(Entry entry) {
        Attribute attribute = entry.getAttribute("userAccountControl");
        if (attribute == null) return NORMAL_ACCOUNT;
        try {
            return Integer.parseInt(attribute.getValue().trim());
        } catch (NumberFormatException error) {
            throw new AdException(AdException.Kind.UNKNOWN, "userAccountControl tidak terbaca: " + attribute.getValue());
        }
    }

    private static boolean isEnabled(Entry entry) {
        return (userAccountControl(entry) & ACCOUNTDISABLE) == 0;
    }
}
