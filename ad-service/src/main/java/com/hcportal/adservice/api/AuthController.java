package com.hcportal.adservice.api;

import java.util.Map;
import java.util.Optional;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RestController;

import com.hcportal.adservice.auth.AdUser;
import com.hcportal.adservice.auth.AuthService;
import com.hcportal.adservice.config.AdSettings;

/**
 * POST /v1/auth/login — { username, password } → the person, or why not.
 *
 *   200 { user }                      the password is right
 *   401 { code: INVALID_CREDENTIALS } wrong — one answer for an unknown
 *                                     account and a wrong password alike
 *   503 { code: AD_UNAVAILABLE }      the directory did not answer
 *   503 { code: NOT_CONFIGURED }      no directory and no demo
 *
 * The password is never logged, and neither is the username on failure: the
 * portal writes the security log, with the address the attempt came from.
 */
@RestController
public class AuthController {

    private static final Logger log = LoggerFactory.getLogger(AuthController.class);

    public record LoginRequest(String username, String password) {}

    private final AuthService auth;
    private final AdSettings settings;

    public AuthController(AuthService auth, AdSettings settings) {
        this.auth = auth;
        this.settings = settings;
    }

    @PostMapping("/v1/auth/login")
    public ResponseEntity<Map<String, Object>> login(@RequestBody LoginRequest request) {
        try {
            Optional<AdUser> user = auth.authenticate(request.username(), request.password());
            return user.<ResponseEntity<Map<String, Object>>>map(found -> ResponseEntity.ok(Map.of("user", found)))
                    .orElseGet(() -> error(HttpStatus.UNAUTHORIZED, "Username atau kata sandi salah.", "INVALID_CREDENTIALS"));
        } catch (AuthService.DirectoryUnavailableException error) {
            // IT needs the cause — certificate, socket, DNS — never the password.
            log.error("AD tidak bisa dihubungi: {}", error.getMessage(), error.getCause());
            return error(HttpStatus.SERVICE_UNAVAILABLE, "Server Active Directory tidak bisa dihubungi. Hubungi tim IT.", "AD_UNAVAILABLE");
        } catch (AuthService.NotConfiguredException error) {
            return error(HttpStatus.SERVICE_UNAVAILABLE, error.getMessage(), "NOT_CONFIGURED");
        }
    }

    /** For the container health check and the portal's status page. Says nothing secret. */
    @GetMapping("/v1/health")
    public Map<String, Object> health() {
        return Map.of(
                "ok", true,
                "directory", settings.ldap().isPresent() ? "ldaps" : settings.demoLogin() ? "demo" : "none");
    }

    private static ResponseEntity<Map<String, Object>> error(HttpStatus status, String message, String code) {
        return ResponseEntity.status(status).body(Map.of("error", message, "code", code));
    }
}
