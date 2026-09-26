import json

import httpx

from app.config import get_settings
from app.sources.base import FatalSourceError, FetchContext
from app.sources.companies_house import CompaniesHouseAdapter, parse_companies_house_items
from app.sources.directory import DirectoryAdapter, parse_directory_html
from app.sources.google_places import parse_google_places
from app.sources.overpass import make_tiles, parse_overpass_elements


def _ctx(settings, checkpoint=None, professions=None):
    return FetchContext(
        location="Barnes, London",
        professions=professions
        or [
            {
                "slug": "plumber",
                "label": "Plumbers",
                "keywords": ["plumber"],
                "sic_codes": ["43220"],
                "osm_tags": [{"key": "craft", "value": "plumber"}],
            }
        ],
        checkpoint=checkpoint or {},
        user_agent="LeadlaneTest/1.0",
        settings=settings,
    )


def test_companies_house_parser_reads_advanced_search_shape():
    leads = parse_companies_house_items(
        [
            {
                "company_name": "PIPES LTD",
                "company_number": "12345678",
                "company_status": "active",
                "registered_office_address": {
                    "address_line_1": "1 High Street",
                    "locality": "London",
                    "postal_code": "SW13 9LW",
                },
            },
            {"company_name": "GONE LTD", "company_number": "99999999", "company_status": "dissolved"},
        ],
        "Plumbers",
    )
    assert len(leads) == 1
    assert leads[0].business_name == "PIPES LTD"
    assert leads[0].postcode == "SW13 9LW"
    assert leads[0].external_id == "12345678"
    assert leads[0].source_url.endswith("/company/12345678")
    assert leads[0].phone is None


def test_companies_house_fetch_pages_until_the_sic_is_exhausted():
    pages = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.company-information.service.gov.uk"
        assert request.url.params["sic_codes"] == "43220"
        assert request.url.params["location"] == "Barnes, London"
        pages["n"] += 1
        if request.url.params["start_index"] == "0":
            items = [
                {
                    "company_name": f"PIPE {index} LTD",
                    "company_number": f"{index:08d}",
                    "company_status": "active",
                    "registered_office_address": {"postal_code": "SW13 9LW", "locality": "London"},
                }
                for index in range(1, 21)
            ]
            return httpx.Response(200, json={"hits": 21, "items": items})
        items = [
            {
                "company_name": "LAST PIPE LTD",
                "company_number": "00000021",
                "company_status": "active",
                "address": {"address_line_1": "2 River Lane", "postal_code": "SW13 0AA"},
            }
        ]
        return httpx.Response(200, json={"hits": 21, "items": items})

    settings = get_settings().model_copy(
        update={"companies_house_api_key": "test-key", "companies_house_min_interval_seconds": 0}
    )
    adapter = CompaniesHouseAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    first = adapter.fetch(_ctx(settings))
    assert first.done is False
    assert first.checkpoint["start_index"] == 20
    assert len(first.leads) == 20
    second = adapter.fetch(_ctx(settings, checkpoint=first.checkpoint))
    assert second.done is True
    assert second.leads[0].business_name == "LAST PIPE LTD"
    assert pages["n"] == 2


def test_companies_house_rejects_a_bad_key():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "Invalid Authorization"})

    settings = get_settings().model_copy(
        update={"companies_house_api_key": "nope", "companies_house_min_interval_seconds": 0}
    )
    adapter = CompaniesHouseAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        adapter.fetch(_ctx(settings))
    except FatalSourceError as exc:
        assert "API key" in str(exc)
    else:
        raise AssertionError("expected the key to be rejected")


def test_nominatim_403_explains_the_user_agent_policy():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="Access denied")

    settings = get_settings().model_copy(update={"nominatim_min_interval_seconds": 0})
    from app.sources.overpass import OverpassAdapter

    adapter = OverpassAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        adapter.fetch(_ctx(settings))
    except FatalSourceError as exc:
        assert "USER_AGENT" in str(exc)
    else:
        raise AssertionError("expected a 403 to stop the job")


def test_overpass_parser_and_tiles():
    leads = parse_overpass_elements(
        [
            {
                "type": "node",
                "id": 418810444,
                "tags": {
                    "name": "System Electrics Contracts",
                    "craft": "electrician",
                    "phone": "+44 20 8878 0281",
                    "email": "office@system.example",
                    "description": "Electrical contractor",
                    "note": "surveyed 2019",
                    "addr:housenumber": "68",
                    "addr:street": "Mortlake High Street",
                    "addr:city": "London",
                    "addr:postcode": "SW14 8HR",
                },
            },
            {"type": "node", "id": 2, "tags": {"craft": "electrician"}},
        ],
        "Electricians",
    )
    assert len(leads) == 1
    assert leads[0].phone.startswith("+44")
    assert leads[0].email == "office@system.example"
    assert leads[0].description == "Electrical contractor"
    assert leads[0].postcode == "SW14 8HR"
    assert leads[0].source_url.endswith("/node/418810444")
    tiles = make_tiles(51.0, 51.2, -0.3, -0.1, max_span=0.08)
    assert len(tiles) > 1
    assert tiles[0]["south"] < tiles[0]["north"]


def test_google_places_parser():
    leads, token = parse_google_places(
        {
            "places": [
                {
                    "id": "abc",
                    "displayName": {"text": "River Plumbing"},
                    "formattedAddress": "1 High Street, London SW13 9LW",
                    "nationalPhoneNumber": "020 7946 0000",
                    "websiteUri": "https://river.example",
                    "googleMapsUri": "https://maps.google.com/?cid=1",
                }
            ],
            "nextPageToken": "next",
        },
        "Plumbers",
    )
    assert token == "next"
    assert leads[0].business_name == "River Plumbing"
    assert leads[0].postcode == "SW13 9LW"
    assert leads[0].website == "https://river.example"


def test_directory_parser_reads_jsonld():
    html = """
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"Plumber","name":"Pipes & Co",
     "telephone":"02079460000","email":"hello@pipes.example",
     "url":"https://pipes.example",
     "address":{"@type":"PostalAddress","streetAddress":"1 High Street",
                "addressLocality":"Barnes","postalCode":"SW13 9LW"}}
    </script>
    </head></html>
    """
    leads = parse_directory_html(html, "https://directory.example/search?q=plumber", "Plumbers")
    assert len(leads) == 1
    assert leads[0].business_name == "Pipes & Co"
    assert leads[0].email == "hello@pipes.example"
    assert leads[0].postcode == "SW13 9LW"


def test_directory_refuses_when_robots_disallows_and_does_not_fetch_the_page():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="should not be fetched")

    settings = get_settings().model_copy(
        update={
            "directory_search_url_template": "https://directory.example/search?q={keyword}&where={location}&page={page}",
            "directory_min_interval_seconds": 0,
        }
    )
    adapter = DirectoryAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    try:
        adapter.fetch(_ctx(settings))
    except FatalSourceError as exc:
        assert "robots.txt" in str(exc)
    else:
        raise AssertionError("expected robots.txt to stop the fetch")
    assert seen == ["/robots.txt"]


def test_directory_fetch_when_robots_allows():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow:\n")
        body = json.dumps({"@type": "Electrician", "name": "Bright Spark", "telephone": "02070000000"})
        html = f'<html><script type="application/ld+json">{body}</script></html>'
        return httpx.Response(200, text=html)

    settings = get_settings().model_copy(
        update={
            "directory_search_url_template": "https://directory.example/search?q={keyword}&where={location}&page={page}",
            "directory_min_interval_seconds": 0,
        }
    )
    adapter = DirectoryAdapter(client=httpx.Client(transport=httpx.MockTransport(handler)))
    result = adapter.fetch(_ctx(settings))
    assert result.leads[0].business_name == "Bright Spark"
    assert result.requests_made == 2
    assert result.checkpoint["page"] == 2
