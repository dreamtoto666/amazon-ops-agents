'use client';

import type { NavItem, NavGroup } from '@/types';
import { useEffect, useState } from 'react';

export function useFilteredNavItems(items: NavItem[]) {
  return items;
}

export function useFilteredNavGroups(groups: NavGroup[]) {
  const [role, setRole] = useState<string>();
  useEffect(() => {
    fetch('/api/auth/me')
      .then((response) => (response.ok ? response.json() : null))
      .then((user) => setRole(user?.role))
      .catch(() => setRole(undefined));
  }, []);
  return groups
    .map((group) => ({
      ...group,
      items: group.items.filter((item) => !item.access?.role || item.access.role === role)
    }))
    .filter((group) => group.items.length > 0);
}
