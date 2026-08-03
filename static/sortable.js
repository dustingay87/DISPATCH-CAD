(function(){
  'use strict';

  const SORT_CLASS = 'sort-indicator';
  const INIT_ATTR = 'data-sortable-initialized';
  const COL_ATTR = 'data-sortable-col';
  const DIR_ATTR = 'data-sortable-dir';

  function isNumeric(s){
    if(s === '' || s == null) return false;
    return /^\s*[\-]?\d[\d,]*\.?\d*\s*$/.test(s);
  }

  function parseNumeric(s){
    return parseFloat(s.replace(/,/g, ''));
  }

  function cellText(row, col){
    const cell = row.cells[col];
    if(!cell) return '';
    return cell.textContent.trim().replace(/\s+/g, ' ');
  }

  function compareValues(a, b){
    if(a === '' && b !== '') return 1;
    if(b === '' && a !== '') return -1;
    if(isNumeric(a) && isNumeric(b)) return parseNumeric(a) - parseNumeric(b);
    return a.localeCompare(b, undefined, {numeric: true, sensitivity: 'base'});
  }

  function sortTable(table, col, dir){
    const tbody = table.querySelector('tbody');
    if(!tbody) return;
    const rows = Array.from(tbody.rows).filter(r => r.cells.length > col);
    rows.sort((a, b) => compareValues(cellText(a, col), cellText(b, col)) * dir);
    rows.forEach(r => tbody.appendChild(r));
  }

  function clearIndicators(ths){
    ths.forEach(th => {
      const existing = th.querySelector('.' + SORT_CLASS);
      if(existing) existing.remove();
    });
  }

  function setIndicator(th, dir){
    clearIndicators([th]);
    const span = document.createElement('span');
    span.className = SORT_CLASS;
    span.textContent = dir > 0 ? ' ▲' : ' ▼';
    span.style.marginLeft = '0.25rem';
    span.style.opacity = '0.75';
    th.appendChild(span);
  }

  function makeSortable(table){
    if(table.getAttribute(INIT_ATTR)) return;
    const thead = table.querySelector('thead');
    const ths = thead ? Array.from(thead.querySelectorAll('th')) : [];
    if(!ths.length) return;

    ths.forEach((th, idx) => {
      if(th.getAttribute('data-no-sort') || th.hasAttribute('data-key')) return;
      th.style.cursor = 'pointer';
      th.addEventListener('click', () => {
        const currentCol = table.getAttribute(COL_ATTR);
        let dir = 1;
        if(String(currentCol) === String(idx)){
          dir = parseInt(table.getAttribute(DIR_ATTR) || '1', 10) === 1 ? -1 : 1;
        }
        table.setAttribute(COL_ATTR, idx);
        table.setAttribute(DIR_ATTR, dir);
        sortTable(table, idx, dir);
        clearIndicators(ths);
        setIndicator(th, dir);
      });
    });

    table.setAttribute(INIT_ATTR, 'true');
  }

  function initAllTables(){
    document.querySelectorAll('table').forEach(makeSortable);
  }

  const observer = new MutationObserver((mutations) => {
    mutations.forEach(m => {
      m.addedNodes.forEach(n => {
        if(n.nodeType !== 1) return;
        if(n.tagName === 'TABLE') makeSortable(n);
        if(n.querySelectorAll) n.querySelectorAll('table').forEach(makeSortable);
      });
    });
  });

  if(document.readyState === 'loading'){
    document.addEventListener('DOMContentLoaded', initAllTables);
  } else {
    initAllTables();
  }

  if(document.body){
    observer.observe(document.body, { childList: true, subtree: true });
  } else {
    document.addEventListener('DOMContentLoaded', () => observer.observe(document.body, { childList: true, subtree: true }));
  }
})();
