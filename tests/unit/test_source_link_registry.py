import src.services.source_link_registry as registry
import pytest


def test_related_resource_page_does_not_merge_different_documents(monkeypatch):
    monkeypatch.setattr(registry, "_load_registry", lambda: {name: {"title": name, "link": "https://example.com/resources", "link_type": "related_page", "link_status": "verified"} for name in ("a.pdf", "b.pdf")})
    result = registry.resolve_source_links(["a.pdf", "b.pdf", "a.pdf"])
    assert len(result) == 2
    assert all(source["link_type"] == "related_page" for source in result)
    assert all(source["link"] == "https://example.com/resources" for source in result)


def test_resolve_source_links_matches_windows_paths_to_filename_registry(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_registry",
        lambda: {
            "brfenergieffektiv_2015.pdf": {
                "title": "BRF Energieffektiv 2015",
                "link": "https://example.com/brfenergieffektiv_2015.pdf",
                "link_status": "verified",
            }
        },
    )

    result = registry.resolve_source_links(
        [r"C:\Users\shada\Documents\spara_fast_deployment\spara-llm-service\temp_folder_download\brfenergieffektiv_2015.pdf"]
    )

    assert result == [
        {
            "name": "BRF Energieffektiv 2015",
            "filename": "brfenergieffektiv_2015.pdf",
            "link": "https://example.com/brfenergieffektiv_2015.pdf",
            "link_status": "verified",
        }
    ]


@pytest.fixture
def checked_catalog(monkeypatch):
    catalog = {
        "guide.pdf": {"title": "Checked handbook", "link_title": "Checked handbook (2015)",
                      "link": "https://trusted.example/guide.pdf", "link_type": "document", "link_status": "verified",
                      "link_aliases": ["https://old.example/guide.pdf"]},
        "tips.pdf": {"title": "Stored tips 2025", "link_title": "Official insulation guidance",
                     "link": "https://trusted.example/insulation", "link_type": "related_page", "link_status": "verified"},
        "unconfirmed.pdf": {"title": "Unconfirmed guide", "link": "https://trusted.example/unconfirmed.pdf",
                            "link_type": "document", "link_status": "indexed_not_fetched"},
    }
    monkeypatch.setattr(registry, "_load_registry", lambda: catalog)
    return catalog


def test_unregistered_retrieval_urls_are_not_published(checked_catalog):
    source = registry.resolve_source_links(["https://unknown.example/guide.pdf"])[0]
    assert source["link"] == ""
    assert source["link_status"] == "unregistered"


def test_unverified_document_keeps_its_name_without_a_link(checked_catalog):
    source = registry.resolve_source_links(["unconfirmed.pdf"])[0]
    assert source["name"] == "Unconfirmed guide"
    assert source["link"] == ""


def test_incoming_source_payload_cannot_override_catalog_destination(checked_catalog):
    source = registry.resolve_source_links([{"filename": "guide.pdf", "name": "Wrong title", "link": "https://invented.example/fake.pdf"}])[0]
    assert source["name"] == "Checked handbook"
    assert source["link"] == "https://trusted.example/guide.pdf"


@pytest.mark.parametrize("text", [
    "Read [made-up file](https://trusted.example/made-up.pdf).",
    "Read https://invented.example/guide.pdf.",
    "Read <https://invented.example/guide.pdf>.",
    "Read [guide][a].\n\n[a]: https://invented.example/guide.pdf",
    "Read [guide].\n\n[guide]: <https://invented.example/guide.pdf>",
    '<a href="https://invented.example/guide.pdf">Guide</a>',
    "Read [download](/api/reports/made-up-id).",
    "Read [unverified](https://trusted.example/unconfirmed.pdf).",
    "Read [wrong query](https://trusted.example/guide.pdf?file=other).",
    "Read [wrong fragment](https://trusted.example/guide.pdf#invented).",
    "Read [wrong host](https://trusted.example.fake/guide.pdf).",
    "Read [credentials](https://user:password@trusted.example/guide.pdf).",
    "Read www.invented.example/guide.pdf.",
])
def test_generated_unchecked_links_are_removed(checked_catalog, text):
    result = registry.guard_response_links(text, "en")
    assert "https://" not in result
    assert "www.invented" not in result
    assert "(/api/" not in result
    assert "omitted links" in result


@pytest.mark.parametrize("text", [
    "Read [wrong title](https://trusted.example/guide.pdf).",
    "Read https://trusted.example/guide.pdf.",
    "Read <https://trusted.example/guide.pdf>.",
    "Read [guide][a].\n\n[a]: https://trusted.example/guide.pdf",
    "Read [a].\n\n[a]: https://trusted.example/guide.pdf",
    '<a href="https://trusted.example/guide.pdf">Wrong title</a>',
    "Read [old publisher](https://old.example/guide.pdf).",
])
def test_checked_destinations_use_the_actual_title_and_canonical_url(checked_catalog, text):
    result = registry.guard_response_links(text, "en")
    assert "[Checked handbook (2015)](https://trusted.example/guide.pdf)" in result
    assert "omitted links" not in result
    assert "old.example" not in result


def test_related_page_cannot_be_labelled_as_the_original_pdf(checked_catalog):
    result = registry.guard_response_links("Read [Stored tips 2025.pdf](https://trusted.example/insulation)", "sv")
    assert "[Official insulation guidance (relaterad information)]" in result
    assert "Stored tips 2025.pdf" not in result
    assert registry.guard_response_links(result, "sv") == result


def test_code_evidence_and_source_numbers_are_preserved(checked_catalog):
    text = 'Recorded data [1].\n\n```json\n{"original_url": "https://unknown.example/raw"}\n```\n\nSchema: `https://unknown.example/schema`'
    assert registry.guard_response_links(text, "en") == text


def test_footnote_prose_is_preserved_while_its_links_are_checked(checked_catalog):
    text = "Recorded fact[^1].\n\n[^1]: Supporting explanation. See https://invented.example/fake."
    result = registry.guard_response_links(text, "en")
    assert "Recorded fact[^1]" in result
    assert "[^1]: Supporting explanation." in result
    assert "invented.example" not in result


def test_encoded_reserved_path_characters_do_not_alias_checked_urls():
    assert registry._url_key("https://example.com/guides%2Ffile.pdf") != registry._url_key("https://example.com/guides/file.pdf")
    assert registry._url_key("https://example.com/%67uide.pdf") == registry._url_key("https://example.com/guide.pdf")


def test_real_catalog_publishes_only_dated_verified_destinations():
    catalog = registry._load_registry()
    assert catalog
    for source in catalog.values():
        if source.get("link"):
            assert source["link_status"] == "verified"
            assert source["link_http_status"] == 200
            assert source["link_verified_at"] == "2026-10-06"
            assert source["link_title"]
    assert catalog["EnergiBRFhandbokenSthlm.pdf"]["link"] == ""
    assert catalog["Kunskapsbank.docx"]["link"] == ""


def test_resolve_source_links_matches_accented_windows_paths(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_registry",
        lambda: {
            "Vägledning för energi- och driftoptimering.pdf": {
                "title": "Vägledning för energi- och driftoptimering",
                "link": "https://aff-forum.se/category/kunskapsbanken/anpassade-mallar/#teknisk-forvaltning-energi-2",
                "link_status": "verified",
            }
        },
    )

    result = registry.resolve_source_links(
        [r"C:\Users\shada\Documents\spara_fast_deployment\spara-llm-service\temp_folder_download\Vägledning för energi- och driftoptimering.pdf"]
    )

    assert result == [
        {
            "name": "Vägledning för energi- och driftoptimering",
            "filename": "Vägledning för energi- och driftoptimering.pdf",
            "link": "https://aff-forum.se/category/kunskapsbanken/anpassade-mallar/#teknisk-forvaltning-energi-2",
            "link_status": "verified",
        }
    ]


def test_resolve_source_links_matches_wrapped_vector_source_strings(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_registry",
        lambda: {
            "Tips vid isolering_2025.pdf": {
                "title": "Tips vid isolering 2025",
                "link": "https://www.energiradgivningen.se/faktablad-och-broschyrer/",
                "link_status": "verified",
            }
        },
    )

    result = registry.resolve_source_links(
        [
            r'source: "C:\\Users\\shada\\Documents\\spara_fast_deployment\\spara-llm-service\\temp_folder_download\\Tips vid isolering_2025.pdf"'
        ]
    )

    assert result == [
        {
            "name": "Tips vid isolering 2025",
            "filename": "Tips vid isolering_2025.pdf",
            "link": "https://www.energiradgivningen.se/faktablad-och-broschyrer/",
            "link_status": "verified",
        }
    ]
