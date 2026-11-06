from crucibleforge.api import _with_array_items


def test_array_without_items_gets_empty_items():
    tools = [{"type": "function", "function": {"name": "f", "parameters": {
        "type": "object", "properties": {"tags": {"type": "array"}, "n": {"type": "integer"},
                                         "nested": {"type": "object", "properties": {"xs": {"type": ["array", "null"]}}},
                                         "ok": {"type": "array", "items": {"type": "string"}}}}}}]
    out = _with_array_items(tools)
    props = out[0]["function"]["parameters"]["properties"]
    assert props["tags"]["items"] == {}
    assert props["nested"]["properties"]["xs"]["items"] == {}
    assert props["ok"]["items"] == {"type": "string"}
    assert "items" not in props["n"]
    assert "items" not in tools[0]["function"]["parameters"]["properties"]["tags"]   # input not mutated
