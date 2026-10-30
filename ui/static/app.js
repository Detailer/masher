const state = {
  transactions: [], categories: [], tags: [], categoryTags: [], unassigned: [],
  page: 1, pageSize: 'all', sort: 'date', ascending: false, ready: false,
  metricsAnimated: false, tableAnimated: false,
};
const byId = (id) => document.getElementById(id);
const categoryName = new Map();
const tagName = new Map();

function showStatus(element, message = '', error = false) {
  element.textContent = message;
  element.classList.toggle('error', error);
}
function setBusy(button, busy, label) {
  button.disabled = busy;
  if (busy) {
    button.dataset.originalMarkup = button.innerHTML;
    button.textContent = label;
  } else if (button.dataset.originalMarkup) {
    button.innerHTML = button.dataset.originalMarkup;
    delete button.dataset.originalMarkup;
  }
}
async function responseJson(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status})`);
  return data;
}

async function loadConfig() {
  try {
    const config = await responseJson(await fetch('/api/config'));
    state.categories = config.categories;
    state.tags = config.tags;
    state.categoryTags = config.category_tags;
    state.unassigned = config.unassigned_tag_ids;
    state.categories.forEach((item) => categoryName.set(String(item.id), item.name));
    state.tags.forEach((item) => tagName.set(String(item.id), item.name));
    byId('taxonomy-path').value = config.taxonomy_path;
    byId('mappings-path').value = config.mappings_path;
    byId('omissions-path').value = config.omissions_path;
  } catch (error) {
    showStatus(byId('process-status'), error.message, true);
  }
}

function esc(value) {
  return String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  })[char]);
}
function formatDate(value) {
  if (!value) return '—';
  const parts = String(value).slice(0, 10).split('-');
  return parts.length === 3 ? `${parts[2]}-${parts[1]}-${parts[0]}` : value;
}
function allowedTags(categoryId) {
  if (!categoryId) return [];
  const linked = state.categoryTags
    .filter((item) => String(item.category_id) === String(categoryId))
    .map((item) => String(item.tag_id));
  return [...linked, ...state.unassigned];
}
function sortValue(row, key) {
  if (key === 'category_id') return (categoryName.get(String(row.category_id)) || '').toLocaleLowerCase();
  if (key === 'tag_ids') return (row.tag_ids || []).map((id) => tagName.get(String(id)) || '').join(',').toLocaleLowerCase();
  if (key === 'amount') return Number(row.amount) || 0;
  return String(row[key] ?? '').toLocaleLowerCase();
}
function sortedTransactions() {
  return state.transactions.map((row, index) => ({ row, index })).sort((a, b) => {
    const av = sortValue(a.row, state.sort);
    const bv = sortValue(b.row, state.sort);
    let result = typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av).localeCompare(String(bv));
    if (result === 0) result = a.index - b.index;
    return state.ascending ? result : -result;
  }).map(({ index }) => index);
}
function tagSummary(ids, categoryId) {
  const names = (ids || []).map((id) => tagName.get(String(id))).filter(Boolean);
  if (!names.length) {
    return `<span class="tag-empty">${categoryId ? 'Choose tags' : 'Select category first'}</span>`;
  }
  return names.map((name) => `<span class="selected-tag">${esc(name)}</span>`).join('');
}
function tagPicker(row, index) {
  const options = allowedTags(row.category_id);
  const selected = new Set((row.tag_ids || []).map(String));
  return `<details class="tag-picker" data-row="${index}">
    <summary title="${esc([...selected].map((id) => tagName.get(id)).filter(Boolean).join(', ') || 'Choose tags')}">${tagSummary([...selected], row.category_id)}</summary>
    <div class="tag-options">${options.map((id) => `<label class="tag-option"><input type="checkbox" data-tag-row="${index}" value="${esc(id)}" ${selected.has(id) ? 'checked' : ''}>${esc(tagName.get(id) || id)}</label>`).join('') || '<span class="tag-option">No tags available</span>'}</div>
  </details>`;
}
function updateMetrics(animate = false) {
  const count = state.transactions.length;
  const matched = state.transactions.filter((row) => row.category_id).length;
  const metrics = [
    ['Transactions', count], ['Matched', matched], ['Need a category', count - matched],
  ];
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  byId('metrics').innerHTML = metrics.map(([label, value]) =>
    `<div class="metric"><span>${label}</span><strong data-target="${value}">${animate && !reduceMotion ? 0 : value.toLocaleString()}</strong></div>`
  ).join('');
  if (!animate || reduceMotion) return;

  byId('metrics').querySelectorAll('[data-target]').forEach((counter) => {
    const target = Number(counter.dataset.target);
    const startedAt = performance.now();
    const duration = 1200;
    const tick = (now) => {
      const progress = Math.min((now - startedAt) / duration, 1);
      const eased = 1 - (1 - progress) ** 3;
      counter.textContent = Math.round(target * eased).toLocaleString();
      if (progress < 1) requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  });
}
function render() {
  if (!state.ready) return;
  const order = sortedTransactions();
  const pageSize = state.pageSize === 'all' ? Math.max(order.length, 1) : state.pageSize;
  const pages = Math.max(1, Math.ceil(order.length / pageSize));
  state.page = Math.min(Math.max(state.page, 1), pages);
  const start = (state.page - 1) * pageSize;
  const visible = order.slice(start, start + pageSize);
  byId('page-indicator').textContent = `Page ${state.page} of ${pages}`;
  byId('previous-page').disabled = state.page <= 1;
  byId('next-page').disabled = state.page >= pages;
  byId('review-section').hidden = false;
  byId('review-controls').hidden = false;
  updateMetrics(!state.metricsAnimated);
  state.metricsAnimated = true;
  document.querySelectorAll('th button[data-sort]').forEach((button) => {
    const key = button.dataset.sort;
    const labels = { date: 'Date', normalised_description: 'Transaction', amount: 'Amount', category_id: 'Category', tag_ids: 'Tags', toshl_description: 'Toshl description' };
    button.textContent = `${labels[key]}${state.sort === key ? (state.ascending ? ' ↑' : ' ↓') : ''}`;
  });
  const animateRows = !state.tableAnimated && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const renderedRows = visible.map((index, rowPosition) => {
    const row = state.transactions[index];
    const categories = `<option value="">Choose…</option>${state.categories.map((item) => `<option value="${esc(item.id)}" ${String(row.category_id || '') === String(item.id) ? 'selected' : ''}>${esc(item.name)}</option>`).join('')}`;
    const actions = ['Include', 'Omit once', 'Omit always'];
    const entryClass = animateRows ? ' class="row-enter"' : '';
    const entryDelay = animateRows ? ` style="--row-delay:${Math.min(rowPosition, 8) * 24}ms"` : '';
    return `<tr${entryClass}${entryDelay} data-index="${index}">
      <td><span class="date-main">${esc(formatDate(row.date))}</span><span class="date-type">${esc(row.type || '')}</span></td>
      <td><div class="normalized">${esc(row.normalised_description || '(no normalized description)')}</div><span class="raw-description" title="${esc(row.description || '')}">${esc(row.description || '')}</span></td>
      <td class="amount-cell"><input class="cell-input" type="number" step="0.01" value="${esc(row.amount)}" aria-label="Amount"></td>
      <td><select class="category-select${row.category_id ? ' has-value' : ''}" aria-label="Category">${categories}</select></td>
      <td>${tagPicker(row, index)}</td>
      <td><input class="cell-input description-input" type="text" value="${esc(row.toshl_description || '')}" placeholder="Description" aria-label="Toshl description"></td>
      <td><select class="inclusion-select" aria-label="Inclusion">${actions.map((value) => `<option ${row.omit_action === value ? 'selected' : ''}>${value}</option>`).join('')}</select></td>
    </tr>`;
  }).join('');
  byId('transaction-rows').innerHTML = renderedRows;
  const tableWrap = document.querySelector('.table-wrap');
  const hasTransactions = state.transactions.length > 0;
  tableWrap.classList.toggle('is-empty', !hasTransactions);
  tableWrap.classList.toggle('has-transactions', hasTransactions);
  const emptyState = byId('table-empty-state');
  emptyState.hidden = hasTransactions;
  if (!hasTransactions) emptyState.textContent = 'No transactions found for the selected files and date range.';
  state.tableAnimated = true;
}

byId('process-button').addEventListener('click', async () => {
  const button = byId('process-button');
  const status = byId('process-status');
  const form = new FormData();
  if (byId('hdfc').files[0]) form.append('hdfc', byId('hdfc').files[0]);
  if (byId('icici').files[0]) form.append('icici', byId('icici').files[0]);
  for (const [key, id] of Object.entries({ start_date: 'start-date', end_date: 'end-date', taxonomy: 'taxonomy-path', mappings: 'mappings-path', omissions: 'omissions-path' })) {
    form.append(key, byId(id).value);
  }
  setBusy(button, true, 'Processing statements…');
  showStatus(status, 'Parsing, normalizing, and matching locally.');
  try {
    const data = await responseJson(await fetch('/api/process', { method: 'POST', body: form }));
    state.transactions = data.transactions;
    state.categories = data.categories;
    state.tags = data.tags;
    state.categoryTags = data.category_tags;
    state.unassigned = data.unassigned_tag_ids;
    categoryName.clear(); tagName.clear();
    state.categories.forEach((item) => categoryName.set(String(item.id), item.name));
    state.tags.forEach((item) => tagName.set(String(item.id), item.name));
    state.page = 1; state.sort = 'date'; state.ascending = false; state.ready = true;
    state.metricsAnimated = false; state.tableAnimated = false;
    byId('downloads').hidden = false;
    showStatus(status, `Processed ${state.transactions.length} transaction(s).`);
    showStatus(byId('save-status'));
    render();
    byId('review-section').scrollIntoView({ behavior: 'smooth', block: 'start' });
  } catch (error) {
    showStatus(status, error.message, true);
  } finally { setBusy(button, false); }
});

byId('transaction-rows').addEventListener('input', (event) => {
  const tr = event.target.closest('tr'); if (!tr) return;
  const row = state.transactions[Number(tr.dataset.index)];
  if (event.target.matches('input[type=number]')) row.amount = event.target.value;
  if (event.target.matches('.description-input')) row.toshl_description = event.target.value;
});
byId('transaction-rows').addEventListener('change', (event) => {
  const tr = event.target.closest('tr'); if (!tr) return;
  const index = Number(tr.dataset.index); const row = state.transactions[index];
  if (event.target.matches('.category-select')) {
    row.category_id = event.target.value || null;
    const valid = new Set(allowedTags(row.category_id));
    row.tag_ids = (row.tag_ids || []).filter((id) => valid.has(String(id)));
    render();
  } else if (event.target.matches('.inclusion-select')) {
    row.omit_action = event.target.value;
  } else if (event.target.matches('input[data-tag-row]')) {
    const value = event.target.value;
    const current = new Set((row.tag_ids || []).map(String));
    if (event.target.checked) current.add(value); else current.delete(value);
    row.tag_ids = [...current];
    const details = event.target.closest('details');
    const summary = details.querySelector('summary');
    summary.innerHTML = tagSummary(row.tag_ids, row.category_id);
    summary.title = row.tag_ids.map((id) => tagName.get(id)).filter(Boolean).join(', ') || 'Choose tags';
  }
});

document.querySelectorAll('th button[data-sort]').forEach((button) => button.addEventListener('click', () => {
  if (state.sort === button.dataset.sort) state.ascending = !state.ascending;
  else { state.sort = button.dataset.sort; state.ascending = !['date', 'amount'].includes(state.sort); }
  state.page = 1; render();
}));
byId('page-size').addEventListener('change', (event) => {
  state.pageSize = event.target.value === 'all' ? 'all' : Number(event.target.value);
  state.page = 1;
  render();
});
byId('previous-page').addEventListener('click', () => { state.page--; render(); document.querySelector('.table-wrap').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
byId('next-page').addEventListener('click', () => { state.page++; render(); document.querySelector('.table-wrap').scrollIntoView({ behavior: 'smooth', block: 'start' }); });

async function downloadReview(kind, button) {
  const status = byId('save-status');
  const otherButton = byId(kind === 'reviewed' ? 'download-toshl' : 'download-reviewed');
  const originalLabel = button.textContent;
  setBusy(button, true, kind === 'reviewed' ? 'Preparing reviewed CSV…' : 'Preparing Toshl CSV…');
  otherButton.disabled = true;
  showStatus(status, 'Preparing the selected CSV…');
  try {
    const response = await fetch('/api/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        transactions: state.transactions,
        update_mappings: byId('update-mappings').checked,
        skip_unclassified: byId('skip-unclassified').checked,
        download_kind: kind,
      }),
    });
    if (!response.ok) {
      const error = await response.json();
      throw new Error(error.error || `Request failed (${response.status})`);
    }
    const blob = await response.blob();
    const filename = kind === 'reviewed' ? 'matched_reviewed.csv' : 'matched_reviewed_toshl.csv';
    const objectUrl = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = objectUrl;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    const exported = response.headers.get('X-Review-Exported') || '0';
    const skipped = response.headers.get('X-Review-Skipped') || '0';
    const mappingMessage = kind !== 'toshl'
      ? 'Mappings were not updated; use the Toshl import download to apply mapping changes.'
      : byId('update-mappings').checked
        ? `Mappings: ${response.headers.get('X-Mappings-Added') || '0'} added, ${response.headers.get('X-Mappings-Changed') || '0'} changed, ${response.headers.get('X-Mappings-Unchanged') || '0'} unchanged.`
        : 'Mapping updates were off.';
    showStatus(status, `${originalLabel} ready. ${exported} Toshl row(s); ${skipped} unclassified row(s) skipped. ${mappingMessage}`);
  } catch (error) { showStatus(status, error.message, true); }
  finally {
    setBusy(button, false);
    otherButton.disabled = false;
  }
}

byId('download-reviewed').addEventListener('click', (event) => downloadReview('reviewed', event.currentTarget));
byId('download-toshl').addEventListener('click', (event) => downloadReview('toshl', event.currentTarget));

const siteHeader = document.querySelector('.site-header');
const heroHeading = byId('setup-title');
if ('IntersectionObserver' in window) {
  const headerObserver = new IntersectionObserver(([entry]) => {
    siteHeader.classList.toggle('is-visible', !entry.isIntersecting && entry.boundingClientRect.bottom <= 0);
  });
  headerObserver.observe(heroHeading);
} else {
  const updateHeaderVisibility = () => {
    siteHeader.classList.toggle('is-visible', heroHeading.getBoundingClientRect().bottom <= 0);
  };
  window.addEventListener('scroll', updateHeaderVisibility, { passive: true });
  updateHeaderVisibility();
}

const currentDate = new Date();
byId('footer-date').textContent = `${currentDate.toLocaleString('en', { month: 'long' })}, ${currentDate.getFullYear()}`;

loadConfig();
