package com.hcportal.adservice.directory;

import java.util.Map;

import com.unboundid.ldap.sdk.LDAPException;
import com.unboundid.ldap.sdk.ResultCode;

/**
 * A directory failure, classified by what the portal's worker should do about it.
 *
 *   TRANSIENT            try again later (the DC is busy or unreachable)
 *   PERMISSION           configuration or rights: retrying changes nothing
 *   CONFLICT             the directory said no to this change
 *   NOT_FOUND            the object is not there
 *   TIMEOUT_AFTER_WRITE  a write was sent and no answer came: it may have
 *                        happened — never retried blindly
 *   NOT_CONFIGURED       no directory or no service account
 *   UNKNOWN              anything else
 */
public final class AdException extends RuntimeException {

    public enum Kind { TRANSIENT, PERMISSION, CONFLICT, NOT_FOUND, TIMEOUT_AFTER_WRITE, NOT_CONFIGURED, UNKNOWN }

    private static final Map<Integer, Kind> BY_CODE = Map.ofEntries(
            Map.entry(8, Kind.PERMISSION),   // strongerAuthRequired
            Map.entry(11, Kind.TRANSIENT),   // adminLimitExceeded
            Map.entry(16, Kind.CONFLICT),    // noSuchAttribute
            Map.entry(19, Kind.CONFLICT),    // constraintViolation
            Map.entry(20, Kind.CONFLICT),    // attributeOrValueExists
            Map.entry(21, Kind.CONFLICT),    // invalidAttributeSyntax
            Map.entry(32, Kind.NOT_FOUND),   // noSuchObject
            Map.entry(49, Kind.PERMISSION),  // invalidCredentials: the service account bind
            Map.entry(50, Kind.PERMISSION),  // insufficientAccessRights
            Map.entry(51, Kind.TRANSIENT),   // busy
            Map.entry(52, Kind.TRANSIENT),   // unavailable
            Map.entry(53, Kind.CONFLICT),    // unwillingToPerform: "enable this, no password"
            Map.entry(64, Kind.CONFLICT),    // namingViolation
            Map.entry(65, Kind.CONFLICT),    // objectClassViolation
            Map.entry(66, Kind.CONFLICT),    // notAllowedOnNonLeaf
            Map.entry(68, Kind.CONFLICT),    // entryAlreadyExists
            Map.entry(81, Kind.TRANSIENT),   // serverDown
            Map.entry(91, Kind.TRANSIENT));  // connectError

    private final Kind kind;

    public AdException(Kind kind, String message) {
        super(message);
        this.kind = kind;
    }

    public Kind kind() {
        return kind;
    }

    /** Classifies an SDK failure; `writing` says whether a write had been sent. */
    public static AdException from(LDAPException error, boolean writing, String what) {
        String detail = what + ": " + error.getExceptionMessage();
        ResultCode code = error.getResultCode();
        if (code == ResultCode.TIMEOUT || code == ResultCode.TIME_LIMIT_EXCEEDED) {
            return writing
                    ? new AdException(Kind.TIMEOUT_AFTER_WRITE, detail + ". Perubahan mungkin sudah diterapkan; periksa direktori sebelum mencoba lagi.")
                    : new AdException(Kind.TRANSIENT, detail);
        }
        Kind kind = BY_CODE.getOrDefault(code.intValue(), Kind.UNKNOWN);
        return new AdException(kind, detail + " (kode LDAP " + code.intValue() + ")");
    }
}
