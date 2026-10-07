package com.hcportal.adservice.security;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

import jakarta.servlet.Filter;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.ServletRequest;
import jakarta.servlet.ServletResponse;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

/**
 * Only the portal may call this service.
 *
 * Every /v1 request carries `Authorization: Bearer <AD_SERVICE_TOKEN>`, the
 * same secret in both containers' environment. Compared in constant time, so
 * the response time says nothing about how much of a guess was right. The
 * health check, which says nothing about the directory, is the one exception.
 */
public final class ServiceTokenFilter implements Filter {

    private final byte[] expected;

    public ServiceTokenFilter(String token) {
        this.expected = ("Bearer " + token).getBytes(StandardCharsets.UTF_8);
    }

    @Override
    public void doFilter(ServletRequest request, ServletResponse response, FilterChain chain)
            throws IOException, ServletException {
        HttpServletRequest http = (HttpServletRequest) request;
        if ("/v1/health".equals(http.getRequestURI())) {
            chain.doFilter(request, response);
            return;
        }

        String header = http.getHeader("Authorization");
        byte[] given = header == null ? new byte[0] : header.getBytes(StandardCharsets.UTF_8);
        if (!MessageDigest.isEqual(expected, given)) {
            HttpServletResponse http401 = (HttpServletResponse) response;
            http401.setStatus(HttpServletResponse.SC_UNAUTHORIZED);
            http401.setContentType("application/json");
            http401.getWriter().write("{\"error\":\"Token layanan tidak valid.\",\"code\":\"UNAUTHORIZED\"}");
            return;
        }
        chain.doFilter(request, response);
    }
}
