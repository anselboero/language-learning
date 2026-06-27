"use client";

import { useState } from "react";
import Link from "next/link";
import type { GrammarSection } from "@/lib/api";

interface TreeNode {
  section: GrammarSection;
  children: TreeNode[];
}

// Build a tree from the flat, number-sorted section list using parent_number.
function buildTree(sections: GrammarSection[]): TreeNode[] {
  const byNumber = new Map<string, TreeNode>();
  for (const s of sections) byNumber.set(s.number, { section: s, children: [] });

  const roots: TreeNode[] = [];
  for (const node of byNumber.values()) {
    const parentNum = node.section.parent_number;
    const parent = parentNum ? byNumber.get(parentNum) : undefined;
    if (parent) parent.children.push(node);
    else roots.push(node); // top-level, or parent not in this chapter
  }
  return roots;
}

function SectionNode({ node }: { node: TreeNode }) {
  const [collapsed, setCollapsed] = useState(true);
  const { section: s } = node;
  const hasChildren = node.children.length > 0;

  return (
    <div className="section-node">
      <div className="section-row">
        {hasChildren ? (
          <button
            className="chevron-btn"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "Expand subsections" : "Collapse subsections"}
            aria-expanded={!collapsed}
          >
            {collapsed ? "▸" : "▾"}
          </button>
        ) : (
          <span className="chevron-spacer" />
        )}
        <Link href={`/sections/${s.number}`} className="section-link">
          <strong>
            §{s.number} {s.title}
          </strong>
          <span className="muted">{s.summary}</span>
        </Link>
      </div>

      {hasChildren && !collapsed && (
        <div className="section-children">
          {node.children.map((child) => (
            <SectionNode key={child.section.number} node={child} />
          ))}
        </div>
      )}
    </div>
  );
}

export default function SectionTree({ sections }: { sections: GrammarSection[] }) {
  const roots = buildTree(sections);
  return (
    <div>
      {roots.map((node) => (
        <SectionNode key={node.section.number} node={node} />
      ))}
    </div>
  );
}
