from __future__ import annotations

from math import sqrt

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.lib.utils import simpleSplit


PAGE_WIDTH, PAGE_HEIGHT = A4
MARGIN_X = 18 * mm
MARGIN_TOP = 16 * mm
MARGIN_BOTTOM = 14 * mm
CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN_X


QUESTIONS = [
    ("Define a point in geometry.", None),
    ("How many points are needed to determine a line?", None),
    ("How many non-collinear points are needed to determine a plane?", None),
    ("What is the intersection of two planes?", None),
    ("Name the line through points A and B in two different ways.", None),
    ("If M is the midpoint of AB and AB = 24, find AM and MB.", None),
    ("If Q is the midpoint of PR, and PQ = 7x - 16 and QR = 4x + 2, find x.", "segment"),
    ("Find the midpoint of A(-7, 4) and B(3, -4).", None),
    ("If the midpoint of segment XY is (2, -1) and X is (-4, 3), find Y.", None),
    ("If PQ = 9 and QR = 28, find PR.", "segment"),
    ("Find the distance between (-2, 5) and (4, 5). Use a graph.", "grid"),
    ("Find the distance between A(-4, 1) and B(3, -1).", None),
    ("Find the midpoint of G(7, -5) and H(19, -11).", None),
    ("Find the distance between E(-7, -2) and F(11, 3).", None),
    ("On a coordinate plane, a segment has endpoints (1, 1) and (1, 7). What is its length?", "grid"),
    ("If ray BD bisects angle ABC and m∠ABD = 3x + 5 while m∠DBC = 5x - 11, find x.", "angle_bisector"),
    ("If vertical angles are 4x + 12 and 7x - 24, find x.", "vertical_angles"),
    ("Two angles form a linear pair. One is 2x + 15 degrees and the other is 3x + 20 degrees. Find x.", "linear_pair"),
    ("An angle measures 68 degrees. Find its supplement.", None),
    ("The midpoint of A(-6, 2) and B(x, 8) is (-1, 5). Find the value of x.", None),
]


def draw_header(c: canvas.Canvas, page_number: int) -> float:
    top = PAGE_HEIGHT - MARGIN_TOP
    c.setFillColor(colors.HexColor("#0f172a"))
    c.rect(MARGIN_X, top - 18 * mm, CONTENT_WIDTH, 18 * mm, stroke=0, fill=1)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(MARGIN_X + 6 * mm, top - 8 * mm, "Mathematics Sample Question Paper")
    c.setFont("Helvetica", 9.5)
    c.drawRightString(PAGE_WIDTH - MARGIN_X - 6 * mm, top - 8 * mm, f"Grade 9  |  Page {page_number}")

    y = top - 24 * mm
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setFillColor(colors.black)
    c.setLineWidth(0.8)
    c.roundRect(MARGIN_X, y - 11 * mm, CONTENT_WIDTH, 11 * mm, 5, stroke=1, fill=0)
    c.setFont("Helvetica", 9.2)
    c.drawString(MARGIN_X + 4 * mm, y - 5 * mm, "Name: ________________________________")
    c.drawString(MARGIN_X + 78 * mm, y - 5 * mm, "Date: ____________________")
    c.drawString(MARGIN_X + 125 * mm, y - 5 * mm, "Class: ____________________")

    intro_y = y - 16 * mm
    intro = (
        "Instructions: Answer all 20 questions. Show your work where needed. "
        "Use diagrams, graphs, and coordinate reasoning carefully."
    )
    draw_wrapped(c, intro, MARGIN_X, intro_y, CONTENT_WIDTH, 9.5, font_name="Helvetica-Oblique", font_size=9)
    return intro_y - 5 * mm


def draw_wrapped(
    c: canvas.Canvas,
    text: str,
    x: float,
    y_top: float,
    width: float,
    leading: float,
    *,
    font_name: str = "Helvetica",
    font_size: float = 10,
    color=colors.black,
):
    c.setFillColor(color)
    c.setFont(font_name, font_size)
    lines = simpleSplit(text, font_name, font_size, width)
    y = y_top
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def draw_question_box(c: canvas.Canvas, number: int, question: str, diagram_kind: str | None, x: float, y_top: float, width: float):
    box_padding = 4 * mm
    diagram_width = 50 * mm if diagram_kind else 0
    text_width = width - box_padding * 2 - diagram_width - (4 * mm if diagram_kind else 0)
    base_height = 20 * mm
    text_lines = simpleSplit(question, "Helvetica", 10.3, text_width)
    height = max(base_height, 7 * mm + len(text_lines) * 4.6 * mm)
    if diagram_kind:
        height = max(height, 34 * mm)

    y_bottom = y_top - height
    c.setStrokeColor(colors.HexColor("#cbd5e1"))
    c.setFillColor(colors.white)
    c.roundRect(x, y_bottom, width, height, 5, stroke=1, fill=1)
    c.setFillColor(colors.HexColor("#0f172a"))
    c.setFont("Helvetica-Bold", 11)
    c.drawString(x + box_padding, y_top - 7 * mm, f"{number}.")

    text_x = x + box_padding + 8 * mm
    text_y = y_top - 7 * mm
    text_end = draw_wrapped(c, question, text_x, text_y, text_width, 5.1 * mm, font_name="Helvetica", font_size=10.3)

    if diagram_kind:
        dx = x + width - diagram_width - box_padding
        dy = y_bottom + 6 * mm
        if diagram_kind == "segment":
            draw_segment_diagram(c, dx, dy, diagram_width, height - 12 * mm)
        elif diagram_kind == "grid":
            draw_grid_diagram(c, dx, dy, diagram_width, height - 12 * mm)
        elif diagram_kind == "angle_bisector":
            draw_angle_bisector_diagram(c, dx, dy, diagram_width, height - 12 * mm)
        elif diagram_kind == "vertical_angles":
            draw_vertical_angles_diagram(c, dx, dy, diagram_width, height - 12 * mm)
        elif diagram_kind == "linear_pair":
            draw_linear_pair_diagram(c, dx, dy, diagram_width, height - 12 * mm)
        elif diagram_kind == "right_angle_bisector":
            draw_right_angle_diagram(c, dx, dy, diagram_width, height - 12 * mm)

    return y_bottom - 3.5 * mm


def draw_point(c: canvas.Canvas, x: float, y: float, label: str | None = None):
    c.setFillColor(colors.HexColor("#111827"))
    c.circle(x, y, 1.6, stroke=1, fill=1)
    if label:
        c.setFont("Helvetica-Bold", 8)
        c.drawString(x + 3, y + 3, label)


def draw_segment_diagram(c: canvas.Canvas, x: float, y: float, width: float, height: float):
    mid_y = y + height * 0.55
    left = x + 8
    right = x + width - 8
    mid1 = x + width * 0.38
    mid2 = x + width * 0.62
    c.setStrokeColor(colors.HexColor("#1d4ed8"))
    c.setLineWidth(1.3)
    c.line(left, mid_y, right, mid_y)
    draw_point(c, left, mid_y, "P")
    draw_point(c, mid1, mid_y, "Q")
    draw_point(c, right, mid_y, "R")
    c.setFont("Helvetica", 8.6)
    c.setFillColor(colors.HexColor("#b45309"))
    c.drawCentredString((left + mid1) / 2, mid_y + 8, "7x - 16")
    c.drawCentredString((mid1 + right) / 2, mid_y + 8, "4x + 2")
    c.setFillColor(colors.HexColor("#334155"))
    c.drawCentredString(x + width / 2, y + 4, "Q is the midpoint")


def draw_grid_diagram(c: canvas.Canvas, x: float, y: float, width: float, height: float):
    grid_w = width
    grid_h = height - 4
    pad = 4
    usable_w = grid_w - 2 * pad
    usable_h = grid_h - 2 * pad
    origin_x = x + pad
    origin_y = y + pad
    c.setStrokeColor(colors.HexColor("#94a3b8"))
    c.setLineWidth(0.4)
    cells_x = 6
    cells_y = 6
    for i in range(cells_x + 1):
        gx = origin_x + usable_w * i / cells_x
        c.line(gx, origin_y, gx, origin_y + usable_h)
    for j in range(cells_y + 1):
        gy = origin_y + usable_h * j / cells_y
        c.line(origin_x, gy, origin_x + usable_w, gy)
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setLineWidth(1)
    x0 = origin_x + usable_w * 0.18
    y0 = origin_y + usable_h * 0.48
    x1 = origin_x + usable_w * 0.82
    y1 = y0
    c.line(x0, y0, x1, y1)
    draw_point(c, x0, y0, "A")
    draw_point(c, x1, y1, "B")
    c.setFont("Helvetica", 7.8)
    c.setFillColor(colors.HexColor("#334155"))
    c.drawCentredString(origin_x + usable_w * 0.5, origin_y - 1, "coordinate grid")


def draw_angle_bisector_diagram(c: canvas.Canvas, x: float, y: float, width: float, height: float):
    cx = x + width * 0.48
    cy = y + height * 0.45
    length = min(width, height) * 0.42
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setLineWidth(1.2)
    c.line(cx, cy, cx - length, cy + length * 0.75)
    c.line(cx, cy, cx + length, cy + length * 0.75)
    c.line(cx, cy, cx, cy - length * 0.9)
    c.setFillColor(colors.HexColor("#7c3aed"))
    c.circle(cx, cy, 2, stroke=1, fill=1)
    c.setFillColor(colors.HexColor("#334155"))
    c.setFont("Helvetica", 8)
    c.drawString(cx - length - 2, cy + length * 0.8, "A")
    c.drawString(cx + length + 2, cy + length * 0.8, "C")
    c.drawString(cx + 2, cy - length * 1.0, "D")
    c.drawString(cx - 1, cy + 2, "B")
    c.setFont("Helvetica", 7.6)
    c.drawCentredString(x + width / 2, y + 3, "bisected angle")


def draw_vertical_angles_diagram(c: canvas.Canvas, x: float, y: float, width: float, height: float):
    cx = x + width * 0.5
    cy = y + height * 0.5
    span = min(width, height) * 0.42
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setLineWidth(1.2)
    c.line(cx - span, cy - span * 0.75, cx + span, cy + span * 0.75)
    c.line(cx - span, cy + span * 0.75, cx + span, cy - span * 0.75)
    draw_point(c, cx, cy, "O")
    c.setFont("Helvetica", 8)
    c.drawString(cx + span + 1, cy + span * 0.75, "1")
    c.drawString(cx + span + 1, cy - span * 0.75, "2")
    c.drawString(cx - span - 6, cy + span * 0.75, "3")
    c.drawString(cx - span - 6, cy - span * 0.75, "4")
    c.setFont("Helvetica", 7.6)
    c.drawCentredString(x + width / 2, y + 3, "vertical angles")


def draw_linear_pair_diagram(c: canvas.Canvas, x: float, y: float, width: float, height: float):
    cy = y + height * 0.5
    left = x + 10
    right = x + width - 10
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setLineWidth(1.2)
    c.line(left, cy, right, cy)
    draw_point(c, x + width * 0.46, cy, "V")
    c.line(x + width * 0.46, cy, x + width * 0.65, cy + height * 0.3)
    c.line(x + width * 0.46, cy, x + width * 0.65, cy - height * 0.3)
    c.setFont("Helvetica", 8)
    c.drawCentredString(x + width * 0.27, cy + 8, "2x + 15")
    c.drawCentredString(x + width * 0.74, cy + 8, "3x + 20")
    c.setFont("Helvetica", 7.6)
    c.drawCentredString(x + width / 2, y + 3, "linear pair")


def draw_right_angle_diagram(c: canvas.Canvas, x: float, y: float, width: float, height: float):
    cx = x + width * 0.46
    cy = y + height * 0.42
    size = min(width, height) * 0.44
    c.setStrokeColor(colors.HexColor("#0f172a"))
    c.setLineWidth(1.2)
    c.line(cx, cy, cx + size, cy)
    c.line(cx, cy, cx, cy + size)
    c.setFillColor(colors.HexColor("#16a34a"))
    c.rect(cx + 5, cy + 5, 10, 10, stroke=1, fill=0)
    c.setFont("Helvetica", 8)
    c.drawString(cx + size + 2, cy - 1, "90°")
    c.setFont("Helvetica", 7.6)
    c.drawCentredString(x + width / 2, y + 3, "right angle")


def generate_pdf(output_path: str):
    c = canvas.Canvas(output_path, pagesize=A4)
    c.setTitle("Mathematics Sample Question Paper")
    c.setAuthor("GitHub Copilot")
    c.setSubject("Grade 9 geometry sample paper")

    questions_per_page = 10
    page_number = 1
    for page_start in range(0, len(QUESTIONS), questions_per_page):
        y = draw_header(c, page_number)
        question_height_gap = 3 * mm
        box_width = CONTENT_WIDTH
        current_top = y
        for idx, (question, diagram_kind) in enumerate(QUESTIONS[page_start:page_start + questions_per_page], start=page_start + 1):
            current_top = draw_question_box(c, idx, question, diagram_kind, MARGIN_X, current_top, box_width)
            current_top -= question_height_gap
        c.setFillColor(colors.HexColor("#64748b"))
        c.setFont("Helvetica-Oblique", 8.5)
        c.drawRightString(PAGE_WIDTH - MARGIN_X, MARGIN_BOTTOM - 2 * mm, "Grade 9 Geometry Sample Paper")
        c.showPage()
        page_number += 1

    c.save()


if __name__ == "__main__":
    generate_pdf(r"c:\Users\TarunChopra\3700\Krish\maths_sample_question_paper.pdf")