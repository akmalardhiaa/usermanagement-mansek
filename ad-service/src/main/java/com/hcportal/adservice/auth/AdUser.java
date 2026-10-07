package com.hcportal.adservice.auth;

import java.util.List;

/**
 * A person as the portal needs them after a successful sign-in.
 *
 * `id` is the sAMAccountName (the username on the demo list): stable, and the
 * key everything else in the portal — sessions, two-step enrolment — files a
 * person under.
 */
public record AdUser(
        String id,
        String username,
        String email,
        String fullName,
        String department,
        List<String> roles) {}
