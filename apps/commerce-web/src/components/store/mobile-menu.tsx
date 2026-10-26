'use client';

import { useRef, type MouseEvent, type ReactNode } from 'react';

import { CloseIcon, FOCUS } from './ui';

/**
 * A drawer opened from a header button. The native modal dialog provides the focus trap,
 * Escape to close and an inert page behind it; following any link inside closes it.
 */
export function MenuDrawer({
  trigger,
  triggerLabel,
  title,
  closeLabel,
  triggerClassName = '',
  side = 'left',
  children,
}: {
  trigger: ReactNode;
  triggerLabel: string;
  title: ReactNode;
  closeLabel: string;
  triggerClassName?: string;
  side?: 'left' | 'bottom';
  children: ReactNode;
}) {
  const dialog = useRef<HTMLDialogElement>(null);

  function onDialogClick(event: MouseEvent<HTMLDialogElement>) {
    const target = event.target as HTMLElement;
    // A click on the backdrop lands on the dialog itself; a click on a link navigates away.
    if (target === event.currentTarget || target.closest('a')) dialog.current?.close();
  }

  const placement =
    side === 'left'
      ? 'inset-y-0 left-0 h-full max-h-none w-[min(340px,88%)]'
      : 'inset-x-0 bottom-0 top-auto max-h-[85%] w-full max-w-none';

  return (
    <>
      <button
        type="button"
        aria-haspopup="dialog"
        aria-label={triggerLabel}
        onClick={() => dialog.current?.showModal()}
        className={`cursor-pointer ${triggerClassName} ${FOCUS}`}
      >
        {trigger}
      </button>
      <dialog
        ref={dialog}
        aria-label={triggerLabel}
        onClick={onDialogClick}
        className={`fixed m-0 overflow-y-auto bg-store-surface p-0 text-store-ink shadow-overlay backdrop:bg-store-ink/45 ${placement}`}
      >
        <div className="flex min-h-full flex-col">
          <div className="sticky top-0 z-10 flex h-[60px] items-center justify-between gap-3 border-b border-store-line bg-store-surface pr-3 pl-5">
            <div className="min-w-0 truncate font-store-brand text-lg font-bold">{title}</div>
            <button
              type="button"
              aria-label={closeLabel}
              onClick={() => dialog.current?.close()}
              className={`flex size-11 cursor-pointer items-center justify-center rounded-base ${FOCUS}`}
            >
              <CloseIcon />
            </button>
          </div>
          <div className="flex-1 p-3">{children}</div>
        </div>
      </dialog>
    </>
  );
}
