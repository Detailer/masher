const state = {
  transactions: [], categories: [], tags: [], categoryTags: [], unassigned: [],
  page: 1, pageSize: 50, sort: 'date', ascending: false, ready: false,
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
    button.dataset.label = button.textContent;
    button.textContent = label;
  } else if (button.dataset.label) {
    button.textContent = button.dataset.label;
    delete button.dataset.label;
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
function updateMetrics() {
  const count = state.transactions.length;
  const matched = state.transactions.filter((row) => row.category_id).length;
  byId('metrics').innerHTML = [
    ['Transactions', count], ['Matched', matched], ['Need a category', count - matched],
  ].map(([label, value]) => `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`).join('');
}
function render() {
  if (!state.ready) return;
  const order = sortedTransactions();
  const pages = Math.max(1, Math.ceil(order.length / state.pageSize));
  state.page = Math.min(Math.max(state.page, 1), pages);
  const start = (state.page - 1) * state.pageSize;
  const visible = order.slice(start, start + state.pageSize);
  byId('page-indicator').textContent = `Page ${state.page} of ${pages}`;
  byId('previous-page').disabled = state.page <= 1;
  byId('next-page').disabled = state.page >= pages;
  byId('review-section').hidden = false;
  updateMetrics();
  document.querySelectorAll('th button[data-sort]').forEach((button) => {
    const key = button.dataset.sort;
    const labels = { date: 'Date', normalised_description: 'Transaction', amount: 'Amount', category_id: 'Category', tag_ids: 'Tags', toshl_description: 'Description' };
    button.textContent = `${labels[key]}${state.sort === key ? (state.ascending ? ' ↑' : ' ↓') : ''}`;
  });
  byId('transaction-rows').innerHTML = visible.map((index) => {
    const row = state.transactions[index];
    const categories = `<option value="">Choose…</option>${state.categories.map((item) => `<option value="${esc(item.id)}" ${String(row.category_id || '') === String(item.id) ? 'selected' : ''}>${esc(item.name)}</option>`).join('')}`;
    const actions = ['Include', 'Omit once', 'Omit always'];
    return `<tr data-index="${index}">
      <td><span class="date-main">${esc(formatDate(row.date))}</span><span class="date-type">${esc(row.type || '')}</span></td>
      <td><div class="normalized">${esc(row.normalised_description || '(no normalized description)')}</div><span class="raw-description" title="${esc(row.description || '')}">${esc(row.description || '')}</span></td>
      <td class="amount-cell"><input class="cell-input" type="number" step="0.01" value="${esc(row.amount)}" aria-label="Amount"></td>
      <td><select class="category-select${row.category_id ? ' has-value' : ''}" aria-label="Category">${categories}</select></td>
      <td>${tagPicker(row, index)}</td>
      <td><input class="cell-input description-input" type="text" value="${esc(row.toshl_description || '')}" placeholder="Description" aria-label="Toshl description"></td>
      <td><select class="inclusion-select" aria-label="Inclusion">${actions.map((value) => `<option ${row.omit_action === value ? 'selected' : ''}>${value}</option>`).join('')}</select></td>
    </tr>`;
  }).join('');
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
    byId('downloads').hidden = true;
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
byId('page-size').addEventListener('change', (event) => { state.pageSize = Number(event.target.value); state.page = 1; render(); });
byId('previous-page').addEventListener('click', () => { state.page--; render(); document.querySelector('.table-wrap').scrollIntoView({ behavior: 'smooth', block: 'start' }); });
byId('next-page').addEventListener('click', () => { state.page++; render(); document.querySelector('.table-wrap').scrollIntoView({ behavior: 'smooth', block: 'start' }); });

byId('save-button').addEventListener('click', async () => {
  const button = byId('save-button'); const status = byId('save-status');
  setBusy(button, true, 'Saving review…'); showStatus(status, 'Validating edits, updating mappings, and preparing CSV files.');
  try {
    const data = await responseJson(await fetch('/api/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ transactions: state.transactions, skip_unclassified: byId('skip-unclassified').checked }),
    }));
    showStatus(status, `${data.message} ${data.exported} row(s) ready for Toshl; ${data.skipped} skipped. Mappings: ${data.mappings.added} added, ${data.mappings.changed} changed, ${data.mappings.unchanged} unchanged.`);
    byId('downloads').hidden = false;
  } catch (error) { showStatus(status, error.message, true); }
  finally { setBusy(button, false); }
});

loadConfig();
