// 共通モーダルの開閉。DOM とフェードの順序は各画面で共通。
/** ctx: shell */
export function init({ shell }) {
  const modalWrap = shell.querySelector("#modalwrap");
  const modal = shell.querySelector("#modal");
  const closeModal = () => {
    modalWrap.classList.remove("show");
    modalWrap.hidden = true;
    modal.replaceChildren();
  };
  modalWrap.addEventListener("click", (e) => { if (e.target === modalWrap) closeModal(); });
  // R80-A8: 全モーダルに閉じる手段を出す。従来は「背景クリック」か Escape だけで、
  // **画面上に閉じ方が一切見えていなかった**（スマホは全シートに「閉じる」があるのに逆転）。
  shell.querySelector("#modalclose").addEventListener("click", closeModal);
  const openModal = () => {
    modalWrap.hidden = false;
    requestAnimationFrame(() => modalWrap.classList.add("show"));   // R67: 開きのフェード
  };

  const mEl = (tag, cls, text) => {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text !== undefined) n.textContent = text;
    return n;
  };

  return { modal, openModal, closeModal, mEl };
}
