'use client';

import { useRef, useState, type KeyboardEvent, type ReactNode } from 'react';

import { FOCUS } from './ui';

/**
 * Tabs following the WAI-ARIA pattern (arrow keys, Home and End). Every panel is server-rendered
 * and stays in the document; inactive panels are only hidden.
 */
export function Tabs({
  label,
  tabs,
}: {
  label: string;
  tabs: { id: string; label: string; content: ReactNode }[];
}) {
  const [active, setActive] = useState(0);
  const buttons = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    const moves: Record<string, number> = {
      ArrowRight: active + 1,
      ArrowLeft: active - 1,
      Home: 0,
      End: tabs.length - 1,
    };
    const target = moves[event.key];
    if (target === undefined) return;
    event.preventDefault();
    const next = (target + tabs.length) % tabs.length;
    setActive(next);
    buttons.current[next]?.focus();
  }

  return (
    <div className="flex flex-col gap-5">
      <div
        role="tablist"
        aria-label={label}
        onKeyDown={onKeyDown}
        className="flex gap-1 overflow-x-auto overflow-y-hidden border-b border-store-line"
      >
        {tabs.map((tab, index) => (
          <button
            key={tab.id}
            ref={(element) => {
              buttons.current[index] = element;
            }}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={index === active}
            aria-controls={`panel-${tab.id}`}
            tabIndex={index === active ? 0 : -1}
            onClick={() => setActive(index)}
            className={`-mb-px h-11 flex-none cursor-pointer border-b-2 px-3.5 text-sm font-semibold ${
              index === active
                ? 'border-store-ink text-store-ink'
                : 'border-transparent text-store-muted'
            } ${FOCUS}`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      {tabs.map((tab, index) => (
        <div
          key={tab.id}
          role="tabpanel"
          id={`panel-${tab.id}`}
          aria-labelledby={`tab-${tab.id}`}
          hidden={index !== active}
          tabIndex={0}
          className="outline-none"
        >
          {tab.content}
        </div>
      ))}
    </div>
  );
}
