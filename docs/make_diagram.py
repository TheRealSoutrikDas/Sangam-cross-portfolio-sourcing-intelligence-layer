#!/usr/bin/env python3
"""Generates the SANGAM architecture diagram as SVG, then rasterises to PNG.

Layout is computed rather than hand-placed so the columns actually align and
the connectors land on box edges instead of near them.
"""
import pathlib

W, H = 1860, 1330

INK = "#1a1f2b"          # text
RULE = "#c8cedb"         # hairlines
DET_FILL = "#eef1f6"     # deterministic nodes
DET_EDGE = "#5b6c7a"
LLM_FILL = "#fdf1dc"     # model nodes
LLM_EDGE = "#c08a2e"
SRC_FILL = "#ffffff"
HUM_FILL = "#eaf3ec"
HUM_EDGE = "#4a7c59"
ARROW = "#7a8496"
MUTED = "#5f6875"

FONT = "'DejaVu Sans','Helvetica Neue',Helvetica,Arial,sans-serif"

# ---------------------------------------------------------------- geometry
COL_SRC_X, COL_SRC_W = 46, 268
COL_GRAPH_X, COL_GRAPH_W = 610, 430
COL_HUM_X, COL_HUM_W = 1268, 446

NODE_TOP, NODE_H, NODE_GAP = 176, 70, 30
NODE_STEP = NODE_H + NODE_GAP

NODES = [
    ("ingest", "det", "connectors pull vendor artefacts and ERP demand"),
    ("extractor", "llm", "unstructured document to schema-constrained terms"),
    ("validate", "det", "deterministic critic. routes back on failure"),
    ("canonicalise", "det", "free text to canonical spec id, or ADJACENT"),
    ("optimise", "det", "landed cost, then pooled award under policy"),
    ("risk", "det", "everything the system refuses to decide alone"),
    ("brief", "det", "assembles the settled facts behind each ask"),
    ("harmonisation analyst", "llm", "writes the proposal for packaging design"),
    ("negotiator", "llm", "drafts the buyer's ask. never sends it"),
]

SOURCES = [
    ("Vendor quotations", "PDF, one buyer's inbox"),
    ("WhatsApp threads", "negotiated rate, Hinglish"),
    ("Import offers", "email, USD, FOB"),
    ("Portal rate cards", "XLSX download"),
    ("ERP purchase orders", "free-text descriptions"),
]

# (node index, heading, body lines)
GATES = [
    (2, "Sourcing analyst", ["extraction review", "when confidence < 0.85"]),
    (3, "Brand + packaging design", ["spec harmonisation queue", "300 gsm vs 350 gsm is a", "design decision, not a match"]),
    (5, "Category buyer", ["award approval, always"]),
    (5, "Supply planner", ["lead-time and cover"]),
    (5, "Head of Sourcing", ["policy exceptions"]),
    (8, "Category buyer", ["reads, edits, sends", "or doesn't"]),
]


def node_y(i):
    return NODE_TOP + i * NODE_STEP


def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text(x, y, s, size=16, fill=INK, weight="normal", anchor="start",
         family=FONT, spacing=None, style=""):
    ls = f' letter-spacing="{spacing}"' if spacing else ""
    st = f' style="{style}"' if style else ""
    return (f'<text x="{x}" y="{y}" font-family="{family}" font-size="{size}" '
            f'fill="{fill}" font-weight="{weight}" text-anchor="{anchor}"{ls}{st}>'
            f'{esc(s)}</text>')


def rect(x, y, w, h, fill, stroke, rx=7, sw=1.4, dash=None):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def arrow(x1, y1, x2, y2, stroke=ARROW, sw=2.0, dash=None, head="url(#ah)"):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<path d="M {x1} {y1} L {x2} {y2}" fill="none" stroke="{stroke}" '
            f'stroke-width="{sw}"{d} marker-end="{head}"/>')


def build() -> str:
    p = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}">',
        '<defs>',
        '<marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{ARROW}"/></marker>',
        '<marker id="ahg" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{HUM_EDGE}"/></marker>',
        '<marker id="aho" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" '
        f'markerHeight="7" orient="auto-start-reverse">'
        f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{LLM_EDGE}"/></marker>',
        '</defs>',
        f'<rect width="{W}" height="{H}" fill="#ffffff"/>',
    ]

    # ---- title
    p.append(text(46, 56, "SANGAM", 34, INK, "bold", spacing="4"))
    p.append(text(250, 56, "cross-portfolio sourcing intelligence layer", 22, MUTED))
    p.append(text(46, 84, "Google ADK. A deterministic award engine, four agents where judgement is "
                          "genuinely required, and one typed state contract.", 15.5, MUTED))
    p.append(f'<path d="M 46 104 L {W-46} 104" stroke="{RULE}" stroke-width="1.4"/>')

    # ---- column headers
    for x, w, label in ((COL_SRC_X, COL_SRC_W, "SOURCES"),
                        (COL_GRAPH_X, COL_GRAPH_W, "THE GRAPH"),
                        (COL_HUM_X, COL_HUM_W, "HUMAN DECISIONS")):
        p.append(text(x, 146, label, 15, MUTED, "bold", spacing="2.5"))
        p.append(f'<path d="M {x} 156 L {x+w} 156" stroke="{RULE}" stroke-width="1"/>')

    # ---- sources
    src_top, src_h, src_step = 196, 64, 84
    for i, (name, sub) in enumerate(SOURCES):
        y = src_top + i * src_step
        p.append(rect(COL_SRC_X, y, COL_SRC_W, src_h, SRC_FILL, RULE, sw=1.3))
        p.append(text(COL_SRC_X + 18, y + 28, name, 17, INK, "bold"))
        p.append(text(COL_SRC_X + 18, y + 50, sub, 13.5, MUTED))

    # bracket from the sources into `ingest`
    bx = COL_SRC_X + COL_SRC_W + 34
    src_first_c = src_top + src_h / 2
    src_last_c = src_top + (len(SOURCES) - 1) * src_step + src_h / 2
    for i in range(len(SOURCES)):
        cy = src_top + i * src_step + src_h / 2
        p.append(f'<path d="M {COL_SRC_X + COL_SRC_W} {cy} L {bx} {cy}" '
                 f'fill="none" stroke="{RULE}" stroke-width="1.6"/>')
    p.append(f'<path d="M {bx} {src_first_c} L {bx} {src_last_c}" stroke="{RULE}" '
             f'stroke-width="1.6" fill="none"/>')
    ing_c = node_y(0) + NODE_H / 2
    p.append(f'<path d="M {bx} {(src_first_c + src_last_c)/2} L {bx+46} '
             f'{(src_first_c + src_last_c)/2} L {bx+46} {ing_c} L {COL_GRAPH_X-14} {ing_c}" '
             f'fill="none" stroke="{ARROW}" stroke-width="2" marker-end="url(#ah)"/>')

    # ---- START pill
    sx = COL_GRAPH_X + COL_GRAPH_W / 2
    p.append(rect(sx - 52, 118, 104, 30, "#ffffff", MUTED, rx=15, sw=1.4))
    p.append(text(sx, 138, "START", 14, MUTED, "bold", anchor="middle", spacing="2"))
    p.append(arrow(sx, 148, sx, node_y(0) - 6))

    # ---- graph nodes
    for i, (name, kind, sub) in enumerate(NODES):
        y = node_y(i)
        fill, edge = (LLM_FILL, LLM_EDGE) if kind == "llm" else (DET_FILL, DET_EDGE)
        p.append(rect(COL_GRAPH_X, y, COL_GRAPH_W, NODE_H, fill, edge, sw=1.8))
        p.append(text(COL_GRAPH_X + 22, y + 32, name, 21, INK, "bold"))
        p.append(text(COL_GRAPH_X + 22, y + 56, sub, 13.5, MUTED))

        badge = "LLM" if kind == "llm" else "det"
        bw = 46 if kind == "llm" else 42
        p.append(rect(COL_GRAPH_X + COL_GRAPH_W - bw - 16, y + 15, bw, 22,
                      "#ffffff", edge, rx=11, sw=1.3))
        p.append(text(COL_GRAPH_X + COL_GRAPH_W - bw / 2 - 16, y + 30, badge, 12.5,
                      edge, "bold", anchor="middle"))

        if i < len(NODES) - 1:
            p.append(arrow(sx, y + NODE_H, sx, y + NODE_H + NODE_GAP - 6))

    # the repair cycle: validate routes back to the extractor
    ex_y = node_y(1) + NODE_H / 2
    val_y = node_y(2) + NODE_H / 2
    loop_x = COL_GRAPH_X - 30
    p.append(f'<path d="M {COL_GRAPH_X} {val_y} L {loop_x} {val_y} L {loop_x} {ex_y} '
             f'L {COL_GRAPH_X - 4} {ex_y}" fill="none" stroke="{LLM_EDGE}" '
             f'stroke-width="2" marker-end="url(#aho)"/>')
    p.append(f'<g transform="translate({loop_x - 12},{(ex_y+val_y)/2}) rotate(-90)">'
             + text(0, 0, "repair", 13, LLM_EDGE, "bold", anchor="middle") + '</g>')

    # ---- human gates
    gate_x, gate_w, gate_gap = COL_HUM_X, COL_HUM_W, 14
    grouped: dict[int, list] = {}
    for node_i, heading, lines in GATES:
        grouped.setdefault(node_i, []).append((heading, lines))

    for node_i, group in grouped.items():
        heights = [34 + 19 * len(lines) for _, lines in group]
        total = sum(heights) + gate_gap * (len(group) - 1)
        y = node_y(node_i) + NODE_H / 2 - total / 2
        src_x = COL_GRAPH_X + COL_GRAPH_W
        src_y = node_y(node_i) + NODE_H / 2
        for (heading, lines), h in zip(group, heights):
            p.append(rect(gate_x, y, gate_w, h, HUM_FILL, HUM_EDGE, rx=7, sw=1.4))
            p.append(text(gate_x + 18, y + 25, heading, 16.5, "#2f5540", "bold"))
            for j, line in enumerate(lines):
                p.append(text(gate_x + 18, y + 46 + j * 19, line, 13.5, "#41604c"))
            p.append(f'<path d="M {src_x} {src_y} L {gate_x - 8} {y + h/2}" '
                     f'fill="none" stroke="{HUM_EDGE}" stroke-width="1.7" '
                     f'stroke-dasharray="6 4" marker-end="url(#ahg)"/>')
            y += h + gate_gap

    # ---- state contract rail
    rail_x = COL_GRAPH_X - 78
    top_c, bot_c = node_y(0) + 10, node_y(len(NODES) - 1) + NODE_H - 10
    p.append(f'<path d="M {rail_x} {top_c} L {rail_x} {bot_c}" stroke="{DET_EDGE}" '
             f'stroke-width="1.6" stroke-dasharray="3 5" fill="none"/>')
    for i in range(len(NODES)):
        cy = node_y(i) + NODE_H / 2
        p.append(f'<circle cx="{rail_x}" cy="{cy}" r="3.6" fill="{DET_EDGE}"/>')
        p.append(f'<path d="M {rail_x} {cy} L {COL_GRAPH_X} {cy}" stroke="{DET_EDGE}" '
                 f'stroke-width="1.2" stroke-dasharray="3 4" fill="none"/>')
    p.append(f'<g transform="translate({rail_x - 16},{(top_c+bot_c)/2}) rotate(-90)">'
             + text(0, 0, "SourcingState  |  typed, validated at graph build", 14,
                    DET_EDGE, "bold", anchor="middle") + '</g>')

    # ---- the analyst: the conversational surface above the batch graph
    ay = node_y(len(NODES) - 1) + NODE_H + 46
    ah = 152
    ax, aw = COL_SRC_X, COL_HUM_X + COL_HUM_W - COL_SRC_X
    p.append(rect(ax, ay, aw, ah, "#fdf1dc", LLM_EDGE, rx=9, sw=2))
    p.append(text(ax + 22, ay + 32, "sourcing_analyst", 21, INK, "bold"))
    p.append(rect(ax + 236, ay + 15, 46, 22, "#ffffff", LLM_EDGE, rx=11, sw=1.3))
    p.append(text(ax + 259, ay + 30, "LLM", 12.5, LLM_EDGE, "bold", anchor="middle"))
    p.append(text(ax + 22, ay + 56, "the surface a buyer talks to. open-ended questions, "
                  "unknown number of steps.", 14, MUTED))
    p.append(text(ax + 22, ay + 84, "tools:", 14, INK, "bold"))
    p.append(text(ax + 78, ay + 84, "explain_award  ·  simulate_award  ·  price_at_volume  ·  "
                  "check_spec_match  ·  open_risks  ·  show_source  ·  find_in_sources",
                  14, MUTED))
    p.append(text(ax + 22, ay + 106, "agents:", 14, INK, "bold"))
    p.append(text(ax + 88, ay + 106, "vendor_scout (web search)  ·  harmonisation_analyst",
                  14, MUTED))
    p.append(text(ax + 22, ay + 132, "It chooses WHICH computation to run. sangam.engine runs it. "
                  "Every figure traces back to the line of the document it came from.",
                  14, "#8a6410", "bold"))

    # the raw documents stay reachable after extraction
    p.append(f'<path d="M {COL_SRC_X + 40} {src_top + (len(SOURCES)-1)*src_step + src_h + 8} '
             f'L {COL_SRC_X + 40} {ay - 8}" fill="none" stroke="{LLM_EDGE}" '
             f'stroke-width="1.6" stroke-dasharray="4 5" marker-end="url(#aho)"/>')
    p.append(text(COL_SRC_X + 50, ay - 26, "documents stay readable", 13, LLM_EDGE, "bold"))

    # ---- legend
    ly = H - 26
    p.append(f'<path d="M 46 {ly - 26} L {W-46} {ly - 26}" stroke="{RULE}" stroke-width="1.2"/>')
    items = [
        (DET_FILL, DET_EDGE, "deterministic Python. reproducible, auditable, no model in the loop"),
        (LLM_FILL, LLM_EDGE, "LlmAgent. schema-constrained, confidence-scored"),
        (HUM_FILL, HUM_EDGE, "human gate. named owner, blocks the PO"),
    ]
    x = 46
    for fill, edge, label in items:
        p.append(rect(x, ly - 13, 26, 18, fill, edge, rx=4, sw=1.4))
        p.append(text(x + 34, ly + 1, label, 13.5, MUTED))
        x += 34 + len(label) * 6.9 + 46
    p.append('</svg>')
    return "\n".join(p)


if __name__ == "__main__":
    out = pathlib.Path(__file__).resolve().parent
    svg = build()
    (out / "architecture.svg").write_text(svg)
    import cairosvg
    cairosvg.svg2png(bytestring=svg.encode(), write_to=str(out / "architecture.png"),
                     output_width=W * 2, output_height=H * 2)
    print("wrote architecture.svg and architecture.png")
