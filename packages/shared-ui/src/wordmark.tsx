interface WordmarkProps {
  className?: string;
}

/** The MateCART wordmark from the approved prototype: "Mate" in the current colour, "CART" in brand green. */
export function Wordmark({ className }: WordmarkProps) {
  return (
    <span
      role="img"
      aria-label="MateCart"
      className={['font-brand leading-none font-bold tracking-tight', className]
        .filter(Boolean)
        .join(' ')}
    >
      Mate<span className="text-brand">CART</span>
    </span>
  );
}
