(function(){
  const THEME_KEY = 'ur_theme';
  const html = document.documentElement;

  function findHeader(){
    const h = document.querySelector('header, .d2d-header');
    if (h) return h;
    const first = document.body && document.body.firstElementChild;
    if (!first) return null;
    if (first.querySelector('h1') && (first.classList.contains('flex') || first.tagName === 'HEADER')) return first;
    const child = first.firstElementChild;
    if (child && child.querySelector('h1') && child.classList.contains('flex')) return child;
    return null;
  }

  function findTarget(header){
    const children = Array.from(header.children);
    // Prefer a right-side flex/gap container with interactive elements
    for (let i = children.length - 1; i >= 0; i--){
      const c = children[i];
      if (!c.classList) continue;
      const isFlex = c.classList.contains('flex') || c.classList.contains('inline-flex') ||
                     c.classList.contains('space-x-2') || c.classList.contains('space-x-1') ||
                     c.classList.contains('gap-2') || c.classList.contains('gap-1');
      if (!isFlex) continue;
      if (i > 0) return c;                         // likely the right-side group
      if (c.querySelector('a, button, select')) return c; // first child is a toolbar, not a logo
    }
    // Header itself is flex: append to it and push to the right
    if (header.classList && header.classList.contains('flex')) return header;
    // Header is not flex but first child is a flex wrapper (e.g. mobile MDT)
    if (children[0] && children[0].classList && children[0].classList.contains('flex')) return children[0];
    return null;
  }

  function insertToggle(){
    const btn = document.createElement('button');
    btn.id = 'themeToggle';
    btn.textContent = html.classList.contains('light') ? 'Dark mode' : 'Light mode';
    btn.setAttribute('aria-label', 'Toggle light and dark mode');

    btn.addEventListener('click', () => {
      const isLight = html.classList.toggle('light');
      localStorage.setItem(THEME_KEY, isLight ? 'light' : 'dark');
      btn.textContent = isLight ? 'Dark mode' : 'Light mode';
    });

    const header = findHeader();
    const target = header ? findTarget(header) : null;
    if (target){
      target.appendChild(btn);
      const style = window.getComputedStyle(target);
      if (style.display === 'flex' || style.display === 'inline-flex'){
        btn.style.marginLeft = 'auto';
      }
    } else {
      // fallback: small fixed top-right, not over bottom content
      btn.style.position = 'fixed';
      btn.style.top = '1rem';
      btn.style.right = '1rem';
      btn.style.zIndex = '2147483647';
      document.body.appendChild(btn);
    }
  }

  if (document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', insertToggle);
  } else {
    insertToggle();
  }
})();
