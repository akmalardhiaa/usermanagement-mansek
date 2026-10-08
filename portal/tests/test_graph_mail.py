"""Sending through Microsoft Graph, against a stand-in for Microsoft's two endpoints."""

from __future__ import annotations

import httpx
import pytest

from app.mail.mailer import GraphMailer, Mail, MailError, graph_config, graph_problem, mailer_from_env

ENV = {"EMAIL_DRIVER": "graph", "GRAPH_TENANT_ID": "tenant-1", "GRAPH_CLIENT_ID": "client-1",
       "GRAPH_CLIENT_SECRET": "not-a-real-secret", "GRAPH_SENDER_MAILBOX": "hc-noreply@corp.example"}


class Microsoft:
    """Answers the token endpoint and sendMail the way Microsoft does."""

    def __init__(self, token_status=200, send_status=202):
        self.token_status, self.send_status = token_status, send_status
        self.tokens = 0
        self.sent: list[dict] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "login.microsoftonline.com":
            assert request.url.path == "/tenant-1/oauth2/v2.0/token"
            form = dict(httpx.QueryParams(request.content.decode()))
            assert form["grant_type"] == "client_credentials" and form["scope"] == "https://graph.microsoft.com/.default"
            self.tokens += 1
            if self.token_status != 200:
                return httpx.Response(self.token_status, json={"error": "invalid_client"})
            return httpx.Response(200, json={"access_token": "token-abc", "expires_in": 3599})
        assert request.url.path == "/v1.0/users/hc-noreply@corp.example/sendMail"
        assert request.headers["authorization"] == "Bearer token-abc"
        self.sent.append(__import__("json").loads(request.content))
        return httpx.Response(self.send_status, headers={"request-id": "req-42"})


def mailer(microsoft: Microsoft) -> GraphMailer:
    return GraphMailer(graph_config(ENV), httpx.Client(transport=httpx.MockTransport(microsoft)))


def test_a_message_goes_out_as_html_from_the_sender_mailbox():
    microsoft = Microsoft()
    ref = mailer(microsoft).send(Mail("sarah@corp.example", "Sarah Wijaya", "Persetujuan diperlukan", "<p>Setujui</p>"))
    assert ref == "graph:req-42"
    message = microsoft.sent[0]["message"]
    assert message["body"] == {"contentType": "HTML", "content": "<p>Setujui</p>"}
    assert message["toRecipients"] == [{"emailAddress": {"address": "sarah@corp.example", "name": "Sarah Wijaya"}}]


def test_the_token_is_reused_until_it_nearly_expires():
    microsoft = Microsoft()
    sender = mailer(microsoft)
    for _ in range(3):
        sender.send(Mail("a@corp.example", None, "s", "<p>x</p>"))
    assert microsoft.tokens == 1


@pytest.mark.parametrize("token_status,send_status,kind", [
    (401, 202, "PERMISSION"),     # wrong secret: retrying will not help
    (200, 403, "PERMISSION"),     # Mail.Send not consented
    (200, 429, "TRANSIENT"),      # throttled: try again later
    (200, 503, "TRANSIENT"),
    (200, 400, "PAYLOAD"),
])
def test_failures_are_classified_so_the_queue_knows_whether_to_retry(token_status, send_status, kind):
    with pytest.raises(MailError) as caught:
        mailer(Microsoft(token_status, send_status)).send(Mail("a@corp.example", None, "s", "<p>x</p>"))
    assert caught.value.kind == kind


def test_missing_settings_are_named():
    assert graph_problem({**ENV, "GRAPH_CLIENT_SECRET": " "}) == "EMAIL_DRIVER=graph butuh GRAPH_CLIENT_SECRET."
    with pytest.raises(MailError) as caught:
        mailer_from_env({"EMAIL_DRIVER": "graph"})
    assert caught.value.kind == "CONFIG" and "GRAPH_TENANT_ID" in str(caught.value)


def test_graph_is_chosen_by_the_driver_setting_and_allowed_in_production():
    assert mailer_from_env({**ENV, "APP_ENV": "production"}).name == "graph"
