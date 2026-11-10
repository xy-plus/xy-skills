"""Build the offline, responsive feature-tree page from map data."""
import json


PAGE = r'''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>仓库功能树</title>
<style>
:root{color-scheme:light;--ink:#202a38;--muted:#687587;--line:#dfe5ec;--code:#4b87e5;--tests:#9c78d6;--docs:#45a384;--comments:#df9a2e}
*{box-sizing:border-box}
body{margin:0;background:#f5f7fa;color:var(--ink);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;overflow-x:hidden}
button{font:inherit;color:inherit}
header{background:white;border-bottom:1px solid var(--line);padding:20px max(16px,calc((100% - 1040px)/2)) 18px}
h1{font-size:22px;line-height:1.25;margin:0 0 7px;overflow-wrap:anywhere}
.what{color:#39485c;margin:0 0 14px;overflow-wrap:anywhere}
.summary{display:grid;gap:9px;max-width:760px}
.summary-counts,.legend,.metrics,.incoming,.why-line,.toolbar,.marker-list{display:flex;flex-wrap:wrap;align-items:center;gap:8px 14px}
.summary-counts{font-weight:650}.summary-counts span{white-space:nowrap}
.bar{height:9px;display:flex;overflow:hidden;border-radius:10px;background:#e9edf2}
.bar span{height:100%;display:block;min-width:0}
.code{background:var(--code)}.tests{background:var(--tests)}.docs{background:var(--docs)}.comments{background:var(--comments)}
.legend{font-size:13px;color:var(--muted)}.legend i,.metric i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:5px}
main{max-width:1040px;margin:0 auto;padding:16px}
.marker-list{margin:0 0 13px}
.marker{border:1px solid var(--line);border-radius:999px;padding:5px 10px;background:white;font-size:13px}
button.marker{cursor:pointer}.marker.decision{background:#ffd45c;border-color:#f0bf39;color:#4e3800;font-weight:700}
.marker[aria-pressed="true"]{outline:2px solid #986800;outline-offset:1px}
.toolbar{margin:0 0 12px;color:var(--muted);font-size:14px}
.toolbar button{border:1px solid var(--line);border-radius:8px;background:white;padding:5px 10px;cursor:pointer;min-height:34px}
.tree{background:white;border:1px solid var(--line);border-radius:14px;padding:8px 12px}
.feature-node{min-width:0}.feature-row{position:relative;padding:12px 4px 12px 10px;border-bottom:1px solid #edf0f4;min-width:0}
.feature-row.decision{border-left:4px solid #e3ac19;padding-left:7px}
.feature-head{display:flex;align-items:flex-start;gap:7px;min-width:0}
.feature-name{min-width:0;flex:1;text-align:left;padding:0;border:0;background:transparent;font-weight:700;font-size:16px;line-height:1.35;cursor:pointer;overflow-wrap:anywhere}
.toggle{flex:0 0 34px;width:34px;height:32px;border:1px solid var(--line);border-radius:8px;background:white;cursor:pointer}
.feature-total{font-weight:700;white-space:nowrap;font-size:14px;color:#3d4b5e}
.what-row{margin:5px 0 4px;color:#3e4b5c;overflow-wrap:anywhere}
.why-line{font-size:13px;color:var(--muted);gap:5px}
.source-label{display:inline-block;border:1px solid #dce2e9;border-radius:5px;padding:0 5px;font-size:12px;background:#f9fafb;color:#506075}
.source-label.draft{background:#ffd45c;border-color:#f0bf39;color:#4e3800;font-weight:700}
.feature-markers{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.feature-markers .marker{padding:1px 6px;font-size:12px}
.relative-bar{height:7px;margin:8px 0 5px;max-width:100%;border-radius:8px;background:#eef1f5;overflow:hidden}
.metrics{font-size:13px;color:#59677a;gap:4px 11px}.metric{white-space:nowrap}
.incoming{font-size:13px;color:var(--muted);gap:4px 10px}
.uses{display:flex;flex-wrap:wrap;align-items:center;gap:5px;margin-top:5px;font-size:13px}
.uses-label{color:var(--muted)}.use-link{border:1px solid transparent;border-radius:6px;background:#f1f4f8;color:#315b88;padding:1px 6px;cursor:pointer;max-width:100%;overflow-wrap:anywhere}
.use-link.same-parent{border-color:#6a98c9;background:#eff6ff}.mention{font-size:12px;color:#718096;margin-left:3px}
.feature-children{margin-left:15px;border-left:1px solid #e3e8ee;padding-left:9px}
.undivided{display:flex;flex-wrap:wrap;justify-content:space-between;gap:6px;margin:6px 0 9px 15px;padding:7px 9px;background:#f6f8fa;color:#5e6a79;font-size:13px;border-radius:7px}
.detail-panel{position:fixed;z-index:5;left:0;right:0;bottom:0;max-height:72vh;overflow:auto;background:white;border-top:2px solid #7593b6;box-shadow:0 -8px 28px #1f293322;padding:16px max(16px,calc((100% - 760px)/2)) 24px}
.detail-top{display:flex;justify-content:space-between;gap:12px;align-items:flex-start}.detail-panel h2{font-size:19px;margin:0 0 3px;overflow-wrap:anywhere}.detail-path{color:var(--muted);font-size:13px;margin:0 0 12px}
.detail-panel p{margin:8px 0;overflow-wrap:anywhere}.detail-panel h3{font-size:14px;margin:14px 0 4px}.detail-list{margin:0;padding-left:20px}.detail-list li{margin:4px 0;overflow-wrap:anywhere}
.close-detail{border:1px solid var(--line);border-radius:8px;background:white;padding:5px 10px;cursor:pointer;min-height:36px}
@media(max-width:600px){body{font-size:14px}header{padding:16px}h1{font-size:20px}main{padding:11px}.tree{padding:5px 8px;border-radius:11px}.feature-row{padding-top:11px;padding-bottom:11px}.feature-name{font-size:15px}.feature-children{margin-left:7px;padding-left:7px}.feature-total{font-size:13px}.detail-panel{max-height:78vh;padding:14px 16px 22px}}
</style>
</head>
<body>
<header>
  <h1 id="page-title"></h1>
  <p class="what" id="page-what"></p>
  <div class="summary">
    <div class="summary-counts" id="summary-counts"></div>
    <div class="bar" id="summary-bar" aria-label="代码、测试、文档、注释行数"></div>
    <div class="legend"><span><i class="code"></i>代码</span><span><i class="tests"></i>测试</span><span><i class="docs"></i>文档</span><span><i class="comments"></i>注释</span></div>
  </div>
</header>
<main>
  <div class="marker-list" id="marker-list"></div>
  <div class="toolbar"><span>层级</span><button id="depth-minus" type="button" aria-label="少展开一层">−</button><span id="depth-value">2</span><button id="depth-plus" type="button" aria-label="多展开一层">＋</button><button id="depth-all" type="button">全部</button></div>
  <div class="tree" id="tree"></div>
</main>
<aside class="detail-panel" id="detail-panel" hidden aria-live="polite"></aside>
<script id="map-data" type="application/json">__MAP_DATA__</script>
<script>
"use strict";
const mapData = JSON.parse(document.querySelector("#map-data").textContent);
window.featureMapData = mapData;
const featureMap = mapData.features;
const lineKeys = ["code", "tests", "docs", "comments"];
const lineTitles = {code:"代码", tests:"测试", docs:"文档", comments:"注释"};
const markerOrder = ["等你拍板", "还没实现", "没归到功能的代码"];
const sourceLabels = {原话:"你的原话", 确认:"你确认过", 起草:"AI 起草 · 等你拍板"};
const rows = new Map();
const childLists = new Map();
let showDecisions = false;
let depth = 2;
let maximumDepth = 1;
const manualExpansion = new Map();

function sumLines(lines){return lineKeys.reduce((sum,key)=>sum+lines[key],0);}
function featureName(feature){return feature.directory_name ? `${feature.title}（${feature.directory_name}）` : feature.title;}
function make(tag, className, text){const element=document.createElement(tag);if(className)element.className=className;if(text!==undefined)element.textContent=text;return element;}
function appendMetrics(parent, lines){
  const metrics=make("div","metrics");
  for(const key of lineKeys){if(lines[key]){const item=make("span","metric");item.append(make("i",key));item.append(document.createTextNode(`${lineTitles[key]} ${lines[key]}`));metrics.append(item);}}
  if(metrics.childElementCount)parent.append(metrics);
}
function descendantsHaveDecision(featureId){
  const feature=featureMap[featureId];
  return feature.markers.includes("等你拍板") || feature.children.some(descendantsHaveDecision);
}
function renderFeature(featureId, level){
  const feature=featureMap[featureId];
  maximumDepth=Math.max(maximumDepth,level);
  const section=make("section","feature-node");
  const row=make("div","feature-row"+(feature.markers.includes("等你拍板")?" decision":""));
  row.dataset.featureId=featureId;
  rows.set(featureId,row);
  const head=make("div","feature-head");
  const nameButton=make("button","feature-name",featureName(feature));
  nameButton.type="button";nameButton.addEventListener("click",()=>openDetails(featureId));
  head.append(nameButton);
  const lineTotal=sumLines(feature.lines.total);
  head.append(make("span","feature-total",lineTotal?`${lineTotal} 行`:"—"));
  if(feature.children.length){
    const toggle=make("button","toggle", "＋");toggle.type="button";toggle.setAttribute("aria-label",`展开${featureName(feature)}`);
    toggle.addEventListener("click",()=>{const list=childLists.get(featureId);manualExpansion.set(featureId,list.hidden);applyVisibility();});head.append(toggle);
  }
  row.append(head);
  row.append(make("p","what-row",feature.what));
  const why=make("div","why-line");why.append(document.createTextNode("为什么："));
  const label=make("span","source-label"+(feature.why.source==="起草"?" draft":""),sourceLabels[feature.why.source]);why.append(label);
  why.append(document.createTextNode(feature.why.text));row.append(why);
  if(lineTotal){
    const parentTotal=feature.parent?sumLines(featureMap[feature.parent].lines.total):lineTotal;
    const relative=make("div","relative-bar");relative.style.width=`${parentTotal?Math.max(0,100*lineTotal/parentTotal):100}%`;
    for(const key of lineKeys){if(feature.lines.total[key]){const part=make("span",key);part.style.width=`${100*feature.lines.total[key]/lineTotal}%`;relative.append(part);}}
    row.append(relative);
  }
  appendMetrics(row,feature.lines.total);
  const incoming=[];
  for(const [otherId,other] of Object.entries(featureMap)){
    const use=other.uses.find(item=>item.feature===featureId);if(use)incoming.push(use.by);
  }
  const importCount=incoming.filter(kind=>kind==="import").length;
  const mentionCount=incoming.filter(kind=>kind==="mention").length;
  if(importCount||mentionCount){
    const counts=make("div","incoming");if(importCount)counts.append(make("span","",`被用 ${importCount}`));if(mentionCount)counts.append(make("span","",`被提及 ${mentionCount}`));row.append(counts);
  }
  if(feature.uses.length){
    const useLine=make("div","uses");useLine.append(make("span","uses-label","用到："));
    for(const use of feature.uses){
      const target=featureMap[use.feature];const button=make("button","use-link"+(target.parent===feature.parent?" same-parent":""),featureName(target));
      button.type="button";button.addEventListener("click",()=>openDetails(use.feature));
      if(use.by==="mention"){const wrap=make("span","");wrap.append(button,make("span","mention","提及"));useLine.append(wrap);}else useLine.append(button);
    }
    row.append(useLine);
  }
  if(feature.markers.length){
    const markerList=make("div","feature-markers");
    for(const marker of markerOrder){if(feature.markers.includes(marker))markerList.append(make("span","marker"+(marker==="等你拍板"?" decision":""),marker));}
    row.append(markerList);
  }
  section.append(row);
  if(feature.children.length){
    const childList=make("div","feature-children");childLists.set(featureId,childList);
    for(const child of feature.children)childList.append(renderFeature(child,level+1));
    section.append(childList);
  }
  if(feature.children.length&&sumLines(feature.lines.own)){
    const own=make("div","undivided");own.append(make("span","","没细分到子功能的部分"));
    own.append(make("span","",`${sumLines(feature.lines.own)} 行`));section.append(own);
  }
  return section;
}
function applyVisibility(){
  for(const [featureId,row] of rows){row.hidden=showDecisions&&!featureMap[featureId].markers.includes("等你拍板");}
  for(const [featureId,list] of childLists){
    const feature=featureMap[featureId];const level=featureLevel(featureId);
    if(showDecisions){list.hidden=!feature.children.some(descendantsHaveDecision);}
    else if(manualExpansion.has(featureId)){list.hidden=!manualExpansion.get(featureId);}
    else list.hidden=level>=depth;
    const toggle=rows.get(featureId).querySelector(".toggle");if(toggle){toggle.textContent=list.hidden?"＋":"−";toggle.setAttribute("aria-expanded",String(!list.hidden));}
  }
  const filter=document.querySelector("#decision-filter");if(filter)filter.setAttribute("aria-pressed",String(showDecisions));
  document.querySelector("#depth-value").textContent=String(depth);
}
function featureLevel(featureId){let count=1;let parent=featureMap[featureId].parent;while(parent){count++;parent=featureMap[parent].parent;}return count;}
function appendBarParts(element,lines){const total=sumLines(lines);if(!total)return;for(const key of lineKeys){if(lines[key]){const part=make("span",key);part.style.width=`${100*lines[key]/total}%`;element.append(part);}}}
function renderHeader(){
  const root=featureMap[mapData.root];document.querySelector("#page-title").textContent=featureName(root);
  document.querySelector("#page-what").textContent=root.what;
  const totalLines=root.lines.total;const total=sumLines(totalLines);
  const countLine=document.querySelector("#summary-counts");countLine.append(make("span","",`${total} 行`),make("span","",`${Object.keys(featureMap).length} 个功能`));
  const bar=document.querySelector("#summary-bar");appendBarParts(bar,totalLines);
  const markers=document.querySelector("#marker-list");
  for(const marker of markerOrder){const count=mapData.markers[marker];if(!count)continue;
    if(marker==="等你拍板"){
      const button=make("button","marker decision",`${marker} ${count}`);button.type="button";button.id="decision-filter";
      button.addEventListener("click",()=>{showDecisions=!showDecisions;applyVisibility();});markers.append(button);
    }else markers.append(make("span","marker",`${marker} ${count}`));
  }
}
function sourceLabel(source){return sourceLabels[source]||source;}
function relationList(items){
  const list=make("ul","detail-list");
  if(!items.length){list.append(make("li","","无"));return list;}
  for(const item of items){const target=featureMap[item.feature];const line=make("li","");line.append(document.createTextNode(featureName(target)));
    if(item.by==="mention")line.append(document.createTextNode("（提及）"));list.append(line);}
  return list;
}
function openDetails(featureId){
  const feature=featureMap[featureId];const panel=document.querySelector("#detail-panel");panel.replaceChildren();
  const top=make("div","detail-top");const heading=make("h2","",featureName(feature));top.append(heading);
  const close=make("button","close-detail","关闭");close.type="button";close.addEventListener("click",()=>{panel.hidden=true;});top.append(close);panel.append(top);
  const path=[];let current=featureId;while(current){path.unshift(featureName(featureMap[current]));current=featureMap[current].parent;}
  panel.append(make("p","detail-path",path.join(" › ")));
  panel.append(make("p","",`要什么：${feature.what}`));
  panel.append(make("p","",`为什么：${feature.why.text}`));
  panel.append(make("p","",`出处：${sourceLabel(feature.why.source)}${feature.why.from?` · ${feature.why.from}`:""}`));
  if(feature.why.quote)panel.append(make("p","",`原文：${feature.why.quote}`));
  panel.append(make("h3","","用到"),relationList(feature.uses));
  const incoming=[];for(const [otherId,other] of Object.entries(featureMap)){for(const use of other.uses){if(use.feature===featureId)incoming.push({feature:otherId,by:use.by});}}
  panel.append(make("h3","","被用到"),relationList(incoming));panel.hidden=false;
}
function setupControls(){
  document.querySelector("#depth-minus").addEventListener("click",()=>{depth=Math.max(1,depth-1);manualExpansion.clear();applyVisibility();});
  document.querySelector("#depth-plus").addEventListener("click",()=>{depth=Math.min(maximumDepth,depth+1);manualExpansion.clear();applyVisibility();});
  document.querySelector("#depth-all").addEventListener("click",()=>{depth=maximumDepth;manualExpansion.clear();applyVisibility();});
}
renderHeader();
document.querySelector("#tree").append(renderFeature(mapData.root,1));
setupControls();
applyVisibility();
</script>
</body>
</html>
'''


def render_page(data):
    """Return one self-contained page with no tracked file paths."""
    embedded = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    embedded = embedded.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return PAGE.replace("__MAP_DATA__", embedded)
