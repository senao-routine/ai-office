// 明るいアプリ様式。共有HUDの操作先は同じidを保ち、会話を中央に置く。
export function template() {
  return `
  <button id="app-backdrop" class="app-backdrop" type="button" hidden data-app-label="app_close"></button>
  <nav id="app-sidebar" class="app-sidebar" data-app-label="app_menu">
    <div class="app-brand"><span class="app-mark" aria-hidden="true"><svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6"><rect x="2.5" y="3.5" width="11" height="9" rx="2"></rect><path d="M5.5 7.5h1M9.5 7.5h1M6 10.5h4"></path></svg></span><strong>AI Office</strong><button id="app-sidebar-close" type="button" data-app-label="app_close">×</button></div>
    <div class="app-new-wrap">
      <button id="app-new" class="app-nav-button" type="button" aria-haspopup="menu" aria-expanded="false" aria-controls="app-new-menu"><span aria-hidden="true">＋</span><span data-app-text="app_new"></span></button>
      <div id="app-new-menu" class="app-new-menu" role="menu" hidden>
        <button id="btn-newproj" type="button" role="menuitem"></button>
        <button id="btn-hire" type="button" role="menuitem" hidden></button>
        <button id="btn-launch" type="button" role="menuitem"></button>
      </div>
    </div>
    <label class="app-search"><span aria-hidden="true">⌕</span><input id="app-search" type="search" autocomplete="off" data-app-label="app_search"></label>
    <div class="app-session-scroll">
      <section class="app-your-turn"><h2><span data-app-text="app_your_turn"></span><span id="app-your-count">0</span></h2><div id="app-attention-list"></div></section>
      <div id="app-projects"></div>
    </div>
    <div class="app-sidebar-bottom">
      <button id="app-usage-open" type="button" class="app-usage"><span data-app-text="app_week"></span><span id="app-usage-value"></span></button>
      <div id="app-usage-bar" class="app-progress" role="progressbar" aria-valuemin="0" aria-valuemax="100" data-app-label="app_week"><i></i></div>
      <button id="app-today" class="app-nav-button" type="button" data-app-text="today_btn"></button>
      <button id="btn-pair" class="app-nav-button" type="button"></button>
      <button id="btn-settings" class="app-nav-button" type="button"></button>
    </div>
  </nav>
  <main class="app-center">
    <header class="app-header">
      <button id="app-menu" type="button" aria-expanded="false" aria-controls="app-sidebar" data-app-label="app_menu">☰</button>
      <div class="app-heading"><h1 id="app-title">AI Office</h1><span id="app-project"></span></div>
      <span id="app-state" class="app-state" hidden></span>
      <div class="app-header-actions"><button id="app-terminal-toggle" type="button" hidden data-app-text="app_terminal"></button><button id="app-jump" type="button" hidden data-app-text="app_jump"></button><button id="app-details-toggle" type="button" aria-expanded="true" aria-controls="app-details" data-app-text="app_details"></button></div>
    </header>
    <div id="app-activity-scroll" class="app-activity-scroll"><div id="app-activity" class="app-activity act"></div></div>
    <div id="app-terminal" class="app-terminal" hidden></div>
    <form id="app-compose" class="app-compose">
      <label for="app-input" class="app-sr" data-app-text="app_input"></label>
      <div class="app-input-box"><textarea id="app-input" rows="1" autocomplete="off"></textarea><button id="app-send" type="submit" data-app-label="app_send">↑</button></div>
      <p id="app-delivery" role="status"></p>
    </form>
  </main>
  <aside id="app-details" class="app-details" data-app-label="app_details">
    <button id="app-details-close" type="button" class="app-drawer-close" data-app-text="app_details_close"></button>
    <section class="app-card app-office-card">
      <div class="app-card-head"><h2 data-app-text="app_office"></h2><div class="app-room-toggle" role="group" data-app-label="app_room_look"><button id="app-room-3d" type="button" aria-pressed="true" data-app-text="app_room_3d"></button><button id="app-room-pixel" type="button" aria-pressed="false" data-app-text="app_room_pixel"></button></div></div>
      <div id="app-room" class="app-room"></div>
      <p class="app-room-hint" data-app-text="app_room_hint"></p>
      <div id="app-people" class="app-people" data-app-label="app_people"></div>
    </section>
    <section class="app-card app-card-pad"><div class="app-card-head"><h2 data-app-text="app_now"></h2><span id="app-todo-count"></span></div><p id="app-current"></p><div id="app-todo-progress" class="app-progress"><i></i></div></section>
    <section class="app-card app-card-pad"><h2 data-app-text="app_todos"></h2><div id="app-todos"></div></section>
    <section class="app-card app-card-pad"><div class="app-card-head"><h2 data-app-text="app_changes"></h2><span id="app-file-count"></span></div><p id="app-file-delta"></p><details id="app-files"><summary data-app-text="app_show_files"></summary><div id="app-file-list"></div></details></section>
    <section class="app-card app-card-pad"><h2 data-app-text="app_environment"></h2><dl id="app-environment"></dl></section>
  </aside>
  <!-- 共有HUDの安全な送信・回答・管理の操作先。会話の回答ボタンはこのトレイへ委譲する。 -->
  <div class="app-hud-parking admin" hidden>
    <div id="attn" class="tray" hidden></div><div id="hist" class="hist"></div>
    <div class="usage"><button id="usage-summary" type="button"></button><div id="gauges" class="gauges" hidden><div id="gcredits" class="gcard" hidden><b id="gtitle-credits"></b><div id="gcreditbody"></div></div><div id="gmoney" class="gcard" hidden><b id="gtitle-money"></b><div id="gmoneybody"></div></div></div></div>
    <button id="btn-run" type="button"></button><button id="btn-res" type="button"></button><button id="btn-help" type="button"></button><span id="freshness"></span><span id="brandoffice"></span>
  </div>
  <aside id="sheet" class="sheet" hidden><header class="sheethead"><b id="sheetname"></b><span class="sheettools"><button id="sheetarch" class="sheetterm" type="button" hidden></button><button id="sheetwide" class="sheetterm sheetwide" type="button">↔</button><button id="sheetterm" class="sheetterm" type="button">↗</button><button id="sheetsnd" class="sheetsnd" type="button"></button><button id="sheetclose" class="sheetclose" type="button">×</button></span></header><p id="sheetact" class="sheetact"></p><div id="sheetbody" class="sheetbody"></div><div id="quickdock" class="quickdock"></div><p id="sheettarget" class="sheettarget" hidden></p><div id="compose" class="compose"><textarea id="composeinput" rows="1"></textarea></div></aside>
  <div id="toast" class="toast" hidden></div>
  <div id="modalwrap" class="modalwrap" hidden><div id="modal" class="modal"></div><button id="modalclose" class="modalclose" type="button" data-app-label="app_close">×</button></div>
  <div id="offbar" class="offbar" hidden></div>`;
}

export function applyAppStrings(shell, T) {
  for (const n of shell.querySelectorAll("[data-app-text]")) n.textContent = T(n.dataset.appText);
  for (const n of shell.querySelectorAll("[data-app-label]")) n.setAttribute("aria-label", T(n.dataset.appLabel));
  shell.querySelector("#app-search").placeholder = T("app_search");
  shell.querySelector("#app-input").placeholder = T("app_input_ph");
  shell.querySelector("#btn-pair").textContent = T("app_phone");
  shell.querySelector("#btn-settings").textContent = T("app_settings");
}
