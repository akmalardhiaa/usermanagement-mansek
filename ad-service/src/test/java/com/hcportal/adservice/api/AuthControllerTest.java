package com.hcportal.adservice.api;

import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

/** The HTTP contract the portal relies on, with the demo directory behind it. */
@SpringBootTest(properties = {"AD_SERVICE_TOKEN=a-service-token-of-24-chars", "DEMO_LOGIN=on"})
@AutoConfigureMockMvc
class AuthControllerTest {

    private static final String AUTH = "Bearer a-service-token-of-24-chars";

    @Autowired
    private MockMvc mvc;

    private static String body(String username, String password) {
        return "{\"username\":\"" + username + "\",\"password\":\"" + password + "\"}";
    }

    @Test
    void refusesACallerWithoutTheServiceToken() throws Exception {
        mvc.perform(post("/v1/auth/login").contentType(MediaType.APPLICATION_JSON).content(body("admin", "admin12345")))
                .andExpect(status().isUnauthorized());
        mvc.perform(post("/v1/auth/login").header("Authorization", "Bearer wrong")
                        .contentType(MediaType.APPLICATION_JSON).content(body("admin", "admin12345")))
                .andExpect(status().isUnauthorized());
    }

    @Test
    void answersWithThePersonAndTheirRoles() throws Exception {
        mvc.perform(post("/v1/auth/login").header("Authorization", AUTH)
                        .contentType(MediaType.APPLICATION_JSON).content(body("admin", "admin12345")))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.user.id").value("admin"))
                .andExpect(jsonPath("$.user.roles[0]").value("SYSTEM_ADMIN"));
    }

    @Test
    void answersAWrongPasswordWithOneCode() throws Exception {
        mvc.perform(post("/v1/auth/login").header("Authorization", AUTH)
                        .contentType(MediaType.APPLICATION_JSON).content(body("admin", "salah")))
                .andExpect(status().isUnauthorized())
                .andExpect(jsonPath("$.code").value("INVALID_CREDENTIALS"));
    }

    @Test
    void healthNeedsNoTokenAndSaysNothingSecret() throws Exception {
        mvc.perform(get("/v1/health"))
                .andExpect(status().isOk())
                .andExpect(jsonPath("$.directory").value("demo"));
    }
}
