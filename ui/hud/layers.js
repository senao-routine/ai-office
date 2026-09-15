// Keep the CSS expansion in sync with this table when an overlay is added.
export const Z = Object.freeze({
  tray: 5, sheet: 7, toast: 8, boss: 9, modal: 9,
  offbar: 30, consent: 30, stream: 40, digest: 45,
});

export function applyLayers(shell) {
  for (const [name, value] of Object.entries(Z)) shell.style.setProperty(`--z-${name}`, String(value));
}

const stacks = new WeakMap();

/** Disable everything outside the top overlay; release restores the preceding overlay. */
export function inertOthers(shell, keep) {
  const kept = (Array.isArray(keep) ? keep : [keep]).filter(Boolean);
  let stack = stacks.get(shell);
  if (!stack) {
    stack = { layers: [], before: new Map() };
    stacks.set(shell, stack);
  }
  const layer = { keep: kept };
  const apply = () => {
    for (const [node, value] of stack.before) node.inert = value;
    const current = stack.layers.at(-1);
    if (!current) {
      stack.before.clear();
      stacks.delete(shell);
      return;
    }
    const set = (node, value) => {
      if (!stack.before.has(node)) stack.before.set(node, node.inert);
      node.inert = value;
    };
    const walk = (parent) => {
      for (const node of parent.children) {
        if (current.keep.includes(node)) { set(node, false); continue; }
        if (current.keep.some((keptNode) => node.contains(keptNode))) {
          set(node, false); walk(node);
        } else set(node, true);
      }
    };
    walk(shell);
  };
  stack.layers.push(layer);
  apply();
  let released = false;
  return () => {
    if (released) return;
    released = true;
    stack.layers.splice(stack.layers.indexOf(layer), 1);
    apply();
  };
}
