import { useState, useEffect } from 'react';
import Table, { TableProps } from '@cloudscape-design/components/table';
import Pagination from '@cloudscape-design/components/pagination';
import CollectionPreferences from '@cloudscape-design/components/collection-preferences';

/** Shared collection controls. Callers retain ownership of filtering and selection. */
export default function ResourceTable<T>(props: TableProps<T>) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const [visibleColumns, setVisibleColumns] = useState<string[] | undefined>();
  const [sorting, setSorting] = useState<{ sortingColumn: TableProps.SortingColumn<T>; isDescending?: boolean }>();
  const itemSignature = JSON.stringify(props.items);
  useEffect(() => { setPage(1); }, [itemSignature]);
  const identity = (item: T) => {
    if (!item || typeof item !== 'object') return String(item);
    const record = item as Record<string, unknown>;
    for (const name of ['id', 'instanceid', 'functionname', 'tablename', 'name', 'arn', 'queueurl', 'queuename', 'clustername', 'keyid', 'topicarn', 'stackname', 'uuid']) {
      const key = Object.keys(record).find(key => key.replace(/_/g, '').toLowerCase() === name);
      if (key && record[key] != null) return String(record[key]);
    }
    return JSON.stringify(item);
  };
  const controlledSort = Boolean(props.onSortingChange);
  const columns = props.columnDefinitions.map(column => {
    if (column.sortingField || column.sortingComparator || !column.id) return column;
    // Only infer sorting when the column id is an actual scalar resource attribute.
    const normalize = (value: string) => value.replace(/[^a-z0-9]/gi, '').toLowerCase();
    const names = [column.id, typeof column.header === 'string' ? column.header : ''].map(normalize);
    const key = props.items.flatMap(item => item && typeof item === 'object' ? Object.keys(item) : [])
      .find(key => names.includes(normalize(key)) && props.items.some(item => ['string', 'number'].includes(typeof (item as Record<string, unknown>)[key])));
    if (!key) return column;
    return { ...column, sortingField: key };
  });
  const items = [...props.items];
  if (!controlledSort && sorting) {
    const { sortingColumn, isDescending } = sorting;
    items.sort((a, b) => {
      const key = sortingColumn.sortingField;
      const av = key ? (a as Record<string, unknown>)[key] : '';
      const bv = key ? (b as Record<string, unknown>)[key] : '';
      const result = sortingColumn.sortingComparator ? sortingColumn.sortingComparator(a, b) :
        typeof av === 'number' && typeof bv === 'number' ? av - bv : String(av ?? '').localeCompare(String(bv ?? ''), undefined, { numeric: true });
      return isDescending ? -result : result;
    });
  }
  const pages = Math.max(1, Math.ceil(items.length / pageSize));
  const currentPage = Math.min(page, pages);
  const displayed = items.slice((currentPage - 1) * pageSize, currentPage * pageSize);
  return <Table {...props}
    trackBy={props.trackBy ?? identity}
    columnDefinitions={columns}
    items={displayed}
    sortingColumn={controlledSort ? props.sortingColumn : sorting?.sortingColumn}
    sortingDescending={controlledSort ? props.sortingDescending : sorting?.isDescending}
    onSortingChange={controlledSort ? props.onSortingChange : ({ detail }) => { setSorting(detail); setPage(1); props.onSelectionChange?.({ detail: { selectedItems: [] } } as any); }}
    visibleColumns={visibleColumns ?? props.visibleColumns}
    pagination={<Pagination ariaLabels={{ nextPageLabel: 'Next page', previousPageLabel: 'Previous page', pageLabel: page => `Page ${page}` }} currentPageIndex={currentPage} pagesCount={pages} onChange={({ detail }) => { setPage(detail.currentPageIndex); props.onSelectionChange?.({ detail: { selectedItems: [] } } as any); }} />}
    preferences={<CollectionPreferences title="Table preferences" confirmLabel="Confirm" cancelLabel="Cancel"
      preferences={{ pageSize, visibleContent: visibleColumns ?? columns.map(c => c.id!).filter(Boolean) }}
      pageSizePreference={{ title: 'Page size', options: [10, 25, 50].map(value => ({ value, label: `${value} resources` })) }}
      visibleContentPreference={{ title: 'Visible columns', options: [{ label: 'Resource attributes', options: columns.filter(c => c.id).map(c => ({ id: c.id!, label: typeof c.header === 'string' ? c.header : c.id! })) }] }}
      onConfirm={({ detail }) => { props.onSelectionChange?.({ detail: { selectedItems: [] } } as any); setPageSize(detail.pageSize ?? 10); setVisibleColumns(detail.visibleContent ? [...detail.visibleContent] : undefined); setPage(1); }} />}
  />;
}
