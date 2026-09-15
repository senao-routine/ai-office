import { inertOthers } from "/ui/hud/layers.js";

// 共通モーダルの開閉。DOM とフェードの順序は各画面で共通。
/** ctx: shell */
export function init({ shell }) {
  const modalWrap = shell.querySelector("#modalwrap");
  const modal = shell.querySelector("#modal");
  modalWrap.tabIndex = -1;
  modalWrap.setAttribute("role", "dialog");
  modalWrap.setAttribute("aria-modal", "true");
  let releaseInert = null;
  let previousFocus = null;
  const closeModal = () => {
    modalWrap.classList.remove("show");
    modalWrap.hidden = true;
    modal.replaceChildren();
    releaseInert?.(); releaseInert = null;
    if (previousFocus?.isConnected && !previousFocus.closest?.("[hidden], [inert]")) previousFocus.focus();
    previousFocus = null;
  };
  modalWrap.addEventListener("click", (e) => { if (e.target === modalWrap) closeModal(); });
  modalWrap.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.stopPropagation(); closeModal(); }
  });
  // R80-A8: 全モーダルに閉じる手段を出す。従来は「背景クリック」か Escape だけで、
  // **画面上に閉じ方が一切見えていなかった**（スマホは全シートに「閉じる」があるのに逆転）。
  shell.querySelector("#modalclose").addEventListener("click", closeModal);
  const openModal = () => {
    if (modalWrap.hidden) previousFocus = document.activeElement;
    modalWrap.hidden = false;
    releaseInert ||= inertOthers(shell, modalWrap);
    if (!modalWrap.contains(document.activeElement)) modalWrap.focus();
    requestAnimationFrame(() => { if (!modalWrap.hidden) modalWrap.classList.add("show"); });
  };

  const mEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  return { modal, openModal, closeModal, mEl };
}
