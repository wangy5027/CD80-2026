<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { USER_FACING_ERROR } from '../../../shared/userFacingError.js';
import { dataFileFormat } from '../dataFilesModel.js';
import {
  getFinancialDataCapabilities, listFinancialCatalog, getFinancialTableSchema,
  queryFinancialRows, getFinancialRow, createFinancialRow, updateFinancialRow,
  deleteFinancialRow,
} from '../financialDataApi.js';
import {
  PAGE_SIZES, MAX_FILTERS, fieldOperators, buildFilters, initialFormValues,
  buildWriteData, buildPageQuery, canMoveToNextPage, displayValue, tableLabel,
  catalogSearchValue, catalogPrimaryColumns, catalogSourceFile, catalogDateInfo,
} from '../financialDataModel.js';

defineProps({
  personalFiles: { type: Array, default: () => [] },
  uploadBusy: { type: Boolean, default: false },
});
const emit = defineEmits(['upload', 'download', 'delete', 'attach']);

function personalFileSize(value) {
  const size = Number(value) || 0;
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function personalFileTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return '时间未记录';
  return new Intl.DateTimeFormat('zh-CN', {
    year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  }).format(date);
}

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
const selectedDateInfo = computed(() => catalogDateInfo(selectedDataset.value));
const selectedPrimaryColumns = computed(() => catalogPrimaryColumns(selectedDataset.value));
const selectedSourceFile = computed(() => catalogSourceFile(selectedDataset.value));
const allFields = computed(() => schema.value?.fields || []);
const sourceFields = computed(() => allFields.value.filter((field) => field.name !== '_row_id'));
const previewFields = computed(() => sourceFields.value.slice(0, 7));
const matchingTables = computed(() => {
  const needle = tableSearch.value.trim().toLocaleLowerCase();
  return tables.value.filter((item) => !needle || catalogSearchValue(item).includes(needle));
});
const searchedTables = computed(() => {
  const found = matchingTables.value;
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
      getFinancialDataCapabilities(), listFinancialCatalog(),
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
  const [nextSchema, nextCatalog] = await Promise.allSettled([
    getFinancialTableSchema(selected), listFinancialCatalog(),
  ]);
  if (destroyed || version !== tableVersion || selected !== selectedTable.value) return;
  if (nextSchema.status === 'fulfilled') schema.value = nextSchema.value;
  if (nextCatalog.status === 'fulfilled' && Array.isArray(nextCatalog.value.data))
    tables.value = nextCatalog.value.data;
  else if (nextSchema.status === 'fulfilled') tables.value = tables.value.map((item) =>
    item.name === selected ? { ...item, row_count: nextSchema.value.row_count } : item);
}

async function refreshVisibleData() {
  if (!schema.value || rowsLoading.value) return;
  await refreshCounts();
  await loadRows();
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

function showRecords() {
  document.getElementById('results-heading')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
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
        <p class="page-subtitle">管理个人数据文件，查看官方本地数据目录与记录。</p>
      </div>
      <div class="toolbar-actions">
        <span v-if="capabilities" class="access-label">{{ canWrite ? '官方表可编辑' : '官方表只读' }}</span>
        <button class="secondary-button" type="button" :disabled="!schema || rowsLoading" @click="refreshVisibleData">刷新数据</button>
        <button v-if="canWrite" class="primary-button" type="button" :disabled="!schema || schemaLoading" @click="openCreate">新增记录</button>
      </div>
    </header>

    <div class="page-body data-body">
      <section class="data-section personal-data-files" aria-labelledby="personal-data-heading">
        <div class="section-head personal-data-head">
          <div>
            <h2 id="personal-data-heading">我的数据文件</h2>
            <p>上传后可在主页面问答的“添加文件”中直接选择，文件仅属于当前账号。</p>
          </div>
          <button class="primary-button" type="button" :disabled="uploadBusy" @click="emit('upload')">{{ uploadBusy ? '正在上传…' : '上传数据文件' }}</button>
        </div>
        <p class="personal-file-rule">支持 CSV、TSV、JSON、Excel（.xlsx）；单个文件不超过 10 MB。</p>
        <div v-if="personalFiles.length" class="personal-file-list">
          <article v-for="file in personalFiles" :key="file.id" class="personal-file-row" :data-file-id="String(file.id)">
            <div class="personal-file-main">
              <strong :title="file.name">{{ file.name }}</strong>
              <span>{{ dataFileFormat(file) }} · {{ personalFileSize(file.size) }} · 上传于 {{ personalFileTime(file.createdAt) }}</span>
            </div>
            <div class="personal-file-actions">
              <button class="secondary-button" type="button" :aria-label="`在问答中引用 ${file.name}`" @click="emit('attach', file.id)">在问答中引用</button>
              <button class="secondary-button" type="button" :aria-label="`下载 ${file.name}`" @click="emit('download', file.id)">下载</button>
              <button class="secondary-button" type="button" :aria-label="`删除 ${file.name}`" @click="emit('delete', file.id)">删除</button>
            </div>
          </article>
        </div>
        <p v-else class="personal-file-empty">还没有上传数据文件。</p>
      </section>
      <p v-if="statusMessage" class="notice success" role="status">{{ statusMessage }}</p>
      <div v-if="catalogLoading" class="state-panel" role="status">正在加载数据表…</div>
      <div v-else-if="catalogError" class="state-panel error-panel" role="alert">
        <p>{{ USER_FACING_ERROR }}</p>
        <button class="secondary-button" type="button" @click="loadCatalog">重试</button>
      </div>
      <div v-else-if="!tables.length" class="state-panel">暂无可用的数据表。</div>
      <template v-else>
        <div class="data-controls">
          <label class="control-group table-search">
            <span>搜索数据表</span>
            <input v-model="tableSearch" type="search" placeholder="表名、中文名称或字段" aria-label="搜索数据表">
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
        <section class="data-section official-catalog" aria-labelledby="official-catalog-heading">
          <div class="section-head catalog-head">
            <div>
              <h2 id="official-catalog-heading">官方数据源 · Tushare</h2>
              <p>这里展示当前本机 SQLite 已收录的历史数据。日期首末值只说明表内记录范围，不代表期间每天都有数据。</p>
            </div>
            <span class="catalog-count">{{ tables.length }} 张本地数据表</span>
          </div>
          <div class="catalog-layout">
            <div class="catalog-list" aria-label="官方数据表目录">
              <button v-for="item in matchingTables" :key="item.name" type="button" class="catalog-item"
                :class="{ selected: item.name === selectedTable }" :aria-pressed="item.name === selectedTable"
                @click="chooseTable(item.name)">
                <strong>{{ tableLabel(item) }}</strong>
                <span>{{ item.name }}</span>
                <small>{{ catalogDateInfo(item).shortText }}</small>
              </button>
              <p v-if="!matchingTables.length" class="catalog-empty">没有匹配的数据表，请换一个名称或字段试试。</p>
            </div>
            <div v-if="selectedDataset" class="catalog-detail">
              <div class="catalog-detail-title"><div><span>当前数据表</span><h3>{{ tableLabel(selectedDataset) }}</h3><code>{{ selectedDataset.name }}</code></div><span class="catalog-total">{{ Number(selectedDataset.row_count || 0).toLocaleString('zh-CN') }} 条</span></div>
              <p v-if="selectedDataset.summary || selectedDataset.title" class="catalog-summary">{{ selectedDataset.summary || selectedDataset.title }}</p>
              <dl class="catalog-facts">
                <div><dt>数据提供方</dt><dd>{{ selectedDataset.provider || 'Tushare' }}</dd></div>
                <div><dt>当前存储</dt><dd>本机 SQLite</dd></div>
                <div><dt>主要日期范围</dt><dd>{{ selectedDateInfo.text }}<small v-if="selectedDateInfo.field">按 {{ selectedDateInfo.field }} 统计</small></dd></div>
                <div><dt>来源文件</dt><dd>{{ selectedSourceFile || '未登记文件名' }}</dd></div>
              </dl>
              <div class="catalog-columns"><h4>主要列</h4><ul v-if="selectedPrimaryColumns.length"><li v-for="column in selectedPrimaryColumns" :key="column.name"><code>{{ column.name }}</code><span v-if="column.description">{{ column.description }}</span></li></ul><p v-else>主要列暂未提供；下方可查看完整字段和记录。</p></div>
              <p class="catalog-source-note">{{ selectedDataset.sourceNote || '数据来自本机已导入的 SQLite 表；页面不会连接 Tushare 实时取数。' }}</p>
              <button class="secondary-button" type="button" :disabled="!schema || schemaLoading" @click="showRecords">查看数据记录</button>
            </div>
          </div>
        </section>
        <p v-if="!canWrite && capabilities" class="read-note">当前账号可查看和筛选数据；修改权限由网站管理员分配。</p>

        <div v-if="schemaLoading" class="state-panel" role="status">正在读取字段信息…</div>
        <div v-else-if="schemaError" class="state-panel error-panel" role="alert">
          <p>{{ USER_FACING_ERROR }}</p>
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
            <p v-if="filterError" class="field-error" role="alert">{{ USER_FACING_ERROR }}</p>
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
              <span>{{ USER_FACING_ERROR }}</span>
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
            <span>{{ USER_FACING_ERROR }}</span>
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

        <p v-if="formError" class="dialog-error" role="alert">{{ USER_FACING_ERROR }}</p>
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
.official-catalog { margin-top: 16px; }
.personal-data-files { margin: 0 0 17px; }
.personal-data-head { align-items: center; }
.personal-data-head .primary-button { flex: none; }
.personal-file-rule { margin: 0; padding: 0 19px 14px; color: var(--muted); font-size: 11px; line-height: 1.5; }
.personal-file-list { display: grid; max-height: min(340px, 42dvh); overflow-y: auto; border-top: 1px solid var(--line); scrollbar-width: thin; }
.personal-file-row { display: flex; align-items: center; justify-content: space-between; gap: 14px; min-width: 0; padding: 12px 19px; border-top: 1px solid var(--line); }
.personal-file-row:first-child { border-top: 0; }
.personal-file-main { display: grid; min-width: 0; gap: 5px; }
.personal-file-main strong { overflow-wrap: anywhere; font-size: 12px; }
.personal-file-main span { color: var(--muted); font-size: 10px; line-height: 1.5; }
.personal-file-actions { display: flex; flex: none; flex-wrap: wrap; justify-content: flex-end; gap: 6px; }
.personal-file-actions button { min-height: 30px; font-size: 11px; white-space: nowrap; }
.personal-file-empty { margin: 0; padding: 15px 19px 19px; border-top: 1px solid var(--line); color: var(--muted); font-size: 11px; }
.catalog-head { align-items: center; border-bottom: 1px solid var(--line); }
.catalog-head p { max-width: 760px; line-height: 1.6; }
.catalog-count { flex: none; color: var(--accent); font-size: 11px; }
.catalog-layout { display: grid; grid-template-columns: minmax(210px, 30%) minmax(0, 1fr); min-width: 0; }
.catalog-list { max-height: min(420px, 54dvh); min-height: 165px; overflow-y: auto; overscroll-behavior: contain; border-right: 1px solid var(--line); scrollbar-width: thin; }
.catalog-item { display: grid; width: 100%; gap: 3px; padding: 10px 16px; border: 0; border-bottom: 1px solid var(--line); background: transparent; color: var(--text); text-align: left; cursor: pointer; }
.catalog-item:hover, .catalog-item:focus-visible { background: var(--surface-2); }
.catalog-item:focus-visible { outline: 2px solid var(--focus-ring); outline-offset: -2px; }
.catalog-item.selected { background: var(--accent-dark); box-shadow: inset 3px 0 var(--accent); }
.catalog-item strong { font-size: 12px; }
.catalog-item span, .catalog-item small { overflow-wrap: anywhere; color: var(--muted); font-size: 10px; }
.catalog-empty { padding: 16px; color: var(--muted); font-size: 11px; line-height: 1.6; }
.catalog-detail { min-width: 0; padding: 17px 20px 20px; }
.catalog-detail-title { display: flex; justify-content: space-between; gap: 12px; align-items: start; }
.catalog-detail-title > div { min-width: 0; }
.catalog-detail-title span, .catalog-detail-title code { color: var(--muted); font-size: 10px; }
.catalog-detail-title h3 { margin: 4px 0; font-size: 17px; line-height: 1.3; }
.catalog-detail-title code { overflow-wrap: anywhere; }
.catalog-total { flex: none; padding: 5px 8px; border-radius: 100px; background: var(--accent-dark); color: var(--accent) !important; }
.catalog-summary { margin: 12px 0 16px; color: var(--muted); font-size: 11px; line-height: 1.7; overflow-wrap: anywhere; }
.catalog-facts { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 16px; margin: 0 0 16px; }
.catalog-facts > div { min-width: 0; }
.catalog-facts dt { margin-bottom: 4px; color: var(--dim); font-size: 10px; }
.catalog-facts dd { margin: 0; color: var(--text); font-size: 11px; overflow-wrap: anywhere; }
.catalog-facts dd small { display: block; margin-top: 3px; color: var(--muted); line-height: 1.4; }
.catalog-columns h4 { margin: 0 0 7px; color: var(--text); font-size: 11px; }
.catalog-columns ul { display: flex; flex-wrap: wrap; gap: 6px; margin: 0; padding: 0; list-style: none; }
.catalog-columns li { display: flex; flex-wrap: wrap; gap: 4px; max-width: 100%; padding: 5px 8px; border: 1px solid var(--line); border-radius: 7px; background: var(--surface-2); font-size: 10px; overflow-wrap: anywhere; }
.catalog-columns li span, .catalog-columns p { color: var(--muted); }
.catalog-source-note { margin: 14px 0; color: var(--muted); font-size: 10px; line-height: 1.6; overflow-wrap: anywhere; }
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
.data-dialog { position: fixed; inset: 0; margin: auto; width: min(900px, calc(100vw - 32px)); max-width: none; max-height: min(86dvh, 900px); padding: 0; border: 1px solid var(--line); border-radius: 16px; background: var(--panel); color: var(--text); box-shadow: 0 24px 90px rgb(27 57 78 / 18%); overflow-y: auto; }
.data-dialog[open] { display: flex; flex-direction: column; }
.data-dialog::backdrop { background: rgba(8,7,10,.74); }
.dialog-head { display: flex; flex: none; align-items: start; justify-content: space-between; gap: 15px; padding: 20px 22px 13px; border-bottom: 1px solid var(--line); }
.dialog-head h2 { margin: 0; font-size: 16px; font-weight: 620; }
.dialog-head p { margin: 5px 0 0; color: var(--muted); font-size: 11px; }
.dialog-close { width: 30px; height: 30px; border: 0; border-radius: 8px; background: transparent; color: var(--muted); font-size: 23px; line-height: 1; }
.dialog-close:hover:not(:disabled) { background: var(--surface-2); }
.dialog-body { flex: 1 1 auto; min-height: 0; padding: 18px 22px; overflow-y: auto; scrollbar-width: thin; }
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
.dialog-error { flex: none; max-height: min(120px, 25dvh); overflow-y: auto; overflow-wrap: anywhere; color: var(--danger); font-size: 11px; margin: 0 22px 12px; }
.dialog-actions { display: flex; flex: none; justify-content: flex-end; flex-wrap: wrap; gap: 8px; padding: 14px 22px 20px; border-top: 1px solid var(--line); }
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
  .catalog-layout { grid-template-columns: 1fr; }
  .catalog-list { min-height: 0; max-height: min(210px, 32dvh); border-right: 0; border-bottom: 1px solid var(--line); }
  .personal-file-row { align-items: flex-start; flex-direction: column; }
  .personal-file-actions { justify-content: flex-start; }
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
  .catalog-detail { padding: 15px; }
  .catalog-facts { grid-template-columns: 1fr; }
  .personal-data-head { align-items: flex-start; }
  .personal-file-actions button { white-space: normal; }
}
</style>
