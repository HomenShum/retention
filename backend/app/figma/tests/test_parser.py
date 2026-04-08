from app.figma.parser import parse_figma_url


def test_parse_figma_url_extracts_file_key() -> None:
    parsed = parse_figma_url("https://www.figma.com/design/ABC123/My-File")
    assert parsed.file_key == "ABC123"
    assert parsed.node_id is None


def test_parse_figma_url_extracts_node_id() -> None:
    parsed = parse_figma_url("https://www.figma.com/design/ABC123/My-File?node-id=1:2")
    assert parsed.file_key == "ABC123"
    assert parsed.node_id == "1:2"
