"""Extract the supplied PPTX's outlined logo as grouped SVG, without rasterization.

This intentionally supports only the geometry in this source, failing on changes
instead of silently dropping unsupported shapes or transformations.
"""
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from zipfile import ZipFile

NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main",
      "p": "http://schemas.openxmlformats.org/presentationml/2006/main"}
SVG = "http://www.w3.org/2000/svg"
INK = "http://www.inkscape.org/namespaces/inkscape"
ET.register_namespace("", SVG)
ET.register_namespace("inkscape", INK)


def node(tag, attrs=None, parent=None):
    return ET.SubElement(parent, f"{{{SVG}}}{tag}", attrs or {}) if parent is not None else ET.Element(f"{{{SVG}}}{tag}", attrs or {})


def convert(source, parent, colors, bounds):
    kind = source.tag.split("}")[-1]
    if kind == "grpSp":
        meta = source.find("p:nvGrpSpPr/p:cNvPr", NS)
        xf = source.find("p:grpSpPr/a:xfrm", NS)
        assert not xf.attrib, "Unexpected group rotation or flip"
        off, ext = xf.find("a:off", NS), xf.find("a:ext", NS)
        ch_off, ch_ext = xf.find("a:chOff", NS), xf.find("a:chExt", NS)
        assert off.attrib == ch_off.attrib and ext.attrib == ch_ext.attrib, "Unexpected group scale"
        group = node("g", {"id": "group-" + meta.get("id"), f"{{{INK}}}label": meta.get("name")}, parent)
        node("title", parent=group).text = meta.get("name")
        for child in source:
            if child.tag.split("}")[-1] in ("sp", "grpSp"):
                convert(child, group, colors, bounds)
        return
    assert kind == "sp", kind
    assert not source.findall(".//a:t", NS), "Expected outlined text"
    meta = source.find("p:nvSpPr/p:cNvPr", NS)
    props = source.find("p:spPr", NS)
    xf = props.find("a:xfrm", NS)
    assert not xf.attrib, "Unexpected shape rotation or flip"
    off, ext = xf.find("a:off", NS), xf.find("a:ext", NS)
    x, y = float(off.get("x")), float(off.get("y"))
    w, h = float(ext.get("cx")), float(ext.get("cy"))
    bounds.append((x, y, x + w, y + h))
    fill = props.find("a:solidFill", NS)[0]
    assert not list(fill), "Unexpected color modifier"
    color = fill.get("val") if fill.tag.endswith("srgbClr") else colors[fill.get("val")]
    assert props.find("a:ln/a:noFill", NS) is not None, "Unexpected stroke"
    group = node("g", {"id": "shape-" + meta.get("id"), f"{{{INK}}}label": meta.get("name"), "fill": "#" + color}, parent)
    node("title", parent=group).text = meta.get("name")
    paths = props.findall("a:custGeom/a:pathLst/a:path", NS)
    assert paths, "Expected custom paths"
    for path in paths:
        commands = []
        for command in path:
            name = command.tag.split("}")[-1]
            code = {"moveTo": "M", "lnTo": "L", "cubicBezTo": "C", "close": "Z"}[name]
            points = " ".join(f'{pt.get("x")},{pt.get("y")}' for pt in command)
            commands.append(code + points)
        node("path", {"d": " ".join(commands), "transform": f'translate({x:g} {y:g}) scale({w / float(path.get("w")):.12g} {h / float(path.get("h")):.12g})'}, group)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("pptx", type=Path)
    parser.add_argument("--output", type=Path, default=Path("deliverables/logo"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with ZipFile(args.pptx) as archive:
        slide = ET.fromstring(archive.read("ppt/slides/slide1.xml"))
        theme = ET.fromstring(archive.read("ppt/theme/theme1.xml"))
        colors = {c.tag.split("}")[-1]: c[0].get("val", c[0].get("lastClr")) for c in theme.find(".//a:clrScheme", NS)}
        source_groups = slide.findall("p:cSld/p:spTree/p:grpSp", NS)
        assert len(source_groups) == 5
        for name, selected in [("casino-logo", source_groups), ("casino-symbol", source_groups[:3]), ("casino-wordmark", source_groups[3:4])]:
            root = node("svg", {"version": "1.1", "role": "img", "aria-labelledby": "logo-title"})
            node("title", {"id": "logo-title"}, root).text = "CASINO — Collaborative Security and Interaction Group"
            node("desc", parent=root).text = "Original PowerPoint vector paths. Transparent canvas; card and die seams retain their original ivory fill. Letterforms are outlines."
            bounds = []
            for group in selected:
                convert(group, root, colors, bounds)
            left, top = min(b[0] for b in bounds), min(b[1] for b in bounds)
            right, bottom = max(b[2] for b in bounds), max(b[3] for b in bounds)
            padding = (right - left) * 0.015
            width, height = right - left + padding * 2, bottom - top + padding * 2
            root.set("viewBox", f"{left-padding:g} {top-padding:g} {width:g} {height:g}")
            root.set("width", "1200")
            root.set("height", str(round(1200 * height / width)))
            ET.indent(root)
            output = args.output / (name + ".svg")
            ET.ElementTree(root).write(output, encoding="utf-8", xml_declaration=True)
            print(f"{output}: {len(root.findall('.//{' + SVG + '}path'))} editable paths")


if __name__ == "__main__":
    main()
