import math, itertools

# --- color utilities ---

def hsl_to_hex(h, s, l):
    h /= 360.0
    s /= 100.0
    l /= 100.0
    c = (1 - abs(2 * l - 1)) * s
    x = c * (1 - abs((h * 6) % 2 - 1))
    m = l - c / 2
    if h < 1/6:
        r, g, b = c, x, 0
    elif h < 2/6:
        r, g, b = x, c, 0
    elif h < 3/6:
        r, g, b = 0, c, x
    elif h < 4/6:
        r, g, b = 0, x, c
    elif h < 5/6:
        r, g, b = x, 0, c
    else:
        r, g, b = c, 0, x
    r = int((r + m) * 255)
    g = int((g + m) * 255)
    b = int((b + m) * 255)
    return f"#{r:02x}{g:02x}{b:02x}"

COLORS = {
    "slate":  (215, 20),
    "gray":   (220, 10),
    "red":    (0,   85),
    "orange": (25,  95),
    "amber":  (38,  95),
    "yellow": (45,  95),
    "lime":   (85,  85),
    "green":  (145, 70),
    "emerald":(155, 75),
    "teal":   (175, 75),
    "cyan":   (190, 90),
    "sky":    (200, 95),
    "blue":   (220, 90),
    "indigo": (240, 85),
    "violet": (260, 80),
    "purple": (270, 80),
    "fuchsia":(300, 80),
    "pink":   (330, 80),
    "rose":   (345, 85),
}

SHADES = [50, 100, 200, 300, 400, 500, 600, 700, 800, 900, 950]
L_MAP = {50:96, 100:91, 200:82, 300:72, 400:61, 500:50, 600:40, 700:30, 800:20, 900:13, 950:8}

# target shade mappings (make dark colors light/light colors readable)
BG_T     = {s: min(s, 1000 - s, 300) for s in SHADES}
TEXT_T   = {s: min(max(s, 1000 - s, 700), 900) for s in SHADES}
BORDER_T = {s: min(max(s, 1000 - s, 400), 700) for s in SHADES}
GRAD_T   = {s: min(max(1000 - s, 50), 300) for s in SHADES}

def color_hex(color, shade):
    h, s = COLORS[color]
    return hsl_to_hex(h, s, L_MAP[shade])

# --- generate CSS ---

lines = [
    "/* Auto-generated light mode overrides for Unified Response */",
    "",
    "html, html.light { color-scheme: light; }",
    "",
    "html.light body { background-color: #f8fafc; color: #0f172a; }",
    "html.light a { color: #2563eb; }",
    "html.light a:hover { color: #1d4ed8; }",
    "html.light input, html.light select, html.light textarea { background-color: #ffffff; color: #0f172a; border-color: #cbd5e1; }",
    "",
]

CATEGORIES = [
    ("bg",          BG_T,   "background-color"),
    ("text",        TEXT_T, "color"),
    ("border",      BORDER_T, "border-color"),
    ("ring",        TEXT_T, "--tw-ring-color"),
    ("placeholder", TEXT_T, "color"),
    ("divide",      BORDER_T, "border-color"),
]

STATES = ["", "hover:", "focus:", "active:"]

for state, (cat, target_map, prop) in itertools.product(STATES, CATEGORIES):
    for color in COLORS:
        for shade in SHADES:
            target = target_map[shade]
            hex = color_hex(color, target)
            base = f"{state}{cat}-{color}-{shade}"
            esc = base.replace(":", "\\:")

            if cat == "placeholder":
                sel = f"html.light .{esc}::placeholder"
            elif cat == "divide":
                sel = f"html.light .{esc}"
            else:
                sel = f"html.light .{esc}"

            if state:
                if cat == "placeholder":
                    sel = f"html.light .{esc}:{state.rstrip(':')}::placeholder"
                elif cat == "divide":
                    sel = f"html.light .{esc}:{state.rstrip(':')} > * + *"
                else:
                    sel = f"html.light .{esc}:{state.rstrip(':')}"

            if cat == "divide" and not state:
                lines.append(f"html.light .{esc} > * + * {{ {prop}: {hex} !important; }}")
            elif cat == "divide" and state:
                lines.append(f"{sel} {{ {prop}: {hex} !important; }}")
            else:
                lines.append(f"{sel} {{ {prop}: {hex} !important; }}")

# gradients
GRAD_CATS = ["from", "to", "via"]
for state, cat in itertools.product(STATES, GRAD_CATS):
    for color in COLORS:
        for shade in SHADES:
            target = GRAD_T[shade]
            hex = color_hex(color, target)
            base = f"{state}{cat}-{color}-{shade}"
            esc = base.replace(":", "\\:")
            sel = f"html.light .{esc}"
            if state:
                sel += f":{state.rstrip(':')}"

            if cat == "from":
                rule = f"--tw-gradient-from: {hex} var(--tw-gradient-from-position) !important; --tw-gradient-to: {hex} var(--tw-gradient-to-position) !important;"
            elif cat == "to":
                rule = f"--tw-gradient-to: {hex} var(--tw-gradient-to-position) !important;"
            else:  # via
                rule = f"--tw-gradient-to: {hex} var(--tw-gradient-to-position) !important; --tw-gradient-stops: var(--tw-gradient-from), {hex} var(--tw-gradient-via-position), var(--tw-gradient-to) !important;"

            lines.append(f"{sel} {{ {rule} }}")

# --- white/black/manual overrides ---
lines.append("")
lines.append("html.light .text-white, html.light .text-white * { color: #0f172a !important; }")
lines.append("html.light .bg-white { background-color: #ffffff !important; }")
lines.append("html.light .bg-black { background-color: #f1f5f9 !important; }")
lines.append("html.light .border-white { border-color: #0f172a !important; }")
lines.append("html.light .border-black { border-color: #cbd5e1 !important; }")
lines.append("html.light .d2d-header { background-color: #e2e8f0 !important; border-bottom-color: #cbd5e1 !important; }")
lines.append("html.light .d2d-logo-text { color: #0f172a !important; }")
lines.append("html.light .d2d-font-body { background-color: #f8fafc !important; color: #0f172a !important; }")

# Toggle button (small, fits in page headers)
lines.append("")
lines.append("""#themeToggle { display: inline-flex !important; align-items: center !important; padding: 0.25rem 0.75rem !important; border-radius: 0.375rem !important; background: #1E4B8C !important; color: #ffffff !important; border: 1px solid #334155 !important; cursor: pointer !important; font-size: 0.75rem !important; font-weight: 600 !important; line-height: 1 !important; white-space: nowrap !important; pointer-events: auto !important; height: 1.75rem !important; }""")
lines.append("html.light #themeToggle { background: #2563eb !important; border-color: #1d4ed8 !important; color: #ffffff !important; }")

with open("static/theme.css", "w", encoding="utf-8") as f:
    f.write("\n".join(lines))

print(f"generated static/theme.css with {len(lines)} lines")
