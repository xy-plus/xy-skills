(async()=>{
  "use strict";
  const DATA=JSON.parse(document.getElementById("page-data").textContent);
  const SVG_NS="http://www.w3.org/2000/svg";
  const COUNT_LABELS=[["code","代码"],["docs","文档"],["comments","注释"],["tests","测试"]];
  const FONT_FAMILY='system-ui,"Noto Sans CJK SC","Microsoft YaHei",sans-serif';
  const METRIC_FONT=`600 15px ${FONT_FAMILY}`;
  const DESCRIPTION_FONT=`13px ${FONT_FAMILY}`;
  const EDGE_FONT=`13px ${FONT_FAMILY}`;
  const LEADING_PUNCTUATION=new Set(Array.from("，。！？、；：）』」》〉】〕］｝”’)]}'\""));
  const REPO_ALIAS=Object.fromEntries(Object.entries(DATA.repo_roots).map(([placeholder,root])=>[root,placeholder]));
  const PALETTE=["#4674bb","#438674","#8062a8","#ad7c38","#448a9a","#a66a78"];
  const TREE_LAYOUT={"elk.algorithm":"layered","elk.direction":"RIGHT","elk.edgeRouting":"ORTHOGONAL",
    "elk.spacing.nodeNode":"48","elk.layered.spacing.nodeNodeBetweenLayers":"120",
    "elk.spacing.edgeNode":"12","elk.spacing.edgeEdge":"8"};
  const DRILL_LAYOUT={"elk.algorithm":"layered","elk.direction":"RIGHT","elk.edgeRouting":"ORTHOGONAL",
    "elk.hierarchyHandling":"INCLUDE_CHILDREN","elk.spacing.nodeNode":"38",
    "elk.layered.spacing.nodeNodeBetweenLayers":"54","elk.spacing.edgeNode":"12",
    "elk.spacing.edgeEdge":"8","elk.edgeLabels.placement":"CENTER","elk.spacing.edgeLabel":"5"};
  const svg=document.getElementById("map"),measure=document.createElement("canvas").getContext("2d");
  const children=new Map();
  for(const [id,node] of Object.entries(DATA.nodes)){
    if(node.parent!==null){const list=children.get(node.parent)||[];list.push(id);children.set(node.parent,list);}
  }
  for(const ids of children.values())ids.sort();
  let view={mode:"expand",current:null},transform={x:0,y:0,scale:1};
  let currentBounds={x:0,y:0,width:1,height:1},drawNumber=0,fontsReady=false;
  let drag=null,pinch=null,pressTimer=null,suppressedGesture=null,highlightedNode=null;
  const pointers=new Map();
  function childrenOf(id){return children.get(id)||[];}
  function visualChildren(id){return childrenOf(DATA.repo_roots[id]||id);}
  function visualParent(id){const parent=DATA.nodes[id].parent;return REPO_ALIAS[parent]||parent;}
  function nodeDepth(id){let depth=0,parent=DATA.nodes[id].parent;while(parent!==null&&parent!==undefined){depth++;parent=DATA.nodes[parent].parent;}return depth;}
  function visualDepth(id){let depth=0,parent=visualParent(id);while(parent!==null&&parent!==undefined){depth++;parent=visualParent(parent);}return depth;}
  function widthOf(text,font){measure.font=font;return measure.measureText(String(text)).width;}
  function wrapText(text,width,font){
    const lines=[];
    for(const paragraph of String(text||"").split("\n")){
      if(paragraph===""){lines.push("");continue;}
      let line="";
      for(const character of paragraph){
        if(line&&widthOf(line+character,font)>width){
          const chars=Array.from(line),carry=[character];
          while(chars.length){const previous=chars.pop();carry.unshift(previous);if(!LEADING_PUNCTUATION.has(previous))break;}
          if(chars.length){lines.push(chars.join(""));line=carry.join("");}else line+=character;
        }else line+=character;
      }
      lines.push(line);
    }
    return lines;
  }
  function nodeSize(id,hasKids,depth,maxWidth=null){
    const node=DATA.nodes[id],titleFontSize=depth===0?17:depth===1?16:15;
    const titleWeight=depth<2?700:600,titleFont=`${titleWeight} ${titleFontSize}px ${FONT_FAMILY}`;
    const badgeWidth=node.kind==="repo"?widthOf("子仓",`700 10px ${FONT_FAMILY}`)+12:0;
    const metricTexts=COUNT_LABELS.map(([key,label])=>`${label} ${Number(node.total[key]).toLocaleString("zh-CN")}`);
    const metricWidths=metricTexts.map(value=>widthOf(value,METRIC_FONT)+14);
    const metricRowWidth=metricWidths.reduce((sum,width)=>sum+width,0)+5*(metricWidths.length-1);
    const titleWidth=widthOf(node.title,titleFont)+badgeWidth+(badgeWidth?6:0);
    const descriptionNatural=Math.min(330,widthOf(node.description,DESCRIPTION_FONT));
    const desiredWidth=Math.max(230,metricRowWidth,Math.min(330,titleWidth),descriptionNatural);
    const contentWidth=maxWidth===null?desiredWidth:Math.max(110,Math.min(desiredWidth,maxWidth-28));
    const titleLines=wrapText(node.title,Math.max(40,contentWidth-badgeWidth-(badgeWidth?6:0)),titleFont);
    const descriptionLines=wrapText(node.description,contentWidth,DESCRIPTION_FONT);
    const titleLineHeight=titleFontSize+2,titleY=10+titleFontSize;
    const metricHeight=23,metricGap=5;
    const metricRows=[];let row=0,x=14;
    metricWidths.forEach((width,index)=>{
      if(x>14&&x+width>14+contentWidth){row++;x=14;}
      metricRows.push({index,row,x,width});x+=width+metricGap;
    });
    const rowCount=row+1,metricY=10+titleLines.length*titleLineHeight+5;
    const descriptionTop=metricY+rowCount*metricHeight+(rowCount-1)*4+6,descriptionLineHeight=17;
    const coverageY=descriptionTop+descriptionLines.length*descriptionLineHeight+14;
    const height=hasKids?coverageY:coverageY+15;
    const metricPositions=metricRows.map(item=>({...item,y:metricY+item.row*(metricHeight+4)}));
    return {width:contentWidth+28,height,titleLines,titleY,titleLineHeight,titleFontSize,
      descriptionLines,descriptionTop,descriptionLineHeight,coverageY,metricY,metricHeight,
      metricTexts,metricWidths,metricGap,badgeWidth,metricPositions};
  }
  function expandedIds(){
    const result=[],seen=new Set();
    function visit(id){if(seen.has(id))throw new Error(`树重复包含节点：${id}`);seen.add(id);result.push(id);visualChildren(id).forEach(visit);}
    DATA.top_roots.forEach(visit);
    return result;
  }
  function expandedGraph(ids){
    const visible=new Set(ids),nodes=ids.map(id=>{
      const depth=visualDepth(id),kids=visualChildren(id),size=nodeSize(id,kids.length>0,depth);
      return {id,width:size.width,height:size.height,_depth:depth};
    });
    const edges=[];
    for(const id of ids){const parent=visualParent(id);if(parent!==null&&visible.has(parent))
      edges.push({id:`tree-${parent}-${id}`,sources:[parent],targets:[id]});}
    return {id:"root",layoutOptions:TREE_LAYOUT,children:nodes,edges};
  }
  function mobileExpandedLayout(ids){
    const nodes=[],right=svg.clientWidth-160;let y=12;
    for(const id of ids){
      const depth=visualDepth(id),x=16+depth*12;
      const size=nodeSize(id,visualChildren(id).length>0,depth,right-x);
      nodes.push({id,x,y,width:size.width,height:size.height,_depth:depth});y+=size.height+14;
    }
    return {id:"mobile-tree",children:nodes,width:svg.clientWidth,height:y};
  }
  function drillGraph(current){
    const projection=DATA.drill_views[current],mobile=svg.clientWidth<600;
    const layoutOptions=mobile?{...DRILL_LAYOUT,"elk.direction":"DOWN",
      "elk.spacing.nodeNode":"30","elk.layered.spacing.nodeNodeBetweenLayers":"30"}:DRILL_LAYOUT;
    const nodes=projection.nodes.map(id=>{
      const size=nodeSize(id,false,nodeDepth(id));
      return {id,width:size.width,height:size.height,_depth:nodeDepth(id)};
    });
    const edges=projection.relations.map((edge,index)=>({
      id:`drill-${index}-${edge.source}-${edge.target}`,sources:[edge.source],targets:[edge.target],
      labels:edge.title?[{text:edge.title,width:Math.max(30,widthOf(edge.title,EDGE_FONT)+16),height:22}]:[]
    }));
    if(mobile)for(let index=1;index<nodes.length;index++)edges.push({
      id:`pack-${index}`,sources:[nodes[index-1].id],targets:[nodes[index].id]});
    return {id:"root",layoutOptions,children:nodes,edges};
  }
  function svgElement(tag,attributes){
    const element=document.createElementNS(SVG_NS,tag);
    for(const [key,value] of Object.entries(attributes))element.setAttribute(key,String(value));
    return element;
  }
  function textLines(parent,x,y,lines,className,lineHeight){
    const text=svgElement("text",{x,y,class:className});
    lines.forEach((line,index)=>{const span=svgElement("tspan",{x,dy:index?lineHeight:0});span.textContent=line;text.appendChild(span);});
    parent.appendChild(text);
  }
  function drawComposition(layer,ids,positions){
    const groups=new Map();
    for(const id of ids){const parent=visualParent(id);if(parent!==null){const list=groups.get(parent)||[];list.push(id);groups.set(parent,list);}}
    for(const [parent,childIds] of groups){
      const source=positions.get(parent),targets=childIds.map(id=>({id,box:positions.get(id)}))
        .sort((a,b)=>a.box.y+a.box.height/2-b.box.y-b.box.height/2);
      const first=targets[0].box,last=targets[targets.length-1].box;
      const trunkX=Math.max(source.x+source.width+12,(source.x+source.width+first.x)/2);
      const startY=source.y+source.height/2,firstY=first.y+first.height/2,lastY=last.y+last.height/2;
      const spine=`M${source.x+source.width} ${startY} H${trunkX}`+
        (Math.abs(lastY-firstY)>1?` M${trunkX} ${firstY} V${lastY}`:"");
      layer.appendChild(svgElement("path",{class:"composition-spine",d:spine,"data-source":parent}));
      for(const {id,box} of targets){
        const centerY=box.y+box.height/2;
        layer.appendChild(svgElement("path",{class:"composition-edge",d:`M${trunkX} ${centerY} H${box.x}`,
          "data-source":parent,"data-target":id}));
      }
    }
  }
  function drawMobileComposition(layer,ids,positions){
    const groups=new Map();
    for(const id of ids){const parent=visualParent(id);if(parent!==null){const list=groups.get(parent)||[];list.push(id);groups.set(parent,list);}}
    let left=Infinity,right=-Infinity,top=Infinity,bottom=-Infinity;
    for(const [parent,childIds] of groups){
      const source=positions.get(parent),targets=childIds.map(id=>({id,box:positions.get(id)})).sort((a,b)=>a.box.y-b.box.y);
      const trunkX=source.x-8,sourceY=source.y+source.height/2;
      const firstY=targets[0].box.y+targets[0].box.height/2,last=targets[targets.length-1].box;
      const lastY=last.y+last.height/2;
      layer.appendChild(svgElement("path",{class:"composition-spine",
        d:`M${source.x} ${sourceY} H${trunkX} M${trunkX} ${firstY} V${lastY}`,"data-source":parent}));
      left=Math.min(left,trunkX);right=Math.max(right,source.x);top=Math.min(top,sourceY,firstY);bottom=Math.max(bottom,sourceY,lastY);
      for(const {id,box} of targets){
        const y=box.y+box.height/2;
        layer.appendChild(svgElement("path",{class:"composition-edge",d:`M${trunkX} ${y} H${box.x}`,
          "data-source":parent,"data-target":id}));
        right=Math.max(right,box.x);top=Math.min(top,y);bottom=Math.max(bottom,y);
      }
    }
    return {x:Number.isFinite(left)?left:0,y:Number.isFinite(top)?top:0,
      width:Number.isFinite(left)?right-left:1,height:Number.isFinite(top)?bottom-top:1};
  }
  function drawElkEdges(layer,edges){
    for(const edge of edges||[]){
      if(edge.id&&edge.id.startsWith("pack-"))continue;
      for(const section of edge.sections||[]){
        const points=[section.startPoint,...(section.bendPoints||[]),section.endPoint];
        if(points.length<2)continue;
        layer.appendChild(svgElement("path",{class:"edge",
          d:`M${points.map(point=>`${point.x} ${point.y}`).join(" L")}`,"marker-end":"url(#arrow)"}));
      }
      const label=edge.labels&&edge.labels[0];
      if(label&&label.x!==undefined&&label.y!==undefined){
        const width=label.width||30,height=label.height||20;
        layer.appendChild(svgElement("rect",{class:"edge-label-bg",x:label.x-4,y:label.y-2,
          width:width+8,height:height+4,rx:5}));
        const text=svgElement("text",{class:"edge-label",x:label.x+width/2,y:label.y+height/2});
        text.textContent=label.text||"";layer.appendChild(text);
      }
    }
  }
  function labelClear(x,y,width,height,positions,skip,labels){
    const left=x-width/2,right=x+width/2,top=y-height/2,bottom=y+height/2;
    for(const [id,box] of positions){
      if(skip.has(id))continue;
      if(left<box.x+box.width&&right>box.x&&top<box.y+box.height&&bottom>box.y)return false;
    }
    return !labels.some(label=>left<label.right&&right>label.left&&top<label.bottom&&bottom>label.top);
  }
  function drawMobileDrillEdges(layer,edges,positions){
    const bounds={left:Infinity,right:-Infinity,top:Infinity,bottom:-Infinity};
    for(const box of positions.values()){bounds.left=Math.min(bounds.left,box.x);bounds.right=Math.max(bounds.right,box.x+box.width);bounds.top=Math.min(bounds.top,box.y);bounds.bottom=Math.max(bounds.bottom,box.y+box.height);}
    const ordered=[...positions.values()].sort((a,b)=>a.y-b.y),placed=[];let index=0;
    for(const edge of edges||[]){if(edge.id&&edge.id.startsWith("pack-"))continue;
      const source=positions.get(edge.sources[0]),target=positions.get(edge.targets[0]);if(!source||!target)continue;
      const sourceY=source.y+source.height/2,targetY=target.y+target.height/2;
      const lane=Math.max(...[...positions.values()].map(box=>box.x+box.width))+24+index*12;
      const points=[{x:source.x+source.width,y:sourceY},{x:lane,y:sourceY},{x:lane,y:targetY},{x:target.x+target.width,y:targetY}];
      layer.appendChild(svgElement("path",{class:"edge",d:`M${points.map(point=>`${point.x} ${point.y}`).join(" L")}`,"marker-end":"url(#arrow)","data-source":edge.sources[0],"data-target":edge.targets[0]}));
      const label=edge.labels&&edge.labels[0];
      if(label){const width=label.width||30,height=label.height||22,midpoint=(sourceY+targetY)/2;
        const candidates=ordered.slice(1).map((box,position)=>{
          const previous=ordered[position],gap=box.y-(previous.y+previous.height);
          return gap>=height+4?previous.y+previous.height+gap/2:null;
        }).filter(value=>value!==null).sort((a,b)=>Math.abs(a-midpoint)-Math.abs(b-midpoint));
        let labelX=lane,labelY=candidates.find(y=>labelClear(lane,y,width+8,height+4,positions,new Set(),placed));
        if(labelY===undefined){labelX=lane+width/2+10;labelY=midpoint;}
        layer.appendChild(svgElement("rect",{class:"edge-label-bg",x:labelX-width/2-4,y:labelY-height/2-2,width:width+8,height:height+4,rx:5}));
        const text=svgElement("text",{class:"edge-label",x:labelX,y:labelY});text.textContent=label.text||"";layer.appendChild(text);
        const rect={left:labelX-width/2-4,right:labelX+width/2+4,top:labelY-height/2-2,bottom:labelY+height/2+2};placed.push(rect);
        bounds.left=Math.min(bounds.left,rect.left);bounds.right=Math.max(bounds.right,rect.right);bounds.top=Math.min(bounds.top,rect.top);bounds.bottom=Math.max(bounds.bottom,rect.bottom);
      }
      for(const point of points){bounds.left=Math.min(bounds.left,point.x);bounds.right=Math.max(bounds.right,point.x);bounds.top=Math.min(bounds.top,point.y);bounds.bottom=Math.max(bounds.bottom,point.y);}index++;
    }
    return {x:bounds.left,y:bounds.top,width:bounds.right-bounds.left,height:bounds.bottom-bounds.top};
  }
  function drawNode(layer,id,position,depth,hasKids){
    const node=DATA.nodes[id],size=nodeSize(id,hasKids,depth,position.width),canEnter=DATA.drill_views[id].nodes.length!==1||DATA.drill_views[id].nodes[0]!==id;
    const group=svgElement("g",{class:`card${canEnter?" clickable":""}`,
      transform:`translate(${position.x} ${position.y})`,"data-node-id":id});
    group.appendChild(svgElement("rect",{class:"card-shape",x:0,y:0,width:position.width,height:position.height,
      rx:10,fill:"#fff",stroke:PALETTE[depth%PALETTE.length],"stroke-dasharray":canEnter?"7 4":"none"}));
    const tooltip=svgElement("title",{});tooltip.textContent=node.description||node.title;group.appendChild(tooltip);
    textLines(group,14,size.titleY,size.titleLines,`card-title title-depth-${Math.min(2,depth)}`,size.titleLineHeight);
    if(node.kind==="repo"){
      const titleFont=`${depth<2?700:600} ${size.titleFontSize}px ${FONT_FAMILY}`;
      const badgeX=14+widthOf(size.titleLines[0]||"",titleFont)+6,badgeY=size.titleY-size.titleFontSize+1;
      group.appendChild(svgElement("rect",{class:"repo-badge",x:badgeX,y:badgeY,width:size.badgeWidth,height:16,rx:6}));
      const badge=svgElement("text",{class:"repo-badge-text",x:badgeX+6,y:badgeY+12});badge.textContent="子仓";group.appendChild(badge);
    }
    size.metricTexts.forEach((value,index)=>{
      const slot=size.metricPositions[index];
      group.appendChild(svgElement("rect",{class:"metric-box",x:slot.x,y:slot.y,width:slot.width,height:size.metricHeight,rx:6}));
      const metric=svgElement("text",{class:"metric-text",x:slot.x+7,y:slot.y+16});metric.textContent=value;group.appendChild(metric);
    });
    textLines(group,14,size.descriptionTop+12,size.descriptionLines,"description",size.descriptionLineHeight);
    if(!hasKids){const coverage=svgElement("text",{class:"coverage",x:14,y:size.coverageY});
      coverage.textContent=`文档覆盖率：${node.coverage}`;group.appendChild(coverage);}
    layer.appendChild(group);
  }
  function drawBreadcrumbs(current){
    const bar=document.getElementById("crumbs");bar.replaceChildren();
    if(view.mode!=="drill")return;
    DATA.drill_views[current].breadcrumbs.forEach((id,index)=>{
      if(index){const separator=document.createElement("span");separator.className="separator";separator.textContent="›";bar.appendChild(separator);}
      const button=document.createElement("button");button.type="button";button.textContent=DATA.nodes[id].title;
      if(index===DATA.drill_views[current].breadcrumbs.length-1)button.setAttribute("aria-current","page");
      button.addEventListener("click",()=>setRoute("drill",id));bar.appendChild(button);
    });
  }
  function applyTransform(){const scene=document.getElementById("scene");if(scene)scene.setAttribute("transform",`translate(${transform.x} ${transform.y}) scale(${transform.scale})`);}
  function fit(){
    const width=svg.clientWidth,height=svg.clientHeight;if(!width||!height||!currentBounds.width||!currentBounds.height)return;
    const padding=44,scale=Math.max(.08,Math.min(1,(width-padding*2)/currentBounds.width,(height-padding*2)/currentBounds.height));
    transform.scale=scale;transform.x=(width-currentBounds.width*scale)/2-currentBounds.x*scale;
    transform.y=(height-currentBounds.height*scale)/2-currentBounds.y*scale;applyTransform();
  }
  function fitWidth(){
    const width=svg.clientWidth;if(!width||!currentBounds.width)return;
    const scale=Math.max(.08,Math.min(3,(width-24)/currentBounds.width));
    transform.scale=scale;transform.x=(width-currentBounds.width*scale)/2-currentBounds.x*scale;
    transform.y=18-currentBounds.y*scale;applyTransform();
  }
  function routeFromHash(){
    let fragment;try{fragment=decodeURIComponent(location.hash.slice(1));}catch(_error){return {mode:"expand",current:null,valid:false};}
    if(fragment==="展开")return {mode:"expand",current:null,valid:true};
    if(fragment.startsWith("钻入/")){const current=fragment.slice(3);
      if(Object.prototype.hasOwnProperty.call(DATA.nodes,current))return {mode:"drill",current,valid:true};}
    return {mode:"expand",current:null,valid:false};
  }
  function setRoute(mode,current){
    const route=routeFromHash();if(route.valid&&route.mode===mode&&route.current===current){view={mode,current};draw();return;}
    location.hash=mode==="expand"?"#展开":`#钻入/${encodeURIComponent(current)}`;
  }
  function unionBounds(first,second){
    const left=Math.min(first.x,second.x),top=Math.min(first.y,second.y);
    const right=Math.max(first.x+first.width,second.x+second.width);
    const bottom=Math.max(first.y+first.height,second.y+second.height);
    return {x:left,y:top,width:right-left,height:bottom-top};
  }
  function draw(){
    const drawId=++drawNumber,modeButton=document.getElementById("mode"),relationButton=document.getElementById("relations");
    modeButton.textContent=svg.clientWidth<=480?(view.mode==="expand"?"钻入":"全部"):(view.mode==="expand"?"切换到钻入":"全部展开");
    relationButton.hidden=view.mode!=="expand";drawBreadcrumbs(view.current);
    const ids=view.mode==="expand"?expandedIds():DATA.drill_views[view.current].nodes;
    const mobileExpanded=view.mode==="expand"&&svg.clientWidth<600;
    const mobileDrill=view.mode==="drill"&&svg.clientWidth<600;
    const graph=mobileExpanded?null:view.mode==="expand"?expandedGraph(ids):drillGraph(view.current);
    const layout=mobileExpanded?Promise.resolve(mobileExpandedLayout(ids)):new ELK().layout(graph);
    layout.then(result=>{
      if(drawId!==drawNumber)return;
      svg.replaceChildren();const defs=svgElement("defs",{});
      for(const [id,fill] of [["arrow","#7b8da8"],["relation-arrow","#b64e83"]]){
        const marker=svgElement("marker",{id,viewBox:"0 0 10 10",refX:9,refY:5,markerWidth:7,markerHeight:7,orient:"auto"});
        marker.appendChild(svgElement("path",{d:"M0 0 L10 5 L0 10 z",fill}));defs.appendChild(marker);
      }
      svg.appendChild(defs);const scene=svgElement("g",{id:"scene"});svg.appendChild(scene);
      const layers={tree:svgElement("g",{id:"tree-layer"}),edges:svgElement("g",{id:"drill-edge-layer"}),
        relations:svgElement("g",{id:"relation-layer"}),cards:svgElement("g",{id:"card-layer"})};
      scene.append(layers.tree,layers.edges,layers.relations,layers.cards);
      if(mobileDrill)for(const node of result.children)node.x=12;
      const positions=new Map(),layoutNodes=new Map();
      for(const node of result.children||[]){positions.set(node.id,{x:node.x||0,y:node.y||0,width:node.width||0,height:node.height||0});layoutNodes.set(node.id,node);}
      if(view.mode==="expand"){
        const treeBounds=mobileExpanded?drawMobileComposition(layers.tree,ids,positions):
          (drawComposition(layers.tree,ids,positions),{x:0,y:0,width:result.width||1,height:result.height||1});
        const relationBounds=window.XYRepoMapRelations.draw({layer:layers.relations,positions,ids,
          data:DATA,repoAlias:REPO_ALIAS,svgElement,widthOf,wrapText,visualDepth,
          mobile:mobileExpanded,viewportWidth:svg.clientWidth});
        currentBounds=mobileExpanded?unionBounds(treeBounds,relationBounds):relationBounds;
      }else{
        currentBounds=mobileDrill?drawMobileDrillEdges(layers.edges,result.edges,positions):
          (drawElkEdges(layers.edges,result.edges),{x:0,y:0,width:result.width||1,height:result.height||1});
      }
      for(const id of ids){const node=layoutNodes.get(id),depth=view.mode==="expand"?visualDepth(id):nodeDepth(id);
        const hasKids=view.mode==="expand"&&visualChildren(id).length>0;
        drawNode(layers.cards,id,positions.get(id),depth,hasKids);}
      svg.setAttribute("data-mode",view.mode);svg.setAttribute("data-current",view.current||"");
      transform={x:0,y:0,scale:1};
      const relationLayer=document.getElementById("relation-layer");relationLayer.style.display=document.getElementById("relations").getAttribute("aria-pressed")==="true"?"":"none";
      applyHighlight();if(mobileExpanded)applyTransform();else requestAnimationFrame(()=>mobileDrill?fitWidth():fit());
    });
  }
  function zoomAt(x,y,factor){
    const next=Math.max(.08,Math.min(3,transform.scale*factor));
    transform.x=x-(x-transform.x)*next/transform.scale;transform.y=y-(y-transform.y)*next/transform.scale;
    transform.scale=next;applyTransform();
  }
  function pointerPosition(event){const rect=svg.getBoundingClientRect();return {x:event.clientX-rect.left,y:event.clientY-rect.top};}
  function distance(a,b){return Math.hypot(a.x-b.x,a.y-b.y);}
  function cancelPress(){if(pressTimer!==null){clearTimeout(pressTimer);pressTimer=null;}}
  function applyHighlight(){
    for(const element of document.querySelectorAll(".relation-edge,.relation-label,.relation-label-bg")){
      const related=highlightedNode!==null&&(element.dataset.source===highlightedNode||element.dataset.target===highlightedNode);
      element.classList.toggle("highlighted",related);element.classList.toggle("dimmed",highlightedNode!==null&&!related);
    }
  }
  function setHighlight(id){highlightedNode=id;applyHighlight();}
  function pointerDown(event){
    if(event.pointerType==="mouse"&&event.button!==0)return;
    suppressedGesture=null;
    const point=pointerPosition(event),card=event.target.closest(".card"),nodeId=card?card.dataset.nodeId:null;
    pointers.set(event.pointerId,{...point,type:event.pointerType});
    svg.setPointerCapture(event.pointerId);
    if(pointers.size>=2&&[...pointers.values()].every(item=>item.type==="touch")){
      cancelPress();drag=null;const pair=[...pointers.entries()].slice(0,2),a=pair[0][1],b=pair[1][1];
      pinch={ids:pair.map(([id])=>id),center:{x:(a.x+b.x)/2,y:(a.y+b.y)/2},distance:Math.max(1,distance(a,b)),
        x:transform.x,y:transform.y,scale:transform.scale};return;
    }
    drag={id:event.pointerId,nodeId,clickable:Boolean(card&&card.classList.contains("clickable")),
      pointerType:event.pointerType,start:point,last:point,moved:false,longPress:false};
    if(event.pointerType==="touch"&&nodeId){
      pressTimer=setTimeout(()=>{if(drag&&drag.id===event.pointerId&&!drag.moved){drag.longPress=true;setHighlight(nodeId);}},500);
    }
  }
  function pointerMove(event){
    const point=pointerPosition(event),active=pointers.get(event.pointerId);if(active)pointers.set(event.pointerId,point.type?{...point,type:active.type}:{...point,type:event.pointerType});
    if(pinch){
      const a=pointers.get(pinch.ids[0]),b=pointers.get(pinch.ids[1]);if(!a||!b)return;
      const center={x:(a.x+b.x)/2,y:(a.y+b.y)/2},scale=Math.max(.08,Math.min(3,pinch.scale*distance(a,b)/pinch.distance));
      transform.scale=scale;transform.x=center.x-(pinch.center.x-pinch.x)*scale/pinch.scale;
      transform.y=center.y-(pinch.center.y-pinch.y)*scale/pinch.scale;applyTransform();return;
    }
    if(!drag||drag.id!==event.pointerId)return;
    if(!drag.moved&&distance(drag.start,point)>5){drag.moved=true;cancelPress();if(drag.longPress)setHighlight(null);svg.classList.add("dragging");}
    if(drag.moved){transform.x+=point.x-drag.last.x;transform.y+=point.y-drag.last.y;drag.last=point;applyTransform();}
  }
  function pointerUp(event){
    pointers.delete(event.pointerId);
    if(pinch){pinch=null;svg.classList.remove("dragging");drag=null;cancelPress();return;}
    if(drag&&drag.id===event.pointerId){
      const completed=drag;cancelPress();
      if(completed.moved||completed.longPress){suppressedGesture={point:completed.last,time:performance.now()};}
      else if(completed.clickable){suppressedGesture={point:completed.last,time:performance.now()};setRoute("drill",completed.nodeId);}
      drag=null;svg.classList.remove("dragging");
    }
  }
  function suppressDraggedClick(event){
    if(!suppressedGesture)return;
    const samePoint=distance(pointerPosition(event),suppressedGesture.point)<24;
    if(performance.now()-suppressedGesture.time<750&&samePoint){
      suppressedGesture=null;event.preventDefault();event.stopImmediatePropagation();return;
    }
    suppressedGesture=null;
  }
  svg.addEventListener("wheel",event=>{event.preventDefault();const point=pointerPosition(event);zoomAt(point.x,point.y,event.deltaY<0?1.12:1/1.12);},{passive:false});
  svg.addEventListener("pointerdown",pointerDown);svg.addEventListener("pointermove",pointerMove);
  svg.addEventListener("pointerup",pointerUp);svg.addEventListener("pointercancel",pointerUp);
  svg.addEventListener("click",suppressDraggedClick,true);
  svg.addEventListener("pointerover",event=>{if(event.pointerType!=="mouse")return;const card=event.target.closest(".card");if(card)setHighlight(card.dataset.nodeId);});
  svg.addEventListener("pointerout",event=>{
    if(event.pointerType!=="mouse")return;const card=event.target.closest(".card");
    if(card&&(!event.relatedTarget||!event.relatedTarget.closest||!event.relatedTarget.closest(`[data-node-id="${card.dataset.nodeId}"]`)))setHighlight(null);
  });
  document.getElementById("mode").addEventListener("click",()=>view.mode==="expand"?setRoute("drill",DATA.top_roots[0]):setRoute("expand",null));
  document.getElementById("fit").addEventListener("click",fit);
  document.getElementById("relations").addEventListener("click",event=>{
    const visible=event.currentTarget.getAttribute("aria-pressed")==="true";
    event.currentTarget.setAttribute("aria-pressed",String(!visible));
    event.currentTarget.textContent=visible?"显示关系":"隐藏关系";
    document.getElementById("relation-layer").style.display=visible?"none":"";
  });
  document.getElementById("zoom-in").addEventListener("click",()=>zoomAt(svg.clientWidth/2,svg.clientHeight/2,1.2));
  document.getElementById("zoom-out").addEventListener("click",()=>zoomAt(svg.clientWidth/2,svg.clientHeight/2,1/1.2));
  window.addEventListener("resize",()=>{
    if(view.mode==="expand"&&svg.clientWidth<600)draw();
    else svg.clientWidth<600?fitWidth():fit();
  });
  window.addEventListener("hashchange",()=>{
    const next=routeFromHash();if(!next.valid){view={mode:"expand",current:null};if(fontsReady)draw();location.hash="#展开";return;}
    if(view.mode===next.mode&&view.current===next.current)return;view=next;if(fontsReady)draw();
  });
  await document.fonts.ready;fontsReady=true;view=routeFromHash();
  if(!view.valid){view={mode:"expand",current:null};location.hash="#展开";}
  draw();
})();
