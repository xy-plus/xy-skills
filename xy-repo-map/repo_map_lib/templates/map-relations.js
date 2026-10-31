(function(){
  "use strict";
  const EDGE_FONT='13px system-ui,"Noto Sans CJK SC","Microsoft YaHei",sans-serif';
  const ROUTE_MARGIN=128;

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

  function relationSamples(curve,count){
    const points=[];for(let step=0;step<=count;step++)points.push(curvePoint(curve,step/count));return points;
  }

  function polylineSamples(points,stepsPerSegment){
    const samples=[];
    for(let index=0;index<points.length-1;index++){
      const first=points[index],second=points[index+1];
      for(let step=0;step<stepsPerSegment;step++){
        const fraction=step/stepsPerSegment;
        samples.push({x:first.x+(second.x-first.x)*fraction,
          y:first.y+(second.y-first.y)*fraction});
      }
    }
    samples.push(points[points.length-1]);return samples;
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

  function sameFocus(label,relation){
    return label.sourceId===relation.sourceId||label.sourceId===relation.targetId||
      label.targetId===relation.sourceId||label.targetId===relation.targetId;
  }

  function labelIssue(box,positions,labels,treeSamples,relation){
    for(const [id,node] of positions){
      if(box.left<node.x+node.width&&box.right>node.x&&box.top<node.y+node.height&&box.bottom>node.y)return `card:${id}`;
    }
    if(treePointsInRange(treeSamples,box.left-2,box.right+2).some(point=>
      point.y>=box.top-2&&point.y<=box.bottom+2))return "tree";
    const gap=10;
    const index=labels.findIndex(label=>sameFocus(label,relation)&&
      box.left<label.right+gap&&box.right>label.left-gap&&
      box.top<label.bottom+gap&&box.bottom>label.top-gap);
    return index<0?null:`label:${index}`;
  }

  function freeLabelCenters(x,width,height,positions,labels,treeSamples,bounds,preferredY,relation){
    const left=x-width/2,right=x+width/2,halfHeight=height/2,blocks=[];
    for(const node of positions.values())if(left<node.x+node.width&&right>node.x)
      blocks.push([node.y-halfHeight-2,node.y+node.height+halfHeight+2]);
    const gap=10;
    for(const label of labels)if(sameFocus(label,relation)&&left<label.right+gap&&right>label.left-gap)
      blocks.push([label.top-halfHeight-gap-2,label.bottom+halfHeight+gap+2]);
    for(const point of treePointsInRange(treeSamples,left-2,right+2))
      blocks.push([point.y-halfHeight-4,point.y+halfHeight+4]);
    blocks.sort((first,second)=>first[0]-second[0]);
    const low=bounds.top-ROUTE_MARGIN+halfHeight,high=bounds.bottom+ROUTE_MARGIN-halfHeight;
    const free=[];let cursor=low;
    for(const [start,end] of blocks){
      const blockStart=Math.max(low,start),blockEnd=Math.min(high,end);
      if(blockStart>cursor+2)free.push([cursor+2,blockStart-2]);
      cursor=Math.max(cursor,blockEnd);
      if(cursor>=high)break;
    }
    if(cursor<high-2)free.push([cursor+2,high]);
    return free.map(([start,end])=>Math.max(start,Math.min(end,preferredY)))
      .sort((first,second)=>Math.abs(first-preferredY)-Math.abs(second-preferredY));
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

  function wrapRelationTitle(title,maxWidth,widthOf){
    if(widthOf(title,EDGE_FONT)<=maxWidth)return [title];
    const lines=[];let line="";
    for(const character of Array.from(title)){
      const next=line+character;
      if(line&&widthOf(next,EDGE_FONT)>maxWidth){lines.push(line);line=character;}else line=next;
    }
    if(line)lines.push(line);return lines;
  }

  function treeLineSamples(treeLayer){
    const cellSize=32,buckets=new Map();
    for(const path of treeLayer.querySelectorAll(".composition-spine,.composition-edge")){
      const length=path.getTotalLength(),steps=Math.max(1,Math.ceil(length/2));
      for(let step=0;step<=steps;step++){
        const point=path.getPointAtLength(length*step/steps),key=Math.floor(point.x/cellSize);
        const bucket=buckets.get(key)||[];bucket.push(point);buckets.set(key,bucket);
      }
    }
    return {cellSize,buckets};
  }

  function treePointsInRange(treeSamples,left,right){
    const {cellSize,buckets}=treeSamples,points=[];
    for(let cell=Math.floor(left/cellSize);cell<=Math.floor(right/cellSize);cell++)
      points.push(...(buckets.get(cell)||[]).filter(point=>point.x>=left&&point.x<=right));
    return points;
  }

  function draw(options){
    const {layer,treeLayer,positions,ids,data,repoAlias,svgElement,widthOf,visualDepth}=options;
    const visible=new Set(ids),bounds={left:Infinity,right:-Infinity,top:Infinity,bottom:-Infinity};
    for(const box of positions.values()){
      bounds.left=Math.min(bounds.left,box.x);bounds.right=Math.max(bounds.right,box.x+box.width);
      bounds.top=Math.min(bounds.top,box.y);bounds.bottom=Math.max(bounds.bottom,box.y+box.height);
    }
    const cardBounds={...bounds};
    const columns=new Map();
    for(const [id,box] of positions){
      const depth=visualDepth(id),column=columns.get(depth)||{left:Infinity,right:-Infinity};
      column.left=Math.min(column.left,box.x);column.right=Math.max(column.right,box.x+box.width);
      columns.set(depth,column);
    }
    const columnDepths=[...columns.keys()].sort((first,second)=>first-second);
    const normalized=data.relations.map((relation,index)=>({...relation,index,
      sourceId:repoAlias[relation.source]||relation.source,targetId:repoAlias[relation.target]||relation.target}))
      .filter(relation=>visible.has(relation.sourceId)&&visible.has(relation.targetId));
    const obstacles=obstacleIndex(positions),treeSamples=treeLineSamples(treeLayer);
    const pairIndexes=new Map(),portCounts=new Map(),portIndexes=new Map();
    for(const relation of normalized){
      const pair=`${relation.sourceId}\u0000${relation.targetId}`;
      pairIndexes.set(pair,0);
      for(const id of new Set([relation.sourceId,relation.targetId]))
        portCounts.set(id,(portCounts.get(id)||0)+1);
    }
    function portY(id,box){
      const count=portCounts.get(id),index=portIndexes.get(id)||0;portIndexes.set(id,index+1);
      const fraction=count===1?.35:.2+.6*(index+1)/(count+1);
      return box.y+box.height*fraction;
    }
    const prepared=normalized.map(relation=>{
      const source=positions.get(relation.sourceId),target=positions.get(relation.targetId);
      const sourceDepth=visualDepth(relation.sourceId),targetDepth=visualDepth(relation.targetId);
      const sx=source.x+source.width,tx=target.x+target.width;
      const sy=portY(relation.sourceId,source);
      const ty=relation.sourceId===relation.targetId?sy:portY(relation.targetId,target);
      const depth=Math.max(sourceDepth,targetDepth);
      const span=Math.abs(ty-sy)+Math.abs(sourceDepth-targetDepth)*48;
      return {...relation,source,target,sx,tx,sy,ty,span,sourceDepth,targetDepth,depth};
    }).sort((first,second)=>first.span-second.span||first.index-second.index);
    const labels=[],routes=new Map();
    for(let rank=0;rank<prepared.length;rank++){
      const relation=prepared[rank];
      const pair=`${relation.sourceId}\u0000${relation.targetId}`;
      const pairIndex=pairIndexes.get(pair)||0;pairIndexes.set(pair,pairIndex+1);
      const {source,target,sx,tx,sy,ty,span,sourceDepth,targetDepth,depth}=relation;
      const column=columns.get(depth),nextDepth=columnDepths.find(value=>value>depth);
      const nextColumn=nextDepth===undefined?null:columns.get(nextDepth);
      const gap=nextColumn?nextColumn.left-column.right:Infinity;
      const title=relation.title,labelMaxWidth=sourceDepth===targetDepth?
        (nextColumn?gap-36:ROUTE_MARGIN-32):Infinity;
      const lines=labelMaxWidth>=18?wrapRelationTitle(title,labelMaxWidth,widthOf):[title];
      const textWidth=Math.max(...lines.map(line=>widthOf(line,EDGE_FONT)));
      const labelWidth=Math.max(34,textWidth+18),labelHeight=Math.max(24,lines.length*14+10);
      const labelBoxWidth=labelWidth+8,labelBoxHeight=labelHeight+4;
      const endpointRight=Math.max(sx,tx),labelHalfWidth=labelBoxWidth/2;
      const peakCeiling=Math.min(nextColumn?nextColumn.left-labelHalfWidth-8:
        bounds.right+ROUTE_MARGIN-labelHalfWidth,bounds.right+ROUTE_MARGIN-labelHalfWidth);
      const skip=new Set([relation.sourceId,relation.targetId]);
      let best=null,bestScore=Infinity,rejectedCards=0;
      const labelBlockers=new Map();
      if(relation.sourceId===relation.targetId){
        const desiredOffset=Math.max(36,labelHalfWidth+6),centers=[sy,
          source.y+source.height*.25,source.y+source.height*.5,source.y+source.height*.75]
          .sort((first,second)=>Math.abs(first-sy)-Math.abs(second-sy));
        for(const centerY of centers){
          for(const offset of [desiredOffset,desiredOffset+12,desiredOffset+24]){
            const peakX=sx+offset;if(peakX>peakCeiling)continue;
            const controlX=sx+offset*4/3,curve={sx,sy:centerY,cx1:controlX,cy1:centerY-26,
              cx2:controlX,cy2:centerY+26,tx:sx,ty:centerY};
            const peak=curvePeak(curve),labelBox={left:peak.x-labelBoxWidth/2,
              right:peak.x+labelBoxWidth/2,top:peak.y-labelBoxHeight/2,
              bottom:peak.y+labelBoxHeight/2};
            const issue=labelIssue(labelBox,positions,labels,treeSamples,relation);
            if(issue){labelBlockers.set(issue,(labelBlockers.get(issue)||0)+1);continue;}
            const samples=relationSamples(curve,120);
            if(routeCollisions(samples,obstacles,skip)!==0){rejectedCards++;continue;}
            const score=bundleHits(samples,routes)*24+Math.abs(offset-desiredOffset)*.2+
              Math.abs(centerY-sy)*.3;
            if(score<bestScore){bestScore=score;best={path:`M${sx} ${centerY} C${controlX} ${centerY-26} ${controlX} ${centerY+26} ${sx} ${centerY}`,
              peak,labelBox,samples};}
          }
          if(best&&bestScore===0)break;
        }
      }else if(sourceDepth===targetDepth&&gap<48){
        const laneX=column.right+gap/2;
        const points=[{x:sx,y:sy},{x:laneX,y:sy},{x:laneX,y:ty},{x:tx,y:ty}];
        const samples=polylineSamples(points,24),peak={x:laneX,y:(sy+ty)/2};
        const labelBox={left:peak.x-labelBoxWidth/2,right:peak.x+labelBoxWidth/2,
          top:peak.y-labelBoxHeight/2,bottom:peak.y+labelBoxHeight/2};
        if(!labelIssue(labelBox,positions,labels,treeSamples,relation)&&routeCollisions(samples,obstacles,skip)===0)
          best={path:`M${sx} ${sy} H${laneX} V${ty} H${tx}`,peak,labelBox,samples};
      }else{
        const naturalBulge=(nextColumn?8:labelHalfWidth+6)+
          Math.min(span*.04,20)+Math.min(rank,12)*1.2+pairIndex*2;
        const labelInsideGap=sourceDepth===targetDepth?column.right+labelHalfWidth+4:column.right;
        const desiredPeak=Math.min(peakCeiling,Math.max(labelInsideGap,
          column.right+Math.min(48,naturalBulge)));
        const peakTargets=[desiredPeak,Math.min(peakCeiling,desiredPeak+8),
          Math.max(endpointRight+4,desiredPeak-8)].filter((value,index,values)=>
          value<=peakCeiling&&value>endpointRight+2&&values.indexOf(value)===index);
        const routeOffsets=[0,24,-24,48,-48,72,-72,96,-96,120,-120,144,-144];
        let safeCandidates=0;
        for(const peakTarget of peakTargets){
          const control1=Math.min(peakTarget,sx+
            Math.max(4,Math.min(24,(peakTarget-endpointRight)*.45)));
          const seed={sx,sy,cx1:control1,cx2:0,cy1:sy,cy2:ty,tx,ty};
          const baseCurve={...seed,cx2:controlForPeak(seed,peakTarget)},basePeak=curvePeak(baseCurve);
          const verticalFactor=3*basePeak.t*(1-basePeak.t);
          const labelCenters=freeLabelCenters(basePeak.x,labelBoxWidth,labelBoxHeight,positions,labels,treeSamples,cardBounds,basePeak.y,relation);
          const labelOffsets=verticalFactor<.05?[]:
            labelCenters
              .map(center=>(center-basePeak.y)/verticalFactor).filter(offset=>Math.abs(offset)<=160);
          const offsets=[...new Set([...labelOffsets,...routeOffsets])];
          for(const offset of offsets){
            const curve={...baseCurve,cy1:sy+offset,cy2:ty+offset},peak=curvePeak(curve);
            if(peak.x<endpointRight+2||peak.x>peakCeiling+1)continue;
            const labelBox={left:peak.x-labelBoxWidth/2,right:peak.x+labelBoxWidth/2,
              top:peak.y-labelBoxHeight/2,bottom:peak.y+labelBoxHeight/2};
            const issue=labelIssue(labelBox,positions,labels,treeSamples,relation);
            if(issue){labelBlockers.set(issue,(labelBlockers.get(issue)||0)+1);continue;}
            const sampleCount=Math.max(160,Math.ceil((peak.x-Math.min(sx,tx)+
              Math.abs(offset)*2+Math.abs(ty-sy))/4));
            const samples=relationSamples(curve,sampleCount);
            if(routeCollisions(samples,obstacles,skip)!==0){rejectedCards++;continue;}
            const bundles=bundleHits(samples,routes),score=bundles*24+
              Math.abs(offset)*.3+Math.abs(peak.x-desiredPeak)*.2;
            if(score<bestScore){bestScore=score;best={
              path:`M${sx} ${sy} C${curve.cx1} ${curve.cy1} ${curve.cx2} ${curve.cy2} ${tx} ${ty}`,
              peak,labelBox,samples
            };}
            safeCandidates++;if(bundles<=5||safeCandidates>=6)break;
          }
          if(best&&(bestScore<24*6||safeCandidates>=6))break;
        }
      }
      if(!best)throw new Error(`关系无法避开卡片或标签：${relation.sourceId} → ${relation.targetId}（${title}）；${JSON.stringify([...labelBlockers])}；路径冲突 ${rejectedCards}`);
      const {path,peak,labelBox,samples}=best;
      const group=svgElement("g",{});
      group.appendChild(svgElement("path",{class:"relation-edge",
        d:path,
        "marker-end":"url(#relation-arrow)","data-source":relation.sourceId,
        "data-target":relation.targetId,"data-title":relation.title}));
      const label=drawLabel(group,svgElement,lines,peak.x,peak.y,labelBoxWidth,labelBoxHeight,relation);
      layer.appendChild(group);labels.push({...label,sourceId:relation.sourceId,targetId:relation.targetId});addRouteToIndex(samples,routes);
      bounds.left=Math.min(bounds.left,...samples.map(point=>point.x),labelBox.left);
      bounds.right=Math.max(bounds.right,...samples.map(point=>point.x),labelBox.right);
      bounds.top=Math.min(bounds.top,...samples.map(point=>point.y),labelBox.top);
      bounds.bottom=Math.max(bounds.bottom,...samples.map(point=>point.y),labelBox.bottom);
    }
    return {x:bounds.left,y:bounds.top,width:bounds.right-bounds.left,height:bounds.bottom-bounds.top};
  }

  window.XYRepoMapRelations={draw};
})();
