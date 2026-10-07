package com.hcportal.adservice.ldap;

import java.io.File;
import java.security.GeneralSecurityException;
import java.util.Set;

import javax.net.ssl.SSLSocketFactory;

import com.hcportal.adservice.config.AdSettings;
import com.unboundid.ldap.sdk.LDAPConnection;
import com.unboundid.ldap.sdk.LDAPConnectionOptions;
import com.unboundid.ldap.sdk.LDAPException;
import com.unboundid.util.ssl.HostNameSSLSocketVerifier;
import com.unboundid.util.ssl.JVMDefaultTrustManager;
import com.unboundid.util.ssl.PEMFileTrustManager;
import com.unboundid.util.ssl.SSLUtil;

/**
 * Opens LDAPS connections to the domain controller, and nothing weaker.
 *
 * The certificate chain is verified against the company CA in
 * LDAP_CA_CERT_PATH (or the JVM's trust store when none is given), the host
 * name is checked against the certificate, and TLS 1.2 is the floor. There is
 * no switch to turn any of it off: passwords travel over this connection.
 *
 * An interface so the authentication logic can be tested against an in-memory
 * directory instead of only against a real domain controller.
 */
public interface LdapConnector {

    LDAPConnection open() throws LDAPException;

    static LdapConnector ldaps(AdSettings.LdapTarget target, String caCertPath, int timeoutMillis) {
        SSLSocketFactory socketFactory;
        try {
            SSLUtil.setEnabledSSLProtocols(Set.of("TLSv1.3", "TLSv1.2"));
            SSLUtil ssl = caCertPath == null
                    ? new SSLUtil(JVMDefaultTrustManager.getInstance())
                    : new SSLUtil(new PEMFileTrustManager(new File(caCertPath)));
            socketFactory = ssl.createSSLSocketFactory();
        } catch (GeneralSecurityException | IllegalArgumentException error) {
            throw new IllegalStateException(
                    "Sertifikat CA di LDAP_CA_CERT_PATH=\"" + caCertPath + "\" tidak bisa dipakai: " + error.getMessage(), error);
        }

        LDAPConnectionOptions options = new LDAPConnectionOptions();
        options.setConnectTimeoutMillis(timeoutMillis);
        options.setResponseTimeoutMillis(timeoutMillis);
        options.setSSLSocketVerifier(new HostNameSSLSocketVerifier(true));

        return () -> new LDAPConnection(socketFactory, options, target.host(), target.port());
    }
}
