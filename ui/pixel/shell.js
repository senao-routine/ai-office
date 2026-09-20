// 様式 pixel の DOM。id は iso と同じ（ui/hud の部品が同じ id を掴む＝tests/hud_ids.test.mjs が番人）。
// 文字は全部 DOM に置く（帯の canvas には描かない＝.claude/rules/ui-2d.md）。
export function template() {
  return `
    <header class="pxhead">
      <div class="brand">
        <span class="mark">▦</span>
        <span class="txt"><b id="greet">AI Office</b><i id="brandoffice">…</i></span>
      </div>
      <span class="pxcounts" id="pxcounts"></span>
      <div class="usage">
        <button id="usage-summary" type="button" aria-expanded="false" aria-controls="gauges"></button>
        <div class="gauges" id="gauges" hidden>
          <div class="gcard" id="gcredits" hidden>
            <b class="gtitle" id="gtitle-credits"></b>
            <div class="gbody" id="gcreditbody"></div>
          </div>
          <div class="gcard" id="gmoney" hidden>
            <b class="gtitle" id="gtitle-money"></b>
            <div class="gbody" id="gmoneybody"></div>
          </div>
        </div>
      </div>
      <i class="freshness" id="freshness" hidden></i>
      <div class="admin">
        <button class="abtn" id="btn-newproj" type="button" data-glyph="➕"></button>
        <button class="abtn" id="btn-hire" type="button" data-glyph="＋" hidden></button>
        <button class="abtn" id="btn-launch" type="button" data-glyph="▶"></button>
        <button class="abtn" id="btn-pair" type="button" data-glyph="📱"></button>
        <button class="abtn" id="btn-run" type="button" data-glyph="📲"></button>
        <button class="abtn" id="btn-res" type="button" data-glyph="⚡"></button>
        <button class="abtn" id="btn-settings" type="button" data-glyph="⚙"></button>
      </div>
      <button id="btn-help" class="abtn" type="button">?</button>
    </header>
    <main class="main">
      <section class="pxstage" id="stage">
        <div class="viewport" id="viewport"></div>
      </section>
      <div class="offbar" id="offbar" hidden></div>
      <div class="tray" id="attn" hidden></div>
      <section class="pxbody">
        <div class="pxtable" role="table">
          <div class="pxhrow" role="row">
            <span class="c-mono"></span>
            <button class="c-project pxsort" type="button" data-sort="name" id="col-project"></button>
            <span class="c-session" id="col-session"></span>
            <button class="c-state pxsort" type="button" data-sort="state" id="col-state"></button>
            <span class="c-detail" id="col-detail"></span>
            <span class="c-evd" id="col-evidence"></span>
            <span class="c-prog" id="col-progress"></span>
            <button class="c-age pxsort" type="button" data-sort="age" id="col-age"></button>
          </div>
          <div class="pxrows" id="agents" role="rowgroup"></div>
        </div>
        <aside class="sheet" id="sheet" hidden>
          <header class="sheethead">
            <b id="sheetname"></b>
            <span class="sheettools">
              <button class="sheetterm" id="sheetarch" type="button" hidden>🎨</button>
              <button class="sheetterm sheetwide" id="sheetwide" type="button">⤢</button>
              <button class="sheetterm" id="sheetterm" type="button">🖥</button>
              <button class="sheetsnd" id="sheetsnd" type="button">🔇</button>
              <button class="sheetclose" id="sheetclose" type="button">✕</button>
            </span>
          </header>
          <p class="sheetact" id="sheetact"></p>
          <div class="sheetbody" id="sheetbody"></div>
          <div class="quickdock" id="quickdock"></div>
          <p class="sheettarget" id="sheettarget" hidden></p>
          <div class="compose" id="compose">
            <input id="composeinput" type="text" autocomplete="off">
          </div>
        </aside>
        <div class="toast" id="toast" hidden></div>
        <div class="modalwrap" id="modalwrap" hidden>
          <div class="modal" id="modal"></div>
          <button class="modalclose" id="modalclose" type="button" aria-label="閉じる">✕</button>
        </div>
      </section>
      <footer class="pxfoot">
        <div class="pxhist">
          <b class="cardtitle" id="title-hist"></b>
          <div class="hist" id="hist"></div>
        </div>
        <span class="pxtoday" id="pxtoday"></span>
      </footer>
    </main>
  `;
}

/** 台帳だけの静的文言（列見出し）。applyStaticStrings の後・言語切替時にも貼る。 */
export function applyPixelStrings(shell, T) {
  for (const [id, key] of [["#col-project", "col_project"], ["#col-session", "col_session"],
    ["#col-state", "col_state"], ["#col-detail", "col_detail"], ["#col-evidence", "col_evidence"],
    ["#col-progress", "col_progress"], ["#col-age", "col_age"]]) {
    const n = shell.querySelector(id);
    if (n) n.textContent = T(key);
  }
}
