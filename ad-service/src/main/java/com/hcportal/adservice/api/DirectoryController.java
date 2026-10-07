package com.hcportal.adservice.api;

import java.util.List;
import java.util.Map;

import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.hcportal.adservice.auth.RoleMapping;
import com.hcportal.adservice.config.AdSettings;
import com.hcportal.adservice.directory.AccountState;
import com.hcportal.adservice.directory.AdException;
import com.hcportal.adservice.directory.DirectoryService;

/**
 * The directory, as the portal's worker, sync and status page use it.
 *
 * Failures answer `{ error, kind }` with a status that says what to do:
 * 503 TRANSIENT / NOT_CONFIGURED, 403 PERMISSION, 409 CONFLICT, 404 NOT_FOUND,
 * 504 TIMEOUT_AFTER_WRITE (a write may have happened — never retried blindly).
 */
@RestController
public class DirectoryController {

    public record GroupChange(List<String> add, List<String> remove) {}
    public record MoveRequest(String ou) {}
    public record EnabledRequest(boolean enabled) {}
    public record ExistsRequest(String dn) {}

    private final DirectoryService directory;
    private final AdSettings settings;

    public DirectoryController(DirectoryService directory, AdSettings settings) {
        this.directory = directory;
        this.settings = settings;
    }

    @GetMapping("/v1/accounts")
    public Map<String, Object> list(@RequestParam("base") String base) {
        return Map.of("accounts", directory.listAccounts(base));
    }

    @GetMapping("/v1/accounts/by-name/{name}")
    public ResponseEntity<AccountState> byName(@PathVariable("name") String name) {
        return directory.findByAccountName(name).map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    @GetMapping("/v1/accounts/by-guid/{guid}")
    public ResponseEntity<AccountState> byGuid(@PathVariable("guid") String guid) {
        return directory.findByGuid(guid).map(ResponseEntity::ok)
                .orElseGet(() -> ResponseEntity.notFound().build());
    }

    /** For the portal's periodic session recheck: does AD still let this person in, and as what? */
    @GetMapping("/v1/accounts/by-name/{name}/standing")
    public Map<String, Object> standing(@PathVariable("name") String name) {
        DirectoryService.Standing standing = directory.standing(name);
        return Map.of(
                "found", standing.found(),
                "enabled", standing.enabled(),
                "roles", RoleMapping.rolesFromGroups(standing.memberOf(), settings.roleGroups()));
    }

    @GetMapping("/v1/groups/members")
    public Map<String, Object> members(@RequestParam("dn") String groupDn) {
        return Map.of("members", directory.groupMembers(groupDn));
    }

    @PostMapping("/v1/accounts")
    public ResponseEntity<AccountState> create(@RequestBody DirectoryService.CreateSpec spec) {
        return ResponseEntity.status(HttpStatus.CREATED).body(directory.createAccount(spec));
    }

    @PatchMapping("/v1/accounts/{guid}")
    public Map<String, Object> patch(@PathVariable("guid") String guid, @RequestBody DirectoryService.AttributePatch patch) {
        directory.setAttributes(guid, patch);
        return Map.of("ok", true);
    }

    @PostMapping("/v1/accounts/{guid}/groups")
    public Map<String, Object> groups(@PathVariable("guid") String guid, @RequestBody GroupChange change) {
        directory.changeGroups(guid,
                change.add() == null ? List.of() : change.add(),
                change.remove() == null ? List.of() : change.remove());
        return Map.of("ok", true);
    }

    @PostMapping("/v1/accounts/{guid}/ou")
    public Map<String, Object> move(@PathVariable("guid") String guid, @RequestBody MoveRequest request) {
        directory.moveToOu(guid, request.ou());
        return Map.of("ok", true);
    }

    @PostMapping("/v1/accounts/{guid}/enabled")
    public Map<String, Object> enabled(@PathVariable("guid") String guid, @RequestBody EnabledRequest request) {
        directory.setEnabled(guid, request.enabled());
        return Map.of("ok", true);
    }

    @PostMapping("/v1/entries/exists")
    public Map<String, Object> exists(@RequestBody ExistsRequest request) {
        return Map.of("exists", directory.entryExists(request.dn()));
    }

    @ExceptionHandler(AdException.class)
    public ResponseEntity<Map<String, Object>> failure(AdException error) {
        HttpStatus status = switch (error.kind()) {
            case TRANSIENT, NOT_CONFIGURED -> HttpStatus.SERVICE_UNAVAILABLE;
            case PERMISSION -> HttpStatus.FORBIDDEN;
            case CONFLICT -> HttpStatus.CONFLICT;
            case NOT_FOUND -> HttpStatus.NOT_FOUND;
            case TIMEOUT_AFTER_WRITE -> HttpStatus.GATEWAY_TIMEOUT;
            case UNKNOWN -> HttpStatus.INTERNAL_SERVER_ERROR;
        };
        return ResponseEntity.status(status).body(Map.of("error", error.getMessage(), "kind", error.kind().name()));
    }
}
