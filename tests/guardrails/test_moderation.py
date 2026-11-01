"""Cliente de la Moderation API con el transporte HTTP simulado (sin red):
se ejercita el parseo real de la respuesta del SDK de OpenAI."""

import httpx
import pytest
from pydantic import SecretStr

from app.guardrails.base import ModerationUnavailableError
from app.guardrails.moderation import ModerationClient

CATEGORIES = ["harassment", "hate", "self-harm", "sexual", "violence"]


def _response(flagged: bool) -> dict:
    return {
        "id": "modr-1",
        "model": "omni-moderation-latest",
        "results": [
            {
                "flagged": flagged,
                "categories": {c: flagged and c == "violence" for c in CATEGORIES},
                "category_scores": {c: 0.91 if flagged and c == "violence" else 0.001 for c in CATEGORIES},
                "category_applied_input_types": {c: ["text"] for c in CATEGORIES},
            }
        ],
    }


def make_client(handler) -> tuple[ModerationClient, list[httpx.Request]]:
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    client = ModerationClient(SecretStr("sk-test"), model="omni-moderation-latest", timeout_s=5)
    client._client = client._client.with_options(http_client=httpx.Client(transport=httpx.MockTransport(record)))
    return client, requests


@pytest.mark.parametrize("flagged", [True, False])
def test_parses_flag_categories_and_scores(flagged):
    client, requests = make_client(lambda _: httpx.Response(200, json=_response(flagged)))
    result = client.check("texto")

    assert result.triggered is flagged
    assert result.internal_detail == ("violence" if flagged else None)
    assert result.scores["violence"] == (0.91 if flagged else 0.001)
    assert result.score == max(result.scores.values())
    (request,) = requests
    assert request.url.path.endswith("/moderations")
    assert b"omni-moderation-latest" in request.content


@pytest.mark.parametrize(
    "handler",
    [
        lambda request: (_ for _ in ()).throw(httpx.ReadTimeout("timeout", request=request)),
        lambda request: (_ for _ in ()).throw(httpx.ConnectError("sin red", request=request)),
        lambda _: httpx.Response(500, json={"error": {"message": "boom"}}),
        lambda _: httpx.Response(401, json={"error": {"message": "bad key"}}),
    ],
)
def test_api_failures_raise_unavailable_without_retries(handler):
    client, requests = make_client(handler)
    with pytest.raises(ModerationUnavailableError):
        client.check("texto")
    assert len(requests) == 1  # max_retries=0
