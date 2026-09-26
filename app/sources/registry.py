from app.sources.base import SourceAdapter


class Registry:
    def __init__(self) -> None:
        self._items: dict[str, SourceAdapter] = {}

    def register(self, adapter: SourceAdapter) -> None:
        self._items[adapter.key] = adapter

    def get(self, key: str) -> SourceAdapter:
        try:
            return self._items[key]
        except KeyError as exc:
            raise KeyError(key) from exc

    def all(self) -> list[SourceAdapter]:
        return list(self._items.values())


def build_default_registry() -> Registry:
    from app.sources.companies_house import CompaniesHouseAdapter
    from app.sources.google_places import GooglePlacesAdapter
    from app.sources.overpass import OverpassAdapter
    from app.sources.serpapi import SerpApiAdapter
    from app.sources.serper import SerperAdapter
    from app.sources.social import social_stubs
    from app.sources.tavily import TavilyAdapter
    from app.sources.unlockers import ApifyAdapter, ScrapingBeeAdapter
    from app.sources.wikidata import WikidataAdapter

    registry = Registry()
    registry.register(CompaniesHouseAdapter())
    registry.register(GooglePlacesAdapter())
    registry.register(OverpassAdapter())
    registry.register(WikidataAdapter())
    registry.register(SerperAdapter())
    registry.register(TavilyAdapter())
    registry.register(SerpApiAdapter())
    registry.register(ScrapingBeeAdapter())
    registry.register(ApifyAdapter())
    for stub in social_stubs():
        registry.register(stub)
    return registry


default_registry = build_default_registry()
