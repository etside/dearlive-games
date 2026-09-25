import React from 'react';
import { cn } from '../../utils/cn';

interface Column<T> {
  header: string;
  accessor: keyof T | ((row: T) => React.ReactNode);
  className?: string;
}

interface TableProps<T> {
  data: T[];
  columns: Column<T>[];
  keyAccessor: (row: T) => string;
  className?: string;
  emptyMessage?: string;
  onRowClick?: (row: T) => void;
}

export function Table<T>({ data, columns, keyAccessor, className, emptyMessage = 'No data available', onRowClick }: TableProps<T>) {
  if (data.length === 0) {
    return (
      <div className="py-12 text-center text-gray-500">
        {emptyMessage}
      </div>
    );
  }

  return (
    <div className="overflow-x-auto">
      <table className={cn('min-w-full divide-y divide-gray-200', className)}>
        <thead className="bg-gray-50">
          <tr>
            {columns.map((column, index) => (
              <th
                key={index}
                scope="col"
                className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider"
              >
                {column.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody className="bg-white divide-y divide-gray-200">
          {data.map((row) => (
            <tr
              key={keyAccessor(row)}
              className="hover:bg-gray-50 cursor-pointer transition-colors"
              onClick={() => onRowClick?.(row)}
            >
              {columns.map((column, index) => (
                <td key={index} className={cn('px-6 py-4 whitespace-nowrap text-sm text-gray-900', column.className)}>
                  {typeof column.accessor === 'function'
                    ? column.accessor(row)
                    : String(row[column.accessor as keyof T] ?? '')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
