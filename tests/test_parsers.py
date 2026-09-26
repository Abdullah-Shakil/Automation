import httpx

from app.config import get_settings
from app.sources.base import FatalSourceError, FetchContext
from app.sources.companies_house import CompaniesHouseAdapter, parse_companies_house_items
from app.sources.overpass import make_tiles, parse_overpass_elements


def _ctx(settings, checkpoint=None, professions=None, location="England"):
    return FetchContext(
        location=location,
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
        assert "location" not in request.url.params
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


def test_serper_organic_parser():
    from app.sources.serper import parse_serper_organic, parse_serper_places

    leads = parse_serper_organic(
        {
            "organic": [
                {
                    "title": "River Plumbing - Barnes",
                    "link": "https://river.example/",
                    "snippet": "Boiler repairs in London SW13 9LW. Call 020 7946 0958 or email hello@river.example",
                }
            ]
        },
        "Plumbers",
    )
    assert leads[0].business_name == "River Plumbing"
    assert leads[0].postcode == "SW13 9LW"
    assert leads[0].website == "https://river.example/"
    assert leads[0].phone == "02079460958"
    assert leads[0].email == "hello@river.example"

    places = parse_serper_places(
        {
            "places": [
                {
                    "title": "Barnes Plumbers Ltd",
                    "address": "1 High Street, London SW13 9LW",
                    "phoneNumber": "020 7946 0100",
                    "website": "https://barnes-plumbers.example/",
                    "category": "Plumber",
                    "cid": "abc123",
                }
            ]
        },
        "Plumbers",
    )
    assert places[0].business_name == "Barnes Plumbers Ltd"
    assert places[0].website == "https://barnes-plumbers.example/"
    assert places[0].phone
    assert places[0].town
    assert places[0].postcode == "SW13 9LW"


def test_junk_hosts_are_skipped():
    from app.sources.serper import parse_serper_organic

    leads = parse_serper_organic(
        {
            "organic": [
                {
                    "title": "How to become a plumber",
                    "link": "https://www.quora.com/how-to-become",
                    "snippet": "Advice",
                }
            ]
        },
        "Plumbers",
    )
    assert leads == []