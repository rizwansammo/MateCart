'use client';

import type { Route } from 'next';
import { useRouter } from 'next/navigation';

/** Sort order picker. The server prepares each option's URL, so filters and search are kept. */
export function SortSelect({
  label,
  value,
  options,
  className,
}: {
  label: string;
  value: string;
  options: { value: string; label: string; href: string }[];
  className: string;
}) {
  const router = useRouter();
  return (
    <label className="flex items-center gap-2 text-[13px] text-store-muted">
      <span className="sr-only sm:not-sr-only">{label}</span>
      <select
        value={value}
        onChange={(event) => {
          const option = options.find((o) => o.value === event.target.value);
          if (option) router.push(option.href as Route);
        }}
        className={className}
      >
        {options.map((option) => (
          <option key={option.value} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
