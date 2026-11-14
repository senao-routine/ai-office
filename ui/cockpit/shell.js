// 様式 cockpit の DOM。id は iso / pixel と同じ（ui/hud の部品が同じ id を掴む
// ＝tests/hud_ids.test.mjs が番人）。文字は全部 DOM に置く（部屋の canvas には描かない）。
//
// R110 骨格 A（3 カラム）: 左＝01 オフィス＋04 受付 ／ 中央＝03 端末（本物の pty・指示欄はその下）／ 右＝02 名簿（台帳）。
// ピクセルは**オフィスの canvas だけ**。周りは等幅・細い罫・余白で組む（本人のモックアップ）。
export function template() {
  return `
    <header class="cphead">
      <div class="brand">
        <span class="mark px" data-px="office" aria-hidden="true"></span>
        <span class="txt"><b id="greet" hidden></b><i id="brandoffice">…</i><span class="cpprompt" aria-hidden="true">:~$</span></span><span class="cur" aria-hidden="true"></span>
      </div>
      <span class="cpcounts" id="pxcounts"></span>
      <div class="usage">
        <button id="usage-summary" type="button" aria-label="Resources" aria-expanded="false" aria-controls="gauges"></button>
        <div class="gauges" id="gauges" hidden>
          <div class="gcard" id="gcredits" hidden><b class="gtitle" id="gtitle-credits"></b><div class="gbody" id="gcreditbody"></div></div>
          <div class="gcard" id="gmoney" hidden><b class="gtitle" id="gtitle-money"></b><div class="gbody" id="gmoneybody"></div></div>
        </div>
      </div>
      <i class="freshness" id="freshness" hidden></i>
      <span id="cp-attn-slot"></span>
      <div class="admin">
        <div class="cpadd">
          <button class="abtn" id="cp-add" type="button" aria-label="New session" aria-haspopup="menu" aria-controls="cp-add-menu" aria-expanded="false"></button>
          <div class="cpmenu" id="cp-add-menu" role="menu" hidden>
            <button class="abtn" id="btn-newproj" type="button" role="menuitem" aria-label="New project"></button>
            <button class="abtn" id="btn-hire" type="button" role="menuitem" aria-label="Add session" hidden></button>
            <button class="abtn" id="btn-launch" type="button" role="menuitem" aria-label="Launch project"></button>
          </div>
        </div>
        <button class="abtn" id="btn-pair" type="button" aria-label="Phone"></button>
        <button class="abtn" id="btn-settings" type="button" aria-label="Settings"></button>
      </div>
    </header>
    <div class="cp-hidden-tools" hidden>
      <button id="btn-run" type="button"></button>
      <button id="btn-res" type="button"></button>
      <button id="btn-help" type="button"></button>
    </div>

    <main class="main">
      <!-- R110 骨格 A: 左＝01 オフィス（部屋）＋04 受付（あなた待ち 1 件・INBOX ZERO・最近の指示）／中央＝03 端末（指示欄は端末の下）／
           右＝02 名簿（台帳と一覧を 1 本に）。窓（.cpwin）は罫で囲んだ面・窓の間は地の溝（.main の gap）。
           2 カラム（900〜1400）は名簿を左の列の下・1 列（<900）は受付→指示欄→部屋→名簿→端末（並べ方は style.css）。 -->
      <section class="cpleft">
        <section class="cpwin" id="win-office">
          <div class="cpsec" id="sec-office" data-win="office">
            <span class="secno">01</span><span class="sectitle" id="cp-t-office"></span>
            <span class="secmeta" id="cp-office-meta"></span>
            <span class="key"><i class="swatch lit"></i><span id="cp-k-lit"></span></span>
            <span class="key"><i class="swatch"></i><span id="cp-k-off"></span></span>
            <span id="cp-pick"></span>
            <button id="cp-motion" type="button" aria-pressed="false"></button>
          </div>
          <div id="cp-setup-slot"></div>
          <section class="cpstage t-dark" id="stage">
            <div class="viewport" id="viewport"></div>
          </section>
        </section>
        <section class="cpwin" id="win-reception">
          <div class="cpsec" id="sec-reception" data-win="reception">
            <span class="secno">04</span><span class="sectitle" id="cp-t-reception"></span>
            <span class="secmeta" id="cp-reception-meta"></span>
          </div>
          <div class="tray" id="attn" hidden></div>
          <p class="cpinboxzero" id="cp-inbox-zero" hidden></p>
          <div class="cphist">
            <b class="cardtitle" id="title-hist"></b>
            <div class="hist" id="hist"></div>
          </div>
        </section>
      </section>

      <section class="cpcenter cpwin" id="cp-center">
        <div class="cpsec" data-win="terminal">
          <span class="secno">03</span><span class="sectitle" id="cp-t-term"></span>
          <span class="secmeta" id="cp-term-meta"></span>
          <button class="cpclose" id="cp-activity-toggle" type="button" hidden></button>
        </div>
        <div class="cptabbar">
          <button class="cptabs-more l" id="cp-tabs-l" type="button" hidden></button>
          <div class="cptabs" id="cp-tabs" role="tablist"></div>
          <button class="cptabs-more r" id="cp-tabs-r" type="button" hidden></button>
          <button class="cpnew" id="cp-term-new" type="button" aria-haspopup="menu" aria-expanded="false"></button>
          <div class="cpmenu" id="cp-term-menu" role="menu" hidden>
            <span class="cpmenu-head" id="cp-term-where"></span>
            <button type="button" role="menuitem" data-kind="claude" id="cp-new-claude"></button>
            <button type="button" role="menuitem" data-kind="codex" id="cp-new-codex"></button>
            <button type="button" role="menuitem" data-kind="shell" id="cp-new-shell"></button>
            <!-- R111-B2: ペインを分けて開く（3 カラムの間だけ出す・新しいペインは空＝続けて上の 3 つから選べる） -->
            <span class="cpmenu-sep" role="separator"></span>
            <button type="button" role="menuitem" data-pane="h" id="cp-pane-split-h"></button>
            <button type="button" role="menuitem" data-pane="v" id="cp-pane-split-v"></button>
          </div>
        </div>
        <div class="cpterm t-dark" id="cp-term">
          <div class="cpwork" id="cp-work"></div>
          <div class="cpdlg" id="cp-dlg"></div>
          <!-- 本物の端末（xterm.js）。AI Office が起こしたセッションだけがここに出る。R111-B2: ペインの根（.cppane を置く・id 据置） -->
          <div class="cpxterm" id="cp-xterm" hidden></div>
          <!-- R111-B2: 会話の組（#cp-work・#cp-dlg）の駐車場（選んでいるペインが端末の間・分けたペインのどれにも会話を出さない間） -->
          <div id="cp-dlg-park" hidden></div>
        </div>
        <div class="cptermfoot act">
          <i class="dot" id="cp-term-dot"></i>
          <span id="cp-term-who"></span>
          <span id="cp-term-state"></span>
          <!-- ここは ui/hud/dialog.js の paintDlgHead が**中身を全消しして作り替える**。
               自分の要素を同じ箱へ置くと、会話が届いた瞬間に消える（実測: 名前も状態も消えて
               件数の「0」だけが残った）。だから dialog 専用の箱を 1 つ分けて渡す。 -->
          <span class="dlghead" id="cp-term-head"></span>
          <span class="foot-end" id="cp-term-note"></span>
          <span class="cpacts" id="cp-term-acts" hidden>
            <button class="cpclose" id="cp-compose" type="button"></button>
            <button class="cpclose" id="cp-jump" type="button"></button>
          </span>
          <button class="cpclose" id="cp-term-close" type="button" hidden></button>
        </div>
      </section>

      <aside class="cpright cpwin" id="cp-right">
        <div class="cpsec" id="sec-roster" data-win="roster">
          <span class="secno">02</span><span class="sectitle" id="cp-t-roster"></span>
          <span class="secmeta" id="cp-roster-meta"></span>
          <div class="cpledsearch">
            <input id="cp-ledger-search" type="search" autocomplete="off" spellcheck="false">
          </div>
          <!-- R110-D1: 名簿の窓が低い 2 カラム（@container cproster）だけ、「▾ あと N 件」を見出しの右に出す（下端の帯の代わり＝行を 1 本ぶん空ける） -->
          <button class="cpmore-chip" id="cp-roster-more-chip" type="button" hidden></button>
        </div>
        <div id="cp-roster">
          <div class="cptable" role="table">
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
        </div>
        <button class="cpmore" id="cp-roster-more" type="button" hidden></button>
      </aside>

      <aside class="sheet" id="sheet" hidden>
        <header class="sheethead">
          <b id="sheetname"></b>
          <span class="sheettools">
            <button class="sheetterm" id="sheetarch" type="button" hidden></button>
            <button class="sheetterm sheetwide" id="sheetwide" type="button">⤢</button>
            <button class="sheetterm" id="sheetterm" type="button" data-px="term"></button>
            <button class="sheetsnd" id="sheetsnd" type="button" data-px="bell-off"></button>
            <button class="sheetclose" id="sheetclose" type="button" data-px="close"></button>
          </span>
        </header>
        <p class="sheetact" id="sheetact"></p>
        <div class="sheetbody" id="sheetbody"></div>
        <div class="quickdock" id="quickdock"></div>
        <p class="sheettarget" id="sheettarget" hidden></p>
        <div class="compose" id="compose">
          <textarea id="composeinput" rows="1" autocomplete="off"></textarea>
        </div>
      </aside>
      <!-- R111: 3 カラムの境目の取っ手（溝の中の透明な 8px・ui/cockpit/layout.js がドラッグを受ける）。列の取っ手は第 2・第 3 列の
           左の溝（grid-column は index.js が gridTemplate の colsplits から書く・これは書く前の値）・段の取っ手は 2 窓の列の溝の行。
           2 カラム・1 列では出さない（style.css）。キーボードの入口は題名の帯（Alt+矢印・メニュー）＝取っ手は読み上げない -->
      <div class="cpsplit" data-split="col" data-between="0" style="grid-column: 2" aria-hidden="true"></div>
      <div class="cpsplit" data-split="col" data-between="1" style="grid-column: 3" aria-hidden="true"></div>
      <div class="cpsplit" data-split="row" aria-hidden="true"></div>
      <!-- R111: 窓を動かすメニュー（題名の帯の右クリック・Enter・Space・ContextMenu。無変化の向きは disabled） -->
      <div class="cpmenu" id="cp-move-menu" role="menu" hidden>
        <button type="button" role="menuitem" data-dir="left"></button>
        <button type="button" role="menuitem" data-dir="right"></button>
        <button type="button" role="menuitem" data-dir="up"></button>
        <button type="button" role="menuitem" data-dir="down"></button>
        <button type="button" role="menuitem" data-dir="reset"></button>
      </div>
      <div class="toast" id="toast" hidden></div>
      <div class="modalwrap" id="modalwrap" hidden>
        <div class="modal" id="modal"></div>
        <button class="modalclose" id="modalclose" type="button" aria-label="Close">✕</button>
      </div>
    </main>

    <footer class="cpfoot">
      <span id="cp-foot-seats"></span>
      <span id="pxtoday"></span>
      <span id="cp-login-slot"></span>
      <div class="offbar" id="offbar" hidden></div>
      <span class="foot-end" id="cp-foot-end"></span>
    </footer>
  `;
}

/** コックピットだけの静的文言。applyStaticStrings の後・言語切替時にも貼る。 */
export function applyCockpitStrings(shell, T) {
  for (const [id, key] of [
    ["#col-project", "col_project"], ["#col-session", "col_session"],
    ["#col-state", "col_state"], ["#col-detail", "col_detail"],
    ["#col-evidence", "col_evidence"], ["#col-progress", "col_progress"],
    ["#col-age", "col_age"],
    ["#cp-t-office", "cp_office"], ["#cp-t-reception", "cp_reception"],
    ["#cp-t-term", "cp_term"], ["#cp-t-roster", "cp_roster"],
    ["#cp-k-lit", "cp_key_lit"], ["#cp-k-off", "cp_key_off"],
    ["#cp-motion", "cp_motion_stop"],
    ["#cp-add", "cp_new_session"], ["#btn-pair", "cp_phone"],
    ["#cp-term-new", "cp_term_new"], ["#cp-new-claude", "cp_term_new_claude"],
    ["#cp-new-codex", "cp_term_new_codex"], ["#cp-new-shell", "cp_term_new_shell"],
    ["#cp-pane-split-h", "cp_pane_split_h"], ["#cp-pane-split-v", "cp_pane_split_v"],
    ["#cp-term-close", "cp_term_close"],
    ["#cp-compose", "cp_compose"], ["#cp-jump", "cp_jump"],
    // R111: 窓を動かすメニュー
    ['#cp-move-menu [data-dir="left"]', "cp_move_left"], ['#cp-move-menu [data-dir="right"]', "cp_move_right"],
    ['#cp-move-menu [data-dir="up"]', "cp_move_up"], ['#cp-move-menu [data-dir="down"]', "cp_move_down"],
    ['#cp-move-menu [data-dir="reset"]', "cp_move_reset"],
  ]) {
    const n = shell.querySelector(id);
    if (n) n.textContent = T(key);   // R110: ボタンの [ ] は外す（枠と地で示すのは style.css の仕事）
  }
  for (const [id, key] of [["#cp-add", "cp_new_session"], ["#btn-pair", "cp_phone"],
    ["#btn-settings", "cp_settings"], ["#usage-summary", "btn_res"], ["#btn-launch", "btn_launch"]]) {
    const n = shell.querySelector(id);
    if (!n) continue;
    n.title = T(key);
    n.setAttribute("aria-label", T(key));
  }
  shell.querySelector("#btn-settings").textContent = "⚙";
  // 文字を持たない印のボタンは、読み上げと title を言語に合わせて貼る（閉じるの aria-label が日本語固定だった）
  for (const [id, key] of [["#modalclose", "cp_term_close"], ["#sheetclose", "cp_term_close"], ["#cp-tabs-l", "cp_tabs_left"],
    ["#cp-tabs-r", "cp_tabs_right"], ["#btn-hire", "btn_hire"], ["#btn-newproj", "btn_newproj"],
    ["#sheetterm", "term_title"], ["#sheetsnd", "snd_title"],
    ["#sheetwide", "sheet_wide"], ["#sheetarch", "avatar_customize"]]) {
    const n = shell.querySelector(id);
    if (!n) continue;
    n.title = T(key);
    n.setAttribute("aria-label", T(key));
  }
  // R106-12: 台帳の絞り込み（placeholder＝aria-label・入力欄自体の挙動は table.js）
  const ledgerSearch = shell.querySelector("#cp-ledger-search");
  if (ledgerSearch) {
    ledgerSearch.placeholder = T("cp_ledger_search_ph");
    ledgerSearch.setAttribute("aria-label", T("cp_ledger_search_ph"));
  }
}
