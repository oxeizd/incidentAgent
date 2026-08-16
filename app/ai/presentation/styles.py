"""
app/ai/presentation/styles.py — CSS шаблона презентации.

Статический ассет: не читаем и не трогаем этот файл при работе с
логикой сборки презентации (см. app/ai/presentation/*.py остальные модули).
Меняем только когда реально правим внешний вид верстки.
"""

CSS = """
:root{
  --bg1:#f3f3f1; --bg2:#d9def7; --text:#242424; --tab-active-1:#98aafc; --tab-active-2:#8398f6;
  --timeline:#223038; --timeline-2:#24323a; --line:#e8e3dd;
}
*, *::before, *::after { box-sizing:border-box; }
html, body{ margin:0; width:100%; height:100%; font-family:"Segoe UI", Arial, sans-serif; font-size:22px; color:var(--text); overflow:hidden; background:linear-gradient(180deg, var(--bg1) 36%, var(--bg2) 100%); }
.app{ width:100vw; height:100vh; display:flex; align-items:center; justify-content:center; }
.viewport{ position:relative; width:2560px; height:1200px; overflow:hidden; background:linear-gradient(180deg, var(--bg1) 36%, var(--bg2) 100%); transform-origin:center center; transition:transform .1s ease-out; }
.shell{ position:absolute; inset:0; display:flex; flex-direction:column; }
.header{ display:flex; align-items:flex-start; justify-content:space-between; gap:24px; padding:24px 36px 10px; }
.header-left{ flex:1 1 auto; min-width:0; }
.header-tribe{ font-size:20px; line-height:1.3; color:#5e5a56; margin-bottom:4px; }
.header-title{ font-size:30px; line-height:1.1; font-weight:700; color:#1e1e1e; margin:0; }
.header-right{ flex:0 0 auto; display:flex; align-items:flex-start; gap:10px; }
.edit-toolbar{ position:fixed; right:14px; bottom:14px; z-index:9999; display:flex; gap:6px; background:transparent; border:none; border-radius:12px; padding:0; box-shadow:none; }
.edit-btn{ border:1px solid rgba(255,255,255,.35); background:rgba(255,255,255,.16); color:#2d2b29; border-radius:9px; padding:5px 9px; font-size:11px; font-weight:600; cursor:pointer; transition:all .2s ease; backdrop-filter:blur(6px); }
.edit-btn:hover{ background:rgba(255,255,255,.24); }
.edit-btn.active{ background:rgba(142,163,251,.35); color:#fff; border-color:rgba(142,163,251,.55); }
.tabs-wrapper{ position:relative; display:flex; align-items:center; max-width:1100px; }
.tab-nav-btn{ border:1px solid #E0E4EE; background:#FFFFFF; cursor:pointer; padding:0 14px; display:flex; align-items:center; justify-content:center; z-index:10; opacity:0; transition:opacity .2s, transform .15s ease, box-shadow .15s ease; height:42px; min-width:42px; gap:8px; border-radius:14px; box-shadow:0 1px 2px rgba(0,0,0,.04); color:#060A0C; white-space:nowrap; font-size:18px; font-weight:500; line-height:1; }
.tab-nav-btn.visible{ opacity:1; }
.tab-btn{ border:1px solid rgba(217,220,231,.9); background:rgba(255,255,255,.96); color:#3d3b39; border-radius:14px; padding:11px 20px; font-size:17px; font-weight:500; cursor:pointer; white-space:nowrap; transition:.15s ease; height:46px; display:flex; align-items:center; flex-shrink:0; box-shadow:0 1px 2px rgba(0,0,0,.04); }
.tab-btn.active{ color:#fff; background:linear-gradient(180deg, var(--tab-active-1) 0%, var(--tab-active-2) 100%); box-shadow:0 8px 18px rgba(131,152,246,.28); border-color:transparent; }
.tabs{ display:flex; align-items:center; gap:10px; overflow-x:auto; scroll-behavior:smooth; scrollbar-width:none; -ms-overflow-style:none; padding:0 5px; }
.tabs::-webkit-scrollbar{ display:none; }
.tab-panels{ flex:1; position:relative; overflow:hidden; padding:18px 20px 22px; }
.tab-panel{ position:absolute; inset:18px 20px 22px 20px; display:none; overflow-y:auto; padding-right:10px; }
.tab-panel.active{ display:block; }
.tab-panel{ scrollbar-width:thin; scrollbar-color:#D6D9DE #F6F7F9; }
.tab-panel::-webkit-scrollbar{ width:8px; height:8px; }
.tab-panel::-webkit-scrollbar-track{ background:#F6F7F9; border-radius:999px; }
.tab-panel::-webkit-scrollbar-thumb{ background:#D6D9DE; border-radius:999px; border:2px solid #F6F7F9; }
.summary-layout{ display:flex; flex-direction:column; gap:24px; margin-top:18px; }
.summary-block{ background:rgba(255,255,255,.72); border:1px solid var(--line); border-radius:16px; overflow:hidden; padding:10px 0; }
.summary-title{ padding:10px 24px; font-size:24px; color:#4e5965; font-weight:700; }
.summary-table{ width:100%; border-collapse:collapse; table-layout:fixed; }
.summary-table thead th{ text-align:left; font-size:18px; color:#8b93a0; font-weight:600; padding:12px 24px; border-bottom:1px solid #E3E8EF; background:transparent; }
.summary-table td{ padding:14px 24px; border-bottom:1px solid #E3E8EF; font-size:20px; color:#404954; vertical-align:middle; line-height:1.32; }
.summary-table tr:last-child td{ border-bottom:none; }
.summary-table tr.clickable-row{ cursor:pointer; }
.col-id{ width:240px; } .col-sys{ width:340px; } .col-desc{ width:420px; } .col-resp{ width:300px; } .col-loss{ width:200px; } .col-arrow{ width:70px; text-align:right; padding-right:20px !important; }
.text-clamp-2{ display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; text-overflow:ellipsis; word-break:break-word; max-height:2.65em; line-height:1.32; }
.arrow-btn{ display:inline-flex; align-items:center; justify-content:center; width:42px; height:42px; border:none; background:transparent; cursor:pointer; padding:0; float:right; }
.loss-na-otsenke{ color:#8b93a0 !important; }
.main-grid{ display:grid; grid-template-columns:minmax(0,1fr) 430px; gap:18px; min-height:100%; margin-top:10px; }
.left-col,.stack{ display:flex; flex-direction:column; gap:14px; min-width:0; }
.right-col{ display:flex; flex-direction:column; gap:14px; min-width:0; height:100%; }
.info-strip{ display:grid; grid-template-columns:.9fr 1fr 1fr 1.35fr 1.35fr; gap:12px; margin-bottom:6px; }
.info-item{ padding:14px 18px; min-width:0; background:rgba(255,255,255,.96); border:1px solid #e3e7ef; border-radius:14px; box-shadow:0 1px 2px rgba(0,0,0,.03); }
.info-label{ font-size:18px; color:#202020; margin-bottom:8px; font-weight:700; }
.info-value{ font-size:20px; color:#2e2d2b; line-height:1.35; font-weight:500; word-break:break-word; }
.detail-card,.table-card,.loss-card{ background:rgba(255,255,255,.78); border:1px solid #e8e3dd; border-radius:16px; overflow:hidden; }
.detail-row{ padding:18px 22px 16px; border-top:1px solid #ece8e2; } .detail-row:first-child{ border-top:none; }
.detail-title,.card-title{ font-size:21px; line-height:1.25; font-weight:700; color:#222; margin-bottom:10px; padding:16px 20px 8px; }
.detail-row .detail-title{ padding:0; margin-bottom:10px; }
.detail-text{ font-size:21px; line-height:1.55; color:#2d2c2a; }
.detail-text.chain-line{ padding:12px 10px; border-top:1px solid #f0ece7; white-space:pre-wrap; }
.detail-text.chain-line.first{ padding-top:0; border-top:none; }
.loss-card{ padding:20px 22px 18px; border-radius:18px; flex-shrink:0; }
.loss-label{ font-size:21px; color:#2a2927; margin-bottom:10px; font-weight:700; }
.loss-value{ font-size:52px; line-height:1.02; color:#202020; font-weight:400; letter-spacing:.3px; }
.timeline-panel{ background:linear-gradient(180deg,var(--timeline) 0%,var(--timeline-2) 100%); color:#fff; border-radius:16px; overflow:hidden; padding:18px 18px 22px; display:flex; flex-direction:column; min-height:200px; max-height:900px; }
.timeline-header{ padding:0 0 14px; font-size:21px; font-weight:700; color:#fff; flex-shrink:0; }
.timeline-body{ position:relative; display:flex; flex-direction:column; gap:14px; padding-left:22px; padding-right:6px; overflow-y:auto; max-height:800px; scrollbar-width:thin; scrollbar-color:rgba(255,255,255,.35) transparent; }
.timeline-body::-webkit-scrollbar{ width:6px; }
.timeline-body::-webkit-scrollbar-track{ background:transparent; }
.timeline-body::-webkit-scrollbar-thumb{ background:rgba(255,255,255,.35); border-radius:999px; }
.timeline-body::before{ content:""; position:absolute; left:6px; top:4px; bottom:4px; width:2px; background:rgba(255,255,255,.78); border-radius:2px; }
.tl-item{ position:relative; padding-bottom:4px; }
.tl-content{ display:block; padding-left:0; }
.tl-dot{ position:absolute; left:-22px; top:7px; width:12px; height:12px; border-radius:50%; background:#f4f0e5; }
.tl-meta{ font-size:15px; line-height:1.2; color:rgba(255,255,255,.72); margin-bottom:5px; font-weight:500; word-break:break-word; }
.tl-main{ font-size:19px; line-height:1.34; color:#fff; font-weight:600; word-break:break-word; }
.table-head,.table-row{ display:grid; grid-template-columns:1fr 260px; padding-left:20px; padding-right:20px; }
.table-head{ font-size:16px; color:#8a847e; padding-top:8px; padding-bottom:10px; }
.table-row{ padding-top:14px; padding-bottom:14px; border-top:1px solid #f0ece7; font-size:20px; color:#2d2b29; line-height:1.45; }
.alert-name{ display:flex; align-items:flex-start; gap:10px; }
.alert-marker{ width:4px; height:20px; border-radius:4px; margin-top:2px; flex:0 0 auto; }
.alert-ok{ background:#54c467; } .alert-bad{ background:#f36d6d; }
.head-right, .status-cell, .resp-cell{ display:flex; justify-content:flex-start; text-align:left !important; }
.head-right span, .status-value{ display:block; width:100%; text-align:left; }
.resp-cell{ line-height:1.45; white-space:normal; }
.alerts-head, .alerts-row{ grid-template-columns:minmax(0,1fr) 220px; }
.measures-head, .measures-row{ grid-template-columns:minmax(0,1fr) 260px; }
.measures-head[style*="1fr"], .measures-row[style*="1fr"]{ grid-template-columns:1fr !important; }
.edit-mode [data-section]{ position:relative; }
.edit-mode [data-section]:hover{ outline:2px dashed rgba(131,152,246,.5); outline-offset:4px; }
.section-controls{ display:none; position:absolute; top:8px; right:8px; z-index:5; gap:6px; }
.edit-mode [data-section] .section-controls{ display:flex; }
.section-btn{ border:none; background:rgba(34,48,56,.45); color:#fff; border-radius:10px; padding:6px 9px; font-size:12px; font-weight:700; cursor:pointer; backdrop-filter:blur(6px); }
.edit-mode .editable-text{ cursor:text; }
.edit-mode .editable-text[contenteditable="true"]{ outline:1px dashed rgba(131,152,246,.65); outline-offset:2px; border-radius:6px; background:rgba(255,255,255,.2); }
"""
