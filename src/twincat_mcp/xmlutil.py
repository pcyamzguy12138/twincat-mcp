"""Bounded XML parsing for user-supplied engineering documents."""
from xml.etree import ElementTree as ET
from xml.parsers import expat


def parse_xml(text: str, *, max_bytes: int = 32 * 1024 * 1024) -> ET.Element:
    if not isinstance(text, str) or not text or "\0" in text:
        raise ValueError("Expected nonempty XML text")
    data = text.encode("utf-8")
    if len(data) > max_bytes:
        raise ValueError("XML byte limit exceeded")
    parser = expat.ParserCreate()
    depth = count = 0

    def reject(*_):
        raise ValueError("DTD and entity declarations are forbidden")

    def start(*_):
        nonlocal depth, count
        depth += 1
        count += 1
        if depth > 64 or count > 250000:
            raise ValueError("XML structure limit exceeded")

    def end(*_):
        nonlocal depth
        depth -= 1

    def declaration(_, encoding, __):
        if encoding and encoding.lower().replace("-", "") != "utf8":
            raise ValueError("XML text must use a UTF-8 declaration or no encoding declaration")

    parser.StartElementHandler, parser.EndElementHandler = start, end
    parser.StartDoctypeDeclHandler = parser.EntityDeclHandler = reject
    parser.ExternalEntityRefHandler = reject
    parser.XmlDeclHandler = declaration
    try:
        parser.Parse(data, True)
        root = ET.fromstring(data)
    except (expat.ExpatError, ET.ParseError) as exc:
        raise ValueError(f"Invalid XML: {exc}") from None
    # Known engineering schemas use either no namespace or a default namespace.
    # Strip expanded namespaces for structural navigation; never rewrite inputs.
    for node in root.iter():
        node.tag = node.tag.rsplit("}", 1)[-1]
    return root


def value(node, tag: str, default=None):
    child = node.find(tag)
    return child.text.strip() if child is not None and child.text else default


def number(text, *, default=None):
    if text is None:
        return default
    text = str(text).strip()
    try:
        if text.lower().startswith("#x"):
            return int(text[2:], 16)
        return int(text, 16 if text.lower().startswith("0x") else 10)
    except ValueError:
        raise ValueError(f"Invalid integer: {text[:80]}") from None
