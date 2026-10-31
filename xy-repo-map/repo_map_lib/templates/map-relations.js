(function(){
  "use strict";
  const EDGE_FONT='13px system-ui,"Noto Sans CJK SC","Microsoft YaHei",sans-serif';

  function curvePoint(curve,t){
    const u=1-t,a=u*u*u,b=3*u*u*t,c=3*u*t*t,d=t*t*t;
    return {x:a*curve.sx+b*curve.cx1+c*curve.cx2+d*curve.tx,
      y:a*curve.sy+b*curve.cy1+c*curve.cy2+d*curve.ty};
  }

  function curvePeak(curve){
    const a=3*(-curve.sx+3*curve.cx1-3*curve.cx2+curve.tx);
    const b=6*(curve.sx-2*curve.cx1+curve.cx2),c=3*(curve.cx1-curve.sx);
    const discriminant=b*b-4*a*c,tValues=[0,1];
    if(Math.abs(a)<.0001){if(Math.abs(b)>.0001)tValues.push(-c/b);}
    else if(discriminant>=0){const root=Math.sqrt(discriminant);tValues.push((-b-root)/(2*a),(-b+root)/(2*a));}
    let peak=curvePoint(curve,0),peakT=0;
    for(const t of tValues){if(t<0||t>1)continue;const point=curvePoint(curve,t);
      if(point.x>peak.x){peak=point;peakT=t;}}
    return {...peak,t:peakT};
  }

  function controlForPeak(curve,desired){
    let low=Math.max(curve.sx,curve.tx,desired),high=low+Math.max(100,(desired-Math.min(curve.sx,curve.tx))*8);
    for(let step=0;step<28;step++){
      const middle=(low+high)/2;curve.cx2=middle;
      if(curvePeak(curve).x<desired)low=middle;else high=middle;
    }
    curve.cx2=high;return high;
  }

  function relationSamples(curve,count=100){
    const points=[];for(let step=0;step<=count;step++)points.push(curvePoint(curve,step/count));return points;
  }

  function obstacleIndex(positions){
    const cellSize=96,cells=new Map();
    for(const [id,box] of positions){
      const firstX=Math.floor((box.x-1)/cellSize),lastX=Math.floor((box.x+box.width+1)/cellSize);
      const firstY=Math.floor((box.y-1)/cellSize),lastY=Math.floor((box.y+box.height+1)/cellSize);
      for(let x=firstX;x<=lastX;x++)for(let y=firstY;y<=lastY;y++){
        const key=`${x}:${y}`,items=cells.get(key)||[];items.push({id,box});cells.set(key,items);
      }
    }
    return {cellSize,cells};
  }

  function routeCollisions(samples,grid,skip){
    const {cellSize,cells}=grid;
    for(let step=1;step<samples.length-1;step++){
      const point=samples[step];
      const items=cells.get(`${Math.floor(point.x/cellSize)}:${Math.floor(point.y/cellSize)}`)||[];
      for(const {id,box} of items){
        if(skip.has(id))continue;
        if(point.x>=box.x-1&&point.x<=box.x+box.width+1&&
           point.y>=box.y-1&&point.y<=box.y+box.height+1)return 1;
      }
    }
    return 0;
  }

  function bundleHits(samples,routes){
    let hits=0;
    for(let index=8;index<samples.length-8;index+=2){
      const point=samples[index];
      const x=Math.floor(point.x/12),y=Math.floor(point.y/12);
      for(let dx=-1;dx<=1;dx++)for(let dy=-1;dy<=1;dy++){
        const nearby=routes.get(`${x+dx}:${y+dy}`)||[];
        if(nearby.some(other=>Math.hypot(point.x-other.x,point.y-other.y)<5)){hits++;dx=2;break;}
      }
    }
    return hits;
  }

  function addRouteToIndex(samples,routes){
    for(let index=0;index<samples.length;index++){
      const point=samples[index],key=`${Math.floor(point.x/12)}:${Math.floor(point.y/12)}`;
      const points=routes.get(key)||[];points.push(point);routes.set(key,points);
    }
  }

  function labelFree(box,positions,labels){
    for(const node of positions.values()){
      if(box.left<node.x+node.width&&box.right>node.x&&box.top<node.y+node.height&&box.bottom>node.y)return false;
    }
    const gap=10;
    return !labels.some(label=>box.left<label.right+gap&&box.right>label.left-gap&&
      box.top<label.bottom+gap&&box.bottom>label.top-gap);
  }

  function drawLabel(layer,svgElement,lines,x,y,width,height,relation){
    const box={left:x-width/2,right:x+width/2,top:y-height/2,bottom:y+height/2};
    layer.appendChild(svgElement("rect",{class:"relation-label-bg",x:box.left,y:box.top,
      width,height,rx:5,"data-source":relation.sourceId,"data-target":relation.targetId}));
    const text=svgElement("text",{class:"relation-label",x,y:y-(lines.length-1)*7,
      "data-source":relation.sourceId,"data-target":relation.targetId,"data-title":relation.title});
    lines.forEach((line,index)=>{
      const span=svgElement("tspan",{x,dy:index?14:0});span.textContent=line;text.appendChild(span);
    });
    layer.appendChild(text);return box;
  }

  function draw(options){
    const {layer,positions,ids,data,repoAlias,svgElement,widthOf,visualDepth}=options;
    const visible=new Set(ids),bounds={left:Infinity,right:-Infinity,top:Infinity,bottom:-Infinity};
    for(const box of positions.values()){
      bounds.left=Math.min(bounds.left,box.x);bounds.right=Math.max(bounds.right,box.x+box.width);
      bounds.top=Math.min(bounds.top,box.y);bounds.bottom=Math.max(bounds.bottom,box.y+box.height);
    }
    const treeRight=bounds.right;
    const normalized=data.relations.map((relation,index)=>({...relation,index,
      sourceId:repoAlias[relation.source]||relation.source,targetId:repoAlias[relation.target]||relation.target}))
      .filter(relation=>relation.sourceId!==relation.targetId&&visible.has(relation.sourceId)&&visible.has(relation.targetId));
    const obstacles=obstacleIndex(positions);
    const pairIndexes=new Map(),portCounts=new Map(),portIndexes=new Map();
    for(const relation of normalized){
      const pair=`${relation.sourceId}\u0000${relation.targetId}`;
      pairIndexes.set(pair,0);for(const id of [relation.sourceId,relation.targetId])portCounts.set(id,(portCounts.get(id)||0)+1);
    }
    function portY(id,box){
      const count=portCounts.get(id),index=portIndexes.get(id)||0;portIndexes.set(id,index+1);
      const fraction=count===1?.35:.2+.6*(index+1)/(count+1);
      return {y:box.y+box.height*fraction,index,count};
    }
    const prepared=normalized.map(relation=>{
      const source=positions.get(relation.sourceId),target=positions.get(relation.targetId);
      const sx=source.x+source.width,tx=target.x+target.width;
      const sourcePort=portY(relation.sourceId,source),targetPort=portY(relation.targetId,target);
      const sy=sourcePort.y,ty=targetPort.y;
      const span=Math.abs(ty-sy)+Math.abs(visualDepth(relation.sourceId)-visualDepth(relation.targetId))*48;
      return {...relation,source,target,sx,tx,sy,ty,span};
    }).sort((first,second)=>first.span-second.span||first.index-second.index);
    const maxCardHeight=Math.max(...[...positions.values()].map(box=>box.height));
    const maxOffset=Math.max(512,Math.ceil((bounds.bottom-bounds.top+maxCardHeight*2+128)/32)*32);
    const labels=[],routes=new Map();
    for(let rank=0;rank<prepared.length;rank++){
      const relation=prepared[rank];
      const pair=`${relation.sourceId}\u0000${relation.targetId}`;
      const pairIndex=pairIndexes.get(pair)||0;pairIndexes.set(pair,pairIndex+1);
      const {source,target,sx,tx,sy,ty,span}=relation;
      const title=relation.title||"关系",lines=[title];
      const textWidth=Math.max(...lines.map(line=>widthOf(line,EDGE_FONT)));
      const labelWidth=Math.max(34,textWidth+18),labelHeight=Math.max(24,lines.length*14+10);
      const labelBoxWidth=labelWidth+8,labelBoxHeight=labelHeight+4;
      const minimumPeak=treeRight+labelBoxWidth/2+10;
      const desiredPeak=minimumPeak+Math.min(span*.2,500)+pairIndex*(labelBoxWidth+14)+rank*8;
      const routeOffsets=[0],offsetSet=new Set(routeOffsets);
      const addOffset=value=>{
        const offset=Math.max(-maxOffset,Math.min(maxOffset,Math.round(value/32)*32));
        if(!offsetSet.has(offset)){routeOffsets.push(offset);offsetSet.add(offset);}
      };
      for(let offset=32;offset<=Math.min(maxOffset,512);offset+=32){addOffset(offset);addOffset(-offset);}
      for(const fraction of [.65,.8,1]){addOffset(maxOffset*fraction);addOffset(-maxOffset*fraction);}
      const controlVariations=[0,16,32],skip=new Set([relation.sourceId,relation.targetId]);
      let best=null,bestScore=Infinity,safeCandidates=0;
      for(const offset of routeOffsets){
        const seed={sx,sy,cx1:sx+32,cx2:0,cy1:sy+offset,cy2:ty+offset,tx,ty};
        const baseControl=controlForPeak(seed,desiredPeak);
        for(const variation of controlVariations){
          const curve={...seed,cx2:baseControl+variation},peak=curvePeak(curve);
          if(peak.x<minimumPeak-1)continue;
          const labelBox={left:peak.x-labelBoxWidth/2,right:peak.x+labelBoxWidth/2,
            top:peak.y-labelBoxHeight/2,bottom:peak.y+labelBoxHeight/2};
          if(!labelFree(labelBox,positions,labels))continue;
          const sampleCount=Math.max(300,Math.ceil((desiredPeak-sx+Math.abs(offset)*2+Math.abs(ty-sy))/4));
          const samples=relationSamples(curve,sampleCount),collisions=routeCollisions(samples,obstacles,skip);
          const bundles=collisions===0?bundleHits(samples,routes):0;
          const score=collisions*10000+bundles*24+
            Math.abs(offset)*.3+Math.abs(peak.x-desiredPeak)*.2+variation*.03;
          if(score<bestScore){bestScore=score;best={curve,peak,labelBox,samples,collisions,bundles};}
          if(collisions===0){safeCandidates++;if(bundles<=5||safeCandidates>=6)break;}
        }
        if(best&&best.collisions===0&&(best.bundles<=5||safeCandidates>=6))break;
      }
      if(!best||best.collisions>0)
        throw new Error(`关系弧线无法避开卡片：${relation.sourceId} → ${relation.targetId}（${title}）`);
      const {curve,peak,labelBox,samples}=best;
      const group=svgElement("g",{class:"relation-item","data-relation-index":relation.index});
      group.appendChild(svgElement("path",{class:"relation-edge",
        d:`M${sx} ${sy} C${curve.cx1} ${curve.cy1} ${curve.cx2} ${curve.cy2} ${tx} ${ty}`,
        "marker-end":"url(#relation-arrow)","data-source":relation.sourceId,
        "data-target":relation.targetId,"data-title":relation.title}));
      const label=drawLabel(group,svgElement,lines,peak.x,peak.y,labelBoxWidth,labelBoxHeight,relation);
      layer.appendChild(group);labels.push(label);addRouteToIndex(samples,routes);
      bounds.left=Math.min(bounds.left,...samples.map(point=>point.x),labelBox.left);
      bounds.right=Math.max(bounds.right,...samples.map(point=>point.x),labelBox.right);
      bounds.top=Math.min(bounds.top,...samples.map(point=>point.y),labelBox.top);
      bounds.bottom=Math.max(bounds.bottom,...samples.map(point=>point.y),labelBox.bottom);
    }
    return {x:bounds.left,y:bounds.top,width:bounds.right-bounds.left,height:bounds.bottom-bounds.top};
  }

  window.XYRepoMapRelations={draw};
})();
