// QUANTDATA Search UI — vanilla JS

// ---- Index page: live filter views table ----
(function () {
  const input = document.getElementById('view-filter');
  if (!input) return;
  const rows = document.querySelectorAll('#views-table tbody tr');
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase().trim();
    rows.forEach(r => {
      const name = r.querySelector('code').textContent.toLowerCase();
      r.style.display = name.includes(q) ? '' : 'none';
    });
  });
  const refresh = document.getElementById('refresh-btn');
  if (refresh) {
    refresh.addEventListener('click', async () => {
      refresh.disabled = true; refresh.textContent = '↻ refreshing…';
      try {
        await fetch('/api/refresh', { method: 'POST' });
        location.reload();
      } catch (e) {
        alert('Refresh failed: ' + e); refresh.disabled = false; refresh.textContent = '↻ Refresh catalog snapshot';
      }
    });
  }
})();

// ---- View page: filter builder + query + render ----
(function () {
  const metaEl = document.getElementById('view-meta');
  if (!metaEl) return;
  const meta = JSON.parse(metaEl.textContent);
  const filterList = document.getElementById('filter-list');
  const addBtn = document.getElementById('add-filter-btn');
  const runBtn = document.getElementById('run-btn');
  const sqlPreview = document.getElementById('sql-preview');
  const resultSection = document.getElementById('result-section');
  const tabBtns = document.querySelectorAll('.tabs button');
  const tabPanes = { table: document.getElementById('tab-table'), chart: document.getElementById('tab-chart') };
  const renderChartBtn = document.getElementById('render-chart-btn');

  // ---- Filter row factory ----
  function operatorsFor(col) {
    if (col.is_date) return [['date_from', '≥ from'], ['date_to', '≤ to'], ['eq', '= equals'], ['notnull', 'is not null']];
    if (col.is_numeric) return [['range_min', '≥ min'], ['range_max', '≤ max'], ['eq', '= equals'], ['isnull', 'is null'], ['notnull', 'is not null']];
    if (col.is_bool) return [['is_true', 'is true'], ['is_false', 'is false'], ['isnull', 'is null']];
    return [['contains', 'contains'], ['eq', '= equals'], ['in', 'in list'], ['isnull', 'is null'], ['notnull', 'is not null']];
  }

  function buildFilterRow(presetCol = null) {
    const row = document.createElement('div');
    row.className = 'filter-row';

    const colSel = document.createElement('select');
    colSel.className = 'col-select';
    meta.columns.forEach(c => {
      const opt = document.createElement('option');
      opt.value = c.name; opt.textContent = `${c.name} (${c.dtype})`;
      colSel.appendChild(opt);
    });
    if (presetCol) colSel.value = presetCol;

    const opSel = document.createElement('select');
    opSel.className = 'op-select';

    const val = document.createElement('input');
    val.className = 'val-input';
    val.type = 'text';
    val.placeholder = 'value';

    const remove = document.createElement('button');
    remove.type = 'button'; remove.className = 'remove'; remove.textContent = '✕';
    remove.addEventListener('click', () => { row.remove(); updateSqlPreview(); });

    function refreshOps() {
      const col = meta.columns.find(c => c.name === colSel.value);
      const ops = operatorsFor(col);
      opSel.innerHTML = '';
      ops.forEach(([v, label]) => {
        const opt = document.createElement('option'); opt.value = v; opt.textContent = label; opSel.appendChild(opt);
      });
      // value widget: if col has distinct_values (≤ 50), use dropdown; if date → date input
      const oldVal = val.value;
      let newVal;
      if (col.distinct_values && col.distinct_values.length > 0 && (opSel.value === 'eq' || opSel.value === 'in')) {
        newVal = document.createElement('select');
        newVal.className = 'val-input';
        if (opSel.value === 'in') newVal.multiple = true;
        col.distinct_values.forEach(v => {
          const o = document.createElement('option'); o.value = v; o.textContent = v; newVal.appendChild(o);
        });
      } else if (col.is_date) {
        newVal = document.createElement('input');
        newVal.type = 'date'; newVal.className = 'val-input';
        newVal.value = oldVal;
      } else if (col.is_numeric) {
        newVal = document.createElement('input');
        newVal.type = 'number'; newVal.step = 'any'; newVal.className = 'val-input';
        newVal.value = oldVal;
      } else {
        newVal = document.createElement('input');
        newVal.type = 'text'; newVal.className = 'val-input';
        newVal.value = oldVal;
        newVal.placeholder = 'value';
      }
      // for nullable ops, hide value input
      const noValue = ['isnull', 'notnull', 'is_true', 'is_false'].includes(opSel.value);
      newVal.style.display = noValue ? 'none' : '';
      val.replaceWith(newVal);
      Object.defineProperty(row, '_valEl', { value: newVal, configurable: true });
    }

    colSel.addEventListener('change', () => { refreshOps(); updateSqlPreview(); });
    opSel.addEventListener('change', () => { refreshOps(); updateSqlPreview(); });
    val.addEventListener('input', updateSqlPreview);

    row.appendChild(colSel); row.appendChild(opSel); row.appendChild(val); row.appendChild(remove);
    filterList.appendChild(row);
    refreshOps();
    Object.defineProperty(row, '_colSel', { value: colSel });
    Object.defineProperty(row, '_opSel', { value: opSel });
    if (!row._valEl) Object.defineProperty(row, '_valEl', { value: val, configurable: true });
    return row;
  }

  addBtn.addEventListener('click', () => { buildFilterRow(); updateSqlPreview(); });

  // ---- Collect filters into payload ----
  function collectPayload() {
    const filters = [];
    filterList.querySelectorAll('.filter-row').forEach(row => {
      const col = row._colSel.value;
      const op = row._opSel.value;
      let value = null;
      const valEl = row._valEl;
      if (!['isnull', 'notnull', 'is_true', 'is_false'].includes(op)) {
        if (valEl.tagName === 'SELECT' && valEl.multiple) {
          value = Array.from(valEl.selectedOptions).map(o => o.value);
          if (value.length === 0) return; // skip empty
        } else {
          value = valEl.value;
          if (value === '' || value == null) return;
          // numeric coercion
          const colMeta = meta.columns.find(c => c.name === col);
          if (colMeta && colMeta.is_numeric && !isNaN(parseFloat(value))) {
            value = parseFloat(value);
          }
        }
      }
      filters.push({ column: col, op, value });
    });
    return {
      view: meta.name,
      filters,
      order_by: document.getElementById('order-by').value || null,
      order_dir: document.getElementById('order-dir').value,
      limit: parseInt(document.getElementById('limit').value, 10) || 1000,
    };
  }

  function updateSqlPreview() {
    const p = collectPayload();
    let sql = `SELECT * FROM ${p.view}`;
    if (p.filters.length) {
      const where = p.filters.map(f => `  ${f.column} <${f.op}> ?`).join('\n  AND\n');
      sql += `\nWHERE\n${where}`;
    }
    if (p.order_by) sql += `\nORDER BY ${p.order_by} ${p.order_dir}`;
    sql += `\nLIMIT ${p.limit};`;
    sqlPreview.textContent = sql;
  }

  // ---- Run query ----
  runBtn.addEventListener('click', async () => {
    runBtn.disabled = true; runBtn.textContent = '⏳ Running…';
    try {
      const r = await fetch('/api/query', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(collectPayload()),
      });
      const data = await r.json();
      if (!r.ok) { alert('Query failed: ' + (data.error || 'unknown')); return; }
      renderResult(data);
    } finally {
      runBtn.disabled = false; runBtn.textContent = '▶ Run query';
    }
  });

  // ---- Result rendering ----
  let lastResult = null;
  function renderResult(data) {
    lastResult = data;
    resultSection.style.display = '';
    sqlPreview.textContent = data.sql;
    const rm = document.getElementById('result-meta');
    rm.innerHTML = `<b>${data.row_count}</b> rows returned${data.truncated ? ' <span class="err">(truncated to 5000)</span>' : ''} · view <code>${data.view}</code>`;
    const wrap = document.getElementById('result-table-wrap');
    wrap.innerHTML = '';
    if (!data.rows.length) { wrap.innerHTML = '<p>No rows match.</p>'; return; }
    const t = document.createElement('table');
    const thead = document.createElement('thead'); const trh = document.createElement('tr');
    data.columns.forEach(c => { const th = document.createElement('th'); th.textContent = c; trh.appendChild(th); });
    thead.appendChild(trh); t.appendChild(thead);
    const tbody = document.createElement('tbody');
    data.rows.forEach(row => {
      const tr = document.createElement('tr');
      row.forEach(cell => { const td = document.createElement('td'); td.textContent = cell == null ? '∅' : cell; tr.appendChild(td); });
      tbody.appendChild(tr);
    });
    t.appendChild(tbody);
    wrap.appendChild(t);
  }

  // ---- Tabs ----
  tabBtns.forEach(b => b.addEventListener('click', () => {
    if (b.disabled) return;
    tabBtns.forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    Object.values(tabPanes).forEach(p => p.classList.remove('active'));
    tabPanes[b.dataset.tab].classList.add('active');
  }));

  // ---- Chart ----
  if (renderChartBtn) {
    renderChartBtn.addEventListener('click', () => {
      if (!lastResult) { alert('Run a query first'); return; }
      const x = document.getElementById('chart-x').value;
      const ySel = document.getElementById('chart-y');
      const ys = Array.from(ySel.selectedOptions).map(o => o.value);
      const groupBy = document.getElementById('chart-group').value;
      if (!x || ys.length === 0) { alert('Pick X (date) + at least one Y'); return; }
      const xIdx = lastResult.columns.indexOf(x);
      if (xIdx < 0) return;

      // If groupBy, build per-group traces
      const traces = [];
      if (groupBy) {
        const gIdx = lastResult.columns.indexOf(groupBy);
        if (gIdx < 0) { alert('group column not in result'); return; }
        const groups = {};
        lastResult.rows.forEach(r => {
          const g = r[gIdx]; const key = String(g);
          if (!groups[key]) groups[key] = { x: [], ys: ys.map(() => []) };
          groups[key].x.push(r[xIdx]);
          ys.forEach((yc, i) => { groups[key].ys[i].push(r[lastResult.columns.indexOf(yc)]); });
        });
        Object.keys(groups).slice(0, 20).forEach(key => {  // cap at 20 series per group
          ys.forEach((yc, i) => {
            traces.push({ type: 'scatter', mode: 'lines', name: `${key} ${yc}`,
                          x: groups[key].x, y: groups[key].ys[i] });
          });
        });
      } else {
        ys.forEach(yc => {
          const yIdx = lastResult.columns.indexOf(yc);
          traces.push({
            type: 'scatter', mode: 'lines', name: yc,
            x: lastResult.rows.map(r => r[xIdx]),
            y: lastResult.rows.map(r => r[yIdx]),
          });
        });
      }
      Plotly.newPlot('plot', traces, {
        margin: { t: 30, r: 20, b: 40, l: 60 },
        xaxis: { title: x },
        yaxis: { title: ys.join(', ') },
        showlegend: true,
      }, { responsive: true });
    });
  }

  // Seed with one default filter on the primary date column if present
  if (meta.date_columns.length > 0) {
    buildFilterRow(meta.date_columns[0]);
  } else {
    buildFilterRow();
  }
  updateSqlPreview();
})();
