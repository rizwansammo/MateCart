import type { CategoryNode } from '@matecart/shared-contracts';
import type { Route } from 'next';

import { localize } from './i18n';

/** The chain of categories from a root down to `slug`, or null when it is not in the tree. */
export function categoryPath(tree: CategoryNode[], slug: string): CategoryNode[] | null {
  for (const node of tree) {
    if (node.slug === slug) return [node];
    const below = categoryPath(node.children, slug);
    if (below) return [node, ...below];
  }
  return null;
}

export interface CategoryLinkItem {
  label: string;
  href: Route;
  active: boolean;
  depth: number;
}

/**
 * Sidebar category links: every top-level category, with the branch of the current category
 * opened down to its children, so customers can move up, across and down the tree.
 */
export function categoryLinks(
  tree: CategoryNode[],
  currentSlug: string | null,
  lang: string,
): CategoryLinkItem[] {
  const open = new Set((currentSlug ? categoryPath(tree, currentSlug) : null)?.map((n) => n.slug));
  const links: CategoryLinkItem[] = [];
  const walk = (nodes: CategoryNode[], depth: number) => {
    for (const node of nodes) {
      links.push({
        label: localize(node.translations, 'name', node.name, lang),
        href: `/categories/${node.slug}` as Route,
        active: node.slug === currentSlug,
        depth,
      });
      if (open.has(node.slug)) walk(node.children, depth + 1);
    }
  };
  walk(tree, 0);
  return links;
}
