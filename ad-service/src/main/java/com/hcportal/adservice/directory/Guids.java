package com.hcportal.adservice.directory;

import java.util.HexFormat;
import java.util.regex.Pattern;

/**
 * objectGUID, between its 16 bytes and the text everyone reads.
 *
 * AD stores the first three groups little-endian, so the bytes are reordered
 * before being printed — the canonical form matches what PowerShell's
 * Get-ADUser shows.
 */
public final class Guids {

    private static final int[] ORDER = {3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15};
    private static final Pattern CANONICAL = Pattern.compile("^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$");

    private Guids() {}

    public static String fromBytes(byte[] bytes) {
        if (bytes == null || bytes.length != 16) {
            throw new AdException(AdException.Kind.UNKNOWN, "objectGUID harus 16 byte.");
        }
        StringBuilder hex = new StringBuilder();
        for (int index : ORDER) hex.append(String.format("%02x", bytes[index] & 0xff));
        String text = hex.toString();
        return text.substring(0, 8) + "-" + text.substring(8, 12) + "-" + text.substring(12, 16) + "-"
                + text.substring(16, 20) + "-" + text.substring(20);
    }

    public static byte[] toBytes(String guid) {
        String cleaned = guid == null ? "" : guid.trim().toLowerCase().replaceAll("^\\{|\\}$", "");
        if (!CANONICAL.matcher(cleaned).matches()) {
            throw new AdException(AdException.Kind.NOT_FOUND, "objectGUID \"" + guid + "\" bukan GUID yang sah.");
        }
        byte[] digits = HexFormat.of().parseHex(cleaned.replace("-", ""));
        byte[] bytes = new byte[16];
        for (int position = 0; position < 16; position++) bytes[ORDER[position]] = digits[position];
        return bytes;
    }
}
