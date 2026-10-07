package com.hcportal.adservice.auth;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import com.hcportal.adservice.config.AdSettings;

/**
 * Which portal roles a person's AD groups give them.
 *
 * A configured group matches a memberOf value when its components are a prefix
 * of the group's, compared case-insensitively — `CN=HC Officers` matches
 * `CN=HC Officers,OU=Groups,DC=…`. A bare name with no `=` matches the group's
 * CN. Same rules as before, so the same AD_GROUP_* values work unchanged.
 */
public final class RoleMapping {

    private RoleMapping() {}

    public static List<String> rolesFromGroups(List<String> memberOf, Map<String, List<String>> configured) {
        List<String> roles = new ArrayList<>();
        for (String role : AdSettings.PORTAL_ROLES) {
            for (String needle : configured.getOrDefault(role, List.of())) {
                if (memberOf.stream().anyMatch(group -> matches(group, needle))) {
                    roles.add(role);
                    break;
                }
            }
        }
        return List.copyOf(roles);
    }

    static boolean matches(String memberOf, String configured) {
        List<String> group = components(memberOf);
        List<String> needle = components(configured);
        if (group.isEmpty() || needle.isEmpty()) return false;

        if (needle.size() == 1 && !needle.get(0).contains("=")) {
            String[] first = group.get(0).split("=", 2);
            return first.length == 2 && first[0].equals("cn") && first[1].equals(needle.get(0));
        }
        if (needle.size() > group.size()) return false;
        for (int index = 0; index < needle.size(); index++) {
            if (!needle.get(index).equals(group.get(index))) return false;
        }
        return true;
    }

    private static List<String> components(String dn) {
        return Arrays.stream(dn.split(","))
                .map(part -> part.trim().toLowerCase(Locale.ROOT))
                .filter(part -> !part.isEmpty())
                .toList();
    }
}
