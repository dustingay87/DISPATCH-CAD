import glob, re

snippet = """
<link rel='stylesheet' href='/static/theme.css'>
<script>if(localStorage.getItem('ur_theme')==='light')document.documentElement.classList.add('light');</script>
"""

for f in glob.glob('static/*.html'):
    with open(f, 'r', encoding='utf-8') as fp:
        text = fp.read()
    if 'theme.css' in text:
        continue
    # Insert before the closing </head> tag (case-insensitive)
    text = re.sub(r'(</head>)', lambda m: snippet + m.group(1), text, count=1, flags=re.IGNORECASE)
    with open(f, 'w', encoding='utf-8') as fp:
        fp.write(text)
    print('wired', f)

print('done')
