<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import {
  getFinancialDataCapabilities, listFinancialTables, getFinancialTableSchema,
  queryFinancialRows, getFinancialRow, createFinancialRow, updateFinancialRow,
  deleteFinancialRow,
} from '../financialDataApi.js';
import {
  PAGE_SIZES, MAX_FILTERS, fieldOperators, buildFilters, initialFormValues,
  buildWriteData, buildPageQuery, canMoveToNextPage, displayValue, tableLabel,
} from '../financialDataModel.js';

const capabilities = ref(null);
const tables = ref([]);
const tableSearch = ref('');
const selectedTable = ref('');
const schema = ref(null);
const rows = ref([]);
const pagination = ref({ has_more: false, next_after_id: null });
const catalogLoading = ref(true);
const catalogError = ref('');
const schemaLoading = ref(false);
const schemaError = ref('');
const rowsLoading = ref(false);
const rowsError = ref('');
const filterError = ref('');
const filterDrafts = ref([]);
const activeFilters = ref([]);
const sortBy = ref('_row_id');
const sortOrder = ref('asc');
const pageSize = ref(50);
const pageIndex = ref(0);
const cursors = ref([null]);
const modal = ref(null);
const dialogEl = ref(null);
const detailRow = ref(null);
const detailLoading = ref(false);
const detailError = ref('');
const formValues = ref({});
const formSearch = ref('');
const formError = ref('');
const saving = ref(false);
const statusMessage = ref('');

let destroyed = false;
let catalogVersion = 0;
let tableVersion = 0;
let rowsVersion = 0;
let modalVersion = 0;
let filterId = 0;
let statusTimer = null;
let returnFocus = null;

const canWrite = computed(() => capabilities.value?.canWrite === true);
const selectedDataset = computed(() => tables.value.find((item) => item.name === selectedTable.value));
const allFields = computed(() => schema.value?.fields || []);
const sourceFields = computed(() => allFields.value.filter((field) => field.name !== '_row_id'));
const previewFields = computed(() => sourceFields.value.slice(0, 7));
const searchedTables = computed(() => {
  const needle = tableSearch.value.trim().toLocaleLowerCase();
  const found = tables.value.filter((item) =>
    !needle || `${tableLabel(item)} ${item.title} ${item.name}`.toLocaleLowerCase().includes(needle));
  const current = selectedDataset.value;
  return current && !found.some((item) => item.name === current.name) ? [current, ...found] : found;
});
const tableCount = computed(() => schema.value?.row_count ?? selectedDataset.value?.row_count);
const cursorMode = computed(() => sortBy.value === '_row_id' && sortOrder.value === 'asc');
const canNext = computed(() => canMoveToNextPage({
  pageSize: pageSize.value, pageIndex: pageIndex.value, sortBy: sortBy.value,
  order: sortOrder.value, hasMore: pagination.value.has_more,
}));
const shownFrom = computed(() => rows.value.length ? pageIndex.value * pageSize.value + 1 : 0);
const shownTo = computed(() => rows.value.length ? shownFrom.value + rows.value.length - 1 : 0);
const filteredFormFields = computed(() => {
  const needle = formSearch.value.trim().toLocaleLowerCase();
  return sourceFields.value.filter((field) =>
    !needle || `${field.name} ${field.description || ''}`.toLocaleLowerCase().includes(needle));
});

function errorText(error) {
  return error instanceof Error ? error.message : '操作失败，请重试。';
}

function announce(message) {
  statusMessage.value = message;
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = setTimeout(() => { statusMessage.value = ''; }, 7000);
}

async function loadCatalog() {
  const version = ++catalogVersion;
  catalogLoading.value = true;
  catalogError.value = '';
  try {
    const [rights, catalog] = await Promise.all([
      getFinancialDataCapabilities(), listFinancialTables(),
    ]);
    if (destroyed || version !== catalogVersion) return;
    capabilities.value = rights;
    tables.value = Array.isArray(catalog.data) ? catalog.data : [];
    if (!rights.canRead) throw new Error('当前账号没有数据查看权限。');
    if (tables.value.length) await chooseTable(tables.value[0].name);
  } catch (error) {
    if (!destroyed && version === catalogVersion) catalogError.value = errorText(error);
  } finally {
    if (!destroyed && version === catalogVersion) catalogLoading.value = false;
  }
}

function resetPaging() {
  pageIndex.value = 0;
  cursors.value = [null];
  pagination.value = { has_more: false, next_after_id: null };
}

async function chooseTable(name) {
  if (!tables.value.some((item) => item.name === name)) return;
  ++tableVersion;
  ++rowsVersion;
  const version = tableVersion;
  selectedTable.value = name;
  statusMessage.value = '';
  if (statusTimer) clearTimeout(statusTimer);
  statusTimer = null;
  schema.value = null;
  rows.value = [];
  schemaError.value = '';
  rowsError.value = '';
  filterError.value = '';
  filterDrafts.value = [];
  activeFilters.value = [];
  sortBy.value = '_row_id';
  sortOrder.value = 'asc';
  resetPaging();
  closeDialog();
  await loadSchema(version);
}

async function loadSchema(version = tableVersion) {
  if (!selectedTable.value) return;
  schemaLoading.value = true;
  schemaError.value = '';
  try {
    const result = await getFinancialTableSchema(selectedTable.value);
    if (destroyed || version !== tableVersion) return;
    schema.value = result;
    await loadRows();
  } catch (error) {
    if (!destroyed && version === tableVersion) schemaError.value = errorText(error);
  } finally {
    if (!destroyed && version === tableVersion) schemaLoading.value = false;
  }
}

async function loadRows() {
  if (!schema.value) return;
  const version = ++rowsVersion;
  const selected = selectedTable.value;
  rowsLoading.value = true;
  rowsError.value = '';
  rows.value = [];
  try {
    const query = buildPageQuery({
      pageSize: pageSize.value, pageIndex: pageIndex.value, sortBy: sortBy.value,
      order: sortOrder.value, filters: activeFilters.value, cursors: cursors.value,
    });
    query.columns = ['_row_id', ...previewFields.value.map((field) => field.name)];
    const result = await queryFinancialRows(selected, query);
    if (destroyed || version !== rowsVersion || selected !== selectedTable.value) return;
    rows.value = Array.isArray(result.data) ? result.data : [];
    pagination.value = result.pagination || { has_more: false, next_after_id: null };
    if (cursorMode.value && pagination.value.has_more &&
        Number.isSafeInteger(pagination.value.next_after_id)) {
      cursors.value[pageIndex.value + 1] = pagination.value.next_after_id;
    }
  } catch (error) {
    if (!destroyed && version === rowsVersion) rowsError.value = errorText(error);
  } finally {
    if (!destroyed && version === rowsVersion) rowsLoading.value = false;
  }
}

async function refreshCounts() {
  const version = tableVersion;
  const selected = selectedTable.value;
  try {
    const result = await getFinancialTableSchema(selected);
    if (destroyed || version !== tableVersion) return;
    schema.value = result;
    tables.value = tables.value.map((item) =>
      item.name === selected ? { ...item, row_count: result.row_count } : item);
  } catch (_) { /* Row refresh still gives a usable page when metadata is temporarily unavailable. */ }
}

async function refreshAfterWrite() {
  await loadRows();
  if (!rowsError.value && !rows.value.length && pageIndex.value > 0) {
    pageIndex.value -= 1;
    await loadRows();
  }
  await refreshCounts();
}

function addFilter() {
  if (filterDrafts.value.length >= MAX_FILTERS || !allFields.value.length) return;
  const first = sourceFields.value[0] || allFields.value[0];
  filterDrafts.value.push({ id: ++filterId, field: first.name, op: 'eq', value: '', nullMode: 'null' });
  filterError.value = '';
}

function removeFilter(id) {
  filterDrafts.value = filterDrafts.value.filter((item) => item.id !== id);
  filterError.value = '';
}

function selectedField(name) {
  return allFields.value.find((field) => field.name === name);
}

function onFilterFieldChanged(item) {
  item.op = 'eq';
  item.value = '';
  item.nullMode = 'null';
}

function applyFilters() {
  try {
    const filters = buildFilters(filterDrafts.value, allFields.value);
    filterError.value = '';
    activeFilters.value = filters;
    resetPaging();
    void loadRows();
  } catch (error) {
    filterError.value = errorText(error);
  }
}

function clearFilters() {
  filterDrafts.value = [];
  activeFilters.value = [];
  filterError.value = '';
  resetPaging();
  void loadRows();
}

function changeOrdering() {
  resetPaging();
  void loadRows();
}

function previousPage() {
  if (pageIndex.value <= 0 || rowsLoading.value) return;
  pageIndex.value -= 1;
  void loadRows();
}

function nextPage() {
  if (!canNext.value || rowsLoading.value) return;
  pageIndex.value += 1;
  void loadRows();
}

function closeDialog() {
  if (saving.value) return;
  ++modalVersion;
  modal.value = null;
  detailRow.value = null;
  detailError.value = '';
  formError.value = '';
  const target = returnFocus;
  returnFocus = null;
  if (target?.isConnected) nextTick(() => target.focus());
}

function rememberFocus() {
  if (!modal.value && document.activeElement instanceof HTMLElement) {
    returnFocus = document.activeElement;
  }
}

function openCreate() {
  if (!canWrite.value || !schema.value) return;
  rememberFocus();
  ++modalVersion;
  detailRow.value = null;
  formValues.value = initialFormValues(allFields.value);
  formSearch.value = '';
  formError.value = '';
  modal.value = { kind: 'create' };
}

function openViewWithRow(row) {
  rememberFocus();
  ++modalVersion;
  detailRow.value = row;
  detailLoading.value = false;
  detailError.value = '';
  modal.value = { kind: 'view', rowId: row._row_id };
}

async function openView(row) {
  if (!row || !Number.isSafeInteger(row._row_id)) return;
  rememberFocus();
  ++modalVersion;
  detailRow.value = null;
  detailError.value = '';
  modal.value = { kind: 'view', rowId: row._row_id };
  await loadDetail();
}

async function loadDetail() {
  if (modal.value?.kind !== 'view') return;
  const version = modalVersion;
  const selected = selectedTable.value;
  const rowId = modal.value.rowId;
  detailLoading.value = true;
  detailError.value = '';
  try {
    const result = await getFinancialRow(selected, rowId);
    if (destroyed || version !== modalVersion || selected !== selectedTable.value) return;
    detailRow.value = result.data;
  } catch (error) {
    if (!destroyed && version === modalVersion) detailError.value = errorText(error);
  } finally {
    if (!destroyed && version === modalVersion) detailLoading.value = false;
  }
}

function openEdit() {
  if (!canWrite.value || !detailRow.value) return;
  ++modalVersion;
  formValues.value = initialFormValues(allFields.value, detailRow.value);
  formSearch.value = '';
  formError.value = '';
  modal.value = { kind: 'edit', rowId: detailRow.value._row_id };
}

function openDelete() {
  if (!canWrite.value || !detailRow.value) return;
  ++modalVersion;
  formError.value = '';
  modal.value = { kind: 'delete', rowId: detailRow.value._row_id };
}

async function saveForm() {
  if (saving.value || !canWrite.value || !['create', 'edit'].includes(modal.value?.kind)) return;
  let data;
  try {
    data = buildWriteData(allFields.value, formValues.value,
      modal.value.kind === 'edit' ? detailRow.value : null);
  } catch (error) {
    formError.value = errorText(error);
    return;
  }
  const kind = modal.value.kind;
  const rowId = modal.value.rowId;
  const selected = selectedTable.value;
  formError.value = '';
  saving.value = true;
  try {
    const result = kind === 'create'
      ? await createFinancialRow(selected, data)
      : await updateFinancialRow(selected, rowId, data);
    if (destroyed) return;
    saving.value = false;
    openViewWithRow(result.data);
    announce(`${kind === 'create' ? '已新增' : '已保存'}记录 #${result.data._row_id}`);
    await refreshAfterWrite();
  } catch (error) {
    if (!destroyed) formError.value = errorText(error);
  } finally {
    saving.value = false;
  }
}

async function confirmDelete() {
  if (saving.value || !canWrite.value || modal.value?.kind !== 'delete') return;
  const rowId = modal.value.rowId;
  const selected = selectedTable.value;
  formError.value = '';
  saving.value = true;
  try {
    await deleteFinancialRow(selected, rowId);
    if (destroyed) return;
    saving.value = false;
    closeDialog();
    announce(`已删除记录 #${rowId}`);
    await refreshAfterWrite();
  } catch (error) {
    if (!destroyed) formError.value = errorText(error);
  } finally {
    saving.value = false;
  }
}

function onDialogKeydown(event) {
  // Keep the workbench's global keyboard shortcuts out of the native modal.
  event.stopPropagation();
  if (event.key === 'Escape') {
    event.preventDefault();
    if (!saving.value) closeDialog();
  }
}

function onDialogCancel(event) {
  event.preventDefault();
  if (!saving.value) closeDialog();
}

watch(() => modal.value?.kind, async (kind) => {
  const dialog = dialogEl.value;
  if (!dialog) return;
  if (!kind) {
    if (dialog.open) dialog.close();
    return;
  }
  await nextTick();
  if (!modal.value || !dialogEl.value) return;
  if (!dialogEl.value.open) dialogEl.value.showModal();
  dialogEl.value.querySelector('[data-initial-focus]')?.focus();
});

onMounted(() => { void loadCatalog(); });
onBeforeUnmount(() => {
  destroyed = true;
  ++catalogVersion;
  ++tableVersion;
  ++rowsVersion;
  ++modalVersion;
  if (statusTimer) clearTimeout(statusTimer);
  if (dialogEl.value?.open) dialogEl.value.close();
});
</script>

<template>
  <section class="page financial-data-page" aria-label="数据管理">
    <header class="page-header data-header">
      <div>
        <h1 class="page-title">数据管理</h1>
        <p class="page-subtitle">查看与维护比赛授权数据</p>
      </div>
      <div class="toolbar-actions">
        <span v-if="capabilities" class="access-label">{{ canWrite ? '可编辑' : '只读' }}</span>
        <button class="secondary-button" type="button" :disabled="!schema || rowsLoading" @click="loadRows">刷新数据</button>
        <button v-if="canWrite" class="primary-button" type="button" :disabled="!schema || schemaLoading" @click="openCreate">新增记录</button>
      </div>
    </header>

    <div class="page-body data-body">
      <p v-if="statusMessage" class="notice success" role="status">{{ statusMessage }}</p>
      <div v-if="catalogLoading" class="state-panel" role="status">正在加载数据表…</div>
      <div v-else-if="catalogError" class="state-panel error-panel" role="alert">
        <p>{{ catalogError }}</p>
        <button class="secondary-button" type="button" @click="loadCatalog">重试</button>
      </div>
      <div v-else-if="!tables.length" class="state-panel">暂无可用的数据表。</div>
      <template v-else>
        <div class="data-controls">
          <label class="control-group table-search">
            <span>搜索数据表</span>
            <input v-model="tableSearch" type="search" placeholder="表名或中文名称" aria-label="搜索数据表">
          </label>
          <label class="control-group table-pick">
            <span>选择数据表 · {{ tables.length }} 张</span>
            <select v-model="selectedTable" aria-label="选择数据表" @change="chooseTable(selectedTable)">
              <option v-for="item in searchedTables" :key="item.name" :value="item.name" :title="item.title">{{ tableLabel(item) }} · {{ item.name }}</option>
            </select>
          </label>
          <div class="table-meta">
            <strong :title="selectedDataset?.title">{{ tableLabel(selectedDataset) }}</strong>
            <span v-if="tableCount != null">{{ Number(tableCount).toLocaleString('zh-CN') }} 条记录</span>
            <span v-if="schema">{{ sourceFields.length }} 个字段</span>
          </div>
        </div>
        <p v-if="!canWrite && capabilities" class="read-note">当前账号可查看和筛选数据；修改权限由网站管理员分配。</p>

        <div v-if="schemaLoading" class="state-panel" role="status">正在读取字段信息…</div>
        <div v-else-if="schemaError" class="state-panel error-panel" role="alert">
          <p>{{ schemaError }}</p>
          <button class="secondary-button" type="button" @click="loadSchema()">重试</button>
        </div>
        <template v-else-if="schema">
          <section class="data-section filter-section" aria-labelledby="filter-heading">
            <div class="section-head">
              <div>
                <h2 id="filter-heading">筛选条件</h2>
                <p>多个条件同时满足；日期文本请按原格式比较。</p>
              </div>
              <button class="secondary-button" type="button" :disabled="filterDrafts.length >= MAX_FILTERS" @click="addFilter">添加筛选条件</button>
            </div>
            <div v-if="filterDrafts.length" class="filter-list">
              <div v-for="item in filterDrafts" :key="item.id" class="filter-row">
                <label class="control-group">
                  <span>字段</span>
                  <select v-model="item.field" :aria-label="`筛选字段 ${item.id}`" @change="onFilterFieldChanged(item)">
                    <option v-for="field in allFields" :key="field.name" :value="field.name">{{ field.name }}</option>
                  </select>
                </label>
                <label class="control-group">
                  <span>条件</span>
                  <select v-model="item.op" :aria-label="`筛选条件 ${item.id}`">
                    <option v-for="op in fieldOperators(selectedField(item.field))" :key="op.value" :value="op.value">{{ op.label }}</option>
                  </select>
                </label>
                <label class="control-group filter-value">
                  <span>值</span>
                  <select v-if="item.op === 'isnull'" v-model="item.nullMode" :aria-label="`筛选值 ${item.id}`">
                    <option value="null">为空 (NULL)</option>
                    <option value="notnull">非空</option>
                  </select>
                  <input v-else v-model="item.value" type="text" :inputmode="selectedField(item.field)?.type === 'TEXT' ? 'text' : 'decimal'" :aria-label="`筛选值 ${item.id}`" :placeholder="selectedField(item.field)?.type === 'TEXT' ? '文本值，空字符串可直接留空' : '输入数值'" @keydown.enter.prevent="applyFilters">
                </label>
                <button class="subtle-button danger-button remove-filter" type="button" :aria-label="`移除筛选条件 ${item.id}`" @click="removeFilter(item.id)">移除</button>
              </div>
            </div>
            <p v-else class="filter-empty">尚未添加筛选条件，当前显示全部记录。</p>
            <p v-if="filterError" class="field-error" role="alert">{{ filterError }}</p>
            <div class="filter-actions">
              <span class="applied-count">已应用 {{ activeFilters.length }} 个条件</span>
              <button class="subtle-button" type="button" :disabled="!filterDrafts.length && !activeFilters.length" @click="clearFilters">重置筛选</button>
              <button class="primary-button" type="button" :disabled="rowsLoading" @click="applyFilters">应用筛选</button>
            </div>
          </section>

          <section class="data-section result-section" aria-labelledby="results-heading">
            <div class="section-head results-head">
              <div>
                <h2 id="results-heading">数据记录</h2>
                <p>列表预览前 {{ previewFields.length }} 个字段；点击查看可阅读全部字段。</p>
              </div>
              <div class="sort-controls">
                <label class="control-group">
                  <span>排序字段</span>
                  <select v-model="sortBy" aria-label="排序字段" @change="changeOrdering">
                    <option v-for="field in allFields" :key="field.name" :value="field.name">{{ field.name }}</option>
                  </select>
                </label>
                <label class="control-group small-select">
                  <span>排序方向</span>
                  <select v-model="sortOrder" aria-label="排序方向" @change="changeOrdering">
                    <option value="asc">升序</option>
                    <option value="desc">降序</option>
                  </select>
                </label>
                <label class="control-group small-select">
                  <span>每页条数</span>
                  <select v-model.number="pageSize" aria-label="每页条数" @change="changeOrdering">
                    <option v-for="size in PAGE_SIZES" :key="size" :value="size">{{ size }}</option>
                  </select>
                </label>
              </div>
            </div>
            <div v-if="rowsError" class="inline-error" role="alert">
              <span>{{ rowsError }}</span>
              <button class="secondary-button" type="button" @click="loadRows">重试</button>
            </div>
            <div v-else-if="rowsLoading" class="state-panel" role="status">正在读取记录…</div>
            <div v-else-if="!rows.length" class="state-panel">没有符合条件的记录。</div>
            <div v-else class="table-scroll">
              <table class="records-table">
                <thead><tr>
                  <th scope="col">行号</th>
                  <th v-for="field in previewFields" :key="field.name" scope="col" :title="field.description || field.name">{{ field.name }}</th>
                  <th scope="col">操作</th>
                </tr></thead>
                <tbody><tr v-for="row in rows" :key="row._row_id">
                  <th scope="row">#{{ row._row_id }}</th>
                  <td v-for="field in previewFields" :key="field.name" :title="displayValue(row[field.name])" :class="{ empty: row[field.name] == null }">{{ displayValue(row[field.name]) }}</td>
                  <td><button class="subtle-button row-action" type="button" :aria-label="`查看记录 #${row._row_id}`" @click="openView(row)">查看</button></td>
                </tr></tbody>
              </table>
            </div>
            <div class="pagination-bar">
              <span>第 {{ pageIndex + 1 }} 页<span v-if="rows.length"> · 本页第 {{ shownFrom.toLocaleString('zh-CN') }}–{{ shownTo.toLocaleString('zh-CN') }} 条</span></span>
              <div>
                <button class="secondary-button" type="button" :disabled="pageIndex === 0 || rowsLoading" @click="previousPage">上一页</button>
                <button class="secondary-button" type="button" :disabled="!canNext || rowsLoading" @click="nextPage">下一页</button>
              </div>
            </div>
            <p v-if="!cursorMode && pagination.has_more && !canNext" class="page-limit">当前排序已到达翻页上限，请添加筛选条件缩小范围。</p>
          </section>
        </template>
      </template>
    </div>

    <dialog ref="dialogEl" class="data-dialog" aria-labelledby="data-dialog-title" @cancel="onDialogCancel" @keydown="onDialogKeydown">
      <template v-if="modal">
        <div class="dialog-head">
          <div>
            <h2 id="data-dialog-title">{{ modal.kind === 'create' ? '新增记录' : modal.kind === 'edit' ? `修改记录 #${modal.rowId}` : modal.kind === 'delete' ? '确认删除' : `记录 #${modal.rowId}` }}</h2>
            <p :title="selectedDataset?.title">{{ tableLabel(selectedDataset) }} · {{ selectedTable }}</p>
          </div>
          <button class="dialog-close" type="button" aria-label="关闭弹窗" :disabled="saving" @click="closeDialog">×</button>
        </div>

        <div v-if="modal.kind === 'view'" class="dialog-body">
          <div v-if="detailLoading" class="state-panel" role="status">正在读取完整记录…</div>
          <div v-else-if="detailError" class="inline-error" role="alert">
            <span>{{ detailError }}</span>
            <button class="secondary-button" type="button" @click="loadDetail">重试</button>
          </div>
          <dl v-else-if="detailRow" class="detail-grid">
            <div v-for="field in allFields" :key="field.name" class="detail-pair">
              <dt :title="field.description || field.name">{{ field.name }} <small>{{ field.type }}</small></dt>
              <dd :class="{ empty: detailRow[field.name] == null, blank: detailRow[field.name] === '' }">{{ displayValue(detailRow[field.name]) }}</dd>
            </div>
          </dl>
        </div>

        <div v-else-if="modal.kind === 'create' || modal.kind === 'edit'" class="dialog-body">
          <p class="form-intro">{{ modal.kind === 'create' ? '只需填写要写入的字段；未填写字段默认为 NULL。' : '只提交选择修改的字段；原值会保留。' }} 文本空字符串与 NULL 分开设置。</p>
          <label class="control-group form-search">
            <span>搜索字段 · {{ sourceFields.length }} 个</span>
            <input v-model="formSearch" type="search" aria-label="搜索字段" placeholder="字段名或说明" data-initial-focus>
          </label>
          <p v-if="!filteredFormFields.length" class="filter-empty">没有匹配的字段。</p>
          <div v-else class="form-fields">
            <div v-for="field in filteredFormFields" :key="field.name" class="edit-field">
              <div class="edit-field-head">
                <strong>{{ field.name }}</strong>
                <span>{{ field.type }}</span>
              </div>
              <p v-if="field.description" class="field-description">{{ field.description }}</p>
              <p v-if="modal.kind === 'edit'" class="field-original">原值：{{ displayValue(detailRow?.[field.name]) }}</p>
              <div class="edit-controls">
                <label class="control-group mode-select">
                  <span>填写方式</span>
                  <select v-model="formValues[field.name].mode" :aria-label="`${field.name} 填写方式`">
                    <option value="omit">{{ modal.kind === 'create' ? '不填写' : '保持原值' }}</option>
                    <option value="value">输入值</option>
                    <option value="null">设为 NULL</option>
                  </select>
                </label>
                <label v-if="formValues[field.name].mode === 'value'" class="control-group input-value">
                  <span>字段值</span>
                  <textarea v-if="field.type === 'TEXT'" v-model="formValues[field.name].text" rows="3" :aria-label="`${field.name} 字段值`" placeholder="可留空，表示空字符串"></textarea>
                  <input v-else v-model="formValues[field.name].text" type="text" inputmode="decimal" :aria-label="`${field.name} 字段值`" placeholder="输入数值">
                </label>
              </div>
            </div>
          </div>
        </div>

        <div v-else-if="modal.kind === 'delete'" class="dialog-body delete-body">
          <p>确定删除 <strong>{{ tableLabel(selectedDataset) }}</strong> 中的记录 <strong>#{{ modal.rowId }}</strong> 吗？此操作会从数据表中移除该记录。</p>
        </div>

        <p v-if="formError" class="dialog-error" role="alert">{{ formError }}</p>
        <div class="dialog-actions">
          <template v-if="modal.kind === 'view'">
            <button class="secondary-button" type="button" data-initial-focus @click="closeDialog">关闭</button>
            <button v-if="canWrite && detailRow" class="secondary-button" type="button" @click="openEdit">编辑记录</button>
            <button v-if="canWrite && detailRow" class="subtle-button danger-button" type="button" @click="openDelete">删除记录</button>
          </template>
          <template v-else-if="modal.kind === 'create' || modal.kind === 'edit'">
            <button class="secondary-button" type="button" :disabled="saving" @click="closeDialog">取消</button>
            <button class="primary-button" type="button" :disabled="saving" :aria-busy="saving" @click="saveForm">{{ modal.kind === 'create' ? '保存新增' : '保存修改' }}</button>
          </template>
          <template v-else>
            <button class="secondary-button" type="button" data-initial-focus :disabled="saving" @click="closeDialog">取消</button>
            <button class="delete-button" type="button" :disabled="saving" :aria-busy="saving" @click="confirmDelete">确认删除</button>
          </template>
        </div>
      </template>
    </dialog>
  </section>
</template>

<style scoped>
.financial-data-page { min-width: 0; }
.data-header { align-items: flex-start; }
.data-header .toolbar-actions { padding-top: 3px; flex-wrap: wrap; justify-content: flex-end; }
.access-label { color: var(--accent); font-size: 11px; border: 1px solid var(--line); background: var(--accent-dark); border-radius: 100px; padding: 6px 9px; }
.data-body { padding-top: 10px; }
.notice { border-radius: 10px; padding: 10px 13px; font-size: 12px; margin: 0 0 14px; }
.notice.success { border: 1px solid #c3e5cf; background: var(--success-wash); color: var(--success-ink); }
.data-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 12px; padding: 16px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel); }
.control-group { display: grid; gap: 6px; min-width: 0; color: var(--muted); font-size: 11px; }
.control-group > span { white-space: nowrap; }
.control-group input, .control-group select, .control-group textarea { min-width: 0; width: 100%; min-height: 36px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface-2); color: var(--text); padding: 0 10px; font-size: 12px; outline: none; }
.control-group textarea { min-height: 74px; padding: 9px 10px; line-height: 1.45; resize: vertical; }
.control-group input:focus, .control-group select:focus, .control-group textarea:focus { border-color: var(--focus-ring); }
.control-group input::placeholder, .control-group textarea::placeholder { color: var(--dim); }
.table-search { flex: 1 1 180px; }
.table-pick { flex: 2 1 310px; }
.table-meta { display: flex; flex: 1 1 240px; flex-wrap: wrap; align-items: center; gap: 6px 12px; min-height: 36px; font-size: 11px; color: var(--muted); }
.table-meta strong { color: var(--text); font-size: 12px; }
.read-note { color: var(--muted); font-size: 11px; margin: 10px 2px 0; }
.state-panel { min-height: 95px; display: flex; flex-wrap: wrap; justify-content: center; align-items: center; gap: 12px; color: var(--muted); font-size: 12px; text-align: center; }
.error-panel { flex-direction: column; color: var(--danger); }
.error-panel p { margin: 0; }
.data-section { margin-top: 17px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel); overflow: hidden; }
.section-head { display: flex; align-items: start; justify-content: space-between; gap: 15px; padding: 18px 19px 12px; }
.section-head h2 { margin: 0; color: var(--text); font-size: 14px; font-weight: 620; }
.section-head p { margin: 5px 0 0; color: var(--muted); font-size: 11px; line-height: 1.4; }
.filter-list { display: grid; gap: 8px; padding: 0 19px 10px; }
.filter-row { display: grid; grid-template-columns: minmax(120px, 1.2fr) minmax(100px, .8fr) minmax(135px, 1.5fr) auto; align-items: end; gap: 9px; }
.remove-filter { min-height: 36px; }
.filter-empty { margin: 5px 19px 14px; color: var(--dim); font-size: 11px; }
.filter-actions { display: flex; align-items: center; justify-content: flex-end; gap: 8px; padding: 11px 19px 16px; border-top: 1px solid var(--line); }
.applied-count { margin-right: auto; color: var(--muted); font-size: 11px; }
.field-error { margin: 3px 19px 10px; color: var(--danger); font-size: 11px; }
.results-head { align-items: end; }
.sort-controls { display: flex; flex-wrap: wrap; align-items: end; gap: 8px; }
.sort-controls .control-group { min-width: 125px; }
.sort-controls .small-select { min-width: 90px; }
.table-scroll { width: 100%; overflow-x: auto; scrollbar-width: thin; border-top: 1px solid var(--line); }
.records-table { width: 100%; border-collapse: collapse; font-size: 11px; text-align: left; }
.records-table th, .records-table td { padding: 10px 12px; border-bottom: 1px solid var(--line); max-width: 215px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.records-table thead th { position: sticky; top: 0; color: var(--muted); background: var(--surface-2); font-weight: 600; }
.records-table tbody th { color: var(--accent); font-weight: 600; }
.records-table tbody tr:hover { background: var(--surface-2); }
.records-table td.empty { color: var(--dim); font-style: italic; }
.records-table th:last-child, .records-table td:last-child { position: sticky; right: 0; width: 70px; background: var(--panel); text-align: right; box-shadow: -1px 0 var(--line); }
.records-table tbody tr:hover td:last-child { background: var(--surface-2); }
.row-action { padding: 0 7px; min-height: 26px; color: var(--accent); }
.inline-error { display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 16px 19px; color: var(--danger); font-size: 12px; border-top: 1px solid var(--line); }
.pagination-bar { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: 12px; min-height: 61px; padding: 12px 19px; color: var(--muted); font-size: 11px; }
.pagination-bar > div { display: flex; gap: 7px; }
.page-limit { margin: -6px 19px 14px; color: var(--warning-ink); font-size: 11px; }
.data-dialog { position: fixed; inset: 0; margin: auto; width: min(900px, calc(100vw - 32px)); max-width: none; max-height: min(86vh, 900px); padding: 0; border: 1px solid var(--line); border-radius: 16px; background: var(--panel); color: var(--text); box-shadow: 0 24px 90px rgb(27 57 78 / 18%); overflow: hidden; }
.data-dialog::backdrop { background: rgba(8,7,10,.74); }
.dialog-head { display: flex; align-items: start; justify-content: space-between; gap: 15px; padding: 20px 22px 13px; border-bottom: 1px solid var(--line); }
.dialog-head h2 { margin: 0; font-size: 16px; font-weight: 620; }
.dialog-head p { margin: 5px 0 0; color: var(--muted); font-size: 11px; }
.dialog-close { width: 30px; height: 30px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); font-size: 23px; line-height: 1; }
.dialog-close:hover:not(:disabled) { background: var(--surface-2); }
.dialog-body { max-height: calc(min(86vh, 900px) - 141px); min-height: 100px; padding: 18px 22px; overflow-y: auto; scrollbar-width: thin; }
.detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 0 16px; margin: 0; }
.detail-pair { min-width: 0; display: grid; grid-template-columns: minmax(90px, 39%) minmax(0, 1fr); gap: 12px; padding: 10px 0; border-bottom: 1px solid var(--line); font-size: 11px; }
.detail-pair dt { min-width: 0; overflow-wrap: anywhere; color: var(--muted); }
.detail-pair dt small { display: block; margin-top: 3px; color: var(--dim); font-size: 9px; }
.detail-pair dd { min-width: 0; margin: 0; overflow-wrap: anywhere; white-space: pre-wrap; color: var(--text); }
.detail-pair dd.empty { color: var(--dim); font-style: italic; }
.detail-pair dd.blank { color: var(--accent); }
.form-intro { margin: 0 0 14px; color: var(--muted); font-size: 11px; line-height: 1.55; }
.form-search { margin-bottom: 12px; }
.form-fields { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
.edit-field { min-width: 0; padding: 13px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-2); }
.edit-field-head { display: flex; align-items: baseline; gap: 8px; }
.edit-field-head strong { overflow-wrap: anywhere; font-size: 12px; font-weight: 620; }
.edit-field-head span { color: var(--dim); font-size: 10px; }
.field-description, .field-original { margin: 5px 0 0; color: var(--muted); font-size: 10px; line-height: 1.35; overflow-wrap: anywhere; }
.field-original { color: var(--accent); }
.edit-controls { display: flex; gap: 8px; margin-top: 10px; align-items: end; }
.mode-select { flex: 0 1 130px; }
.input-value { flex: 1 1 130px; }
.delete-body p { margin: 10px 0; font-size: 13px; line-height: 1.6; }
.dialog-error { color: var(--danger); font-size: 11px; margin: 0 22px 12px; }
.dialog-actions { display: flex; justify-content: flex-end; flex-wrap: wrap; gap: 8px; padding: 14px 22px 20px; border-top: 1px solid var(--line); }
.delete-button { min-height: 34px; border: 1px solid var(--danger); border-radius: 9px; padding: 0 13px; background: var(--error-wash); color: var(--danger); font-size: 12px; }
.delete-button:hover:not(:disabled) { background: #ffe1e5; }
@media (max-width: 760px) {
  .data-header { padding: 66px 19px 13px; flex-wrap: wrap; }
  .data-body { padding: 10px 19px 25px; }
  .filter-row { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  .filter-value { grid-column: 1 / 2; }
  .remove-filter { align-self: end; }
  .section-head, .results-head { align-items: start; flex-direction: column; }
  .sort-controls { width: 100%; }
  .detail-grid, .form-fields { grid-template-columns: 1fr; }
}
@media (max-width: 480px) {
  .data-header .toolbar-actions { justify-content: flex-start; }
  .data-header .toolbar-actions button, .pagination-bar button { white-space: nowrap; }
  .filter-row { grid-template-columns: 1fr; border-top: 1px solid var(--line); padding-top: 9px; }
  .filter-value { grid-column: auto; }
  .remove-filter { justify-self: start; }
  .filter-actions { flex-wrap: wrap; }
  .applied-count { flex: 0 0 100%; margin-right: 0; white-space: nowrap; }
  .filter-actions button { flex: 0 0 auto; white-space: nowrap; }
  .sort-controls .control-group { flex: 1 1 100px; }
  .dialog-body { padding: 14px; }
  .dialog-head, .dialog-actions { padding-left: 14px; padding-right: 14px; }
}
</style>
