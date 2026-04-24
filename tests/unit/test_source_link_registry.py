import src.services.source_link_registry as registry


def test_resolve_source_links_matches_windows_paths_to_filename_registry(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_registry",
        lambda: {
            "brfenergieffektiv_2015.pdf": {
                "title": "BRF Energieffektiv 2015",
                "link": "https://example.com/brfenergieffektiv_2015.pdf",
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
        }
    ]


def test_resolve_source_links_matches_accented_windows_paths(monkeypatch):
    monkeypatch.setattr(
        registry,
        "_load_registry",
        lambda: {
            "Vägledning för energi- och driftoptimering.pdf": {
                "title": "Vägledning för energi- och driftoptimering",
                "link": "https://aff-forum.se/category/kunskapsbanken/anpassade-mallar/#teknisk-forvaltning-energi-2",
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
        }
    ]
