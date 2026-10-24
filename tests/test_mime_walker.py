import base64

from gmail_ingestion.normalization import analyze_message


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def test_recursive_mime_walk_extracts_bodies_addresses_and_attachments() -> None:
    message = {
        "id": "m1",
        "payload": {
            "partId": "",
            "mimeType": "multipart/mixed",
            "headers": [
                {"name": "From", "value": "Alice <Alice@Example.COM>"},
                {"name": "To", "value": "Bob <bob@example.com>, Carol <c@example.org>"},
                {"name": "Subject", "value": "=?utf-8?q?H=C3=A9llo?="},
                {"name": "X-Test", "value": "one"},
                {"name": "X-Test", "value": "two"},
            ],
            "body": {"size": 0},
            "parts": [
                {
                    "partId": "0",
                    "mimeType": "multipart/alternative",
                    "headers": [],
                    "body": {"size": 0},
                    "parts": [
                        {
                            "partId": "0.0",
                            "mimeType": "text/plain",
                            "headers": [
                                {
                                    "name": "Content-Type",
                                    "value": "text/plain; charset=iso-8859-1",
                                }
                            ],
                            "body": {"size": 4, "data": _b64("café".encode("iso-8859-1"))},
                        },
                        {
                            "partId": "0.1",
                            "mimeType": "text/html",
                            "headers": [
                                {"name": "Content-Type", "value": "text/html; charset=utf-8"}
                            ],
                            "body": {
                                "size": 12,
                                "data": _b64(b"<b>hello</b>"),
                            },
                        },
                    ],
                },
                {
                    "partId": "1",
                    "mimeType": "application/pdf",
                    "filename": "report.pdf",
                    "headers": [
                        {"name": "Content-Disposition", "value": "attachment; filename=report.pdf"}
                    ],
                    "body": {"size": 123, "attachmentId": "a1"},
                },
                {
                    "partId": "2",
                    "mimeType": "image/png",
                    "filename": "logo.png",
                    "headers": [
                        {"name": "Content-Disposition", "value": "inline; filename=logo.png"},
                        {"name": "Content-ID", "value": "<logo-1>"},
                    ],
                    "body": {"size": 3, "data": _b64(b"png")},
                },
            ],
        },
    }

    analysis = analyze_message(message)

    assert [part.part_path for part in analysis.parts] == [
        "root",
        "root/0",
        "root/0/0",
        "root/0/1",
        "root/1",
        "root/2",
    ]
    assert analysis.text_body == "café"
    assert analysis.html_body == "<b>hello</b>"
    assert analysis.headers.first("subject") == "Héllo"
    assert analysis.headers.values("x-test") == ("one", "two")
    assert analysis.addresses["from"][0].address == "Alice@example.com"
    assert len(analysis.addresses["to"]) == 2
    assert len(analysis.attachments) == 2
    assert analysis.attachments[0].attachment_id == "a1"
    assert analysis.attachments[1].is_inline is True
    assert analysis.attachments[1].content_id == "logo-1"


def test_message_without_payload_is_valid_but_has_warning() -> None:
    analysis = analyze_message({"id": "m-empty"})
    assert analysis.parts == ()
    assert analysis.text_body is None
    assert analysis.html_body is None
    assert analysis.warnings
